"""Spesifikasi provider/model yang sudah di-resolve (immutable, API key sudah didekripsi)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ProviderKind = Literal["openai_compatible", "anthropic_compatible"]
ModelKind = Literal["chat", "embedding"]
Purpose = Literal["chat", "sql", "judge", "embedding"]

PROVIDER_KINDS: tuple[str, ...] = ("openai_compatible", "anthropic_compatible")
MODEL_KINDS: tuple[str, ...] = ("chat", "embedding")
PURPOSES: tuple[str, ...] = ("chat", "sql", "judge", "embedding")
PURPOSE_MODEL_KIND: dict[str, str] = {
    "chat": "chat",
    "sql": "chat",
    "judge": "chat",
    "embedding": "embedding",
}


@dataclass(frozen=True, eq=False)
class ProviderSpec:
    id: int | None
    name: str
    kind: ProviderKind
    base_url: str
    api_key: str = field(default="", repr=False)
    headers: dict[str, str] = field(default_factory=dict)
    timeout_s: float | None = None
    version: str = ""


@dataclass(frozen=True, eq=False)
class ModelSpec:
    id: int | None
    name: str
    provider: ProviderSpec
    model: str
    kind: ModelKind = "chat"
    temperature: float | None = None
    max_tokens: int | None = None
    embedding_dim: int | None = None
    params: dict[str, Any] = field(default_factory=dict)
    version: str = ""
    source: Literal["db", "env"] = "db"

    @property
    def key(self) -> str:
        """Kunci cache: berubah bila model atau provider-nya diubah admin."""
        return f"{self.source}:{self.id}:{self.name}:{self.version}:{self.provider.version}"

    @property
    def label(self) -> str:
        """Nama yang dicatat di chat_messages.model."""
        if self.source == "env":
            return self.model
        return f"{self.provider.name}/{self.model}"
