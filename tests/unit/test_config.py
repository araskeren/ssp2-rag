import pytest
from pydantic import ValidationError

from ai_service.config import Settings


def test_default_local_settings():
    s = Settings(_env_file=None)
    assert s.app_env == "local"
    assert s.docs_enabled is True
    assert s.api_key_list == ["dev-api-key"]
    assert s.effective_embedding_base_url == s.llm_base_url
    assert s.embedding_is_openai is True
    assert "Administrator" in s.analyst_role_set


def test_prod_requires_non_default_secrets():
    with pytest.raises(ValidationError, match="APP_ENV=prod"):
        Settings(_env_file=None, app_env="prod")


def test_prod_ok_with_secrets():
    s = Settings(
        _env_file=None,
        app_env="prod",
        jwt_secret="x" * 32,
        api_keys="k1,k2",
        admin_api_keys="a1",
        llm_base_url="http://vllm:8000/v1",
        be_base_url="http://ssp2_prod_nginx/api/v1",
    )
    assert s.docs_enabled is False
    assert s.api_key_list == ["k1", "k2"]


def test_log_effective_masks_secrets():
    s = Settings(_env_file=None, llm_api_key="rahasia")
    eff = s.log_effective()
    assert eff["llm_api_key"] == "***"
    assert eff["embedding_api_key"] == "<kosong>"
    assert eff["jwt_secret"] == "***"


def test_embedding_non_openai_detection():
    s = Settings(_env_file=None, embedding_base_url="http://localhost:11434/v1")
    assert s.embedding_is_openai is False


def test_invalid_log_level():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, log_level="verbose")
