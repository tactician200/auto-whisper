"""POST /process — run an LLM mode on user-supplied text.

Modes (matches shared.processing.MODES):
- summarize        Reduce to 2-3 voice-ready sentences
- explain          Voice-friendly conversational explanation (default)
- explain_paste    Same content but structured for paste (allows bullets)
- organize_ideas   Clean dictated text — strip filler, keep meaning
- optimize_prompt  Restructure into a Claude Code 4-section prompt
- optimize_writing Restructure into a writing brief OR polished draft
- research_brief   Restructure into a research brief (Question/Context/Scope/Sources/Output)
- decision_brief   Restructure into a decision brief (Decision/Options/Criteria/Risks/Open questions)

Auth: same X-Auth-Token contract as the rest of the service.
Body: JSON. (Multipart was used for /transcribe because audio is binary;
text fits cleanly in JSON.)

Response: { result: str | None, mode: str, duration_s: float }.
result=None when the underlying LLM call returns None (no API key, upstream
exception). 502 only when the request was malformed at the boundary —
runtime LLM failures degrade to result=None so the client can decide
whether to retry or fall back.
"""

import logging
import time

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from auto_whisper_service import SERVICE_NAME
from auto_whisper_service.auth import AUTH_HEADER, verify_token
from shared.processing import MAX_INPUT_CHARS, MODES

logger = logging.getLogger(__name__)

router = APIRouter()

# Reject runaway payloads at the boundary. shared.processing also truncates
# internally (MAX_INPUT_CHARS) but enforcing a hard ceiling on the wire keeps
# the service from buffering megabytes pointlessly.
MAX_TEXT_CHARS = MAX_INPUT_CHARS * 4  # 16 KB-ish — generous headroom on top of internal cap


class ProcessRequest(BaseModel):
    mode: str = Field(..., description="One of: summarize | explain | explain_paste | organize_ideas | optimize_prompt | optimize_writing | research_brief | decision_brief | classify_intent")
    text: str = Field(..., min_length=1, description="Text to process")


class ProcessResponse(BaseModel):
    result: str | None
    mode: str
    duration_s: float


def _require_auth(x_auth_token: str | None = Header(default=None, alias=AUTH_HEADER)) -> None:
    if not verify_token(x_auth_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing auth token",
            headers={"WWW-Authenticate": f"Token realm={SERVICE_NAME}"},
        )


@router.post(
    "/process",
    response_model=ProcessResponse,
    dependencies=[Depends(_require_auth)],
)
async def process(req: ProcessRequest) -> ProcessResponse:
    if len(req.text) > MAX_TEXT_CHARS:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"text exceeds {MAX_TEXT_CHARS} chars",
        )

    fn = MODES.get(req.mode)
    if fn is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown mode {req.mode!r}; valid: {sorted(MODES.keys())}",
        )

    t0 = time.time()
    try:
        result = fn(req.text)
    except Exception as e:
        # shared.processing already swallows Groq exceptions and returns None,
        # so reaching here means a programming error in the mode callable —
        # surface as 500 for visibility.
        logger.exception(f"process({req.mode}) raised unexpectedly")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"internal processing error: {e.__class__.__name__}",
        )
    elapsed = time.time() - t0

    logger.info(
        f"process({req.mode}): {len(req.text)} chars in {elapsed:.2f}s → "
        f"{len(result) if result else 0} chars"
    )

    return ProcessResponse(
        result=result,
        mode=req.mode,
        duration_s=round(elapsed, 3),
    )
