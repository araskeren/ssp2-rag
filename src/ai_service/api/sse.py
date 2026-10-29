"""Pengodean Server-Sent Events + heartbeat `ping` saat produser diam."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi.responses import StreamingResponse

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def format_sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


async def with_heartbeat(
    source: AsyncIterator[tuple[str, Any]], heartbeat_s: float
) -> AsyncIterator[str]:
    queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()
    error_holder: dict[str, BaseException] = {}

    async def produce() -> None:
        try:
            async for item in source:
                await queue.put(item)
        except BaseException as exc:  # noqa: BLE001
            error_holder["error"] = exc
        finally:
            await queue.put(None)

    task = asyncio.create_task(produce())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
            except TimeoutError:
                yield format_sse("ping", {})
                continue
            if item is None:
                break
            yield format_sse(item[0], item[1])
        if "error" in error_holder:
            yield format_sse("error", {"statusCode": 500, "message": "terjadi kesalahan internal"})
    finally:
        if not task.done():
            task.cancel()


def sse_response(source: AsyncIterator[tuple[str, Any]], heartbeat_s: float) -> StreamingResponse:
    return StreamingResponse(
        with_heartbeat(source, heartbeat_s), media_type="text/event-stream", headers=SSE_HEADERS
    )
