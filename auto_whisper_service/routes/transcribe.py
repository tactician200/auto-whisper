"""POST /transcribe — accept audio (multipart), return transcribed text.

Phase 2.1 scope:
- Cloud only (Groq whisper-large-v3). Local whisper.cpp fallback is Phase 2.x.
- WAV input only. Other formats rejected at boundary; no on-the-fly conversion.
- Cleanup applied via shared.transcription_cleanup.clean_transcription.
- Vocabulary hint (`prompt` param of Groq) is plumbed but not populated;
  Vocab Manager fills it in Phase 2.4.

Auth: same X-Auth-Token contract as /version.
"""

import io
import logging
import time
import wave

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel

from auto_whisper_service import SERVICE_NAME
from auto_whisper_service.auth import AUTH_HEADER, verify_token
from shared.config import GROQ_API_KEY_DICTATION
from shared.groq_client import get_groq_client
from shared.transcription_cleanup import clean_transcription
from shared.vocab import VocabManager, get_default_db_path

logger = logging.getLogger(__name__)

router = APIRouter()

# Sized to fit ~5 minutes of 16 kHz mono PCM (≈10 MB) plus headroom.
# Matches v4.2 MAX_RECORDING_SECONDS (300) at SAMPLE_RATE=16000, 16-bit.
MAX_AUDIO_BYTES = 12 * 1024 * 1024  # 12 MB

GROQ_TRANSCRIPTION_MODEL = "whisper-large-v3"
DEFAULT_LANGUAGE = "es"


# Lazy module-level VocabManager — opens DB on first use, reused across requests.
_vocab_manager: VocabManager | None = None


def _get_vocab_manager() -> VocabManager:
    global _vocab_manager
    if _vocab_manager is None:
        _vocab_manager = VocabManager(get_default_db_path())
    return _vocab_manager


class TranscribeResponse(BaseModel):
    text: str | None
    language: str | None
    duration_s: float
    cleaned: bool  # True if cleanup stripped artifacts (transparency for clients)


def _require_auth(x_auth_token: str | None = Header(default=None, alias=AUTH_HEADER)) -> None:
    if not verify_token(x_auth_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing auth token",
            headers={"WWW-Authenticate": f"Token realm={SERVICE_NAME}"},
        )


def _validate_wav(audio_bytes: bytes) -> None:
    """Reject non-WAV uploads at the boundary.

    Groq accepts other formats but for v5.0-dev we keep the contract narrow:
    callers (v5 menubar) always send WAV. This avoids ambiguity about which
    formats are first-class vs. accidentally working."""
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
            if wf.getnchannels() != 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"audio must be mono, got {wf.getnchannels()} channels",
                )
    except wave.Error as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid WAV: {e}",
        )


@router.post(
    "/transcribe",
    response_model=TranscribeResponse,
    dependencies=[Depends(_require_auth)],
)
async def transcribe(
    audio: UploadFile = File(..., description="WAV audio (mono, 16-bit PCM recommended)"),
    language: str | None = Form(default=DEFAULT_LANGUAGE, description="Language hint (ISO 639-1)"),
    project: str | None = Form(default=None, description="Project tag for vocabulary scoping"),
) -> TranscribeResponse:
    if not GROQ_API_KEY_DICTATION:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Groq API key not configured on this service",
        )

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="empty audio payload",
        )
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"audio exceeds {MAX_AUDIO_BYTES} bytes",
        )

    _validate_wav(audio_bytes)

    # Build vocabulary hint for Whisper (empty string when no vocab matches).
    vocab = _get_vocab_manager()
    hint = vocab.get_hint(project=project, language=language)

    client = get_groq_client()
    t0 = time.time()
    try:
        params: dict = {
            "model": GROQ_TRANSCRIPTION_MODEL,
            "file": (audio.filename or "audio.wav", audio_bytes),
            "response_format": "text",
        }
        if language:
            params["language"] = language
        if hint:
            params["prompt"] = hint
        result = client.audio.transcriptions.create(**params)
    except Exception as e:
        logger.error(f"Groq transcription failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"upstream transcription failure: {e.__class__.__name__}",
        )
    elapsed = time.time() - t0

    if not isinstance(result, str):
        logger.error(f"unexpected Groq response type: {type(result)}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="unexpected upstream response shape",
        )

    raw_text = result.strip()
    cleaned_text = clean_transcription(raw_text) if raw_text else ""
    cleaned = bool(raw_text) and (cleaned_text != raw_text)

    # Apply vocab corrections to the cleaned text.
    if cleaned_text:
        corrected = vocab.apply_corrections(cleaned_text, project=project, language=language)
    else:
        corrected = ""
    final_text = corrected if corrected else None

    logger.info(
        f"transcribe: {len(audio_bytes)}B in {elapsed:.2f}s → "
        f"{len(final_text) if final_text else 0} chars"
        f"{' (cleaned)' if cleaned else ''}"
        f"{f' (project={project})' if project else ''}"
        f"{' (hint=' + str(len(hint)) + 'c)' if hint else ''}"
    )

    return TranscribeResponse(
        text=final_text,
        language=language,
        duration_s=round(elapsed, 3),
        cleaned=cleaned,
    )
