"""Memori percakapan: N pesan terakhir dari chat_messages → pesan LangChain yang dipangkas."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.messages.utils import trim_messages


def approx_token_count(messages: Sequence[BaseMessage]) -> int:
    total = 0
    for message in messages:
        content = message.content if isinstance(message.content, str) else str(message.content)
        total += len(content) // 3 + 4
    return total


def rows_to_messages(rows: Sequence[Any]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for row in rows:
        if row.role == "user":
            out.append(HumanMessage(content=row.content))
        elif row.role == "assistant":
            out.append(AIMessage(content=row.content))
    return out


def trim_history(messages: list[BaseMessage], max_tokens: int) -> list[BaseMessage]:
    if not messages:
        return []
    return trim_messages(
        messages,
        strategy="last",
        max_tokens=max_tokens,
        token_counter=approx_token_count,
        start_on="human",
        include_system=False,
        allow_partial=False,
    )
