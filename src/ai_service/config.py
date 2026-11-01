"""Konfigurasi aplikasi dari environment variable (pydantic-settings).

Semua nilai dibaca dari env/.env — dokumentasi tiap variabel ada di `.env.example`.
Validasi gagal-cepat saat boot meniru notification-service (`internal/config/config.go`).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SECRET_FIELDS: frozenset[str] = frozenset(
    {
        "jwt_secret",
        "api_keys",
        "admin_api_keys",
        "llm_api_key",
        "embedding_api_key",
        "ai_database_url",
        "analytics_pg_dsn",
        "analytics_ch_password",
        "ai_secrets_key",
    }
)

ANALYST_ROLES_DEFAULT = (
    "Administrator,Developer,Admin Pusdatin,Admin Kemenkes,Operator Kemenkes,"
    "Eksekutif Kemenkes,Eksekutif Provinsi,Eksekutif KabKota,Eksekutif Faskes"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- aplikasi ---
    app_env: Literal["local", "dev", "prod"] = "local"
    port: int = 3003
    log_level: str = "info"
    cors_origin: str = ""

    # --- database milik ai-service (pgvector) ---
    ai_database_url: str = "postgresql+asyncpg://postgres@127.0.0.1:5432/ai_service"
    ai_db_pool_size: int = 5
    auto_migrate: bool = True

    # --- auth ---
    jwt_secret: str = "dev-jwt-secret-samakan-dengan-backend"
    api_keys: str = "dev-api-key"
    admin_api_keys: str = "dev-admin-key"
    be_base_url: str = "http://localhost:3001"
    be_timeout_s: float = 3.0
    profile_cache_ttl_s: int = 300
    ai_analyst_roles: str = ANALYST_ROLES_DEFAULT
    ai_analyst_permission: str = "menu_epi_intel:ai"
    internal_unknown_channel_policy: Literal["public", "deny"] = "public"

    # --- LLM (OpenAI-compatible) ---
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_sql_model: str = ""
    llm_temperature: float = 0.0
    llm_timeout_s: float = 60.0
    llm_max_concurrency: int = 8

    # --- registry provider LLM (DB, dikelola via /api/v1/admin/llm) ---
    # Fernet key untuk mengenkripsi API key provider di DB (`ai-service llm gen-key`).
    ai_secrets_key: str = ""
    llm_registry_ttl_s: float = 30.0
    anthropic_default_max_tokens: int = 4096

    # --- embedding ---
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    embedding_send_dimensions: bool = True

    # --- RAG ---
    rag_chunk_size: int = 1000
    rag_chunk_overlap: int = 150
    rag_top_k: int = 6
    rag_fetch_k: int = 20
    rag_rrf_k: int = 60
    rag_prefetch: bool = True
    rerank_enabled: bool = False
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_top_n: int = 4
    upload_dir: str = "./data/uploads"
    ingest_max_file_mb: int = 25

    # --- chat / agent ---
    chat_history_messages: int = 16
    chat_history_max_tokens: int = 4000
    chat_timeout_s: float = 90.0
    chat_max_query_chars: int = 2000
    agent_max_tool_calls: int = 6
    sse_heartbeat_s: int = 15
    rate_limit_chat_per_min: int = 20
    rate_limit_ip_per_min: int = 60
    intent_force_tool: bool = True

    # --- Text-to-SQL ---
    sql_backend: Literal["postgres", "clickhouse"] = "postgres"
    analytics_pg_dsn: str = "postgresql://ai_ro:ai_ro@127.0.0.1:5432/sdp_fixture"
    analytics_pg_pool_size: int = 3
    analytics_ch_url: str = "http://localhost:3110"
    analytics_ch_user: str = "app_ro"
    analytics_ch_password: str = ""
    analytics_ch_db: str = "satu_data_penyakit"
    sql_statement_timeout_ms: int = 15000
    sql_max_rows: int = 200
    sql_llm_rows: int = 50
    sql_max_retries: int = 2

    # ------------------------------------------------------------------ validasi
    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        level = value.strip().lower()
        if level == "warn":
            level = "warning"
        if level not in {"debug", "info", "warning", "error"}:
            raise ValueError("LOG_LEVEL harus salah satu dari debug|info|warn|error")
        return level

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        if self.embedding_dim <= 0:
            raise ValueError("EMBEDDING_DIM harus > 0")
        if self.rag_chunk_overlap >= self.rag_chunk_size:
            raise ValueError("RAG_CHUNK_OVERLAP harus lebih kecil dari RAG_CHUNK_SIZE")
        if self.anthropic_default_max_tokens <= 0:
            raise ValueError("ANTHROPIC_DEFAULT_MAX_TOKENS harus > 0")
        if self.sql_llm_rows > self.sql_max_rows:
            raise ValueError("SQL_LLM_ROWS tidak boleh melebihi SQL_MAX_ROWS")
        if self.app_env == "prod":
            masalah: list[str] = []
            for name in ("jwt_secret", "api_keys", "admin_api_keys"):
                if getattr(self, name) in ("", type(self).model_fields[name].default):
                    masalah.append(name.upper())
            for name in ("be_base_url", "llm_base_url"):
                if not getattr(self, name):
                    masalah.append(name.upper())
            if masalah:
                raise ValueError(
                    "APP_ENV=prod wajib mengisi nilai non-default untuk: " + ", ".join(masalah)
                )
        return self

    # ------------------------------------------------------------------ turunan
    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    @property
    def docs_enabled(self) -> bool:
        return self.app_env in ("local", "dev")

    @staticmethod
    def _csv(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    @property
    def api_key_list(self) -> list[str]:
        return self._csv(self.api_keys)

    @property
    def admin_api_key_list(self) -> list[str]:
        return self._csv(self.admin_api_keys)

    @property
    def analyst_role_set(self) -> frozenset[str]:
        return frozenset(self._csv(self.ai_analyst_roles))

    @property
    def effective_embedding_base_url(self) -> str:
        return self.embedding_base_url or self.llm_base_url

    @property
    def effective_embedding_api_key(self) -> str:
        return self.embedding_api_key or self.llm_api_key

    @property
    def embedding_is_openai(self) -> bool:
        host = urlparse(self.effective_embedding_base_url).hostname or ""
        return host.endswith("openai.com")

    @property
    def effective_llm_sql_model(self) -> str:
        return self.llm_sql_model or self.llm_model

    def log_effective(self) -> dict[str, Any]:
        """Konfigurasi efektif untuk log boot; rahasia disamarkan."""
        out: dict[str, Any] = {}
        for name in type(self).model_fields:
            value = getattr(self, name)
            if name in SECRET_FIELDS:
                out[name] = "<kosong>" if not value else "***"
            else:
                out[name] = value
        return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
