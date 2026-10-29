"""Pembungkus alembic: migrasi dijalankan sinkron (thread) di dalam pg_advisory_lock."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from ai_service.config import Settings
from ai_service.logging import get_logger

log = get_logger(__name__)

ALEMBIC_DIR = Path(__file__).resolve().parent / "alembic"


def alembic_config(settings: Settings) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", settings.ai_database_url.replace("%", "%%"))
    cfg.attributes["settings"] = settings
    return cfg


def run_migrations(settings: Settings, revision: str = "head") -> None:
    log.info("menjalankan migrasi", revision=revision)
    command.upgrade(alembic_config(settings), revision)
    log.info("migrasi selesai", revision=revision)


def current_revision(settings: Settings) -> str | None:
    """Revisi aktif di database (None bila belum pernah migrasi)."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _query() -> str | None:
        engine = create_async_engine(settings.ai_database_url)
        try:
            async with engine.connect() as conn:
                exists = await conn.execute(
                    text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
                )
                if not exists.scalar():
                    return None
                row = await conn.execute(text("SELECT version_num FROM alembic_version"))
                return row.scalar()
        finally:
            await engine.dispose()

    return asyncio.run(_query())
