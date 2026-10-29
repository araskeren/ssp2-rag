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
