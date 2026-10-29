"""Alur chat end-to-end dengan DB nyata, embedding palsu, dan model chat terskrip (tanpa LLM nyata)."""

from __future__ import annotations

import json

import httpx
import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from ai_service.auth.identity import Identity
from ai_service.auth.scope import UserScope
from ai_service.main import create_app
from ai_service.rag.embeddings import EmbeddingService
from ai_service.rag.ingest import IngestOptions, UploadedFile
from ai_service.services import Services, build_services

pytestmark = pytest.mark.pg


class ScriptedModel(FakeMessagesListChatModel):
    """Model palsu yang mengikuti skrip dan merekam pesan masuk (untuk memeriksa memori)."""

    seen_inputs: list = []

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001
        type(self).seen_inputs.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _tool_call(name: str, question: str, call_id: str) -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": {"pertanyaan": question}, "id": call_id}]
    )


SCRIPT = [
    _tool_call("cari_dokumen", "alur pelaporan SARI", "c1"),
    AIMessage(content="Alur pelaporan dimulai dari pendaftaran kasus di faskes [1]."),
    _tool_call("analisis_data", "jumlah kasus SARI per provinsi", "c2"),
    AIMessage(content="Maaf, fitur Analisis Data hanya tersedia untuk analis berwenang."),
    AIMessage(content="Halo! Ada yang bisa saya bantu?"),
    _tool_call("analisis_data", "jumlah kasus SARI per provinsi", "c3"),
    AIMessage(content="Analisis data belum tersedia di server ini."),
    _tool_call("cari_dokumen", "spesimen", "c4"),
    AIMessage(content="Spesimen dikirim pada suhu 2-8 °C [1]."),
]


class FakeResolver:
    def __init__(self) -> None:
        self.web = {
            "43": Identity(
                "43",
                "web",
                "public",
                UserScope("FASKES", facility_id=3),
                "role_public",
                email="u43@x.id",
                session_id="s43",
            ),
            "42": Identity(
                "42",
                "web",
                "analyst",
                UserScope("GLOBAL"),
                "role:Admin Kemenkes",
                email="u42@x.id",
                session_id="s42",
            ),
        }

    async def resolve_web(self, token: str, client_ip: str | None = None) -> Identity:
        if token not in self.web:
            from ai_service.errors import ApiError

            raise ApiError(401, "token tidak valid")
        return self.web[token]

    async def resolve_internal(
        self, platform: str, channel_id: str, display_name: str | None = None
    ) -> Identity:
        from ai_service.auth.identity import normalize_channel_id

        return Identity(
            normalize_channel_id(platform, channel_id),
            platform,
            "public",
            UserScope("GLOBAL"),
            "unknown_channel",
            known_channel=False,
        )


async def _app(pg_settings, pg_db):
    ScriptedModel.seen_inputs = []
    settings = pg_settings.model_copy(update={"chat_history_messages": 6})
    app = create_app(settings)
    app.state.db = pg_db
    services: Services = await build_services(
        settings,
        pg_db,
        embeddings=EmbeddingService(settings, DeterministicFakeEmbedding(size=64)),
        llm=ScriptedModel(responses=list(SCRIPT)),
        with_sql=False,
    )
    services.identity = FakeResolver()  # type: ignore[assignment]
    app.state.services = services
    await services.ingest.submit(
        [
            UploadedFile(
                "panduan.md",
                b"# Panduan SARI\n\nAlur pelaporan kasus SARI dimulai dari pendaftaran kasus di faskes sentinel.\n\nSpesimen dikirim pada suhu 2-8 derajat ke laboratorium rujukan.",
            )
        ],
        IngestOptions(),
        sync=True,
    )
    return app, services


