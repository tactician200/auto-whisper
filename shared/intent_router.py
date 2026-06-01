"""Intent router — turns a transcribed phrase into a RouteDecision.

The pipeline:
  1. Short-circuit: <6 words → dictate (a casual note is not a command).
  2. Fast-path: phrase starts with a canonical verb → that action, conf 1.0,
     no LLM call. Params (target_lang / tone) extracted locally by regex.
  3. Classifier: a cheap Groq call returns {intent, confidence} restricted to
     the exhibited actions. Params are still extracted locally (more robust than
     trusting the LLM to format them).
  4. Fallback: unparseable / unknown intent / confidence < threshold → dictate.
     The worst case is ALWAYS clean dictation — never a surprise action.

Runs entirely local (Groq direct); it returns a label, not user-visible text,
so it never goes through the service path. Privacy gating happens at the daemon
layer before route_intent is called.
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field

from shared import voice_actions
from shared.processing import MAX_INPUT_CHARS, _call_llm

logger = logging.getLogger(__name__)


def _threshold() -> float:
    try:
        return float(os.environ.get("AUTO_WHISPER_ROUTER_THRESHOLD", "0.6"))
    except ValueError:
        return 0.6


CONFIDENCE_THRESHOLD: float = _threshold()
_MIN_WORDS = 6


@dataclass(frozen=True)
class RouteDecision:
    action_id: str          # "translate" | "tone" | ... | "dictate"
    confidence: float
    params: dict = field(default_factory=dict)
    source: str = ""        # "short_circuit" | "verb_fastpath" | "classifier" | "fallback"


def _dictate(confidence: float, source: str) -> RouteDecision:
    return RouteDecision("dictate", confidence, {}, source)


# --- Local param extraction (regex, no LLM) ---

_LANG_MAP = {
    "inglés": "English", "ingles": "English", "english": "English",
    "español": "Spanish", "espanol": "Spanish", "castellano": "Spanish", "spanish": "Spanish",
    "francés": "French", "frances": "French", "french": "French",
    "alemán": "German", "aleman": "German", "german": "German",
    "italiano": "Italian", "italian": "Italian",
    "portugués": "Portuguese", "portugues": "Portuguese", "portuguese": "Portuguese",
    "japonés": "Japanese", "japones": "Japanese", "japanese": "Japanese",
    "chino": "Chinese", "mandarín": "Chinese", "chinese": "Chinese",
    "catalán": "Catalan", "catalan": "Catalan",
}

_TONES = (
    "formal", "informal", "casual", "amable", "firme", "cordial", "profesional",
    "serio", "amigable", "directo", "educado", "seco", "cercano", "entusiasta",
    "diplomático", "diplomatico", "relajado",
)


def _extract_target_lang(text: str) -> str:
    low = text.lower()
    for word, name in _LANG_MAP.items():
        if re.search(rf"\b{re.escape(word)}\b", low):
            return name
    return "English"  # dominant case: "tradúcelo al inglés"


def _extract_tone(text: str) -> str:
    low = text.lower()
    for tone in _TONES:
        if re.search(rf"\b{re.escape(tone)}\b", low):
            return tone
    return "formal"


def _params_for(action: voice_actions.VoiceAction, text: str) -> dict:
    if "target_lang" in action.params_schema:
        return {"target_lang": _extract_target_lang(text)}
    if "tone" in action.params_schema:
        return {"tone": _extract_tone(text)}
    return {}


# --- LLM classifier ---

def _router_prompt() -> str:
    lines = []
    for a in voice_actions.exhibited_actions():
        lines.append(f"- {a.id}")
    actions = "\n".join(lines)
    return f"""Classify the user's spoken request into exactly one intent. Options:

{actions}
- dictate:   a plain note, message, thought, or anything that is NOT one of the above actions

Guidance:
- translate:  user wants text in another language
- tone:       user wants the same message reworded (more formal/casual/amable/firme)
- organize:   user wants rough dictated ideas cleaned up
- summarize:  user wants something shortened to its essence
- reply:      user wants a reply drafted to a message
- dictate:    default — when unsure, choose dictate

Reply with ONLY a JSON object: {{"intent": "<one option>", "confidence": <0.0-1.0>}}
No other text.

REQUEST:
{{text}}"""


_ROUTER_PROMPT = _router_prompt()


def _parse_classifier(raw: str | None) -> tuple[str, float] | None:
    """Pull {intent, confidence} from the classifier's reply. None on any failure."""
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        intent = str(obj["intent"]).strip().lower()
        conf = float(obj.get("confidence", 0.0))
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None
    return intent, max(0.0, min(1.0, conf))


def route_intent(text: str, use_llm: bool = True) -> RouteDecision:
    """Map a transcript to a RouteDecision. Never raises for routing reasons;
    degrades to dictate on anything ambiguous or failed.

    use_llm=False restricts detection to the canonical-verb fast-path (zero LLM
    round-trips). The daemon uses this on the smart-dictation hotkey, where a
    no-command phrase falls through to the prompt classifier (classify_intent)
    instead — so paying for the router's own classifier here would mean a
    redundant second classification call.
    """
    stripped = (text or "").strip()
    word_count = len(stripped.split())
    if word_count < _MIN_WORDS:
        logger.info("[router] dictate (short-circuit: <%d words, n=%d)", _MIN_WORDS, word_count)
        return _dictate(1.0, "short_circuit")

    # Fast-path: canonical verb at the start.
    fast = voice_actions.match_canonical_verb(stripped)
    if fast is not None:
        params = _params_for(fast, stripped)
        logger.info("[router] %s (verb_fastpath, params=%s)", fast.id, params)
        return RouteDecision(fast.id, 1.0, params, "verb_fastpath")

    if not use_llm:
        logger.info("[router] dictate (no canonical verb, classifier disabled)")
        return _dictate(0.0, "fallback")

    # Classifier.
    t0 = time.time()
    # replace (not .format) — the prompt embeds literal JSON braces.
    raw = _call_llm(
        _ROUTER_PROMPT.replace("{text}", stripped[:MAX_INPUT_CHARS]),
        max_tokens=120, engine="groq",
    )
    elapsed = time.time() - t0
    parsed = _parse_classifier(raw)
    if parsed is None:
        logger.info("[router] dictate (classifier unparseable after %.2fs, raw=%r)", elapsed, raw)
        return _dictate(0.0, "fallback")

    intent, conf = parsed
    action = voice_actions.get(intent)
    if action is None or not action.exhibited or conf < CONFIDENCE_THRESHOLD:
        logger.info(
            "[router] dictate (fallback: intent=%r conf=%.2f thr=%.2f %.2fs)",
            intent, conf, CONFIDENCE_THRESHOLD, elapsed,
        )
        return _dictate(conf, "fallback")

    params = _params_for(action, stripped)
    logger.info("[router] %s (classifier conf=%.2f %.2fs, params=%s)", intent, conf, elapsed, params)
    return RouteDecision(intent, conf, params, "classifier")
