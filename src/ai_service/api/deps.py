"""Dependency FastAPI: settings/services, IP klien, identitas web (Bearer), API key konektor/admin."""

from __future__ import annotations

from fastapi import Depends, Header, Request

from ai_service.auth.apikey import match_api_key
from ai_service.auth.identity import Identity
from ai_service.config import Settings
from ai_service.errors import ApiError
from ai_service.services import Services


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_services(request: Request) -> Services:
    services = getattr(request.app.state, "services", None)
    if services is None:
        raise ApiError(503, "layanan belum siap")
    return services


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else ""


def bearer_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(401, "header Authorization Bearer wajib diisi")
    token = authorization[7:].strip()
    if not token:
        raise ApiError(401, "token kosong")
    return token


async def get_web_identity(
    request: Request,
    token: str = Depends(bearer_token),
    services: Services = Depends(get_services),
) -> Identity:
    if services.identity is None:
        raise ApiError(503, "resolver identitas belum siap")
    return await services.identity.resolve_web(token, client_ip(request))


def require_api_key(
    x_api_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings_dep),
) -> None:
    if not match_api_key(x_api_key, settings.api_key_list):
        raise ApiError(401, "X-API-Key tidak valid")


def require_admin_key(
    x_api_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings_dep),
) -> None:
    if not match_api_key(x_api_key, settings.admin_api_key_list):
        raise ApiError(401, "X-API-Key admin tidak valid")
