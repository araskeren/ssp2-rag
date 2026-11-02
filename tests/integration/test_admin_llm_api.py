"""Registry LLM via admin API: provider/model/penugasan, enkripsi credentials, resolusi per role/kanal."""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from sqlalchemy import text

from ai_service.main import create_app
from ai_service.rag.embeddings import EmbeddingService
from ai_service.services import build_services

pytestmark = pytest.mark.pg

ADMIN = {"X-API-Key": "dev-admin-key"}
CONNECTOR = {"X-API-Key": "dev-api-key"}
BASE = "/api/v1/admin/llm"


class EchoModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        return self


def echo_factory(spec, settings, *, temperature=None):  # noqa: ANN001
    return EchoModel(responses=[AIMessage(content=f"jawaban dari {spec.name}")])


class _ProviderHandler(BaseHTTPRequestHandler):
    """Meniru endpoint OpenAI `/v1/chat/completions` dan Anthropic `/anthropic/v1/messages`."""

    requests: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        type(self).requests.append(
            {
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body,
            }
        )
        if self.path.endswith("/chat/completions"):
            payload = {
                "id": "c1",
                "object": "chat.completion",
                "created": 0,
                "model": body.get("model"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        elif self.path.endswith("/v1/messages"):
            payload = {
                "id": "m1",
                "type": "message",
                "role": "assistant",
                "model": body.get("model"),
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        else:
            self.send_response(404)
            self.end_headers()
            return
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args) -> None:  # noqa: ANN002
        pass


@pytest.fixture
def fake_provider(monkeypatch):
    # Panggilan ke 127.0.0.1 tidak boleh lewat proxy env (sandbox/CI).
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    _ProviderHandler.requests = []
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = ThreadingHTTPServer(("127.0.0.1", port), _ProviderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.05)

    class Info:
        url = f"http://127.0.0.1:{port}"
        requests = _ProviderHandler.requests

    yield Info
    server.shutdown()


async def _app(pg_settings, pg_db):
    app = create_app(pg_settings)
    app.state.db = pg_db
    services = await build_services(
        pg_settings,
        pg_db,
        embeddings=EmbeddingService(pg_settings, DeterministicFakeEmbedding(size=64)),
        with_sql=False,
    )
    services.llm_registry.chat_factory = echo_factory  # type: ignore[union-attr]
    app.state.services = services
    return app, services


async def test_llm_registry_admin_flow(pg_settings, pg_db, fake_provider):
    app, services = await _app(pg_settings, pg_db)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        # --- provider: credentials terenkripsi, tidak pernah dikembalikan
        assert (await client.get(f"{BASE}/providers")).status_code == 401
        r = await client.post(
            f"{BASE}/providers",
            headers=ADMIN,
            json={
                "name": "oa",
                "kind": "openai_compatible",
                "base_url": f"{fake_provider.url}/v1/",
                "api_key": "sk-openai-rahasia-1234",
            },
        )
        assert r.status_code == 201, r.text
        oa = r.json()
        assert oa["api_key_set"] is True and oa["api_key_hint"] == "…1234"
        assert oa["base_url"] == f"{fake_provider.url}/v1"
        assert "rahasia" not in r.text
        async with pg_db.engine.connect() as conn:
            stored = (
                await conn.execute(text("SELECT api_key_enc FROM llm_providers WHERE name='oa'"))
            ).scalar()
        assert stored and "rahasia" not in stored

        r = await client.post(
            f"{BASE}/providers",
            headers=ADMIN,
            json={
                "name": "anth",
                "kind": "anthropic_compatible",
                "base_url": f"{fake_provider.url}/anthropic",
                "api_key": "sk-ant-rahasia-9999",
                "headers": {"x-gateway-token": "tok-rahasia", "anthropic-beta": "b1"},
            },
        )
        assert r.status_code == 201, r.text
        anth = r.json()
        assert anth["headers"] == {"x-gateway-token": "***", "anthropic-beta": "b1"}
        dup = await client.post(
            f"{BASE}/providers", headers=ADMIN, json={"name": "oa", "kind": "openai_compatible"}
        )
        assert dup.status_code == 409
        bad = await client.post(
            f"{BASE}/providers", headers=ADMIN, json={"name": "x", "kind": "gemini"}
        )
        assert bad.status_code in (400, 422)

        # --- model
        async def model(body):
            return await client.post(f"{BASE}/models", headers=ADMIN, json=body)

        gpt = (
            await model({"provider_id": oa["id"], "name": "gpt-mini", "model": "gpt-4o-mini"})
        ).json()
        claude = (
            await model(
                {
                    "provider_id": anth["id"],
                    "name": "claude",
                    "model": "claude-sonnet-4-5",
                    "max_tokens": 1024,
                }
            )
        ).json()
        assert gpt["kind"] == "chat" and claude["max_tokens"] == 1024
        r = await model(
            {
                "provider_id": anth["id"],
                "name": "e1",
                "model": "x",
                "kind": "embedding",
                "embedding_dim": 8,
            }
        )
        assert r.status_code == 400  # anthropic tidak punya embedding
        r = await model({"provider_id": oa["id"], "name": "e2", "model": "x", "kind": "embedding"})
        assert r.status_code == 400  # embedding_dim wajib
        emb = (
            await model(
                {
                    "provider_id": oa["id"],
                    "name": "emb",
                    "model": "fake-64",
                    "kind": "embedding",
                    "embedding_dim": 64,
                }
            )
        ).json()

        # --- uji koneksi nyata lewat SDK ke server provider palsu (lokal)
        t1 = (await client.post(f"{BASE}/models/{gpt['id']}/test", headers=ADMIN)).json()
        t2 = (await client.post(f"{BASE}/models/{claude['id']}/test", headers=ADMIN)).json()
        assert t1["ok"] and t1["output"] == "ok", t1
        assert t2["ok"] and t2["output"] == "ok", t2
        oa_req = next(r for r in fake_provider.requests if r["path"] == "/v1/chat/completions")
        an_req = next(r for r in fake_provider.requests if r["path"] == "/anthropic/v1/messages")
        assert oa_req["headers"]["authorization"] == "Bearer sk-openai-rahasia-1234"
        assert oa_req["body"]["model"] == "gpt-4o-mini"
        assert "sk-ant-rahasia-9999" in (
            an_req["headers"].get("x-api-key") or an_req["headers"].get("authorization", "")
        )
        assert "anthropic-version" in an_req["headers"]
        assert an_req["headers"]["x-gateway-token"] == "tok-rahasia"
        assert (
            an_req["body"]["model"] == "claude-sonnet-4-5" and an_req["body"]["max_tokens"] == 1024
        )

        # --- penugasan
        async def assign(body):
            return await client.put(f"{BASE}/assignments", headers=ADMIN, json=body)

        assert (await assign({"purpose": "chat", "model_id": gpt["id"]})).status_code == 200
        assert (
            await assign({"purpose": "chat", "ai_role": "analyst", "model_id": claude["id"]})
        ).status_code == 200
        assert (
            await assign({"purpose": "sql", "ai_role": "analyst", "model_id": claude["id"]})
        ).status_code == 400
        assert (await assign({"purpose": "embedding", "model_id": gpt["id"]})).status_code == 400
        assert (await assign({"purpose": "chat", "model_id": emb["id"]})).status_code == 400
        r = await assign({"purpose": "embedding", "model_id": emb["id"]})
        assert r.status_code == 200 and "restart_required" not in r.json()
        listing = (await client.get(f"{BASE}/assignments", headers=ADMIN)).json()["items"]
        assert {(a["purpose"], a["ai_role"]) for a in listing} == {
            ("chat", None),
            ("chat", "analyst"),
            ("embedding", None),
        }

        res = (await client.get(f"{BASE}/resolve?ai_role=analyst", headers=ADMIN)).json()
        assert (
            res["chat"]["name"] == "claude"
            and res["chat"]["provider_kind"] == "anthropic_compatible"
        )
        assert res["sql"]["name"] == "gpt-mini" and res["judge"]["name"] == "gpt-mini"
        assert res["embedding"]["source"] == "db" and res["embedding"]["active_in_process"] is True

        # --- chat: public → default, analyst → per role, kanal dengan override → model kanal
        async def chat(channel_id):
            r = await client.post(
                "/api/v1/internal/chat",
                headers=CONNECTOR,
                json={"platform": "wa", "channel_id": channel_id, "query": "halo"},
            )
            assert r.status_code == 200, r.text
            return r.json()

        assert "jawaban dari gpt-mini" in (await chat("081111111111"))["answer"]

        r = await client.post(
            "/api/v1/admin/channels",
            headers=ADMIN,
            json={"platform": "wa", "channel_id": "082222222222", "role": "analyst"},
        )
        assert r.status_code == 201 and r.json()["llm_model_id"] is None
        assert "jawaban dari claude" in (await chat("082222222222"))["answer"]

        r = await client.post(
            "/api/v1/admin/channels",
            headers=ADMIN,
            json={
                "platform": "wa",
                "channel_id": "083333333333",
                "role": "analyst",
                "llm_model_id": gpt["id"],
            },
        )
        assert r.status_code == 201, r.text
        assert "jawaban dari gpt-mini" in (await chat("083333333333"))["answer"]
        bad = await client.patch(
            f"/api/v1/admin/channels/{r.json()['id']}",
            headers=ADMIN,
            json={"llm_model_id": emb["id"]},
        )
        assert bad.status_code == 400

        msgs = await services.chats.list_messages("wa_6282222222222")
        assert [m.model for m in msgs if m.role == "assistant"] == ["anth/claude-sonnet-4-5"]

        # --- perubahan admin langsung berlaku (invalidate): nonaktifkan claude → analyst ke default
        r = await client.patch(
            f"{BASE}/models/{claude['id']}", headers=ADMIN, json={"is_active": False}
        )
        assert r.status_code == 200
        assert "jawaban dari gpt-mini" in (await chat("082222222222"))["answer"]

        # --- integritas hapus
        assert (await client.delete(f"{BASE}/models/{gpt['id']}", headers=ADMIN)).status_code == 409
        assert (
            await client.delete(f"{BASE}/providers/{oa['id']}", headers=ADMIN)
        ).status_code == 409
        assert (
            await client.delete(f"{BASE}/assignments?purpose=chat&ai_role=analyst", headers=ADMIN)
        ).status_code == 204
        assert (
            await client.delete(f"{BASE}/models/{claude['id']}", headers=ADMIN)
        ).status_code == 204

        # --- header bertopeng "***" dipertahankan; api_key "" menghapus credentials
        r = await client.patch(
            f"{BASE}/providers/{anth['id']}",
            headers=ADMIN,
            json={"api_key": "", "headers": {"x-gateway-token": "***", "anthropic-beta": "b2"}},
        )
        assert r.json()["api_key_set"] is False and r.json()["headers"]["anthropic-beta"] == "b2"
        row = await services.llm_repo.get_provider(anth["id"])
        assert row.headers["x-gateway-token"] == "tok-rahasia"
        assert (
            await client.delete(f"{BASE}/providers/{anth['id']}", headers=ADMIN)
        ).status_code == 204

        ready = (await client.get("/readyz")).json()
        assert ready["llm"] == "configured"


async def test_db_embedding_assignment_applies_at_boot(pg_settings, pg_db):
    _, services = await _app(pg_settings, pg_db)
    provider = await services.llm_repo.create_provider(
        {"name": "emb-p", "kind": "openai_compatible", "base_url": "http://emb.test/v1"}
    )
    model = await services.llm_repo.create_model(
        {
            "provider_id": provider.id,
            "name": "emb",
            "model": "fake-64",
            "kind": "embedding",
            "embedding_dim": 64,
        }
    )
    await services.llm_repo.upsert_assignment("embedding", None, model.id)
    rebuilt = await build_services(pg_settings, pg_db, with_sql=False, with_agent=False)
    assert rebuilt.settings.embedding_model == "fake-64" and rebuilt.settings.embedding_dim == 64
    assert rebuilt.embeddings._embeddings.openai_api_base == "http://emb.test/v1"  # type: ignore[union-attr]


async def test_embedding_assignment_applies_live_and_refuses_when_data_exists(pg_settings, pg_db):
    """Penugasan dari menu berlaku tanpa restart; model lain ditolak selama ada vektor lama."""
    settings = pg_settings.model_copy(update={"embedding_dim": 64, "embedding_model": "fake-64"})
    app = create_app(settings)
    app.state.db = pg_db
    services = await build_services(settings, pg_db, with_agent=False, with_sql=False)
    app.state.services = services
    headers = {"X-API-Key": "dev-admin-key"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        provider = (
            await client.post(
                "/api/v1/admin/llm/providers",
                headers=headers,
                json={
                    "name": "emb-live",
                    "kind": "openai_compatible",
                    "base_url": "http://emb.test/v1",
                    "api_key": "kunci",
                },
            )
        ).json()

        async def buat(name: str, dim: int) -> dict:
            r = await client.post(
                "/api/v1/admin/llm/models",
                headers=headers,
                json={
                    "provider_id": provider["id"],
                    "name": name,
                    "model": name,
                    "kind": "embedding",
                    "embedding_dim": dim,
                },
            )
            assert r.status_code == 201, r.text
            return r.json()

        async def tugaskan(model: dict) -> httpx.Response:
            return await client.put(
                "/api/v1/admin/llm/assignments",
                headers=headers,
                json={"purpose": "embedding", "model_id": model["id"]},
            )

        kecil = await buat("emb-kecil", 32)
        besar = await buat("emb-besar", 128)

        # Tanpa vektor tersimpan: langsung berlaku, termasuk tipe kolom.
        assert (await tugaskan(kecil)).status_code == 200
        assert services.embeddings.model == "emb-kecil" and services.embeddings.dim == 32
        async with pg_db.engine.connect() as conn:
            kolom = (
                await conn.execute(
                    text(
                        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                        "WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'"
                    )
                )
            ).scalar()
        assert kolom == "vector(32)"
        res = (await client.get("/api/v1/admin/llm/resolve", headers=headers)).json()
        assert res["embedding"]["active_in_process"] is True

        # Kunci provider baru ikut terbaca tanpa restart.
        lama = services.embeddings._embeddings
        r = await client.patch(
            f"/api/v1/admin/llm/providers/{provider['id']}",
            headers=headers,
            json={"api_key": "baru"},
        )
        assert r.status_code == 200
        await services.embeddings.sync()
        assert services.embeddings._embeddings is not lama

        # Ada vektor tersimpan: model lain ditolak sebelum disimpan, jadi boot berikutnya aman.
        async with pg_db.engine.begin() as conn:
            doc_id = (
                await conn.execute(
                    text(
                        "INSERT INTO documents (filename, mime_type, sha256, size_bytes, content, status) "
                        "VALUES ('a.md', 'text/markdown', :sha, 1, 'isi', 'indexed') RETURNING id"
                    ),
                    {"sha": "e" * 64},
                )
            ).scalar()
            vektor = "[" + ",".join(["0.1"] * 32) + "]"
            await conn.execute(
                text(
                    "INSERT INTO document_chunks (id, document_id, chunk_index, content, embedding) "
                    "VALUES (uuidv7(), :doc, 0, 'isi', CAST(:vec AS vector))"
                ),
                {"doc": doc_id, "vec": vektor},
            )
        ditolak = await tugaskan(besar)
        assert ditolak.status_code == 409 and "Hapus semua dokumen" in ditolak.json()["message"]
        assert services.embeddings.model == "emb-kecil"
        aktif = (await client.get("/api/v1/admin/llm/assignments", headers=headers)).json()["items"]
        assert [a["model_id"] for a in aktif if a["purpose"] == "embedding"] == [kecil["id"]]

        # Mengubah model yang sedang ditugaskan dan melepasnya ke env juga ditolak.
        r = await client.patch(
            f"/api/v1/admin/llm/models/{kecil['id']}", headers=headers, json={"embedding_dim": 16}
        )
        assert r.status_code == 409
        r = await client.delete(
            "/api/v1/admin/llm/assignments", headers=headers, params={"purpose": "embedding"}
        )
        assert r.status_code == 409

        # Boot ulang tetap berhasil karena penugasan yang tersimpan cocok dengan data.
        rebuilt = await build_services(settings, pg_db, with_agent=False, with_sql=False)
        assert rebuilt.settings.embedding_model == "emb-kecil"

        # Setelah dokumen dihapus, model lain boleh dipakai lagi.
        async with pg_db.engine.begin() as conn:
            await conn.execute(text("DELETE FROM document_chunks"))
            await conn.execute(text("DELETE FROM documents WHERE id = :doc"), {"doc": doc_id})
        assert (await tugaskan(besar)).status_code == 200
        assert services.embeddings.dim == 128
