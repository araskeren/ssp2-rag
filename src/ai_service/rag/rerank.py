"""Re-ranker cross-encoder opsional (extra `rerank`). Dimuat malas; gagal impor = fitur mati."""

from __future__ import annotations

import asyncio
from typing import Any

from ai_service.logging import get_logger

log = get_logger(__name__)


class CrossEncoderReranker:
    def __init__(self, model_name: str) -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:  # pragma: no cover - tergantung extra
            raise RuntimeError(
                "RERANK_ENABLED=true tetapi paket sentence-transformers belum terpasang "
                "(uv sync --extra rerank)"
            ) from exc
        self._model: Any = CrossEncoder(model_name)
        self.model_name = model_name

    def _score(self, query: str, texts: list[str]) -> list[float]:
        scores = self._model.predict([(query, t) for t in texts])
        return [float(s) for s in scores]

    async def rerank(self, query: str, items: list[Any], top_n: int, text_of: Any) -> list[Any]:
        if not items:
            return items
        scores = await asyncio.to_thread(self._score, query, [text_of(i) for i in items])
        ranked = sorted(zip(scores, items, strict=True), key=lambda pair: pair[0], reverse=True)
        return [item for _, item in ranked[:top_n]]
