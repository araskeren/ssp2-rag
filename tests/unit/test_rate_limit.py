"""enforce_rate_limit: limit per principal selalu, limit per IP bisa dilewati konektor."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from ai_service.api.chat import enforce_rate_limit
from ai_service.api.ratelimit import RateLimiter
from ai_service.errors import ApiError


def _request(ip: str = "10.0.0.5") -> Request:
    return Request({"type": "http", "headers": [], "client": (ip, 1234)})


def _services(chat_per_min: int, ip_per_min: int) -> SimpleNamespace:
    return SimpleNamespace(rate_chat=RateLimiter(chat_per_min), rate_ip=RateLimiter(ip_per_min))


def _identity(principal: str, platform: str = "wa") -> SimpleNamespace:
    return SimpleNamespace(platform=platform, principal_id=principal)


def test_ip_limit_applies_by_default():
    services = _services(chat_per_min=100, ip_per_min=1)
    enforce_rate_limit(_request(), services, _identity("6281"))
    with pytest.raises(ApiError) as exc:
        enforce_rate_limit(_request(), services, _identity("6282"))
    assert exc.value.status_code == 429


def test_connector_skips_ip_limit_for_many_numbers():
    services = _services(chat_per_min=100, ip_per_min=1)
    for n in range(5):  # banyak nomor WA, satu IP notification-service
        enforce_rate_limit(_request(), services, _identity(f"62811{n}"), check_ip=False)


def test_connector_still_limited_per_number():
    services = _services(chat_per_min=1, ip_per_min=100)
    enforce_rate_limit(_request(), services, _identity("6281"), check_ip=False)
    with pytest.raises(ApiError) as exc:
        enforce_rate_limit(_request(), services, _identity("6281"), check_ip=False)
    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == "60"
