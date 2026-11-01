"""Skema request/response API (pydantic)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ScopeOut(BaseModel):
    level: Literal["GLOBAL", "PROVINCE", "KABKOTA", "FASKES"]
    provinsi_kode: str | None = None
    kabupaten_kota_kode: str | None = None
    facility_id: int | None = None


class MeOut(BaseModel):
    user_id: str
    email: str | None = None
    display_name: str | None = None
    role_name: str | None = None
    ai_role: Literal["public", "analyst"]
    analyst_reason: str
    scope: ScopeOut
    scope_label: str


class ChannelIn(BaseModel):
    platform: Literal["wa", "tg", "web"]
    channel_id: str = Field(min_length=1, max_length=100)
    role: Literal["public", "analyst"] = "public"
    scope_level: Literal["GLOBAL", "PROVINCE", "KABKOTA", "FASKES"] = "GLOBAL"
    provinsi_kode: str | None = None
    kabupaten_kota_kode: str | None = None
    facility_id: int | None = None
    display_name: str | None = None
    notes: str | None = None
    is_active: bool = True
    # Override model chat untuk kanal/user ini (id llm_models); null = ikut penugasan role.
    llm_model_id: int | None = None

    @field_validator("channel_id")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    def validate_scope(self) -> list[str]:
        masalah: list[str] = []
        if self.scope_level == "PROVINCE" and not self.provinsi_kode:
            masalah.append("provinsi_kode: wajib untuk scope PROVINCE")
        if self.scope_level == "KABKOTA" and not self.kabupaten_kota_kode:
            masalah.append("kabupaten_kota_kode: wajib untuk scope KABKOTA")
        if self.scope_level == "FASKES" and self.facility_id is None:
            masalah.append("facility_id: wajib untuk scope FASKES")
        return masalah


class ChannelPatch(BaseModel):
    role: Literal["public", "analyst"] | None = None
    scope_level: Literal["GLOBAL", "PROVINCE", "KABKOTA", "FASKES"] | None = None
    provinsi_kode: str | None = None
    kabupaten_kota_kode: str | None = None
    facility_id: int | None = None
    display_name: str | None = None
    notes: str | None = None
    is_active: bool | None = None
    llm_model_id: int | None = None


class ChannelOut(BaseModel):
    id: int
    platform: str
    channel_id: str
    role: str
    scope_level: str
    provinsi_kode: str | None
    kabupaten_kota_kode: str | None
    facility_id: int | None
    display_name: str | None
    notes: str | None
    is_active: bool
    llm_model_id: int | None = None
    created_at: Any
    updated_at: Any

    model_config = {"from_attributes": True}


class PageOut(BaseModel):
    items: list[Any]
    total: int
    page: int
    limit: int


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=6, ge=1, le=50)


class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=80)
    query: str = Field(min_length=1, max_length=20000)


class InternalChatRequest(BaseModel):
    platform: Literal["wa", "tg"]
    channel_id: str = Field(min_length=1, max_length=100)
    query: str = Field(min_length=1, max_length=20000)
    display_name: str | None = Field(default=None, max_length=200)


# ---------------------------------------------------------------------- registry LLM
SLUG_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"
ProviderKindLit = Literal["openai_compatible", "anthropic_compatible"]
ModelKindLit = Literal["chat", "embedding"]
PurposeLit = Literal["chat", "sql", "judge", "embedding"]
AiRoleLit = Literal["public", "analyst"]


class LlmProviderIn(BaseModel):
    name: str = Field(pattern=SLUG_PATTERN)
    kind: ProviderKindLit
    # Kosong = endpoint resmi (api.openai.com / api.anthropic.com).
    base_url: str = Field(default="", max_length=500)
    api_key: str | None = Field(default=None, max_length=4000)
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_s: float | None = Field(default=None, gt=0, le=600)
    is_active: bool = True
    notes: str | None = None

    @field_validator("base_url")
    @classmethod
    def _url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("base_url harus diawali http:// atau https://")
        return value


class LlmProviderPatch(BaseModel):
    name: str | None = Field(default=None, pattern=SLUG_PATTERN)
    base_url: str | None = Field(default=None, max_length=500)
    # null/tidak dikirim = tidak diubah; "" = hapus API key.
    api_key: str | None = Field(default=None, max_length=4000)
    headers: dict[str, str] | None = None
    timeout_s: float | None = Field(default=None, gt=0, le=600)
    is_active: bool | None = None
    notes: str | None = None

    @field_validator("base_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return None if value is None else LlmProviderIn._url(value)


class LlmProviderOut(BaseModel):
    id: int
    name: str
    kind: str
    base_url: str
    api_key_set: bool
    api_key_hint: str | None
    headers: dict[str, str]
    timeout_s: float | None
    is_active: bool
    notes: str | None
    created_at: Any
    updated_at: Any


class LlmModelIn(BaseModel):
    provider_id: int
    name: str = Field(pattern=SLUG_PATTERN)
    model: str = Field(min_length=1, max_length=200)
    kind: ModelKindLit = "chat"
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=200000)
    embedding_dim: int | None = Field(default=None, ge=1, le=16000)
    params: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    notes: str | None = None


class LlmModelPatch(BaseModel):
    name: str | None = Field(default=None, pattern=SLUG_PATTERN)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=200000)
    embedding_dim: int | None = Field(default=None, ge=1, le=16000)
    params: dict[str, Any] | None = None
    is_active: bool | None = None
    notes: str | None = None


class LlmModelOut(BaseModel):
    id: int
    provider_id: int
    name: str
    model: str
    kind: str
    temperature: float | None
    max_tokens: int | None
    embedding_dim: int | None
    params: dict[str, Any]
    is_active: bool
    notes: str | None
    created_at: Any
    updated_at: Any

    model_config = {"from_attributes": True}


class LlmAssignmentIn(BaseModel):
    purpose: PurposeLit
    ai_role: AiRoleLit | None = None
    model_id: int


class LlmAssignmentOut(BaseModel):
    purpose: str
    ai_role: str | None
    model_id: int
    updated_at: Any

    model_config = {"from_attributes": True}
