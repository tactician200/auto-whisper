"""FastAPI application factory.

The module-level `app` is what uvicorn imports. `create_app()` is exposed
for tests so each test can spin up an isolated instance if needed.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from auto_whisper_service import SCHEMA_VERSION, SERVICE_NAME, __version__
from auto_whisper_service.auth import get_or_create_token
from auto_whisper_service.routes import health, process, transcribe, tts, version

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Modern FastAPI lifespan handler — replaces deprecated on_event."""
    logger.info(f"{SERVICE_NAME} {__version__} (schema={SCHEMA_VERSION}) ready")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


def create_app() -> FastAPI:
    """Build a FastAPI instance with all routes registered.

    Side effects on first call:
    - Ensures the auth token file exists (creates one if missing).
    - Logs the token location (NOT the token value).
    """
    app = FastAPI(
        title=SERVICE_NAME,
        version=__version__,
        description="Local HTTP service for auto-whisper transcription, processing, and TTS.",
        openapi_url=None,  # disabled in v5 phase 1; revisit when public API stabilizes
        docs_url=None,
        redoc_url=None,
        lifespan=_lifespan,
    )

    # Eagerly ensure the token exists so the first request doesn't race
    # token creation between processes.
    get_or_create_token()

    app.include_router(health.router)
    app.include_router(version.router)
    app.include_router(transcribe.router)
    app.include_router(process.router)
    app.include_router(tts.router)

    return app


app = create_app()
