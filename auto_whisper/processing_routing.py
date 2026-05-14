"""LLM processing dispatcher — direct shared.processing vs via service.

Mirror of auto_whisper.transcription's routing pattern, but for LLM-mode
calls (summarize / explain / organize / optimize) instead of audio
transcription.

Toggle: env var AUTO_WHISPER_USE_SERVICE_PROCESSING=1 (default off).
Read once at module import — restart daemon to flip paths.

With flag OFF, calls go directly to shared.processing functions (preserves
v4.2 cloud behavior). With flag ON, calls route through ServiceClient.process()
which hits POST /process on the local service.

Public API matches shared.processing exactly so auto_whisper.text_processor
can re-export from here transparently.
"""

import logging
import os

from shared import processing as _direct

logger = logging.getLogger(__name__)


# Read once at import. Restart to flip.
USE_SERVICE_PROCESSING: bool = (
    os.environ.get("AUTO_WHISPER_USE_SERVICE_PROCESSING", "0") == "1"
)


def _via_service(mode: str, text: str) -> str | None:
    """Send (mode, text) to the local service and return its result string.

    Returns None on any failure — caller treats it the same as a None from
    shared.processing (no paste, log warning, user retries).
    """
    # Local import: get_service_client lives in auto_whisper.transcription;
    # importing at function-call time avoids circulars on rare load orders.
    from auto_whisper.transcription import get_service_client

    response = get_service_client().process(mode, text)
    if response is None:
        return None
    # The endpoint contract: response = { result, mode, duration_s }.
    # `result` may itself be None when the underlying shared.processing
    # call returned None — propagate that as the dispatcher's None.
    return response.get("result")


def _blocked_by_privacy(action: str) -> bool:
    """Privacy mode kills network-bound LLM calls. There's no local LLM
    fallback yet, so we return None and let the caller fall back to the
    raw transcript (existing 'returned empty' handling)."""
    from shared.user_profile import is_privacy_mode
    if is_privacy_mode():
        logger.info("Privacy mode on → skipping LLM %s, returning raw", action)
        return True
    return False


def summarize(text: str) -> str | None:
    if _blocked_by_privacy("summarize"):
        return None
    if USE_SERVICE_PROCESSING:
        return _via_service("summarize", text)
    return _direct.summarize(text)


def explain(text: str, for_voice: bool = True) -> str | None:
    if _blocked_by_privacy("explain"):
        return None
    if USE_SERVICE_PROCESSING:
        mode = "explain" if for_voice else "explain_paste"
        return _via_service(mode, text)
    return _direct.explain(text, for_voice=for_voice)


def organize_ideas(text: str) -> str | None:
    if _blocked_by_privacy("organize_ideas"):
        return None
    if USE_SERVICE_PROCESSING:
        return _via_service("organize_ideas", text)
    return _direct.organize_ideas(text)


def classify_intent(text: str) -> str:
    """Classifier never goes via the service path — short call, no privacy
    block needed since it returns a label, not user-visible text. Privacy
    mode does still gate downstream LLM action via _blocked_by_privacy on
    organize/optimize, so a "prompt" classification under privacy will still
    degrade to raw paste at the daemon layer."""
    return _direct.classify_intent(text)


def optimize_writing(text: str) -> str | None:
    if _blocked_by_privacy("optimize_writing"):
        return None
    if USE_SERVICE_PROCESSING:
        return _via_service("optimize_writing", text)
    return _direct.optimize_writing(text)


def optimize_prompt(text: str, emphasis: str | None = None) -> str | None:
    if _blocked_by_privacy("optimize_prompt"):
        return None
    if USE_SERVICE_PROCESSING:
        # Service contract: append emphasis as a suffix so the existing
        # endpoint contract stays unchanged. shared.processing on the other
        # side re-parses via the emphasis dict (no-op if no marker present).
        # Keep wire format simple: emphasis goes inline in the text payload.
        if emphasis:
            return _via_service("optimize_prompt", f"{text}\n\n[[EMPHASIS:{emphasis}]]")
        return _via_service("optimize_prompt", text)
    return _direct.optimize_prompt(text, emphasis=emphasis)


def research_brief(text: str) -> str | None:
    if _blocked_by_privacy("research_brief"):
        return None
    if USE_SERVICE_PROCESSING:
        return _via_service("research_brief", text)
    return _direct.research_brief(text)


def decision_brief(text: str) -> str | None:
    if _blocked_by_privacy("decision_brief"):
        return None
    if USE_SERVICE_PROCESSING:
        return _via_service("decision_brief", text)
    return _direct.decision_brief(text)
