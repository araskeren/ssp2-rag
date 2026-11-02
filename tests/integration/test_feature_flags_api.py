"""Sakelar fitur dengan DB nyata: API admin, penegakan endpoint chat, dan /me."""

from __future__ import annotations

import httpx
import pytest

from .test_chat_api import _app

pytestmark = pytest.mark.pg

ADMIN = {"X-API-Key": "dev-admin-key"}
KONEKTOR = {"X-API-Key": "dev-api-key"}
PUBLIK = {"Authorization": "Bearer 43"}
ANALIS = {"Authorization": "Bearer 42"}


async def _ubah(client: httpx.AsyncClient, **changes: bool) -> dict:
    r = await client.put(
        "/api/v1/admin/features", headers={**ADMIN, "X-Admin-Actor": "user:37"}, json=changes
    )
    assert r.status_code == 200, r.text
    return {i["key"]: i for i in r.json()["items"]}


async def test_feature_flags_enforced_end_to_end(pg_settings, pg_db):
    app, _services = await _app(pg_settings, pg_db)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        # Migrasi mengisi keempat sakelar dalam keadaan aktif.
        awal = await client.get("/api/v1/admin/features", headers=ADMIN)
        assert {i["key"]: i["enabled"] for i in awal.json()["items"]} == {
            "assistant": True,
            "helpdesk": True,
            "analysis": True,
            "connector": True,
        }
        assert (await client.get("/api/v1/admin/features")).status_code == 401
        assert (
            await client.put("/api/v1/admin/features", headers=KONEKTOR, json={"analysis": False})
        ).status_code == 401
        assert (
            await client.put("/api/v1/admin/features", headers=ADMIN, json={"lainnya": False})
        ).status_code == 400

        me = (await client.get("/api/v1/me", headers=PUBLIK)).json()
        assert me["available"] is True and me["features"] == {"helpdesk": True, "analysis": True}

        # Sakelar utama menutup chat, stream, dan konektor, tetapi /me tetap menjawab.
        flags = await _ubah(client, assistant=False)
        assert flags["assistant"]["enabled"] is False
        assert flags["assistant"]["updated_by"] == "user:37"
        assert flags["analysis"]["updated_by"] is None

        for path, headers, body in (
            ("/api/v1/chat", ANALIS, {"query": "halo"}),
            ("/api/v1/chat/stream", ANALIS, {"query": "halo"}),
            (
                "/api/v1/internal/chat",
                KONEKTOR,
                {"platform": "wa", "channel_id": "62", "query": "x"},
            ),
        ):
            r = await client.post(path, headers=headers, json=body)
            assert r.status_code == 503, (path, r.text)
            assert "dinonaktifkan" in r.json()["message"]
        assert (await client.get("/api/v1/me", headers=ANALIS)).json()["available"] is False
        await _ubah(client, assistant=True)

        # Helpdesk mati: pengguna umum tidak punya fitur tersisa, analis masih tersedia.
        await _ubah(client, helpdesk=False)
        assert (await client.get("/api/v1/me", headers=PUBLIK)).json()["available"] is False
        assert (await client.get("/api/v1/me", headers=ANALIS)).json()["available"] is True
        r = await client.post("/api/v1/chat", headers=PUBLIK, json={"query": "halo"})
        assert r.status_code == 503
        await _ubah(client, helpdesk=True)

        # Konektor mati hanya menutup jalur internal.
        await _ubah(client, connector=False)
        r = await client.post(
            "/api/v1/internal/chat",
            headers=KONEKTOR,
            json={"platform": "wa", "channel_id": "62", "query": "x"},
        )
        assert r.status_code == 503
        r = await client.post("/api/v1/chat", headers=PUBLIK, json={"query": "Halo"})
        assert r.status_code == 200, r.text
        await _ubah(client, connector=True)
