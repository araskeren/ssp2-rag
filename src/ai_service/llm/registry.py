"""LlmRegistry: snapshot registry dari DB (TTL) + resolusi model per keperluan/role/kanal.

Urutan resolusi:
- chat      : fixed → override kanal (allowed_channels.llm_model_id) → (chat, ai_role) → (chat, default) → env LLM_MODEL
- sql       : fixed → (sql) → env LLM_SQL_MODEL (bila diisi) → (chat, default) → env LLM_MODEL
- judge     : fixed → (judge) → fixed chat → (chat, default) → env LLM_MODEL
- embedding : (embedding) → env EMBEDDING_* (dipakai saat boot; ganti model = restart + reindex)

"fixed" = model yang diinjeksi langsung (test/eval) dan selalu menang.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel

from ai_service.config import Settings
from ai_service.llm.crypto import SecretBox, SecretError
from ai_service.llm.factory import build_chat_model
from ai_service.llm.repo import LlmRepo, RegistryRows
from ai_service.llm.spec import ModelSpec, ProviderSpec
from ai_service.logging import get_logger

log = get_logger(__name__)

ChatFactory = Callable[..., BaseChatModel]


def _version(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


@dataclass
class Snapshot:
    models: dict[int, ModelSpec] = field(default_factory=dict)
    assignments: dict[tuple[str, str | None], int] = field(default_factory=dict)
    fingerprint: str = ""

    def assigned(self, purpose: str, ai_role: str | None = None) -> ModelSpec | None:
        model_id = self.assignments.get((purpose, ai_role))
        return self.models.get(model_id) if model_id is not None else None


@dataclass(frozen=True)
class ResolvedModel:
    key: str
    label: str
    llm: BaseChatModel
    spec: ModelSpec | None = None
    via: str = ""

    def to_dict(self) -> dict[str, Any]:
        spec = self.spec
        return {
            "label": self.label,
            "via": self.via,
            "model_id": spec.id if spec else None,
            "name": spec.name if spec else None,
            "provider": spec.provider.name if spec else None,
            "provider_kind": spec.provider.kind if spec else None,
            "model": spec.model if spec else None,
        }


def provider_spec(row: Any, box: SecretBox) -> ProviderSpec:
    """ProviderSpec dari baris llm_providers (API key didekripsi). Bisa melempar SecretError."""
    return ProviderSpec(
        id=row.id,
        name=row.name,
        kind=row.kind,
        base_url=row.base_url or "",
        api_key=box.decrypt(row.api_key_enc),
        headers={str(k): str(v) for k, v in (row.headers or {}).items()},
        timeout_s=row.timeout_s,
        version=_version(row.updated_at),
    )


def model_spec(row: Any, provider: ProviderSpec) -> ModelSpec:
    return ModelSpec(
        id=row.id,
        name=row.name,
        provider=provider,
        model=row.model,
        kind=row.kind,
        temperature=row.temperature,
        max_tokens=row.max_tokens,
        embedding_dim=row.embedding_dim,
        params=dict(row.params or {}),
        version=_version(row.updated_at),
    )


def build_snapshot(rows: RegistryRows, box: SecretBox) -> Snapshot:
    providers: dict[int, ProviderSpec] = {}
    for row in rows.providers:
        if not row.is_active:
            continue
        try:
            providers[row.id] = provider_spec(row, box)
        except SecretError as exc:
            log.error("provider LLM dilewati", provider=row.name, error=str(exc))
    models: dict[int, ModelSpec] = {}
    for row in rows.models:
        provider = providers.get(row.provider_id)
        if row.is_active and provider is not None:
            models[row.id] = model_spec(row, provider)
    assignments = {(a.purpose, a.ai_role): a.model_id for a in rows.assignments}
    fingerprint = "|".join(
        sorted(m.key for m in models.values())
        + sorted(f"{p}:{r}:{m}" for (p, r), m in assignments.items())
    )
    return Snapshot(models, assignments, fingerprint)


class LlmRegistry:
    def __init__(
        self,
        settings: Settings,
        repo: LlmRepo | None = None,
        box: SecretBox | None = None,
        *,
        fixed: dict[str, BaseChatModel] | None = None,
        chat_factory: ChatFactory = build_chat_model,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._repo = repo
        self._box = box or SecretBox.from_settings(settings)
        self._fixed = {k: v for k, v in (fixed or {}).items() if v is not None}
        self.chat_factory = chat_factory
        self._clock = clock
        self._snapshot: Snapshot | None = None
        self._loaded_at = 0.0
        self._lock = asyncio.Lock()
        self._instances: dict[tuple[str, float | None], BaseChatModel] = {}

    # ------------------------------------------------------------------ snapshot
    def invalidate(self) -> None:
        self._loaded_at = 0.0

    def _fresh(self) -> Snapshot | None:
        ttl = max(0.0, float(self._settings.llm_registry_ttl_s))
        if self._snapshot is None or not self._loaded_at:
            return None
        return self._snapshot if self._clock() - self._loaded_at < ttl else None

    async def snapshot(self) -> Snapshot:
        if (snap := self._fresh()) is not None:
            return snap
        async with self._lock:
            if (snap := self._fresh()) is not None:
                return snap
            snap = await self._load()
            if self._snapshot is None or snap.fingerprint != self._snapshot.fingerprint:
                self._instances.clear()
            self._snapshot = snap
            self._loaded_at = self._clock() or 1e-9
            return snap

    async def _load(self) -> Snapshot:
        if self._repo is None:
            return Snapshot()
        try:
            return build_snapshot(await self._repo.snapshot(), self._box)
        except Exception as exc:  # noqa: BLE001 — DB bermasalah: pakai snapshot lama / fallback env
            log.error("registry LLM gagal dimuat", error=str(exc))
            return self._snapshot or Snapshot()

    # ------------------------------------------------------------------ spec env
    def env_chat_spec(self, model: str | None = None) -> ModelSpec:
        s = self._settings
        provider = ProviderSpec(
            id=None,
            name="env",
            kind="openai_compatible",
            base_url=s.llm_base_url,
            api_key=s.llm_api_key,
        )
        name = model or s.llm_model
        return ModelSpec(id=None, name=f"env:{name}", provider=provider, model=name, source="env")

    def env_embedding_spec(self) -> ModelSpec:
        s = self._settings
        provider = ProviderSpec(
            id=None,
            name="env",
            kind="openai_compatible",
            base_url=s.effective_embedding_base_url,
            api_key=s.effective_embedding_api_key,
        )
        return ModelSpec(
            id=None,
            name=f"env:{s.embedding_model}",
            provider=provider,
            model=s.embedding_model,
            kind="embedding",
            embedding_dim=s.embedding_dim,
            params={"send_dimensions": s.embedding_send_dimensions},
            source="env",
        )

    # ------------------------------------------------------------------ resolusi
    async def resolve_spec(
        self, purpose: str, ai_role: str | None = None, model_id: int | None = None
    ) -> tuple[ModelSpec, str]:
        """Spec model chat untuk purpose (chat|sql|judge) + jalur resolusinya (untuk debug)."""
        snap = await self.snapshot()
        s = self._settings

        def chat_kind(spec: ModelSpec | None) -> ModelSpec | None:
            return spec if spec is not None and spec.kind == "chat" else None

        if purpose == "chat":
            if model_id is not None:
                spec = chat_kind(snap.models.get(model_id))
                if spec is not None:
                    return spec, "channel"
                log.warning(
                    "override model kanal tidak aktif; pakai penugasan role", model_id=model_id
                )
            if ai_role and (spec := chat_kind(snap.assigned("chat", ai_role))):
                return spec, f"role:{ai_role}"
            if spec := chat_kind(snap.assigned("chat")):
                return spec, "default"
            return self.env_chat_spec(), "env"
        if purpose == "sql":
            if spec := chat_kind(snap.assigned("sql")):
                return spec, "sql"
            if s.llm_sql_model:
                return self.env_chat_spec(s.llm_sql_model), "env:sql"
            if spec := chat_kind(snap.assigned("chat")):
                return spec, "default"
            return self.env_chat_spec(), "env"
        if purpose == "judge":
            if spec := chat_kind(snap.assigned("judge")):
                return spec, "judge"
            if spec := chat_kind(snap.assigned("chat")):
                return spec, "default"
            return self.env_chat_spec(), "env"
        raise ValueError(f"purpose chat tidak dikenal: {purpose}")

    async def resolve(
        self, purpose: str = "chat", ai_role: str | None = None, model_id: int | None = None
    ) -> ResolvedModel:
        fixed = self._fixed.get(purpose)
        if fixed is None and purpose == "judge" and "chat" in self._fixed:
            snap = await self.snapshot()
            if snap.assigned("judge") is None:
                fixed = self._fixed["chat"]
        if fixed is not None:
            return ResolvedModel(
                key=f"fixed:{purpose}:{id(fixed)}",
                label=self._settings.llm_model,
                llm=fixed,
                via="fixed",
            )
        spec, via = await self.resolve_spec(purpose, ai_role, model_id)
        temperature = 0.0 if purpose == "sql" and spec.temperature is None else None
        cache_key = (spec.key, temperature)
        llm = self._instances.get(cache_key)
        if llm is None:
            llm = self.chat_factory(spec, self._settings, temperature=temperature)
            self._instances[cache_key] = llm
        return ResolvedModel(key=spec.key, label=spec.label, llm=llm, spec=spec, via=via)

    async def resolve_embedding_spec(self) -> ModelSpec:
        snap = await self.snapshot()
        spec = snap.assigned("embedding")
        if spec is not None and spec.kind == "embedding" and spec.embedding_dim:
            return spec
        return self.env_embedding_spec()

    async def configured(self) -> bool:
        snap = await self.snapshot()
        if snap.assigned("chat") is not None or "chat" in self._fixed:
            return True
        return bool(self._settings.llm_base_url and self._settings.llm_model)
