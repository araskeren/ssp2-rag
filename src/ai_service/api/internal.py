"""Endpoint internal untuk konektor WA/TG (X-API-Key): sesi diturunkan dari platform + channel_id."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ai_service.agent.plain_text import to_messaging_text
from ai_service.api.chat import _runner, enforce_rate_limit
from ai_service.api.deps import get_services, require_api_key
from ai_service.api.schemas import InternalChatRequest
from ai_service.errors import ApiError
from ai_service.services import Services

router = APIRouter(
    prefix="/api/v1/internal", tags=["internal"], dependencies=[Depends(require_api_key)]
)


@router.post("/chat")
async def internal_chat(
    body: InternalChatRequest, request: Request, services: Services = Depends(get_services)
) -> dict:
    if services.identity is None:
        raise ApiError(503, "resolver identitas belum siap")
    identity = await services.identity.resolve_internal(
        body.platform, body.channel_id, body.display_name
    )
    enforce_rate_limit(request, services, identity)
    session_id = f"{identity.platform}_{identity.principal_id}"
    result = await _runner(services).run(identity, session_id, body.query)
    payload = result.to_dict()
    payload["text_plain"] = to_messaging_text(result.answer, result.sources)
    payload["identity"] = {
        "ai_role": identity.ai_role,
        "scope_level": identity.scope.level,
        "scope_label": identity.scope_label,
        "known_channel": identity.known_channel,
    }
    return payload
