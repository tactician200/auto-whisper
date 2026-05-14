"""Service authentication via shared bearer token.

Phase 1 stub: token persisted in a file under Application Support with
mode 0600 (owner read/write only). Comparison is constant-time to prevent
timing attacks.

Phase 2+ target: store the token in macOS Keychain via the `security`
CLI. Migration path: read from Keychain first, fall back to file (this
module's get_token() should grow a Keychain branch).
"""

import logging
import secrets
from pathlib import Path

from auto_whisper_service.config import TOKEN_FILE, ensure_dirs

logger = logging.getLogger(__name__)

TOKEN_BYTES = 32
AUTH_HEADER = "X-Auth-Token"


def _generate_token() -> str:
    """Cryptographically random URL-safe token (~43 chars from 32 bytes)."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def _read_token(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        token = path.read_text().strip()
        return token or None
    except OSError as e:
        logger.warning(f"Could not read token file {path}: {e}")
        return None


def _write_token(path: Path, token: str) -> None:
    """Write with mode 0600 (owner-only)."""
    ensure_dirs()
    path.write_text(token + "\n")
    path.chmod(0o600)


def get_or_create_token() -> str:
    """Return the persisted token, generating + persisting one if absent.

    Idempotent across calls. Safe to invoke at every service startup.
    """
    existing = _read_token(TOKEN_FILE)
    if existing:
        return existing

    token = _generate_token()
    _write_token(TOKEN_FILE, token)
    logger.info(f"Generated new service token at {TOKEN_FILE}")
    return token


def verify_token(supplied: str | None) -> bool:
    """Constant-time check that supplied token matches persisted one.

    Returns False on missing input, missing token file, or mismatch.
    """
    if not supplied:
        return False
    expected = _read_token(TOKEN_FILE)
    if not expected:
        return False
    return secrets.compare_digest(supplied, expected)
