import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from ai_service.config import Settings
from ai_service.llm.factory import LlmConfigError, build_chat_model, build_embeddings
from ai_service.llm.spec import ModelSpec, ProviderSpec

SETTINGS = Settings(_env_file=None, llm_timeout_s=42, anthropic_default_max_tokens=2048)


def _spec(kind: str, *, model_kind: str = "chat", **kw) -> ModelSpec:
    provider = ProviderSpec(
        id=1,
        name="p",
        kind=kind,  # type: ignore[arg-type]
        base_url=kw.pop("base_url", "https://gw.example/v1"),
        api_key="sk-provider",
        headers=kw.pop("headers", {}),
        timeout_s=kw.pop("timeout_s", None),
    )
    return ModelSpec(id=7, name="m", provider=provider, model="model-x", kind=model_kind, **kw)  # type: ignore[arg-type]


def test_openai_compatible_builds_chat_openai():
    llm = build_chat_model(
        _spec("openai_compatible", temperature=0.3, headers={"X-Org": "a"}, timeout_s=5), SETTINGS
    )
    assert isinstance(llm, ChatOpenAI)
    assert llm.openai_api_base == "https://gw.example/v1"
    assert llm.openai_api_key.get_secret_value() == "sk-provider"
    assert llm.model_name == "model-x" and llm.temperature == 0.3
    assert llm.request_timeout == 5 and llm.default_headers == {"X-Org": "a"}


def test_anthropic_compatible_builds_chat_anthropic_with_max_tokens():
    llm = build_chat_model(
        _spec("anthropic_compatible", base_url="https://gw.example/anthropic"), SETTINGS
    )
    assert isinstance(llm, ChatAnthropic)
    assert llm.anthropic_api_url == "https://gw.example/anthropic"
    assert llm.anthropic_api_key.get_secret_value() == "sk-provider"
    assert llm.model == "model-x" and llm.max_tokens == 2048
    assert llm.default_request_timeout == 42
    assert llm.temperature == SETTINGS.llm_temperature

    explicit = build_chat_model(
        _spec("anthropic_compatible", max_tokens=512, params={"top_k": 5}),
        SETTINGS,
        temperature=0.0,
    )
    assert explicit.max_tokens == 512 and explicit.top_k == 5 and explicit.temperature == 0.0


def test_anthropic_forced_tool_choice_by_name():
    """middleware intent_router memaksa tool lewat nama → harus jadi {"type":"tool"} di Anthropic."""

    @tool
    def analisis_data(pertanyaan: str) -> str:
        """Analisis data."""
        return pertanyaan

    llm = build_chat_model(_spec("anthropic_compatible"), SETTINGS)
    bound = llm.bind_tools([analisis_data], tool_choice="analisis_data")
    assert bound.kwargs["tool_choice"] == {"type": "tool", "name": "analisis_data"}


def test_embeddings_only_openai_compatible():
    emb = build_embeddings(
        _spec(
            "openai_compatible",
            model_kind="embedding",
            embedding_dim=1024,
            params={"send_dimensions": False},
        ),
        SETTINGS,
    )
    assert isinstance(emb, OpenAIEmbeddings)
    assert emb.dimensions is None and emb.check_embedding_ctx_length is False

    emb2 = build_embeddings(
        _spec(
            "openai_compatible",
            model_kind="embedding",
            embedding_dim=256,
            base_url="https://api.openai.com/v1",
        ),
        SETTINGS,
    )
    assert emb2.dimensions == 256 and emb2.check_embedding_ctx_length is True

    with pytest.raises(LlmConfigError):
        build_embeddings(
            _spec("anthropic_compatible", model_kind="embedding", embedding_dim=8), SETTINGS
        )
    with pytest.raises(LlmConfigError):
        build_chat_model(
            _spec("openai_compatible", model_kind="embedding", embedding_dim=8), SETTINGS
        )
    with pytest.raises(LlmConfigError):
        build_embeddings(_spec("openai_compatible"), SETTINGS)
