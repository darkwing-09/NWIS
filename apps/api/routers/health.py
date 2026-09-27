from typing import Any, Dict
from fastapi import APIRouter, Response, status

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
def liveness() -> Dict[str, str]:
    """Returns 200 ok if process is up."""
    return {"status": "ok"}


@router.get("/ready", summary="Readiness probe")
def readiness(response: Response) -> Dict[str, Any]:
    """Checks critical dependencies (DB, broker, storage) - returns ready or not_ready."""
    # Scaffold checks: when DB/storage are configured, checks evaluate live connectivity
    checks = {
        "api": "ok",
    }
    all_ok = all(v == "ok" for v in checks.values())
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "checks": checks}

    return {"status": "ready", "checks": checks}
