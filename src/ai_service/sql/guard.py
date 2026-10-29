"""Validator SQL buatan LLM (sqlglot): satu SELECT, hanya view katalog, tanpa schema, LIMIT diklem."""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


class GuardError(ValueError):
    pass


@dataclass
class ValidatedSql:
    sql: str
    body: str
    ctes: list[str] = field(default_factory=list)
    views: list[str] = field(default_factory=list)
    limit: int = 0


DENY_FUNCS: dict[str, frozenset[str]] = {
    "postgres": frozenset(
        {
            "current_setting",
            "set_config",
            "dblink",
            "dblink_exec",
            "lo_import",
            "lo_export",
            "lo_get",
            "query_to_xml",
            "query_to_json",
            "txid_current",
            "inet_server_addr",
            "inet_client_addr",
            "version",
            "current_user",
            "session_user",
            "copy",
        }
    ),
    "clickhouse": frozenset(
        {
            "url",
            "file",
            "s3",
            "remote",
            "remotesecure",
            "mysql",
            "postgresql",
            "jdbc",
            "odbc",
            "executable",
            "input",
            "hdfs",
            "azureblobstorage",
            "sleep",
            "sleepeachrow",
            "cluster",
            "clusterallreplicas",
            "merge",
            "view",
            "dictionary",
        }
    ),
}

_FORBIDDEN_NAMES = (
    "Insert",
    "Update",
    "Delete",
    "Create",
    "Drop",
    "Alter",
    "AlterTable",
    "Command",
    "Transaction",
    "Commit",
    "Rollback",
    "Set",
    "Merge",
    "Grant",
    "Revoke",
    "Lock",
    "Into",
    "Placeholder",
    "Parameter",
    "SessionParameter",
    "Copy",
    "Truncate",
    "Use",
    "Call",
    "Execute",
    "Prepare",
)
FORBIDDEN_TYPES = tuple(
    t for t in (getattr(exp, name, None) for name in _FORBIDDEN_NAMES) if t is not None
)


def _func_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    try:
        return str(node.sql_name()).lower()
    except Exception:  # noqa: BLE001
        return type(node).__name__.lower()


def _select_depth(node: exp.Expression) -> int:
    depth = 0
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Select):
            depth += 1
        parent = parent.parent
    return depth


def _current_limit(expr: exp.Expression) -> int | None:
    limit = expr.args.get("limit")
    if limit is None:
        return None
    value = limit.expression
    if isinstance(value, exp.Literal) and value.is_int:
        return int(value.this)
    raise GuardError("LIMIT harus berupa angka literal")


def _check_columns(
    expr: exp.Expression,
    view_columns: Mapping[str, set[str]],
    cte_aliases: set[str],
) -> None:
    """Tolak kolom yang tidak ada di view katalog (dengan saran kolom mirip) — umpan balik presisi untuk LLM."""
    alias_to_view: dict[str, str] = {}
    view_refs: list[str] = []
    for table in expr.find_all(exp.Table):
        name = str(table.name)
        if name.lower() in cte_aliases or name not in view_columns:
            continue
        view_refs.append(name)
        alias_to_view[(table.alias or name).lower()] = name
    output_aliases = {
        str(e.alias).lower()
        for sel in expr.find_all(exp.Select)
        for e in sel.expressions
        if isinstance(e, exp.Alias) and e.alias
    }
    single_view = view_refs[0] if len(set(view_refs)) == 1 and not cte_aliases else None
    for column in expr.find_all(exp.Column):
        col = str(column.name)
        if not col or col == "*":
            continue
        qualifier = str(column.table).lower() if column.table else ""
        if qualifier:
            view = alias_to_view.get(qualifier)
            if view is None:
                continue  # alias CTE/subquery — tidak diperiksa
        elif single_view:
            view = single_view
            if col.lower() in output_aliases:
                continue
        else:
            continue
        if col not in view_columns[view]:
            if col.lower() in output_aliases:
                continue
            suggestions = difflib.get_close_matches(
                col, sorted(view_columns[view]), n=3, cutoff=0.5
            )
            hint = f"; kolom mirip: {', '.join(suggestions)}" if suggestions else ""
            raise GuardError(f"kolom '{col}' tidak ada di {view}{hint}")


