"""Kontrak backend analitik + util render CTE view dan konversi nilai."""

from __future__ import annotations

import datetime as dt
import decimal
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from ai_service.auth.scope import UserScope
from ai_service.sql.catalog import SCOPE_PLACEHOLDER, ViewDef
from ai_service.sql.guard import ValidatedSql

SCOPE_KEY_BY_LEVEL = {
    "PROVINCE": "provinsi_kode",
    "KABKOTA": "kabupaten_kota_kode",
    "FASKES": "facility_id",
}


class SqlExecutionError(Exception):
    pass


class SqlTimeoutError(SqlExecutionError):
    pass


@dataclass
class RenderedQuery:
    sql: str
    params: list[Any] = field(default_factory=list)
    views: list[str] = field(default_factory=list)


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    elapsed_ms: int


class SqlBackend(Protocol):
    name: str
    dialect: str

    async def connect(self) -> None: ...

    def render(
        self, views: list[ViewDef], scope: UserScope, validated: ValidatedSql
    ) -> RenderedQuery: ...

    async def execute(
        self, query: RenderedQuery, max_rows: int, timeout_ms: int
    ) -> QueryResult: ...

    async def ping(self) -> bool: ...

    async def aclose(self) -> None: ...


def scope_condition(
    view: ViewDef, dialect: str, scope: UserScope, params: list[Any], placeholder: Any
) -> str:
    """Kondisi WHERE untuk scope; nilai selalu diikat parameter, tak pernah diinterpolasi."""
    if scope.level == "GLOBAL" or view.unscoped:
        return "TRUE"
    key = SCOPE_KEY_BY_LEVEL[scope.level]
    column = view.scope_mapping(dialect).get(key)
    if column is None:
        raise SqlExecutionError(f"view {view.name} tidak punya pemetaan scope {key}")
    value = getattr(scope, key)
    if value is None:
        return "FALSE"
    params.append(value)
    return f"{column} = {placeholder(len(params), key)}"


def compose_sql(view_ctes: list[str], validated: ValidatedSql) -> str:
    ctes = list(view_ctes) + list(validated.ctes)
    return "WITH " + ",\n".join(ctes) + "\n" + validated.body


def render_view_cte(view: ViewDef, dialect: str, condition: str) -> str:
    body = view.sql_for(dialect).replace(SCOPE_PLACEHOLDER, condition).strip()
    return f"{view.name} AS (\n{body}\n)"


def jsonable(value: Any) -> Any:
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    return value


def sanitize_error(message: str, limit: int = 300) -> str:
    first = (message or "").strip().splitlines()[0] if message else "kesalahan tidak diketahui"
    return first[:limit]
