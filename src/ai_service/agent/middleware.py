"""Middleware agent: prompt sistem dinamis + otorisasi tool-level (deterministik, diaudit)."""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import (
    ModelRequest,
    ToolCallRequest,
    dynamic_prompt,
    wrap_model_call,
    wrap_tool_call,
)
from langchain_core.messages import HumanMessage, ToolMessage

from ai_service.agent.context import ChatContext, ToolCallRecord
from ai_service.agent.prompting import render_system_prompt
from ai_service.agent.tools.sql_tool import DENIAL_TOOL_MESSAGE
from ai_service.logging import get_logger

log = get_logger(__name__)

RESTRICTED_TOOLS = frozenset({"analisis_data"})

# Pola pertanyaan data: angka/statistik/agregasi/tren. Dipakai intent router deterministik.
DATA_INTENT_RE = re.compile(
    r"\b(berapa|jumlah|total|hitung|statistik|tren|trend|rata[- ]rata|persentase|proporsi|"
    r"distribusi|rekap|agregat|per (provinsi|kabupaten|kota|faskes|minggu|bulan|tahun)|"
    r"terbanyak|tertinggi|terendah|peringkat|bandingkan|perbandingan|grafik)\b",
    re.I,
)
SMALLTALK_RE = re.compile(
    r"^\s*(halo|hai|hi|selamat (pagi|siang|sore|malam)|terima kasih|makasih)\b", re.I
)


# Pertanyaan durasi/waktu ("berapa lama", "kapan") adalah pertanyaan prosedur, bukan analisis data.
NON_DATA_RE = re.compile(
    r"\b(berapa (lama|hari|jam|minggu|bulan|kali|langkah)|kapan|bagaimana cara)\b", re.I
)


def is_data_intent(text: str) -> bool:
    if not text or SMALLTALK_RE.match(text) or NON_DATA_RE.search(text):
        return False
    return bool(DATA_INTENT_RE.search(text))


def decide_tool_choice(messages: list[Any], enabled: bool) -> str | None:
    """`analisis_data` bila pesan terakhir adalah pertanyaan data pengguna (giliran pertama), selain itu None."""
    if not enabled or not messages:
        return None
    last = messages[-1]
    if not isinstance(last, HumanMessage):
        return None  # sudah ada hasil tool pada giliran ini → biarkan model menjawab
    content = last.content if isinstance(last.content, str) else str(last.content)
    return "analisis_data" if is_data_intent(content) else None


DenialAudit = Callable[[ChatContext, str], Awaitable[None]]


def build_middleware(audit_denial: DenialAudit, *, force_tool: bool = True) -> list[Any]:
    @dynamic_prompt
    def context_prompt(request: ModelRequest) -> str:
        return render_system_prompt(request.runtime.context)

    @wrap_model_call
    async def intent_router(request: ModelRequest, handler):  # noqa: ANN001
        choice = decide_tool_choice(list(request.messages), force_tool)
        if choice and any(t.name == choice for t in request.tools):
            request = request.override(tool_choice=choice)
        return await handler(request)

    @wrap_tool_call
    async def tool_authorization(request: ToolCallRequest, handler):  # noqa: ANN001
        ctx: ChatContext = request.runtime.context
        call = request.tool_call
        name = str(call.get("name"))
        args = call.get("args") or {}
        question = str(args.get("pertanyaan", ""))[:2000]

        if name in RESTRICTED_TOOLS and not ctx.identity.is_analyst:
            ctx.trace.denied = True
            ctx.trace.denied_question = question
            ctx.trace.tool_calls.append(
                ToolCallRecord(name, "denied", "akses analisis data ditolak")
            )
            log.info(
                "akses analisis data ditolak",
                principal=ctx.identity.principal_id,
                platform=ctx.platform,
            )
            try:
                await audit_denial(ctx, question)
            except Exception as exc:  # noqa: BLE001 — audit gagal tidak boleh membuka akses
                log.error("audit penolakan gagal", error=str(exc))
            return ToolMessage(content=DENIAL_TOOL_MESSAGE, tool_call_id=call["id"], name=name)

        started = time.perf_counter()
        sources_before = len(ctx.trace.sources)
        try:
            result = await handler(request)
        except Exception as exc:  # noqa: BLE001 — model diberi tahu agar menjawab apa adanya
            elapsed = int((time.perf_counter() - started) * 1000)
            ctx.trace.tool_calls.append(ToolCallRecord(name, "error", str(exc)[:200], elapsed))
            log.warning("tool gagal", tool=name, error=str(exc))
            return ToolMessage(
                content=f"Tool {name} gagal dijalankan: {exc}. Sampaikan kepada pengguna bahwa "
                "terjadi gangguan dan sarankan mencoba lagi.",
                tool_call_id=call["id"],
                name=name,
            )
        elapsed = int((time.perf_counter() - started) * 1000)
        ctx.trace.tool_calls.append(
            ToolCallRecord(name, "ok", tool_summary(ctx, name, sources_before), elapsed)
        )
        return result

    return [context_prompt, intent_router, tool_authorization]


def tool_summary(ctx: ChatContext, name: str, sources_before: int = 0) -> str:
    if name == "cari_dokumen":
        added = len(ctx.trace.sources) - sources_before
        docs = {s.document_id for s in ctx.trace.sources[sources_before:]}
        return (
            f"{added} potongan dari {len(docs)} dokumen" if added else "tidak ada dokumen relevan"
        )
    if name == "analisis_data":
        sql = ctx.trace.sql
        if sql is None:
            return "tanpa hasil"
        if sql.status == "ok":
            return f"{sql.row_count} baris ({sql.scope_label})"
        return sql.status
    return "selesai"
