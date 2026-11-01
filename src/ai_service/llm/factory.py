"""Pabrik klien LangChain dari ModelSpec: ChatOpenAI / ChatAnthropic / OpenAIEmbeddings."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from ai_service.config import Settings
from ai_service.llm.spec import ModelSpec


class LlmConfigError(Exception):
    """Konfigurasi provider/model tidak bisa dipakai untuk keperluan yang diminta."""


def _timeout(spec: ModelSpec, settings: Settings) -> float:
    return float(spec.provider.timeout_s or settings.llm_timeout_s)


def _temperature(spec: ModelSpec, settings: Settings, override: float | None) -> float:
    if override is not None:
        return override
    if spec.temperature is not None:
        return float(spec.temperature)
    return float(settings.llm_temperature)


def build_chat_model(
    spec: ModelSpec, settings: Settings, *, temperature: float | None = None
) -> BaseChatModel:
    if spec.kind != "chat":
        raise LlmConfigError(f"model '{spec.name}' bertipe {spec.kind}, bukan chat")
    provider = spec.provider
    params = dict(spec.params or {})
    headers = dict(provider.headers or {}) or None

    if provider.kind == "anthropic_compatible":
        from langchain_anthropic import ChatAnthropic

        kwargs: dict[str, Any] = {
            "model": spec.model,
            "api_key": provider.api_key or "sk-none",
            "temperature": _temperature(spec, settings, temperature),
            "max_tokens": int(spec.max_tokens or settings.anthropic_default_max_tokens),
            "timeout": _timeout(spec, settings),
            "max_retries": 2,
        }
        if provider.base_url:
            kwargs["base_url"] = provider.base_url
        if headers:
            kwargs["default_headers"] = headers
        return ChatAnthropic(**kwargs, **params)

    if provider.kind == "openai_compatible":
        from langchain_openai import ChatOpenAI

        kwargs = {
            "base_url": provider.base_url or None,
            "api_key": provider.api_key or "sk-none",
            "model": spec.model,
            "temperature": _temperature(spec, settings, temperature),
            "timeout": _timeout(spec, settings),
            "max_retries": 2,
        }
        if spec.max_tokens:
            kwargs["max_tokens"] = int(spec.max_tokens)
        if headers:
            kwargs["default_headers"] = headers
        return ChatOpenAI(**kwargs, **params)

    raise LlmConfigError(f"tipe provider tidak dikenal: {provider.kind}")


def build_embeddings(spec: ModelSpec, settings: Settings) -> Embeddings:
    if spec.kind != "embedding":
        raise LlmConfigError(f"model '{spec.name}' bertipe {spec.kind}, bukan embedding")
    provider = spec.provider
    if provider.kind != "openai_compatible":
        raise LlmConfigError(
            "embedding hanya didukung provider openai_compatible (Anthropic tidak punya API embedding)"
        )
    from langchain_openai import OpenAIEmbeddings

    params = dict(spec.params or {})
    send_dimensions = bool(params.pop("send_dimensions", True))
    kwargs: dict[str, Any] = {
        "model": spec.model,
        "base_url": provider.base_url or None,
        "api_key": provider.api_key or "sk-none",
        "chunk_size": 64,
        "timeout": _timeout(spec, settings),
        "max_retries": 2,
    }
    if provider.headers:
        kwargs["default_headers"] = dict(provider.headers)
    if send_dimensions and spec.embedding_dim:
        kwargs["dimensions"] = int(spec.embedding_dim)
    host = urlparse(provider.base_url or "https://api.openai.com").hostname or ""
    if not host.endswith("openai.com"):
        # Server non-OpenAI (Ollama/vLLM/TEI) mentokenisasi sendiri; kirim teks mentah.
        kwargs["check_embedding_ctx_length"] = False
    kwargs.update(params)
    return OpenAIEmbeddings(**kwargs)
