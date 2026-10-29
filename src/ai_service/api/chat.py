"""Endpoint chat web (Bearer): /chat, /chat/stream, daftar/riwayat/hapus sesi."""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from ai_service.api.deps import client_ip, get_services, get_web_identity
from ai_service.api.schemas import ChatRequest
from ai_service.api.sse import sse_response
from ai_service.auth.identity import Identity
from ai_service.errors import ApiError
from ai_service.services import Services

router = APIRouter(prefix="/api/v1", tags=["chat"])

WEB_SESSION_RE = re.compile(r"^web_[0-9a-fA-F-]{8,64}$")


def resolve_web_session_id(raw: str | None) -> str:
    if raw is None or not raw.strip():
        return f"web_{uuid.uuid4()}"
    value = raw.strip()
    if not WEB_SESSION_RE.match(value):
        raise ApiError(400, "validasi request gagal", ["session_id: format harus web_<uuid>"])
    return value


def _runner(services: Services):
    if services.runner is None:
        raise ApiError(503, "asisten belum siap (periksa konfigurasi LLM)")
    return services.runner


def enforce_rate_limit(request: Request, services: Services, identity: Identity) -> None:
    if services.rate_chat is not None and not services.rate_chat.allow(
        f"{identity.platform}:{identity.principal_id}"
    ):
        raise ApiError(
            429, "terlalu banyak permintaan, coba lagi sebentar", headers={"Retry-After": "60"}
        )
    if services.rate_ip is not None and not services.rate_ip.allow(client_ip(request)):
        raise ApiError(
            429, "terlalu banyak permintaan dari alamat ini", headers={"Retry-After": "60"}
        )


@router.post("/chat")
async def chat(
    body: ChatRequest,
    request: Request,
    identity: Identity = Depends(get_web_identity),
    services: Services = Depends(get_services),
) -> dict:
    enforce_rate_limit(request, services, identity)
    session_id = resolve_web_session_id(body.session_id)
    result = await _runner(services).run(identity, session_id, body.query)
    return result.to_dict()


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    request: Request,
    identity: Identity = Depends(get_web_identity),
    services: Services = Depends(get_services),
) -> Response:
    enforce_rate_limit(request, services, identity)
    session_id = resolve_web_session_id(body.session_id)
    runner = _runner(services)
    runner.validate_query(body.query)
    await runner.ensure_session(identity, session_id)
    return sse_response(
        runner.stream(identity, session_id, body.query), services.settings.sse_heartbeat_s
    )


@router.get("/chat/sessions")
async def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    identity: Identity = Depends(get_web_identity),
    services: Services = Depends(get_services),
) -> dict:
    if services.chats is None:
        raise ApiError(503, "database belum siap")
    rows = await services.chats.list_sessions(identity.platform, identity.principal_id, limit)
    return {
        "items": [
            {
                "session_id": r.session_id,
                "title": r.title,
                "message_count": r.message_count,
                "created_at": r.created_at,
                "last_message_at": r.last_message_at,
            }
            for r in rows
        ]
    }


@router.get("/chat/sessions/{session_id}/messages")
async def session_messages(
    session_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    identity: Identity = Depends(get_web_identity),
    services: Services = Depends(get_services),
) -> dict:
    if services.chats is None:
        raise ApiError(503, "database belum siap")
    session = await services.chats.get_session(session_id)
    if session is None:
        raise ApiError(404, "sesi tidak ditemukan")
    if session.platform != identity.platform or session.principal_id != identity.principal_id:
        raise ApiError(403, "sesi bukan milik pengguna ini")
    rows = await services.chats.list_messages(session_id, limit)
    return {
        "session_id": session_id,
        "items": [
            {
                "id": str(r.id),
                "role": r.role,
                "content": r.content,
                "intent": r.intent,
                "sources": r.sources,
                "sql": r.sql_result,
                "created_at": r.created_at,
            }
            for r in rows
        ],
    }


@router.delete("/chat/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    identity: Identity = Depends(get_web_identity),
    services: Services = Depends(get_services),
) -> Response:
    if services.chats is None:
        raise ApiError(503, "database belum siap")
    session = await services.chats.get_session(session_id)
    if session is None:
        raise ApiError(404, "sesi tidak ditemukan")
    if session.platform != identity.platform or session.principal_id != identity.principal_id:
        raise ApiError(403, "sesi bukan milik pengguna ini")
    await services.chats.delete_session(session_id)
    return Response(status_code=204)
