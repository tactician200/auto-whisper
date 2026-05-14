"""Service configuration — paths, ports, defaults.

Conventions:
- All paths under ~/Library/Application Support/auto-whisper/ (macOS standard).
- Localhost-only binding: never expose to network.
- Port chosen in private range with low collision risk; user can override
  via AUTO_WHISPER_SERVICE_PORT env var.
"""

import os
from pathlib import Path

HOME = Path.home()

APP_SUPPORT_DIR = HOME / "Library" / "Application Support" / "auto-whisper"
TOKEN_FILE = APP_SUPPORT_DIR / "service-token"

SERVICE_HOST = "127.0.0.1"
SERVICE_PORT = int(os.environ.get("AUTO_WHISPER_SERVICE_PORT", "8765"))

LOG_DIR = HOME / "Library" / "Logs" / "auto-whisper"


def ensure_dirs() -> None:
    """Create app-support and log directories if missing."""
    APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
