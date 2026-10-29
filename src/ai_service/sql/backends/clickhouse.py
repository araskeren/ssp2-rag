"""Backend ClickHouse (extra `clickhouse`): view dn_* data-pipeline via user read-only `app_ro`."""

from __future__ import annotations

import time
from typing import Any

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
    return f"{{p{index}:Int32}}" if key == "facility_id" else f"{{p{index}:String}}"


class ClickHouseBackend:
    name = "clickhouse"
    dialect = "clickhouse"

    def __init__(
        self, url: str, user: str, password: str, database: str, statement_timeout_ms: int = 15000
    ) -> None:
        self._url = url
        self._user = user
        self._password = password
        self._database = database
        self._timeout_s = max(1, statement_timeout_ms // 1000)
        self._client: Any = None

    async def connect(self) -> None:
        try:
            import clickhouse_connect
        except ImportError as exc:  # pragma: no cover - tergantung extra
            raise SqlExecutionError(
                "SQL_BACKEND=clickhouse butuh `uv sync --extra clickhouse`"
            ) from exc
        self._client = await clickhouse_connect.get_async_client(
            dsn=self._url,
            username=self._user,
            password=self._password,
            database=self._database,
            settings={"readonly": 2, "max_execution_time": self._timeout_s},
        )

    def render(
        self, views: list[ViewDef], scope: UserScope, validated: ValidatedSql
    ) -> RenderedQuery:
        params: list[Any] = []
        ctes = [
            render_view_cte(
                v, self.dialect, scope_condition(v, self.dialect, scope, params, _placeholder)
            )
            for v in views
        ]
        return RenderedQuery(
            sql=compose_sql(ctes, validated), params=params, views=[v.name for v in views]
        )

    async def execute(self, query: RenderedQuery, max_rows: int, timeout_ms: int) -> QueryResult:
        if self._client is None:
            raise SqlExecutionError("backend belum terhubung")
        started = time.perf_counter()
        parameters = {f"p{i}": v for i, v in enumerate(query.params, start=1)}
        try:
            result = await self._client.query(
                query.sql,
                parameters=parameters,
                settings={
                    "max_result_rows": max_rows + 1,
                    "result_overflow_mode": "break",
                    "max_execution_time": max(1, timeout_ms // 1000),
                },
            )
        except Exception as exc:  # noqa: BLE001
            text = sanitize_error(str(exc))
            if "TIMEOUT_EXCEEDED" in text or "timed out" in text.lower():
                raise SqlTimeoutError("query melebihi batas waktu") from exc
            raise SqlExecutionError(text) from exc
        elapsed = int((time.perf_counter() - started) * 1000)
        rows = [[jsonable(v) for v in row] for row in result.result_rows[:max_rows]]
        return QueryResult(
            columns=list(result.column_names),
            rows=rows,
            truncated=len(result.result_rows) > max_rows,
            elapsed_ms=elapsed,
        )

    async def ping(self) -> bool:
        if self._client is None:
            return False
        try:
            await self._client.query("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            return False

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
