import uuid

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding
from sqlalchemy import text

from ai_service.db.repositories import DocumentRepo, JobRepo
from ai_service.rag.embeddings import (
    EmbeddingConfigError,
    EmbeddingService,
    ensure_embedding_config,
)
from ai_service.rag.ingest import IngestOptions, IngestService, UploadedFile
from ai_service.rag.retriever import Retriever
from ai_service.rag.store import ChunkStore

pytestmark = pytest.mark.pg


def _embeddings(settings):
    return EmbeddingService(settings, DeterministicFakeEmbedding(size=settings.embedding_dim))


async def _services(pg_settings, pg_db):
    await ensure_embedding_config(pg_db, pg_settings)
    emb = _embeddings(pg_settings)
    store = ChunkStore(pg_db)
    documents, jobs = DocumentRepo(pg_db), JobRepo(pg_db)
    ingest = IngestService(pg_settings, documents, jobs, store, emb)
    return ingest, Retriever(pg_settings, store, emb), documents, store


async def test_ingest_dedup_search_delete(pg_settings, pg_db):
    ingest, retriever, documents, store = await _services(pg_settings, pg_db)
    doc_a = b"# Panduan SARI\n\n## Spesimen\n\nSpesimen swab nasofaring dikirim dengan suhu 2-8 derajat ke laboratorium rujukan.\n\n## Verifikasi\n\nAdmin provinsi memverifikasi kelengkapan kasus sebelum penutupan."
    doc_b = b"# Panduan Kesling\n\nInspeksi sanitasi tempat pengelolaan pangan dilakukan oleh sanitarian puskesmas setiap semester."
    job = await ingest.submit(
        [UploadedFile("sari.md", doc_a), UploadedFile("kesling.md", doc_b)],
        IngestOptions(source="tes"),
        sync=True,
    )
    assert job.status == "done" and [i["status"] for i in job.items] == ["indexed", "indexed"]
    assert await store.count() >= 2

    async with pg_db.engine.connect() as conn:
        tsv_ok = (
            await conn.execute(
                text("SELECT bool_and(content_tsv IS NOT NULL) FROM document_chunks")
            )
        ).scalar()
    assert tsv_ok is True

    # duplikat sha256 → status duplicate, tidak menambah chunk
    before = await store.count()
    dup = await ingest.submit([UploadedFile("sari-copy.md", doc_a)], IngestOptions(), sync=True)
    assert dup.items[0]["status"] == "duplicate" and await store.count() == before

    # hybrid: kata kunci 'spesimen' + 'laboratorium' → chunk SARI di atas
    hits = await retriever.retrieve("pengiriman spesimen ke laboratorium rujukan", top_k=3)
    assert hits and hits[0].filename == "sari.md"
    assert hits[0].sparse_rank == 1
    context, sources = Retriever.format_context(hits)
    assert context.startswith("[1] ") and sources[0].ref == "[1]"

    # replace → re-index idempoten (jumlah chunk dokumen sama)
    rep = await ingest.submit(
        [UploadedFile("sari.md", doc_a)], IngestOptions(replace=True), sync=True
    )
    assert rep.items[0]["status"] == "indexed" and await store.count() == before

    # hapus → tidak muncul lagi di pencarian
    doc_id = uuid.UUID(rep.items[0]["document_id"])
    assert await ingest.delete_document(doc_id) is True
    hits_after = await retriever.retrieve("pengiriman spesimen ke laboratorium rujukan", top_k=3)
    assert all(h.filename != "sari.md" for h in hits_after)
    assert (await documents.get(doc_id)).status == "deleted"


async def test_embedding_config_guard(pg_settings, pg_db):
    ingest, _, _, store = await _services(pg_settings, pg_db)
    await ingest.submit(
        [UploadedFile("a.txt", b"teks percobaan satu dua tiga empat lima enam")],
        IngestOptions(),
        sync=True,
    )
    assert await store.count() > 0
    changed = pg_settings.model_copy(update={"embedding_dim": 32, "embedding_model": "fake-32"})
    with pytest.raises(EmbeddingConfigError, match="reindex"):
        await ensure_embedding_config(pg_db, changed)
    # tanpa data: penyelarasan otomatis
    async with pg_db.engine.begin() as conn:
        await conn.execute(text("DELETE FROM document_chunks"))
    await ensure_embedding_config(pg_db, changed)
    async with pg_db.engine.connect() as conn:
        col = (
            await conn.execute(
                text(
                    "SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid='document_chunks'::regclass AND attname='embedding'"
                )
            )
        ).scalar()
        cfg = (await conn.execute(text("SELECT model, dim FROM embedding_config"))).first()
    assert col == "vector(32)" and tuple(cfg) == ("fake-32", 32)
    # kembalikan ke 64 untuk tes lain
    await ensure_embedding_config(pg_db, pg_settings)
