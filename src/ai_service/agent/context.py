"""Konteks runtime per request untuk agent (dibaca tool & middleware via ToolRuntime) + jejak hasil."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ai_service.auth.identity import Identity
from ai_service.rag.retriever import Source


@dataclass
class SqlOutcome:
    backend: str
    status: str  # ok | empty | rejected | error | timeout | denied | unavailable
    query: str | None = None
    views: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    scope_label: str = ""
    elapsed_ms: int = 0
    attempts: int = 0
    error: str | None = None

    def to_dict(self, include_rows: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "backend": self.backend,
            "status": self.status,
            "query": self.query,
            "views": list(self.views),
            "columns": list(self.columns),
            "row_count": self.row_count,
            "truncated": self.truncated,
            "scope_label": self.scope_label,
            "elapsed_ms": self.elapsed_ms,
            "attempts": self.attempts,
        }
        if include_rows:
            out["rows"] = [list(r) for r in self.rows]
        if self.error:
            out["error"] = self.error
        return out


@dataclass
class ToolCallRecord:
    name: str
    status: str  # ok | error | denied
    summary: str
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class RequestTrace:
    sources: list[Source] = field(default_factory=list)
    sql: SqlOutcome | None = None
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    denied: bool = False
    denied_question: str | None = None
    prefetched: bool = False

    @property
    def next_ref(self) -> int:
        return len(self.sources) + 1


@dataclass
class ChatContext:
    identity: Identity
    session_id: str
    message_id: str
    today: date
    trace: RequestTrace = field(default_factory=RequestTrace)
    prefetched_context: str | None = None

    @property
    def ai_role(self) -> str:
        return self.identity.ai_role

    @property
    def platform(self) -> str:
        return self.identity.platform

    @property
    def scope_label(self) -> str:
        return self.identity.scope_label
