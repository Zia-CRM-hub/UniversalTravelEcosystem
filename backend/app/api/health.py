from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ..db import SessionLocal

router = APIRouter(tags=["ops"])


@router.get("/health/live")
async def live():
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request):
    """DB reachable? Provider breakers listed for visibility (a down supplier degrades, not fails, readiness)."""
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "unavailable", "db": "down"}, status_code=503)
    return {"status": "ok", "providers": [p.health() for p in request.app.state.providers.values()],
            "agent": request.app.state.llm is not None, "whatsapp": request.app.state.whatsapp is not None}
