"""LLM text processing — summarize, explain, organize, optimize.

Pure logic, zero macOS deps. Imported by both:
- auto_whisper.text_processor (daemon, when USE_SERVICE_PROCESSING is off)
- auto_whisper_service.routes.process (service, when flag is on)

Public API (stable contract for the v5 strangler-fig migration):
    summarize(text) -> str | None
    explain(text, for_voice=True) -> str | None
    organize_ideas(text) -> str | None
    optimize_prompt(text) -> str | None

Each returns None on failure (missing API key, Groq exception). Truncation
to MAX_INPUT_CHARS happens internally — callers pass raw text and get
trimmed input semantics for free.
"""

import logging

from shared.config import GROQ_API_KEY_DICTATION
from shared.groq_client import get_groq_client
from shared.prompts import (
    PROMPT_DECISION,
    PROMPT_EXPLAIN_PASTE,
    PROMPT_EXPLAIN_VOICE,
    PROMPT_OPTIMIZE,
    PROMPT_ORGANIZE,
    PROMPT_RESEARCH,
    PROMPT_SUMMARIZE,
    PROMPT_WRITING,
)

logger = logging.getLogger(__name__)


MAX_INPUT_CHARS = 4000
DEFAULT_MAX_COMPLETION_TOKENS = 2000
OPTIMIZE_MAX_COMPLETION_TOKENS = 1500
LLM_MODEL = "llama-3.3-70b-versatile"
LLM_TEMPERATURE = 0.3


def _call_groq(prompt: str, max_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS) -> str | None:
    """Call Groq LLM. Returns trimmed text or None on failure."""
    if not GROQ_API_KEY_DICTATION:
        logger.error("No Groq API key configured")
        return None
    try:
        client = get_groq_client()
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=LLM_TEMPERATURE,
            max_completion_tokens=max_tokens,
        )
        text = response.choices[0].message.content.strip()
        logger.info(f"LLM response ({len(text)} chars): {text[:100]}...")
        return text
    except Exception as e:
        logger.error(f"Groq LLM failed: {e}")
        return None


def summarize(text: str) -> str | None:
    """Summarize text to 2-3 sentences. Returns voice-ready Spanish prose."""
    return _call_groq(PROMPT_SUMMARIZE.format(text=text[:MAX_INPUT_CHARS]))


def explain(text: str, for_voice: bool = True) -> str | None:
    """Explain text. for_voice=True: conversational, no markdown. False: structured for paste."""
    prompt = PROMPT_EXPLAIN_VOICE if for_voice else PROMPT_EXPLAIN_PASTE
    return _call_groq(prompt.format(text=text[:MAX_INPUT_CHARS]))


def organize_ideas(text: str) -> str | None:
    """Turn rough dictated ideas or clipboard text into clean prose ready to paste."""
    return _call_groq(PROMPT_ORGANIZE.format(text=text[:MAX_INPUT_CHARS]))


def optimize_prompt(text: str, emphasis: str | None = None) -> str | None:
    """Convert rough spoken instructions into a structured Claude Code prompt.

    emphasis: optional category key from the picker sub-menu — when set,
    appends a directive telling the LLM to lead with that section. Valid
    values: 'context' | 'task' | 'details' | 'constraints'. None = balanced
    output across all four sections (default behaviour).
    """
    from shared.prompts import PROMPT_OPTIMIZE_EMPHASIS
    # Service path sends emphasis inline as an [[EMPHASIS:key]] suffix so the
    # /process wire format doesn't need a new field. Extract it here so direct
    # callers and service-routed callers both end up at the same place.
    import re
    if emphasis is None:
        m = re.search(r"\[\[EMPHASIS:([a-z_]+)\]\]\s*$", text)
        if m:
            emphasis = m.group(1)
            text = text[:m.start()].rstrip()
    emphasis_directive = ""
    if emphasis:
        emphasis_directive = PROMPT_OPTIMIZE_EMPHASIS.get(emphasis.lower(), "")
        if emphasis_directive:
            emphasis_directive = "\n\n" + emphasis_directive
    return _call_groq(
        PROMPT_OPTIMIZE.format(
            text=text[:MAX_INPUT_CHARS],
            emphasis=emphasis_directive,
        ),
        max_tokens=OPTIMIZE_MAX_COMPLETION_TOKENS,
    )