def validate_sql(
    raw_sql: str,
    dialect: str,
    allowed_views: set[str],
    max_rows: int,
    *,
    max_views: int = 3,
    max_chars: int = 4000,
    max_depth: int = 3,
    view_columns: Mapping[str, set[str]] | None = None,
) -> ValidatedSql:
    sql = (raw_sql or "").strip()
    while sql.endswith(";"):
        sql = sql[:-1].rstrip()
    if not sql:
        raise GuardError("SQL kosong")
    if len(sql) > max_chars:
        raise GuardError(f"SQL terlalu panjang (> {max_chars} karakter)")
    if ";" in sql:
        raise GuardError("hanya satu statement yang diizinkan")
    if "--" in sql or "/*" in sql or "#" in sql:
        raise GuardError("komentar tidak diizinkan")

    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except ParseError as exc:
        raise GuardError(f"SQL tidak valid: {str(exc).splitlines()[0][:200]}") from exc
    if len(statements) != 1:
        raise GuardError("hanya satu statement yang diizinkan")
    expr = statements[0]
    if not isinstance(expr, exp.Select | exp.Union):
        raise GuardError(f"hanya SELECT yang diizinkan (ditemukan {type(expr).__name__})")

    # sqlglot menyimpan klausa WITH di args["with"] (versi lama) atau args["with_"] (versi baru).
    with_key = "with_" if expr.args.get("with_") is not None else "with"
    with_clause = expr.args.get(with_key)
    cte_aliases: set[str] = set()
    for cte in expr.find_all(exp.CTE):
        alias = str(cte.alias_or_name)
        if alias in allowed_views:
            raise GuardError(f"alias CTE '{alias}' menyamai nama view katalog")
        cte_aliases.add(alias.lower())

    deny = DENY_FUNCS.get(dialect, frozenset())
    views: list[str] = []
    for node in expr.walk():
        if isinstance(node, FORBIDDEN_TYPES):
            raise GuardError(f"konstruksi {type(node).__name__} tidak diizinkan")
        if isinstance(node, exp.Table):
            if node.db or node.catalog:
                raise GuardError(
                    "nama schema/database tidak diizinkan; gunakan nama view katalog saja"
                )
            name = str(node.name)
            if not name:
                raise GuardError("fungsi tabel tidak diizinkan")
            if name.lower() in cte_aliases:
                continue
            if name not in allowed_views:
                raise GuardError(f"relasi tidak dikenal: {name}")
            if name not in views:
                views.append(name)
        elif isinstance(node, exp.Func):
            fname = _func_name(node)
            if fname.startswith("pg_") or fname in deny:
                raise GuardError(f"fungsi {fname} tidak diizinkan")
        elif isinstance(node, exp.Select) and _select_depth(node) > max_depth:
            raise GuardError(f"subquery terlalu dalam (> {max_depth})")
    if not views:
        raise GuardError("query harus merujuk minimal satu view katalog")
    if len(views) > max_views:
        raise GuardError(f"maksimal {max_views} view per query")
    if view_columns:
        _check_columns(expr, view_columns, cte_aliases)

    current = _current_limit(expr)
    if current is None or current > max_rows or current <= 0:
        expr = expr.limit(max_rows)
        current = max_rows

    ctes = [
        cte.sql(dialect=dialect)
        for cte in (with_clause.expressions if with_clause is not None else [])
    ]
    body_expr = expr.copy()
    body_expr.set(with_key, None)
    return ValidatedSql(
        sql=expr.sql(dialect=dialect),
        body=body_expr.sql(dialect=dialect),
        ctes=ctes,
        views=views,
        limit=current,
    )
