"""Engine SQLAlchemy async (asyncpg) untuk database milik ai-service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ai_service.logging import get_logger

log = get_logger(__name__)


class Database:
    def __init__(self, url: str, pool_size: int = 5) -> None:
        self.engine: AsyncEngine = create_async_engine(
            url,
            pool_size=pool_size,
            max_overflow=pool_size,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        # Catatan: codec biner pgvector (pgvector.asyncpg.register_vector) sengaja TIDAK
        # didaftarkan — tipe pgvector.sqlalchemy.Vector mengirim/menerima vektor sebagai teks
        # dan keduanya bertabrakan bila codec aktif.
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session

    async def ping(self) -> bool:
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("ping database gagal", error=str(exc))
            return False

    async def dispose(self) -> None:
        await self.engine.dispose()
