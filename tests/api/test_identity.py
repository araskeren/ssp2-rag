"""Identitas web via JWT + profil BE (respx) tanpa database: channel lookup dipalsukan."""

import time
import uuid
from dataclasses import dataclass

import httpx
import jwt
import pytest
import respx

from ai_service.api.ratelimit import RateLimiter
from ai_service.auth.identity import IdentityResolver, normalize_channel_id
from ai_service.auth.profile_client import ProfileClient
from ai_service.config import Settings
from ai_service.errors import ApiError
from ai_service.main import create_app
from ai_service.services import Services

SECRET = "rahasia-tes"
BE = "http://be.test"


def _token(sub=42, **overrides):
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": f"u{sub}@x.id",
        "roleId": 1,
        "sessionId": str(uuid.uuid4()),
        "iat": now,
        "exp": now + 300,
    }
    payload.update(overrides)
    return jwt.encode(payload, SECRET, algorithm="HS256")


@dataclass
class FakeRow:
    role: str = "analyst"
    scope_level: str = "GLOBAL"
    provinsi_kode: str | None = None
    kabupaten_kota_kode: str | None = None
    facility_id: int | None = None
    is_active: bool = True
    display_name: str | None = None


def _profile(role_name, facility, permissions=()):
    return {
        "id": 42,
        "email": "u42@x.id",
        "name": "Tes",
        "role": {"id": 1, "name": role_name},
        "facility": facility,
        "permissions": list(permissions),
    }


PUSKESMAS = {
    "id": 3,
    "jenisSarana": "Pusat Kesehatan Masyarakat",
    "subJenis": "Puskesmas",
    "provinsiKode": "33",
    "kabupatenKotaKode": "33.28",
}


def _resolver(rows: dict | None = None, **settings_kw):
    settings = Settings(_env_file=None, jwt_secret=SECRET, be_base_url=BE, **settings_kw)
    rows = rows or {}

    async def lookup(platform, channel_id):
        return rows.get((platform, channel_id))

    return settings, IdentityResolver(settings, ProfileClient(BE, 1.0, 300), lookup)


@respx.mock
async def test_analyst_by_role_global_scope():
    _, resolver = _resolver()
    respx.get(f"{BE}/auth/profile").mock(
        return_value=httpx.Response(200, json=_profile("Admin Kemenkes", PUSKESMAS))
    )
    identity = await resolver.resolve_web(_token(), "10.0.0.5")
    assert identity.ai_role == "analyst" and identity.scope.level == "GLOBAL"
    assert identity.reason == "role:Admin Kemenkes"
    sent = respx.calls.last.request
    assert sent.headers["X-Forwarded-For"] == "10.0.0.5"
    assert sent.headers["Authorization"].startswith("Bearer ")


@respx.mock
async def test_public_faskes_scope_and_permission_grant():
    _, resolver = _resolver()
    route = respx.get(f"{BE}/auth/profile")
    route.mock(return_value=httpx.Response(200, json=_profile("Operator Faskes", PUSKESMAS)))
    identity = await resolver.resolve_web(_token(sub=43))
    assert (
        identity.ai_role == "public"
        and identity.scope.level == "FASKES"
        and identity.scope.facility_id == 3
    )

    route.mock(
        return_value=httpx.Response(
            200, json=_profile("Operator Faskes", PUSKESMAS, ["menu_epi_intel:ai"])
        )
    )
    identity2 = await resolver.resolve_web(_token(sub=44))
    assert identity2.ai_role == "analyst" and identity2.reason == "permission:menu_epi_intel:ai"


@respx.mock
async def test_profile_cache_per_session_and_stale_fallback():
    _, resolver = _resolver()
    route = respx.get(f"{BE}/auth/profile")
    route.mock(return_value=httpx.Response(200, json=_profile("Admin Kemenkes", None)))
    token = _token()
    await resolver.resolve_web(token)
    await resolver.resolve_web(token)
    assert route.call_count == 1  # cache per sessionId

    # BE down tanpa cache → public (fail-closed)
    route.mock(side_effect=httpx.ConnectError("down"))
    identity = await resolver.resolve_web(_token(sub=99))
    assert identity.ai_role == "public" and identity.reason == "be_unavailable"


@respx.mock
async def test_revoked_session_is_401_and_bad_tokens_rejected():
    _, resolver = _resolver()
    respx.get(f"{BE}/auth/profile").mock(return_value=httpx.Response(401, json={"statusCode": 401}))
    with pytest.raises(ApiError) as exc:
        await resolver.resolve_web(_token())
    assert exc.value.status_code == 401
    with pytest.raises(ApiError) as exc2:
        await resolver.resolve_web(_token(isTemp=True, sessionId=None))
    assert exc2.value.status_code == 401


@respx.mock
async def test_allowed_channels_web_override_narrows_scope():
    _, resolver = _resolver(
        {("web", "42"): FakeRow(role="analyst", scope_level="PROVINCE", provinsi_kode="33")}
    )
    respx.get(f"{BE}/auth/profile").mock(
        return_value=httpx.Response(200, json=_profile("Viewer", PUSKESMAS))
    )
    identity = await resolver.resolve_web(_token())
    assert (
        identity.ai_role == "analyst"
        and identity.scope.level == "PROVINCE"
        and identity.scope.provinsi_kode == "33"
    )


async def test_internal_channel_resolution_and_policy():
    _, resolver = _resolver(
        {("wa", "628123456789"): FakeRow(role="analyst", scope_level="FASKES", facility_id=3)}
    )
    identity = await resolver.resolve_internal("wa", "+62 812-3456-789")
    assert (
        identity.principal_id == "628123456789"
        and identity.ai_role == "analyst"
        and identity.scope.facility_id == 3
    )
    unknown = await resolver.resolve_internal("tg", "-1001")
    assert unknown.ai_role == "public" and unknown.known_channel is False

    _, deny_resolver = _resolver(internal_unknown_channel_policy="deny")
    with pytest.raises(ApiError) as exc:
        await deny_resolver.resolve_internal("tg", "-1001")
    assert exc.value.status_code == 403
    assert normalize_channel_id("wa", "08123") == "628123"


@respx.mock
async def test_me_endpoint_end_to_end():
    settings, resolver = _resolver()
    respx.get(f"{BE}/auth/profile").mock(
        return_value=httpx.Response(
            200,
            json=_profile(
                "Eksekutif KabKota",
                {
                    "id": 5,
                    "jenisSarana": "Dinas Kesehatan",
                    "subJenis": "Dinas Kesehatan Kota",
                    "provinsiKode": "33",
                    "kabupatenKotaKode": "33.74",
                },
            ),
        )
    )
    app = create_app(settings)
    app.state.services = Services(settings=settings, identity=resolver)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        r = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {_token()}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ai_role"] == "analyst" and body["scope"] == {
            "level": "KABKOTA",
            "kabupaten_kota_kode": "33.74",
        }
        assert r.headers["x-request-id"]
        assert (await client.get("/api/v1/me")).status_code == 401
        bad = await client.get("/api/v1/me", headers={"Authorization": "Bearer abc"})
        assert bad.status_code == 401 and bad.json()["error"] == "Unauthorized"
        no_key = await client.get("/api/v1/admin/channels")
        assert no_key.status_code == 401


def test_rate_limiter_bucket():
    limiter = RateLimiter(per_minute=3, burst=3)
    assert [limiter.allow("a") for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("b") is True
    assert RateLimiter(per_minute=0).allow("x") is True
