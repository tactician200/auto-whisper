"""POST /tts — synthesize speech for the given text and return audio bytes.

The service is compute-only: it generates audio and returns the bytes. The
client (menubar daemon) is responsible for playback. This keeps the service
portable to non-local clients (iPad v6) where playback must happen client-side
regardless.

Body: JSON {text, backend?, voice?}.
Response: raw audio bytes (mp3 for google/edge, aiff for macos) with metadata
in custom headers — keeps the body free of base64 overhead.

Headers in response:
  X-TTS-Backend     resolved backend after fallback chain (e.g. "edge" or "macos")
  X-TTS-Format      "mp3" or "aiff" — extension to write before playing
  X-TTS-Duration-S  generation time in seconds (rounded to 3 decimals)

Auth: X-Auth-Token, same as the other endpoints.
"""

import logging
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field

from auto_whisper.voice_agent import BACKENDS, DEFAULT_BACKEND, synthesize
from auto_whisper_service import SERVICE_NAME
from auto_whisper_service.auth import AUTH_HEADER, verify_token

logger = logging.getLogger(__name__)

router = APIRouter()

# Cap text size at the wire boundary. TTS providers all enforce their own limits
# (Edge ~5K chars per request, Google 5K, etc.), but rejecting early avoids
# uploading megabytes of text only to fail downstream.
MAX_TTS_CHARS = 5_000


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Text to synthesize")
    backend: str | None = Field(
        default=None,
        description=f"One of: {', '.join(BACKENDS)}. Defaults to {DEFAULT_BACKEND!r}.",
    )
    voice: str | None = Field(
        default=None,
        description="Backend-specific voice name. None → backend default.",
    )


def _require_auth(x_auth_token: str | None = Header(default=None, alias=AUTH_HEADER)) -> None:
    if not verify_token(x_auth_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing auth token",
            headers={"WWW-Authenticate": f"Token realm={SERVICE_NAME}"},
        )


@router.post("/tts", dependencies=[Depends(_require_auth)])
async def tts(req: TTSRequest) -> Response:
    if len(req.text) > MAX_TTS_CHARS:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"text exceeds {MAX_TTS_CHARS} chars",
        )

    backend = req.backend or DEFAULT_BACKEND
    if backend not in BACKENDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown backend {backend!r}; valid: {list(BACKENDS)}",
        )

    t0 = time.time()
    try:
        result = synthesize(req.text, backend=backend, voice=req.voice)
    except Exception as e:
        # synthesize() catches per-backend exceptions and walks the fallback
        # chain internally — reaching here means a programming error.
        logger.exception("synthesize raised unexpectedly")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"internal tts error: {e.__class__.__name__}",
        )
    elapsed = time.time() - t0

    if result is None:
        # Every backend in the fallback chain failed. 502 conveys "we tried,
        # the upstream stack didn't deliver" — distinct from auth/validation 4xx.
        logger.warning(f"tts({backend}, {len(req.text)} chars): all backends failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="all TTS backends failed",
        )

    audio_bytes, ext = result
    media_type = "audio/mpeg" if ext == "mp3" else "audio/aiff"

    logger.info(
        f"tts({backend}): {len(req.text)} chars in {elapsed:.2f}s → "
        f"{len(audio_bytes)} bytes ({ext})"
    )

    return Response(
        content=audio_bytes,
        media_type=media_type,
        headers={
            "X-TTS-Backend": backend,
            "X-TTS-Format": ext,
            "X-TTS-Duration-S": f"{elapsed:.3f}",
        },
    )
