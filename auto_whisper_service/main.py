"""Standalone uvicorn launcher.

Run with:
    cd ~/src/auto-whisper-v5
    PYTHONPATH=. .venv/bin/python -m auto_whisper_service.main

Or via Makefile: `make run-service`.

Production (Phase 6) target: a separate LaunchAgent plist invoking this
entry point. For Phase 1 we only support manual launch — keeps the v4.2
LaunchAgent untouched.
"""

import logging
import sys

import uvicorn

from auto_whisper_service.config import (
    LOG_DIR,
    SERVICE_HOST,
    SERVICE_PORT,
    ensure_dirs,
)


def _configure_logging() -> None:
    """Stream to stderr at INFO. File handler can be added later — for
    Phase 1, stderr capture by the launcher (Makefile, future LaunchAgent)
    is sufficient."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        stream=sys.stderr,
    )


def main() -> None:
    ensure_dirs()
    _configure_logging()
    uvicorn.run(
        "auto_whisper_service.app:app",
        host=SERVICE_HOST,
        port=SERVICE_PORT,
        log_level="info",
        access_log=False,  # noisy on localhost; flip on for debugging
    )


if __name__ == "__main__":
    main()
