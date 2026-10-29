"""Alembic env (async/asyncpg). DDL ditulis manual di tiap revisi (tanpa autogenerate).

Seluruh migrasi berjalan di dalam `pg_advisory_lock` sehingga beberapa replica yang
boot bersamaan (AUTO_MIGRATE=true) tidak saling bertabrakan.
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from ai_service.config import get_settings

config = context.config
settings = config.attributes.get("settings") or get_settings()
url = (config.get_main_option("sqlalchemy.url") or "").replace(
    "%%", "%"
) or settings.ai_database_url
config.attributes.setdefault("settings", settings)

MIGRATION_LOCK_ID = 804115
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:  # noqa: ANN001
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f"SELECT pg_advisory_lock({MIGRATION_LOCK_ID})"))
            # Tutup transaksi autobegin: lock advisory bersifat sesi (tetap dipegang), dan
            # alembic hanya meng-commit bila ia sendiri yang membuka transaksi.
            await conn.commit()
            try:
                await conn.run_sync(_do_run_migrations)
            finally:
                await conn.execute(text(f"SELECT pg_advisory_unlock({MIGRATION_LOCK_ID})"))
                await conn.commit()
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
