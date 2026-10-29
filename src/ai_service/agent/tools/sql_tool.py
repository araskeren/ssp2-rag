"""Tool `analisis_data`: delegasi ke SqlService (M4). Otorisasi ditegakkan di middleware."""

from __future__ import annotations

from typing import Protocol

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool

from ai_service.agent.context import ChatContext, SqlOutcome

DENIAL_TOOL_MESSAGE = (
    "DITOLAK: pengguna ini tidak berwenang mengakses analisis data. Sampaikan dengan sopan bahwa "
    "fitur Analisis Data hanya tersedia untuk analis berwenang dan tawarkan bantuan helpdesk dokumen. "
    "Jangan memberikan angka apa pun."
)
UNAVAILABLE_MESSAGE = (
    "Fitur analisis data belum dikonfigurasi pada server ini. Sampaikan kepada pengguna bahwa "
    "analisis data belum tersedia."
)


class SqlAnswerer(Protocol):
    async def answer(self, question: str, ctx: ChatContext) -> SqlOutcome: ...

    def format_for_model(self, outcome: SqlOutcome) -> str: ...


def make_sql_tool(sql_service: SqlAnswerer | None) -> BaseTool:
    @tool("analisis_data")
    async def analisis_data(pertanyaan: str, runtime: ToolRuntime[ChatContext]) -> str:
        """Analisis data surveilans dari basis data Satu Data Penyakit: jumlah kasus, spesimen,
        hasil laboratorium, tren mingguan, agregat per provinsi/kabupaten/faskes, laporan agregat.
        Gunakan HANYA untuk pertanyaan yang membutuhkan angka atau statistik. Argumen `pertanyaan`:
        pertanyaan analisis lengkap dalam bahasa Indonesia (modul penyakit, periode, pengelompokan).
        """
        ctx = runtime.context
        if sql_service is None:
            ctx.trace.sql = SqlOutcome(
                backend="none", status="unavailable", scope_label=ctx.scope_label
            )
            return UNAVAILABLE_MESSAGE
        outcome = await sql_service.answer(pertanyaan, ctx)
        ctx.trace.sql = outcome
        return sql_service.format_for_model(outcome)

    return analisis_data
