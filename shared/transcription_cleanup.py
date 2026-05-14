"""Post-transcription cleanup — shared between v4.2 daemon and v5 service.

Whisper occasionally hallucinates boilerplate ("subtítulos realizados por
la comunidad...", YouTube intro phrases) on near-silent or short audio.
This module strips those artifacts plus normalizes whitespace.

Single source of truth: any new artifact pattern goes in EXACT_ARTIFACTS
below. Both v4.2 daemon (eventually) and the service must use this module
so cleanup behavior stays consistent across paths.
"""

import unicodedata


EXACT_ARTIFACTS: frozenset[str] = frozenset({
    "subtitulos realizados por la comunidad de amara org",
    "subtitulos por la comunidad de amara org",
    "gracias por ver el video",
    "suscribete al canal",
    "hola buenos dias",
})


def normalize_text(text: str) -> str:
    """Lowercase + strip diacritics + drop punctuation + collapse whitespace.

    Used for artifact comparison only — never returned to the user.

    Punctuation stripping is critical: Whisper sometimes emits artifacts
    with trailing punctuation ("Subtítulos por la comunidad de Amara.org",
    "Hola, buenos días.") that would otherwise sneak past exact match.
    """
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    # Replace any non-alphanumeric / non-whitespace char with a space,
    # then collapse runs of whitespace.
    text = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text)
    return " ".join(text.split())


def clean_transcription(text: str) -> str:
    """Strip surrounding noise, collapse internal whitespace, drop known
    Whisper hallucination artifacts.

    Returns "" (empty string) when the entire transcription is an artifact
    so callers can distinguish "real but empty result" from "no result".
    """
    text = text.strip(" \n\t-–—")
    while "  " in text:
        text = text.replace("  ", " ")

    normalized = normalize_text(text)
    if not normalized:
        return ""
    if normalized in EXACT_ARTIFACTS:
        return ""
    return text
