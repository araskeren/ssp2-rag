"""Backend Postgres: pool asyncpg sebagai role read-only, CTE view + filter scope `$n`, READ ONLY tx."""

from __future__ import annotations

import time
from typing import Any

import asyncpg

from ai_service.auth.scope import UserScope
from ai_service.sql.backends.base import (
    QueryResult,
    RenderedQuery,
    SqlExecutionError,
    SqlTimeoutError,
    compose_sql,
    jsonable,
    render_view_cte,
    sanitize_error,
    scope_condition,
)
from ai_service.sql.catalog import ViewDef
from ai_service.sql.guard import ValidatedSql


def _placeholder(index: int, key: str) -> str:
    return f"${index}::int" if key == "facility_id" else f"${index}"


class PostgresBackend:
    name = "postgres"
    dialect = "postgres"

    def __init__(self, dsn: str, pool_size: int = 3, statement_timeout_ms: int = 15000) -> None:
        self._dsn = dsn
        self._pool_size = max(1, pool_size)
        self._statement_timeout_ms = statement_timeout_ms
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=1,
            max_size=self._pool_size,
            timeout=10,
            server_settings={
                "application_name": "ai-service-analytics",
                "default_transaction_read_only": "on",
                "statement_timeout": str(self._statement_timeout_ms),
            },
        )

    def render(
        self, views: list[ViewDef], scope: UserScope, validated: ValidatedSql
    ) -> RenderedQuery:
        params: list[Any] = []
        ctes = []
        for view in views:
            condition = scope_condition(view, self.dialect, scope, params, _placeholder)
            ctes.append(render_view_cte(view, self.dialect, condition))
        return RenderedQuery(
            sql=compose_sql(ctes, validated), params=params, views=[v.name for v in views]
        )

    async def execute(self, query: RenderedQuery, max_rows: int, timeout_ms: int) -> QueryResult:
        if self._pool is None:
            raise SqlExecutionError("backend belum terhubung")
        started = time.perf_counter()
        try:
            async with self._pool.acquire() as conn, conn.transaction(readonly=True):
                await conn.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
                statement = await conn.prepare(query.sql)
                columns = [attr.name for attr in statement.get_attributes()]
                records = await statement.fetch(*query.params)
        except asyncpg.QueryCanceledError as exc:
            raise SqlTimeoutError("query melebihi batas waktu") from exc
        except asyncpg.PostgresError as exc:
            raise SqlExecutionError(sanitize_error(getattr(exc, "message", str(exc)))) from exc
        except (OSError, asyncpg.InterfaceError) as exc:
            raise SqlExecutionError(f"koneksi analitik gagal: {sanitize_error(str(exc))}") from exc
        elapsed = int((time.perf_counter() - started) * 1000)
        rows = [[jsonable(v) for v in record] for record in records[:max_rows]]
        return QueryResult(
            columns=columns, rows=rows, truncated=len(records) >= max_rows, elapsed_ms=elapsed
        )

    async def ping(self) -> bool:
        if self._pool is None:
            return False
        try:
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            return False

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
