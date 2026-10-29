"""Probe kesehatan: /healthz (liveness) dan /readyz (readiness, 503 hanya bila DB sendiri down)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

router = APIRouter(tags=["ops"])


@router.get("/healthz", response_class=PlainTextResponse, include_in_schema=False)
async def healthz() -> str:
    return "ok"


@router.get("/readyz", include_in_schema=False)
async def readyz(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    db = getattr(request.app.state, "db", None)
    db_ok = await db.ping() if db is not None else False
    services = getattr(request.app.state, "services", None)
    analytics = "skipped"
    if services is not None and getattr(services, "sql_backend", None) is not None:
        analytics = "ok" if await services.sql_backend.ping() else "down"
    body = {
        "db": "ok" if db_ok else "down",
        "analytics_db": analytics,
        "llm": "configured" if settings.llm_base_url and settings.llm_model else "missing",
    }
    return JSONResponse(status_code=200 if db_ok else 503, content=body)
