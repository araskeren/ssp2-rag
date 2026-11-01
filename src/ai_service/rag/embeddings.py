"""Embedding via endpoint OpenAI-compatible + penjaga dimensi/model (embedding_config)."""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.embeddings import Embeddings
from sqlalchemy import text
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from ai_service.config import Settings
from ai_service.db.engine import Database
from ai_service.logging import get_logger

log = get_logger(__name__)


class EmbeddingError(Exception):
    pass


class EmbeddingConfigError(Exception):
    """Dimensi/model embedding tidak cocok dengan data tersimpan; perlu reindex."""


def _is_transient(exc: BaseException) -> bool:
    name = type(exc).__name__
    return name in {
        "RateLimitError",
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
        "ServiceUnavailableError",
    }


def build_embeddings(settings: Settings) -> Embeddings:
    """Embedding dari konfigurasi env (EMBEDDING_*); registry DB memakai `llm.factory.build_embeddings`."""
    from ai_service.llm.factory import build_embeddings as build_from_spec
    from ai_service.llm.registry import LlmRegistry

    return build_from_spec(LlmRegistry(settings).env_embedding_spec(), settings)


class EmbeddingService:
    def __init__(self, settings: Settings, embeddings: Embeddings | None = None) -> None:
        self.dim = settings.embedding_dim
        self.model = settings.embedding_model
        self.batch_size = 64
        self._embeddings = embeddings or build_embeddings(settings)
        self._semaphore = asyncio.Semaphore(max(1, settings.llm_max_concurrency))

    async def _call(self, fn: Any, *args: Any) -> Any:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception(_is_transient),
            reraise=True,
        ):
            with attempt:
                async with self._semaphore:
                    return await fn(*args)
        raise EmbeddingError("embedding gagal")  # pragma: no cover

    def _check(self, vectors: list[list[float]]) -> None:
        for vector in vectors:
            if len(vector) != self.dim:
                raise EmbeddingConfigError(
                    f"model embedding '{self.model}' mengembalikan {len(vector)} dimensi, "
                    f"sedangkan EMBEDDING_DIM={self.dim}; sesuaikan EMBEDDING_DIM lalu reindex"
                )

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            vectors = await self._call(self._embeddings.aembed_documents, batch)
            self._check(vectors)
            out.extend([list(map(float, v)) for v in vectors])
        return out

    async def embed_query(self, query: str) -> list[float]:
        vector = await self._call(self._embeddings.aembed_query, query)
        self._check([vector])
        return list(map(float, vector))


async def ensure_embedding_config(db: Database, settings: Settings) -> None:
    """Selaraskan embedding_config + tipe kolom dengan settings; tolak bila ada data lama."""
    async with db.engine.begin() as conn:
        row = (
            await conn.execute(text("SELECT model, dim FROM embedding_config WHERE id = 1"))
        ).first()
        col_type = (
            await conn.execute(
                text(
                    "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                    "WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'"
                )
            )
        ).scalar()
        chunk_count = (
            await conn.execute(text("SELECT count(*) FROM document_chunks"))
        ).scalar() or 0

        col_dim: int | None = None
        if col_type and col_type.startswith("vector(") and col_type.endswith(")"):
            col_dim = int(col_type[7:-1])

        stored_model = row.model if row else None
        stored_dim = row.dim if row else None
        if (
            stored_model == settings.embedding_model
            and stored_dim == settings.embedding_dim
            and col_dim == settings.embedding_dim
        ):
            return

        if chunk_count > 0:
            raise EmbeddingConfigError(
                f"konfigurasi embedding berubah ({stored_model}/{stored_dim}, kolom {col_type}) → "
                f"{settings.embedding_model}/{settings.embedding_dim} sementara ada {chunk_count} chunk "
                "tersimpan; jalankan `ai-service reindex --confirm`"
            )
        if col_dim != settings.embedding_dim:
            await conn.execute(
                text(
                    f"ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector({int(settings.embedding_dim)})"
                )
            )
        await conn.execute(
            text(
                "INSERT INTO embedding_config (id, model, dim, updated_at) VALUES (1, :model, :dim, now()) "
                "ON CONFLICT (id) DO UPDATE SET model = EXCLUDED.model, dim = EXCLUDED.dim, updated_at = now()"
            ),
            {"model": settings.embedding_model, "dim": settings.embedding_dim},
        )
        log.info(
            "konfigurasi embedding diselaraskan",
            model=settings.embedding_model,
            dim=settings.embedding_dim,
        )
