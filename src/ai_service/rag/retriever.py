"""Retriever: hybrid search → dedup per (dokumen, halaman) → rerank opsional → konteks bersitasi."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ai_service.config import Settings
from ai_service.rag.embeddings import EmbeddingService
from ai_service.rag.rerank import CrossEncoderReranker
from ai_service.rag.store import ChunkStore, RetrievedChunk

SNIPPET_CHARS = 200


@dataclass
class Source:
    ref: str
    document_id: str
    filename: str
    title: str | None
    page: int | None
    section: str | None
    snippet: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Retriever:
    def __init__(
        self,
        settings: Settings,
        store: ChunkStore,
        embeddings: EmbeddingService,
        reranker: CrossEncoderReranker | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._embeddings = embeddings
        self._reranker = reranker

    async def retrieve(self, query: str, top_k: int | None = None) -> list[RetrievedChunk]:
        settings = self._settings
        k = top_k or settings.rag_top_k
        query_vector = await self._embeddings.embed_query(query)
        candidates = await self._store.hybrid_search(
            query_vector,
            query,
            top_k=settings.rag_fetch_k,
            fetch_k=settings.rag_fetch_k,
            rrf_k=settings.rag_rrf_k,
        )
        deduped: list[RetrievedChunk] = []
        seen: set[tuple[str, int | None]] = set()
        for chunk in candidates:
            key = (str(chunk.document_id), chunk.page)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(chunk)
        if self._reranker is not None and deduped:
            deduped = await self._reranker.rerank(
                query, deduped, settings.rerank_top_n, lambda c: c.content
            )
        return deduped[:k]

    @staticmethod
    def format_context(chunks: list[RetrievedChunk], start: int = 1) -> tuple[str, list[Source]]:
        blocks: list[str] = []
        sources: list[Source] = []
        for n, chunk in enumerate(chunks, start=start):
            ref = f"[{n}]"
            label = chunk.title or chunk.filename
            where = f" (hal. {chunk.page})" if chunk.page else ""
            blocks.append(f"{ref} {label}{where}\n{chunk.content}")
            snippet = " ".join(chunk.content.split())
            if len(snippet) > SNIPPET_CHARS:
                snippet = snippet[: SNIPPET_CHARS - 1].rstrip() + "…"
            sources.append(
                Source(
                    ref=ref,
                    document_id=str(chunk.document_id),
                    filename=chunk.filename,
                    title=chunk.title,
                    page=chunk.page,
                    section=chunk.section,
                    snippet=snippet,
                    score=round(chunk.score, 6),
                )
            )
        return "\n\n".join(blocks), sources
