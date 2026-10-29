"""ChatRunner: sesi + memori → agent → hasil terstruktur (non-stream) atau event SSE (stream)."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from ai_service.agent.context import ChatContext, RequestTrace
from ai_service.agent.memory import rows_to_messages, trim_history
from ai_service.agent.middleware import tool_summary
from ai_service.auth.identity import Identity
from ai_service.config import Settings
from ai_service.db.models import ChatSession
from ai_service.db.repositories import AuditRepo, ChatRepo
from ai_service.errors import ApiError
from ai_service.logging import get_logger
from ai_service.rag.retriever import Retriever

log = get_logger(__name__)
CITATION_RE = re.compile(r"\[(\d+)\]")


@dataclass
class ChatResult:
    session_id: str
    message_id: str
    intent: str
    answer: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    sql: dict[str, Any] | None = None
    usage: dict[str, int] = field(
        default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0}
    )
    latency_ms: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    denied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "message_id": self.message_id,
            "intent": self.intent,
            "answer": self.answer,
            "sources": self.sources,
            "sql": self.sql,
            "usage": self.usage,
            "latency_ms": self.latency_ms,
            "tool_calls": self.tool_calls,
        }


def message_text(message: BaseMessage | None) -> str:
    if message is None:
        return ""
    text = getattr(message, "text", None)
    if callable(text):
        text = text()
    if isinstance(text, str) and text:
        return text
    content = message.content
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif isinstance(block, str):
            parts.append(block)
    return "".join(parts)


def _usage_of(message: Any) -> tuple[int, int]:
    usage = getattr(message, "usage_metadata", None) or {}
    return int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)


class ChatRunner:
    def __init__(
        self,
        settings: Settings,
        agent: Any,
        chats: ChatRepo,
        audit: AuditRepo,
        *,
        model_name: str,
        backend_name: str = "postgres",
        retriever: Retriever | None = None,
    ) -> None:
        self._settings = settings
        self._agent = agent
        self._chats = chats
        self._audit = audit
        self._model_name = model_name
        self._backend_name = backend_name
        self._retriever = retriever

    async def _prefetch(self, ctx: ChatContext, query: str) -> None:
        """Retrieval awal (deterministik) sebelum agent: sumber dicatat, konteks masuk prompt sistem."""
        if not self._settings.rag_prefetch or self._retriever is None:
            return
        try:
            chunks = await self._retriever.retrieve(query)
        except Exception as exc:  # noqa: BLE001 — agent tetap jalan dengan tool cari_dokumen
            log.warning("prefetch dokumen gagal", error=str(exc))
            return
        if not chunks:
            return
        context_text, sources = Retriever.format_context(chunks, start=ctx.trace.next_ref)
        ctx.trace.sources.extend(sources)
        ctx.trace.prefetched = True
        ctx.prefetched_context = context_text

    # ------------------------------------------------------------------ persiapan
    def validate_query(self, query: str) -> str:
        cleaned = (query or "").strip()
        if not cleaned:
            raise ApiError(400, "validasi request gagal", ["query: wajib diisi"])
        if len(cleaned) > self._settings.chat_max_query_chars:
            raise ApiError(
                400,
                "validasi request gagal",
                [f"query: maksimal {self._settings.chat_max_query_chars} karakter"],
            )
        return cleaned

    async def ensure_session(self, identity: Identity, session_id: str) -> ChatSession:
        session = await self._chats.get_session(session_id)
        if session is None:
            return await self._chats.create_session(
                session_id, identity.platform, identity.principal_id, identity.ai_role
            )
        if session.platform != identity.platform or session.principal_id != identity.principal_id:
            raise ApiError(403, "sesi bukan milik pengguna ini")
        return session

    async def _history(self, session_id: str) -> list[BaseMessage]:
        rows = await self._chats.last_messages(session_id, self._settings.chat_history_messages)
        return trim_history(rows_to_messages(rows), self._settings.chat_history_max_tokens)

    def _context(self, identity: Identity, session_id: str) -> ChatContext:
        return ChatContext(
            identity=identity,
            session_id=session_id,
            message_id=str(uuid.uuid4()),
            today=date.today(),
            trace=RequestTrace(),
        )

    def _config(self) -> dict[str, Any]:
        return {"recursion_limit": 2 * self._settings.agent_max_tool_calls + 1}

    async def audit_denial(self, ctx: ChatContext, question: str) -> None:
        await self._audit.add(
            session_id=ctx.session_id,
            message_id=uuid.UUID(ctx.message_id),
            principal_id=ctx.identity.principal_id,
            platform=ctx.platform,
            ai_role=ctx.ai_role,
            scope=ctx.identity.scope.to_dict(),
            backend=self._backend_name,
            question=question or "(tanpa pertanyaan)",
            status="access_denied",
        )

    # ------------------------------------------------------------------ non-stream
    async def run(self, identity: Identity, session_id: str, query: str) -> ChatResult:
        query = self.validate_query(query)
        await self.ensure_session(identity, session_id)
        ctx = self._context(identity, session_id)
        await self._prefetch(ctx, query)
        messages = await self._history(session_id) + [HumanMessage(content=query)]
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._settings.chat_timeout_s):
                state = await self._agent.ainvoke(
                    {"messages": messages}, context=ctx, config=self._config()
                )
        except TimeoutError as exc:
            raise ApiError(504, "asisten tidak merespons tepat waktu, coba lagi") from exc
        answer, usage = self._extract(state)
        return await self._finalize(ctx, query, answer, usage, started)

    # ------------------------------------------------------------------ stream
    async def stream(
        self, identity: Identity, session_id: str, query: str
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        query = self.validate_query(query)
        await self.ensure_session(identity, session_id)
        ctx = self._context(identity, session_id)
        messages = await self._history(session_id) + [HumanMessage(content=query)]
        yield (
            "meta",
            {"session_id": session_id, "message_id": ctx.message_id, "ai_role": identity.ai_role},
        )

        started = time.perf_counter()
        streamed: list[str] = []
        last_model_text = ""
        prompt_tokens = completion_tokens = 0
        final_state: Any = None
        try:
            async with asyncio.timeout(self._settings.chat_timeout_s):
                async for event in self._agent.astream_events(
                    {"messages": messages}, context=ctx, version="v2", config=self._config()
                ):
                    kind = event["event"]
                    data = event.get("data") or {}
                    if kind == "on_chat_model_start":
                        streamed = []
                    elif kind == "on_chat_model_stream":
                        text = message_text(data.get("chunk"))
                        if text:
                            streamed.append(text)
                            yield "token", {"t": text}
                    elif kind == "on_chat_model_end":
                        output = data.get("output")
                        p, c = _usage_of(output)
                        prompt_tokens += p
                        completion_tokens += c
                        last_model_text = message_text(output) or last_model_text
                    elif kind == "on_tool_start":
                        yield "tool", {"name": event.get("name"), "status": "start"}
                    elif kind == "on_tool_end":
                        name = str(event.get("name"))
                        yield (
                            "tool",
                            {"name": name, "status": "end", "summary": tool_summary(ctx, name)},
                        )
                    elif kind == "on_chain_end" and not event.get("parent_ids"):
                        final_state = data.get("output")
        except TimeoutError:
            yield (
                "error",
                {"statusCode": 504, "message": "asisten tidak merespons tepat waktu, coba lagi"},
            )
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("stream chat gagal", error=str(exc))
            yield "error", {"statusCode": 500, "message": "terjadi kesalahan internal"}
            return

        answer, usage = (
            self._extract(final_state)
            if isinstance(final_state, dict)
            else ("", {"prompt_tokens": 0, "completion_tokens": 0})
        )
        if not answer:
            answer = last_model_text or "".join(streamed)
        if usage["prompt_tokens"] == 0 and usage["completion_tokens"] == 0:
            usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
        if "".join(streamed) != answer and answer:
            yield "token", {"t": answer if not streamed else answer[len("".join(streamed)) :]}

        result = await self._finalize(ctx, query, answer, usage, started)
        if result.sources:
            yield "sources", {"items": result.sources}
        if result.sql is not None:
            yield "sql", result.sql
        yield (
            "done",
            {
                "message_id": result.message_id,
                "intent": result.intent,
                "usage": result.usage,
                "latency_ms": result.latency_ms,
            },
        )

    # ------------------------------------------------------------------ util
    @staticmethod
    def _extract(state: Any) -> tuple[str, dict[str, int]]:
        messages = (state or {}).get("messages", []) if isinstance(state, dict) else []
        prompt_tokens = completion_tokens = 0
        answer = ""
        for message in messages:
            if isinstance(message, AIMessage):
                p, c = _usage_of(message)
                prompt_tokens += p
                completion_tokens += c
        if messages and isinstance(messages[-1], AIMessage):
            answer = message_text(messages[-1])
        return answer, {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}

    @staticmethod
    def _intent(trace: RequestTrace, answer: str) -> str:
        if trace.denied:
            return "denied"
        if trace.sql is not None:
            return "data_analysis"
        tool_called = any(t.name == "cari_dokumen" for t in trace.tool_calls)
        if tool_called or (trace.sources and CITATION_RE.search(answer)):
            return "helpdesk"
        return "smalltalk"

    @staticmethod
    def _select_sources(trace: RequestTrace, answer: str) -> list[dict[str, Any]]:
        """Sumber yang dikembalikan: yang dikutip [n]; bila tool dipanggil tanpa sitasi, semuanya;
        hasil prefetch yang tidak dikutip tidak dikembalikan (agar tidak mengesankan jawaban berdasar dokumen)."""
        cited = {f"[{n}]" for n in CITATION_RE.findall(answer)}
        if cited:
            return [s.to_dict() for s in trace.sources if s.ref in cited]
        if any(t.name == "cari_dokumen" for t in trace.tool_calls):
            return [s.to_dict() for s in trace.sources]
        return []

    async def _finalize(
        self, ctx: ChatContext, query: str, answer: str, usage: dict[str, int], started: float
    ) -> ChatResult:
        latency_ms = int((time.perf_counter() - started) * 1000)
        trace = ctx.trace
        if not answer.strip():
            answer = "Maaf, saya tidak dapat menyusun jawaban saat ini. Silakan coba lagi."
        intent = self._intent(trace, answer)
        sources = self._select_sources(trace, answer)
        sql_public = trace.sql.to_dict(include_rows=True) if trace.sql is not None else None
        sql_stored = trace.sql.to_dict(include_rows=False) if trace.sql is not None else None
        reply = await self._chats.add_messages(
            ctx.session_id,
            query,
            {
                "id": uuid.UUID(ctx.message_id),
                "content": answer,
                "intent": intent,
                "access_denied": trace.denied,
                "sources": sources or None,
                "sql_result": sql_stored,
                "model": self._model_name,
                "token_usage": usage,
                "latency_ms": latency_ms,
            },
            title=query[:120],
        )
        log.info(
            "chat selesai",
            session_id=ctx.session_id,
            intent=intent,
            latency_ms=latency_ms,
            tools=[t.name for t in trace.tool_calls],
            denied=trace.denied,
        )
        return ChatResult(
            session_id=ctx.session_id,
            message_id=str(reply.id),
            intent=intent,
            answer=answer,
            sources=sources,
            sql=sql_public,
            usage=usage,
            latency_ms=latency_ms,
            tool_calls=[t.to_dict() for t in trace.tool_calls],
            denied=trace.denied,
        )