_CLASSIFIER_PROMPT = """Classify the user's spoken transcript by intent. Pick exactly one of:

- raw:             casual note, message, observation, single thought, short remark
- organize:        a rough cluster of ideas that needs cleanup before sharing
- prompt_coding:   instructions/request for an AI coding assistant (Claude Code, Cursor, etc.)
                    — signals: file paths, function names, "implement", "fix", "refactor",
                      "write a script", "build a", technical jargon, code-aware steps
- prompt_writing:  instructions to draft a written piece (email, post, message, article,
                    announcement) OR the dictated body of such a piece itself
                    — signals: "escribe un email", "redacta", "necesito un texto",
                      "post de", "artículo sobre", mentions audience/tone/length,
                      dictated prose that reads as a draft rather than instructions
- research:        a question or topic the user wants investigated before acting
                    — signals: "investiga", "research", "busca info sobre",
                      "compara X vs Y", "estado del arte", "qué se sabe de",
                      "ventajas y desventajas de", framed as an open question
                      about external information rather than a task to execute
- decision_making: the user is weighing options and wants the choice structured
                    — signals: "debo decidir", "qué elijo", "pros y contras",
                      "decisión entre", "should I", "vale la pena", "me conviene",
                      mentions of trade-offs, alternatives, criteria

Heuristics:
- Under 6 words → raw
- A single question with no technical depth → raw
- "do X, then Y" style task with code references → prompt_coding
- "escribe / redacta / draft / write a [piece]" → prompt_writing
- "investiga / research / compara / qué se sabe de" → research
- "debo decidir / pros y contras / qué elijo / vale la pena" → decision_making
- Free-flowing ideas to refine, no clear deliverable → organize

Reply with EXACTLY ONE TOKEN: raw, organize, prompt_coding, prompt_writing, research, or decision_making.

TRANSCRIPT:
{text}"""


_VALID_INTENTS = (
    "raw",
    "organize",
    "prompt_coding",
    "prompt_writing",
    "research",
    "decision_making",
)


# Heuristic signal tables — kept module-level so tests can introspect them
# and so the order of checks is explicit. Order matters: the first table
# that matches wins, so put the highest-precision intent first.
_CODING_SIGNALS: tuple[str, ...] = (
    "implementa", "implement ", "fix ", "refactor", "write a script",
    "crea un script", "build a ", "function ", ".py", ".ts", ".tsx",
    ".js", "import ", "def ", "class ", "claude code", "cursor",
)
_WRITING_SIGNALS: tuple[str, ...] = (
    "escribe un email", "escribe un mensaje", "redacta", "necesito un texto",
    "post de ", "post sobre", "artículo sobre", "articulo sobre",
    "write an email", "write a post", "draft a ", "write a message",
    "para publicar", "tweet sobre", "comunicado",
)
# Decision before research because "pros y contras de X vs Y" implies the
# user is weighing options, not gathering external info.
_DECISION_SIGNALS: tuple[str, ...] = (
    "debo decidir", "qué elijo", "que elijo", "qué escojo", "que escojo",
    "decisión entre", "decision entre", "should i ", "vale la pena",
    "me conviene", "me sirve más", "me sirve mas",
    "elegir entre", "decidir entre",
    "pros y contras", "ventajas y desventajas",
)
_RESEARCH_SIGNALS: tuple[str, ...] = (
    "investiga ", "investigar ", "busca info", "busca información",
    "compara ", "comparar ", "estado del arte", "qué se sabe de",
    "que se sabe de", "research ", "compare ",
    "alternativas a ", "alternativas para ",
)


def _first_match(text: str, signals: tuple[str, ...]) -> str | None:
    """Return the first signal in `signals` that appears in `text`, or None."""
    for sig in signals:
        if sig in text:
            return sig
    return None


