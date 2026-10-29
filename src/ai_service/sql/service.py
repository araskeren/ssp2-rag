"""SqlService: pertanyaan → SQL (LLM) → guard → render+eksekusi backend → koreksi ≤N → audit."""

from __future__ import annotations

import json
import re
import uuid
from string import Template
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from ai_service.agent.build import build_llm
from ai_service.agent.context import ChatContext, SqlOutcome
from ai_service.agent.prompting import load_prompt
from ai_service.config import Settings
from ai_service.db.repositories import AuditRepo
from ai_service.logging import get_logger
from ai_service.sql.backends.base import SqlBackend, SqlExecutionError, SqlTimeoutError
from ai_service.sql.catalog import Catalog, CatalogError, load_catalog
from ai_service.sql.guard import GuardError, validate_sql

log = get_logger(__name__)

DIALECT_NAMES = {"postgres": "PostgreSQL 18", "clickhouse": "ClickHouse"}


class SqlDraftError(Exception):
    pass


def parse_sql_draft(text: str) -> tuple[str, str]:
    """Ambil (sql, penjelasan) dari balasan LLM: JSON (boleh dalam ```json), ```sql, atau SELECT mentah."""
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    candidates = [fenced.group(1)] if fenced else []
    brace = raw.find("{")
    if brace >= 0:
        candidates.append(raw[brace : raw.rfind("}") + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("sql"), str) and data["sql"].strip():
            return data["sql"].strip(), str(data.get("penjelasan", "") or "")
    sql_block = re.search(r"```sql\s*(.*?)```", raw, re.S | re.I)
    if sql_block:
        return sql_block.group(1).strip(), ""
    if re.match(r"^\s*(select|with)\b", raw, re.I):
        return raw, ""
    raise SqlDraftError("LLM tidak mengembalikan SQL yang bisa dibaca")


class SqlService:
    def __init__(
        self,
        settings: Settings,
        catalog: Catalog,
        backend: SqlBackend,
        audit: AuditRepo,
        llm: BaseChatModel,
    ) -> None:
        self._settings = settings
        self._catalog = catalog
        self._backend = backend
        self._audit = audit
        self._llm = llm
        self._template = Template(load_prompt("sql_writer.md"))

    # ------------------------------------------------------------------ publik
    async def answer(self, question: str, ctx: ChatContext) -> SqlOutcome:
        settings = self._settings
        scope = ctx.identity.scope
        feedback: str | None = None
        last_error = "tidak diketahui"
        last_status = "error"
        generated: str | None = None
        attempts = 0
        max_attempts = 1 + max(0, settings.sql_max_retries)
        while attempts < max_attempts:
            attempts += 1
            try:
                generated, _ = await self._draft(question, ctx, feedback)
            except SqlDraftError as exc:
                last_error, last_status = str(exc), "error"
                await self._log(ctx, question, None, None, [], attempts, "error", error=last_error)
                feedback = "Balasan sebelumnya bukan JSON dengan field sql. Kirim JSON yang valid."
                continue

            try:
                validated = validate_sql(
                    generated,
                    self._backend.dialect,
                    self._catalog.names,
                    settings.sql_max_rows,
                    view_columns=self._catalog.column_map,
                )
            except GuardError as exc:
                last_error, last_status = str(exc), "rejected"
                await self._log(
                    ctx, question, generated, None, [], attempts, "rejected", error=last_error
                )
                feedback = f"Validator menolak SQL sebelumnya: {exc}. Perbaiki dan patuhi aturan."
                continue

            views = [self._catalog.views[name] for name in validated.views]
            try:
                rendered = self._backend.render(views, scope, validated)
                result = await self._backend.execute(
                    rendered, settings.sql_max_rows, settings.sql_statement_timeout_ms
                )
            except SqlTimeoutError as exc:
                last_error, last_status = str(exc), "timeout"
                await self._log(
                    ctx,
                    question,
                    generated,
                    None,
                    validated.views,
                    attempts,
                    "timeout",
                    error=last_error,
                )
                break
            except SqlExecutionError as exc:
                last_error, last_status = str(exc), "error"
                await self._log(
                    ctx,
                    question,
                    generated,
                    None,
                    validated.views,
                    attempts,
                    "error",
                    error=last_error,
                )
                feedback = f"Database menolak SQL sebelumnya: {exc}. Perbaiki query."
                continue

            await self._log(
                ctx,
                question,
                generated,
                rendered.sql,
                validated.views,
                attempts,
                "ok",
                row_count=len(result.rows),
                elapsed_ms=result.elapsed_ms,
            )
            return SqlOutcome(
                backend=self._backend.name,
                status="ok" if result.rows else "empty",
                query=validated.sql,
                views=validated.views,
                columns=result.columns,
                rows=result.rows,
                row_count=len(result.rows),
                truncated=result.truncated,
                scope_label=scope.label,
                elapsed_ms=result.elapsed_ms,
                attempts=attempts,
            )

        return SqlOutcome(
            backend=self._backend.name,
            status=last_status,
            query=generated,
            scope_label=scope.label,
            attempts=attempts,
            error=last_error,
        )

    def format_for_model(self, outcome: SqlOutcome) -> str:
        if outcome.status in ("ok", "empty"):
            header = f"Hasil analisis data (backend {outcome.backend}, wilayah data: {outcome.scope_label})."
            if outcome.status == "empty":
                return (
                    f"{header}\nQuery berhasil tetapi tidak ada data yang cocok.\nSQL: {outcome.query}\n"
                    "Sampaikan kepada pengguna bahwa tidak ada data untuk kriteria tersebut."
                )
            shown = outcome.rows[: self._settings.sql_llm_rows]
            table = _markdown_table(outcome.columns, shown)
            note = (
                f"Total {outcome.row_count} baris"
                + (" (dipotong oleh batas maksimum)" if outcome.truncated else "")
                + (
                    f"; hanya {len(shown)} baris pertama ditampilkan."
                    if len(shown) < outcome.row_count
                    else "."
                )
            )
            return (
                f"{header}\nSQL: {outcome.query}\n{table}\n{note}\n"
                "Angka di atas adalah data; sajikan ringkas dan apa adanya kepada pengguna, "
                "jangan menambah angka lain."
            )
        if outcome.status == "timeout":
            return "Analisis data gagal: query melebihi batas waktu. Sarankan pengguna mempersempit periode/wilayah."
        return (
            f"Analisis data gagal setelah {outcome.attempts} percobaan: {outcome.error}. "
            "Sampaikan kepada pengguna bahwa analisis belum bisa dijalankan dan sarankan merumuskan ulang pertanyaan."
        )

    # ------------------------------------------------------------------ internal
    async def _draft(
        self, question: str, ctx: ChatContext, feedback: str | None
    ) -> tuple[str, str]:
        feedback_block = f"\n## Koreksi\n{feedback}\n" if feedback else ""
        views = self._catalog.select_views(question)
        prompt = self._template.safe_substitute(
            dialect_name=DIALECT_NAMES.get(self._backend.dialect, self._backend.dialect),
            today=ctx.today.isoformat(),
            scope_label=ctx.identity.scope_label,
            catalog=self._catalog.render_prompt(self._backend.dialect, views=views),
            max_rows=self._settings.sql_max_rows,
            feedback_block=feedback_block,
            question=question.strip(),
        )
        response = await self._llm.ainvoke([HumanMessage(content=prompt)])
        text = (
            response.text
            if isinstance(getattr(response, "text", None), str)
            else str(response.content)
        )
        return parse_sql_draft(text)

    async def _log(
        self,
        ctx: ChatContext,
        question: str,
        generated: str | None,
        executed: str | None,
        views: list[str],
        attempt: int,
        status: str,
        *,
        error: str | None = None,
        row_count: int | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        try:
            await self._audit.add(
                session_id=ctx.session_id,
                message_id=uuid.UUID(ctx.message_id),
                principal_id=ctx.identity.principal_id,
                platform=ctx.platform,
                ai_role=ctx.ai_role,
                scope=ctx.identity.scope.to_dict(),
                backend=self._backend.name,
                question=question[:2000],
                generated_sql=generated,
                executed_sql=executed,
                views=views,
                attempt=attempt,
                status=status,
                error=error,
                row_count=row_count,
                elapsed_ms=elapsed_ms,
            )
        except Exception as exc:  # noqa: BLE001 — audit gagal dicatat di log, tidak menghentikan jawaban
            log.error("audit SQL gagal", error=str(exc))


def _markdown_table(columns: list[str], rows: list[list[Any]]) -> str:
    if not columns:
        return "(tanpa kolom)"
    head = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join(["---"] * len(columns)) + "|"
    body = ["| " + " | ".join("" if v is None else str(v) for v in row) + " |" for row in rows]
    return "\n".join([head, sep, *body])


async def build_sql_service(
    settings: Settings, audit: AuditRepo, llm: BaseChatModel | None = None
) -> tuple[SqlService, SqlBackend]:
    catalog = load_catalog()
    issues = catalog.validate(settings.sql_backend)
    if issues:
        raise CatalogError("katalog tidak valid: " + "; ".join(issues))
    backend: SqlBackend
    if settings.sql_backend == "clickhouse":
        from ai_service.sql.backends.clickhouse import ClickHouseBackend

        backend = ClickHouseBackend(
            settings.analytics_ch_url,
            settings.analytics_ch_user,
            settings.analytics_ch_password,
            settings.analytics_ch_db,
            settings.sql_statement_timeout_ms,
        )
    else:
        from ai_service.sql.backends.postgres import PostgresBackend

        backend = PostgresBackend(
            settings.analytics_pg_dsn,
            settings.analytics_pg_pool_size,
            settings.sql_statement_timeout_ms,
        )
    await backend.connect()
    sql_llm = llm or build_llm(settings, settings.effective_llm_sql_model, temperature=0)
    return SqlService(settings, catalog, backend, audit, sql_llm), backend
