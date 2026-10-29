"""Penyimpanan chunk + hybrid search (dense pgvector ⊕ sparse FTS Indonesia, fusi RRF) dalam satu query."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, insert, text

from ai_service.db.engine import Database
from ai_service.db.models import DocumentChunk
from ai_service.rag.chunking import Chunk

HYBRID_SQL = text(
    """
    WITH dense AS (
        SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> CAST(:qvec AS vector)) AS rnk
        FROM document_chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE d.status = 'indexed'
        ORDER BY c.embedding <=> CAST(:qvec AS vector)
        LIMIT :fetch_k
    ),
    q AS (
        SELECT to_tsquery('pg_catalog.indonesian', :qor) || to_tsquery('pg_catalog.simple', :qor) AS query
    ),
    sparse AS (
        SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.content_tsv, q.query) DESC) AS rnk
        FROM document_chunks c
        JOIN documents d ON d.id = c.document_id
        CROSS JOIN q
        WHERE d.status = 'indexed' AND c.content_tsv @@ q.query
        ORDER BY ts_rank_cd(c.content_tsv, q.query) DESC
        LIMIT :fetch_k
    ),
    fused AS (
        SELECT COALESCE(dense.id, sparse.id) AS id,
               COALESCE(1.0 / (:rrf_k + dense.rnk), 0) + COALESCE(1.0 / (:rrf_k + sparse.rnk), 0) AS score,
               dense.rnk AS dense_rank,
               sparse.rnk AS sparse_rank
        FROM dense
        FULL OUTER JOIN sparse ON dense.id = sparse.id
    )
    SELECT c.id, c.document_id, d.filename, d.title, c.page, c.section, c.content,
           f.score, f.dense_rank, f.sparse_rank
    FROM fused f
    JOIN document_chunks c ON c.id = f.id
    JOIN documents d ON d.id = c.document_id
    ORDER BY f.score DESC, f.dense_rank NULLS LAST
    LIMIT :top_k
    """
)

# Kata fungsi Indonesia yang tidak membawa makna pencarian (PG tidak menyediakan stoplist Indonesia).
STOPWORDS = frozenset(
    """
    di ke dari yang dan atau untuk pada dengan ini itu adalah ada apa apakah bagaimana berapa
    kapan mengapa kenapa siapa mana dimana saya kami kita anda tolong mohon bisa dapat boleh
    harus akan sudah telah belum juga serta oleh agar supaya bila jika maka tentang tersebut
    para nya lah kah pun yg dgn utk tsb
    """.split()
)
MAX_TERMS = 32
TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def build_or_query(query_text: str) -> str:
    """Ubah teks bebas menjadi tsquery ber-OR ('kirim | spesimen | ...') yang aman untuk to_tsquery."""
    terms: list[str] = []
    seen: set[str] = set()
    for token in TOKEN_RE.findall(query_text.lower()):
        if len(token) < 2 or token in STOPWORDS or token in seen:
            continue
        seen.add(token)
        terms.append(token)
        if len(terms) >= MAX_TERMS:
            break
    return " | ".join(terms) if terms else "__kosong__"


@dataclass
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    title: str | None
    page: int | None
    section: str | None
    content: str
    score: float
    dense_rank: int | None
    sparse_rank: int | None


def chunk_uuid(document_id: uuid.UUID, chunk_index: int) -> uuid.UUID:
    return uuid.uuid5(document_id, str(chunk_index))


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in vector) + "]"


class ChunkStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def replace_document_chunks(
        self, document_id: uuid.UUID, chunks: list[Chunk], vectors: list[list[float]]
    ) -> int:
        if len(chunks) != len(vectors):
            raise ValueError("jumlah chunk dan vektor tidak sama")
        rows = [
            {
                "id": chunk_uuid(document_id, c.index),
                "document_id": document_id,
                "chunk_index": c.index,
                "page": c.page,
                "section": c.section,
                "content": c.content,
                "metadata_": {},
                "embedding": v,
            }
            for c, v in zip(chunks, vectors, strict=True)
        ]
        async with self._db.session() as session:
            await session.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
            )
            if rows:
                await session.execute(insert(DocumentChunk), rows)
            await session.commit()
        return len(rows)

    async def delete_document_chunks(self, document_id: uuid.UUID) -> None:
        async with self._db.session() as session:
            await session.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
            )
            await session.commit()

    async def count(self) -> int:
        async with self._db.engine.connect() as conn:
            return int(
                (await conn.execute(text("SELECT count(*) FROM document_chunks"))).scalar() or 0
            )

    async def hybrid_search(
        self,
        query_vector: list[float],
        query_text: str,
        *,
        top_k: int,
        fetch_k: int,
        rrf_k: int,
    ) -> list[RetrievedChunk]:
        params = {
            "qvec": vector_literal(query_vector),
            "qor": build_or_query(query_text),
            "fetch_k": fetch_k,
            "rrf_k": rrf_k,
            "top_k": top_k,
        }
        async with self._db.engine.connect() as conn:
            result = await conn.execute(HYBRID_SQL, params)
            return [
                RetrievedChunk(
                    chunk_id=r.id,
                    document_id=r.document_id,
                    filename=r.filename,
                    title=r.title,
                    page=r.page,
                    section=r.section,
                    content=r.content,
                    score=float(r.score),
                    dense_rank=r.dense_rank,
                    sparse_rank=r.sparse_rank,
                )
                for r in result
            ]
