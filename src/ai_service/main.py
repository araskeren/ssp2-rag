"""Pabrik aplikasi FastAPI: middleware, router, lifespan (DB, migrasi, service)."""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ai_service import __version__
from ai_service.api import health
from ai_service.api.routers import all_routers
from ai_service.config import Settings, get_settings
from ai_service.db.engine import Database
from ai_service.db.migrate import run_migrations
from ai_service.errors import install_exception_handlers
from ai_service.logging import configure_logging, get_logger
from ai_service.rag.embeddings import EmbeddingConfigError
from ai_service.services import build_services

log = get_logger(__name__)

PROBE_PATHS = frozenset({"/healthz", "/readyz"})


class RequestLogMiddleware:
    """Middleware ASGI murni: request_id (contextvars) + log method/path/status/duration_ms."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        request_id = headers.get(b"x-request-id", b"").decode() or uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message: Any) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                raw = list(message.get("headers") or [])
                raw.append((b"x-request-id", request_id.encode()))
                message["headers"] = raw
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "")
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            client = scope.get("client") or ("", 0)
            event = log.debug if path in PROBE_PATHS else log.info
            event(
                "http",
                method=scope.get("method"),
                path=path,
                status=status_holder["status"],
                duration_ms=duration_ms,
                ip=client[0],
            )
            structlog.contextvars.unbind_contextvars("request_id")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    log.info("konfigurasi efektif", **settings.log_effective())
    db = Database(settings.ai_database_url, settings.ai_db_pool_size)
    app.state.db = db
    if settings.auto_migrate:
        try:
            await asyncio.to_thread(run_migrations, settings)
        except Exception as exc:  # noqa: BLE001 — service tetap boot, readyz melaporkan DB
            log.error("migrasi database gagal", error=str(exc))
    app.state.services = None
    try:
        app.state.services = await build_services(settings, db)
    except EmbeddingConfigError:
        await db.dispose()
        raise  # gagal-cepat: konfigurasi embedding tidak cocok dengan data tersimpan
    except Exception as exc:  # noqa: BLE001 — readyz akan melaporkan komponen yang gagal
        log.error("inisialisasi services gagal", error=str(exc))
    try:
        yield
    finally:
        services = getattr(app.state, "services", None)
        if services is not None and hasattr(services, "aclose"):
            await services.aclose()
        await db.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="ai-service — Satu Data Penyakit",
        version=__version__,
        description="Agentic RAG helpdesk + analisis data (Text-to-SQL) dengan RBAC.",
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    install_exception_handlers(app)
    if settings.cors_origin:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[o.strip() for o in settings.cors_origin.split(",") if o.strip()],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(RequestLogMiddleware)
    app.include_router(health.router)
    for router in all_routers():
        app.include_router(router)
    return app


app = create_app()
