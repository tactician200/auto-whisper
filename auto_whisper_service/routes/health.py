"""Health probe — liveness check, no auth required.

Used by:
- Client polling on launch ("is the service up?")
- launchd / brew services watchdogs
- Manual debugging (`curl http://127.0.0.1:8765/health`)

MUST stay unauthenticated and trivial. Anything that could fail belongs
in /version (which is authenticated).
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}
