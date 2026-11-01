import pytest

from ai_service.config import Settings
from ai_service.llm.crypto import SecretBox, SecretError, api_key_hint, generate_key


def test_roundtrip_and_hint():
    box = SecretBox(generate_key())
    token = box.encrypt("sk-ant-rahasia-1234")
    assert "rahasia" not in token
    assert box.decrypt(token) == "sk-ant-rahasia-1234"
    assert box.decrypt(None) == "" and box.decrypt("") == ""
    assert api_key_hint("sk-ant-rahasia-1234") == "…1234"
    assert api_key_hint("short") == "…" and api_key_hint("") == ""


def test_wrong_key_fails_loudly():
    token = SecretBox(generate_key()).encrypt("x")
    with pytest.raises(SecretError):
        SecretBox(generate_key()).decrypt(token)


def test_from_settings_local_dev_key_and_prod_requires_key():
    local = SecretBox.from_settings(Settings(_env_file=None))
    assert local.enabled and local.decrypt(local.encrypt("a")) == "a"
    # dev key deterministik → data lokal tetap terbaca setelah restart
    assert SecretBox.from_settings(Settings(_env_file=None)).decrypt(local.encrypt("b")) == "b"

    prod_box = SecretBox.from_settings(Settings(_env_file=None, app_env="dev"))
    assert not prod_box.enabled
    with pytest.raises(SecretError):
        prod_box.encrypt("x")
    with pytest.raises(SecretError):
        SecretBox.from_settings(Settings(_env_file=None, ai_secrets_key="bukan-fernet"))


def test_secret_key_masked_in_log():
    out = Settings(_env_file=None, ai_secrets_key=generate_key()).log_effective()
    assert out["ai_secrets_key"] == "***"
