"""Endpoint admin ingest/dokumen/search dengan DB nyata dan embedding palsu."""

import httpx
import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from ai_service.main import create_app
from ai_service.rag.embeddings import EmbeddingService
from ai_service.services import build_services

pytestmark = pytest.mark.pg

HEADERS = {"X-API-Key": "dev-admin-key"}


async def test_admin_ingest_flow(pg_settings, pg_db):
    app = create_app(pg_settings)
    app.state.db = pg_db
    app.state.services = await build_services(
        pg_settings,
        pg_db,
        embeddings=EmbeddingService(pg_settings, DeterministicFakeEmbedding(size=64)),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        files = [
            (
                "files",
                (
                    "panduan.md",
                    b"# Panduan\n\nAlur pelaporan kasus dimulai dari pendaftaran pasien di faskes.",
                    "text/markdown",
                ),
            )
        ]
        r = await client.post(
            "/api/v1/admin/ingest",
            headers=HEADERS,
            files=files,
            data={"sync": "true", "tags": "sari,juknis"},
        )
        assert r.status_code == 202, r.text
        job = r.json()
        assert job["status"] == "done" and job["items"][0]["status"] == "indexed"
        doc_id = job["items"][0]["document_id"]

        j = await client.get(f"/api/v1/admin/ingest/{job['job_id']}", headers=HEADERS)
        assert j.status_code == 200 and j.json()["done_files"] == 1

        d = await client.get(f"/api/v1/admin/documents/{doc_id}", headers=HEADERS)
        assert d.json()["tags"] == ["sari", "juknis"] and d.json()["chunk_count"] >= 1

        lst = await client.get("/api/v1/admin/documents", headers=HEADERS)
        assert lst.json()["total"] == 1

        s = await client.post(
            "/api/v1/admin/search",
            headers=HEADERS,
            json={"query": "alur pelaporan kasus pendaftaran", "top_k": 3},
        )
        assert s.status_code == 200 and s.json()["hits"][0]["filename"] == "panduan.md"

        dup = await client.post(
            "/api/v1/admin/ingest", headers=HEADERS, files=files, data={"sync": "true"}
        )
        assert dup.json()["items"][0]["status"] == "duplicate"

        bad = await client.post(
            "/api/v1/admin/ingest",
            headers=HEADERS,
            files=[("files", ("x.exe", b"abc", "application/octet-stream"))],
        )
        assert bad.status_code == 400 and "tidak didukung" in bad.json()["message"]

        rx = await client.post(f"/api/v1/admin/documents/{doc_id}/reindex", headers=HEADERS)
        assert rx.status_code == 200 and rx.json()["chunk_count"] >= 1

        assert (
            await client.delete(f"/api/v1/admin/documents/{doc_id}", headers=HEADERS)
        ).status_code == 204
        assert (
            await client.delete(f"/api/v1/admin/documents/{doc_id}", headers=HEADERS)
        ).status_code == 404
        assert (await client.post("/api/v1/admin/search", json={"query": "x"})).status_code == 401
    await app.state.services.aclose()
