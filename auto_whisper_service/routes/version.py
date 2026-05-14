"""Version probe — authenticated metadata about the running service.

Used by clients to:
- Verify their auth token is correct (401 means bad token)
- Detect schema mismatches between client and service (schema_version)
- Display "Service: vX.Y.Z" in UI

The version returned is auto_whisper_service.__version__ (the package's
own version). Schema version increments only when API contract changes
in a breaking way — independent from package version.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, status

from auto_whisper_service import SCHEMA_VERSION, SERVICE_NAME, __version__
from auto_whisper_service.auth import AUTH_HEADER, verify_token

router = APIRouter()


def _require_auth(x_auth_token: str | None = Header(default=None, alias=AUTH_HEADER)) -> None:
    if not verify_token(x_auth_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing auth token",
            headers={"WWW-Authenticate": f"Token realm={SERVICE_NAME}"},
        )


@router.get("/version", dependencies=[Depends(_require_auth)])
def version() -> dict:
    return {
        "service": SERVICE_NAME,
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
    }
