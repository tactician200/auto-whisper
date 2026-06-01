"""Voice action registry — the single source of truth for the intent router.

Each VoiceAction declares how a detected intent maps to an execution path:
which LLM engine, which shared.processing mode (callable key in MODES), what
params to extract from the spoken phrase, and how it surfaces in the HUD.

Adding a capability = adding a row here. The pipeline (router → daemon dispatch)
reads this registry; it never hardcodes action lists.

`exhibited` and the canonical-verb fast-path are ORTHOGONAL:
- `exhibited` gates the LLM classifier surface — True actions (translate, tone,
  organize, reply) are the user-medium communication core the classifier may
  suggest. It keeps prompt_coding/research/etc. from cluttering that surface.
- `canonical_verbs` gates the fast-path — if the user explicitly says the verb
  ("crea un prompt para…"), that action runs regardless of `exhibited`. An
  explicit command is intent enough; it doesn't need to be a "suggested" action.

So prompt_coding is exhibited=False (never auto-suggested) yet has verbs (you can
invoke it on demand). summarize is cut from the MVP: exhibited=False AND no verbs
(fully inert, reactivable by adding either).
"""

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class VoiceAction:
    id: str
    label: str                       # HUD chip text, e.g. "TRADUCIR"
    canonical_verbs: tuple[str, ...] # fast-path: matched anchored at phrase start
    engine: str                      # "groq" | "claude" | "" (dictate/raw)
    mode: str                        # key in shared.processing.MODES ("" for dictate)
    params_schema: tuple[str, ...] = ()   # expected param keys (target_lang, tone, ...)
    needs_payload: bool = False      # may read clipboard when dictation is instruction-only
    exhibited: bool = True           # shown to the classifier / user-medium surface


# Special fallback action — raw transcription pasted as-is. Never routed to an LLM.
DICTATE = VoiceAction(
    id="dictate", label="DICTAR", canonical_verbs=(), engine="", mode="",
    exhibited=False,
)


REGISTRY: dict[str, VoiceAction] = {
    "dictate": DICTATE,
    # --- MVP exhibited (user-medium communication core) ---
    "translate": VoiceAction(
        id="translate", label="TRADUCIR",
        canonical_verbs=(
            "traduce", "tradúceme", "traduceme", "traducir", "traducelo",
            "tradúcelo", "translate", "ponlo en", "pásalo a", "pasalo a",
        ),
        engine="groq", mode="translate",
        params_schema=("target_lang",), needs_payload=True,
    ),
    "tone": VoiceAction(
        id="tone", label="TONO",
        canonical_verbs=(
            "hazlo más", "hazlo mas", "ponlo más", "ponlo mas", "suena más",
            "suena mas", "más formal", "mas formal", "tono", "más amable",
            "mas amable", "más firme", "mas firme",
        ),
        engine="groq", mode="adjust_tone",
        params_schema=("tone",),
    ),
    "organize": VoiceAction(
        id="organize", label="ORGANIZAR",
        canonical_verbs=("organiza", "ordena", "limpia", "ordename", "organízame"),
        engine="groq", mode="organize_ideas",
    ),
    # SUMMARIZE cut from the MVP router surface (premortem: "resumir" is content
    # consumption, not text production at the cursor — collides with ORGANIZE).
    # Fully inert: no verbs (no fast-path) AND exhibited=False (no classifier).
    # Reactivate by restoring canonical_verbs and/or exhibited=True.
    "summarize": VoiceAction(
        id="summarize", label="RESUMIR",
        canonical_verbs=(),
        engine="groq", mode="summarize", exhibited=False,
    ),
    "reply": VoiceAction(
        id="reply", label="RESPONDER",
        canonical_verbs=(
            "responde a esto", "responde a", "responde", "contesta",
            "redacta una respuesta", "reply",
        ),
        engine="claude", mode="reply_message",
        needs_payload=True,
    ),
    # --- Advanced (hidden from classifier, but verb-invokable on demand) ---
    # prompt_coding is ALSO the no-command default (daemon → classify_intent),
    # so the explicit verbs just let the user force "make this a prompt".
    "prompt_coding": VoiceAction(
        id="prompt_coding", label="PROMPT",
        canonical_verbs=(
            "crea un prompt", "créame un prompt", "creame un prompt", "crea prompt",
            "créame el prompt", "creame el prompt", "hazme un prompt", "haz un prompt",
            "hazme el prompt", "genera un prompt", "genérame un prompt", "generame un prompt",
            "ármame un prompt", "armame un prompt", "arma un prompt",
            "optimiza en prompt", "optimízalo en prompt", "optimizalo en prompt",
            "convierte en prompt", "conviértelo en prompt", "conviertelo en prompt",
            "un prompt para", "prompt para", "prompt de",
        ),
        engine="groq", mode="optimize_prompt", exhibited=False,
    ),
    "prompt_writing": VoiceAction(
        id="prompt_writing", label="ESCRIBIR", canonical_verbs=(),
        engine="groq", mode="optimize_writing", exhibited=False,
    ),
    "research": VoiceAction(
        id="research", label="RESEARCH", canonical_verbs=(),
        engine="groq", mode="research_brief", exhibited=False,
    ),
    "decision_making": VoiceAction(
        id="decision_making", label="DECISIÓN", canonical_verbs=(),
        engine="groq", mode="decision_brief", exhibited=False,
    ),
}


def get(action_id: str) -> VoiceAction | None:
    """Return the VoiceAction for an id, or None if unknown."""
    return REGISTRY.get(action_id)


def exhibited_actions() -> list[VoiceAction]:
    """MVP-surface actions, in registry order — fed to the LLM classifier."""
    return [a for a in REGISTRY.values() if a.exhibited]


# Pre-compile one anchored regex per action that HAS verbs (exhibited or not —
# an explicit verb is an explicit request). Longest verbs first so "responde a
# esto" wins over "responde". Match is at the phrase start (after optional
# whitespace), word-boundary terminated.
def _compile() -> list[tuple[re.Pattern, VoiceAction]]:
    compiled: list[tuple[re.Pattern, VoiceAction]] = []
    for action in REGISTRY.values():
        if not action.canonical_verbs:
            continue
        verbs = sorted(action.canonical_verbs, key=len, reverse=True)
        alt = "|".join(re.escape(v) for v in verbs)
        compiled.append((re.compile(rf"^\s*(?:{alt})\b", re.IGNORECASE), action))
    return compiled


_VERB_PATTERNS = _compile()


def match_canonical_verb(text: str) -> VoiceAction | None:
    """Fast-path: if the phrase starts with a known canonical verb, return its
    action (exhibited or not). No LLM. Returns None when nothing matches (the
    router then falls through to its fallback). The longest match wins; ties
    broken by registry order.
    """
    if not text:
        return None
    best: tuple[int, VoiceAction] | None = None
    for pattern, action in _VERB_PATTERNS:
        m = pattern.match(text)
        if m:
            span = m.end()
            if best is None or span > best[0]:
                best = (span, action)
    return best[1] if best else None