async def test_chat_flow_public_analyst_memory_stream(pg_settings, pg_db):
    app, services = await _app(pg_settings, pg_db)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        # 1) helpdesk sebagai public (43): tool cari_dokumen → sitasi
        r = await client.post(
            "/api/v1/chat",
            headers={"Authorization": "Bearer 43"},
            json={"query": "Bagaimana alur pelaporan SARI?"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert (
            body["intent"] == "helpdesk"
            and body["sources"][0]["ref"] == "[1]"
            and body["sql"] is None
        )
        assert (
            body["session_id"].startswith("web_")
            and body["tool_calls"][0]["name"] == "cari_dokumen"
        )
        session_id = body["session_id"]

        # 2) pertanyaan data sebagai public → ditolak deterministik + audit access_denied
        r2 = await client.post(
            "/api/v1/chat",
            headers={"Authorization": "Bearer 43"},
            json={"session_id": session_id, "query": "Berapa kasus SARI per provinsi?"},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["intent"] == "denied" and "analis" in r2.json()["answer"]
        audit = await client.get(
            "/api/v1/admin/sql-audit",
            headers={"X-API-Key": "dev-admin-key"},
            params={"principal_id": "43"},
        )
        assert audit.json()["total"] == 1 and audit.json()["items"][0]["status"] == "access_denied"
        assert audit.json()["items"][0]["scope"] == {"level": "FASKES", "facility_id": 3}

        # 3) memori: pesan ke-3 di sesi yang sama membawa riwayat 2 giliran sebelumnya
        r3 = await client.post(
            "/api/v1/chat",
            headers={"Authorization": "Bearer 43"},
            json={"session_id": session_id, "query": "Halo"},
        )
        assert r3.json()["intent"] == "smalltalk"
        last_input = ScriptedModel.seen_inputs[-1]
        human_texts = [m.content for m in last_input if m.type == "human"]
        assert human_texts[-1] == "Halo" and "Bagaimana alur pelaporan SARI?" in human_texts
        assert any(
            m.type == "system" and "Asisten Satu Data Penyakit" in m.content for m in last_input
        )

        # riwayat & daftar sesi
        hist = await client.get(
            f"/api/v1/chat/sessions/{session_id}/messages", headers={"Authorization": "Bearer 43"}
        )
        assert hist.status_code == 200 and len(hist.json()["items"]) == 6
        assert hist.json()["items"][1]["sources"][0]["ref"] == "[1]"
        sessions = await client.get("/api/v1/chat/sessions", headers={"Authorization": "Bearer 43"})
        assert sessions.json()["items"][0]["session_id"] == session_id
        # sesi milik orang lain → 403
        other = await client.get(
            f"/api/v1/chat/sessions/{session_id}/messages", headers={"Authorization": "Bearer 42"}
        )
        assert other.status_code == 403

        # 4) analis (42): SQL tool belum dikonfigurasi (M4) → status unavailable, bukan ditolak
        r4 = await client.post(
            "/api/v1/chat",
            headers={"Authorization": "Bearer 42"},
            json={"query": "Berapa kasus SARI per provinsi?"},
        )
        assert r4.status_code == 200, r4.text
        assert (
            r4.json()["intent"] == "data_analysis" and r4.json()["sql"]["status"] == "unavailable"
        )

        # 5) streaming SSE
        events = []
        async with client.stream(
            "POST",
            "/api/v1/chat/stream",
            headers={"Authorization": "Bearer 42"},
            json={"query": "Spesimen?"},
        ) as resp:
            assert resp.status_code == 200 and resp.headers["content-type"].startswith(
                "text/event-stream"
            )
            async for line in resp.aiter_lines():
                if line.startswith("event: "):
                    events.append([line[7:], None])
                elif line.startswith("data: ") and events:
                    events[-1][1] = json.loads(line[6:])
        names = [e[0] for e in events]
        assert names[0] == "meta" and names[-1] == "done"
        assert "token" in names and "sources" in names and ("tool" in names)
        token_text = "".join(e[1]["t"] for e in events if e[0] == "token")
        assert "Spesimen dikirim" in token_text and events[-1][1]["intent"] == "helpdesk"

        # 6) validasi & auth
        assert (
            await client.post(
                "/api/v1/chat", headers={"Authorization": "Bearer 43"}, json={"query": "  "}
            )
        ).status_code == 400
        assert (
            await client.post(
                "/api/v1/chat",
                headers={"Authorization": "Bearer 43"},
                json={"session_id": "bukan", "query": "x"},
            )
        ).status_code == 400
        assert (await client.post("/api/v1/chat", json={"query": "x"})).status_code == 401
        assert (
            await client.delete(
                f"/api/v1/chat/sessions/{session_id}", headers={"Authorization": "Bearer 43"}
            )
        ).status_code == 204
    await services.aclose()


async def test_internal_chat_plain_text(pg_settings, pg_db):
    app, services = await _app(pg_settings, pg_db)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        r = await client.post(
            "/api/v1/internal/chat",
            headers={"X-API-Key": "dev-api-key"},
            json={
                "platform": "wa",
                "channel_id": "+62 812-0000-0001",
                "query": "Bagaimana alur pelaporan SARI?",
                "display_name": "Budi",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert (
            body["session_id"] == "wa_6281200000001" and body["identity"]["known_channel"] is False
        )
        assert body["text_plain"].endswith("Sumber:\n[1] Panduan SARI")
        assert (
            await client.post(
                "/api/v1/internal/chat",
                headers={"X-API-Key": "salah"},
                json={"platform": "wa", "channel_id": "1", "query": "x"},
            )
        ).status_code == 401
    await services.aclose()