def classify_intent(text: str) -> str:
    """Cheap LLM classifier for the smart-hotkey path.

    Returns one of "raw", "organize", "prompt_coding", "prompt_writing",
    "research", "decision_making". Falls back to "raw" on any parse failure
    — never blocks the paste flow. Cheap heuristics short-circuit the LLM
    call when the answer is obvious.

    Logs every decision path (heuristic match or LLM call + latency) at
    INFO level under [classifier] so the daemon log is enough to debug
    misclassifications without rerunning anything.
    """
    import time

    stripped = (text or "").strip()
    word_count = len(stripped.split())
    if word_count < 6:
        logger.info("[classifier] raw (heuristic: <6 words, n=%d)", word_count)
        return "raw"
    lowered = stripped.lower()

    for intent, signals in (
        ("prompt_coding", _CODING_SIGNALS),
        ("prompt_writing", _WRITING_SIGNALS),
        ("decision_making", _DECISION_SIGNALS),
        ("research", _RESEARCH_SIGNALS),
    ):
        matched = _first_match(lowered, signals)
        if matched is not None:
            logger.info("[classifier] %s (heuristic: %r)", intent, matched)
            return intent

    # LLM call for the ambiguous middle.
    t0 = time.time()
    result = _call_groq(
        _CLASSIFIER_PROMPT.format(text=stripped[:MAX_INPUT_CHARS]),
        max_tokens=8,
    )
    elapsed = time.time() - t0
    if not result:
        logger.info("[classifier] raw (LLM returned None after %.2fs)", elapsed)
        return "raw"
    cleaned = result.strip().lower().rstrip(".,!?\n")
    if cleaned in _VALID_INTENTS:
        logger.info(
            "[classifier] %s (LLM %.2fs, input_len=%d, output=%r)",
            cleaned, elapsed, len(stripped), result,
        )
        return cleaned
    # Legacy "prompt" output (older classifier responses) → assume coding.
    if cleaned == "prompt":
        logger.info(
            "[classifier] prompt_coding (LLM %.2fs, legacy 'prompt' output)",
            elapsed,
        )
        return "prompt_coding"
    logger.info(
        "[classifier] raw (LLM %.2fs, unrecognized output=%r)",
        elapsed, result,
    )
    return "raw"


def optimize_writing(text: str) -> str | None:
    """Restructure dictated writing instructions into a brief OR polish a
    dictated draft directly. Always preserves the source language (unlike
    optimize_prompt() which forces English for AI-assistant use)."""
    return _call_groq(
        PROMPT_WRITING.format(text=text[:MAX_INPUT_CHARS]),
        max_tokens=OPTIMIZE_MAX_COMPLETION_TOKENS,
    )


def research_brief(text: str) -> str | None:
    """Restructure dictated questions / topics into a research brief ready
    for an AI assistant or human researcher. Preserves source language."""
    return _call_groq(
        PROMPT_RESEARCH.format(text=text[:MAX_INPUT_CHARS]),
        max_tokens=OPTIMIZE_MAX_COMPLETION_TOKENS,
    )


def decision_brief(text: str) -> str | None:
    """Restructure dictated decision-making thoughts into a structured
    decision brief (options, criteria, risks, open questions). Preserves
    source language. Never decides for the user."""
    return _call_groq(
        PROMPT_DECISION.format(text=text[:MAX_INPUT_CHARS]),
        max_tokens=OPTIMIZE_MAX_COMPLETION_TOKENS,
    )


# Mode → callable map. Used by the service /process endpoint and the future
# routing module to dispatch by mode name. Adding a mode here is the canonical
# extension point — keep daemon menu wiring in sync separately.
MODES: dict[str, callable] = {
    "summarize": summarize,
    "explain": explain,                    # default for_voice=True
    "explain_paste": lambda t: explain(t, for_voice=False),
    "organize_ideas": organize_ideas,
    "optimize_prompt": optimize_prompt,
    "optimize_writing": optimize_writing,
    "research_brief": research_brief,
    "decision_brief": decision_brief,
    "classify_intent": classify_intent,
}
