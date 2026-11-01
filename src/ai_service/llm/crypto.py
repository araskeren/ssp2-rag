"""Enkripsi API key provider di DB (Fernet, kunci dari env AI_SECRETS_KEY)."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from ai_service.config import Settings
from ai_service.logging import get_logger

log = get_logger(__name__)

# Kunci dev deterministik: HANYA dipakai APP_ENV=local bila AI_SECRETS_KEY kosong.
_DEV_SEED = b"ai-service-dev-secrets-key-jangan-dipakai-di-prod"


class SecretError(Exception):
    pass


def generate_key() -> str:
    return Fernet.generate_key().decode()


def api_key_hint(api_key: str) -> str:
    """Petunjuk aman untuk UI: 4 karakter terakhir saja."""
    value = api_key.strip()
    if not value:
        return ""
    return "…" + value[-4:] if len(value) > 8 else "…"


class SecretBox:
    def __init__(self, key: str | None) -> None:
        self._fernet = Fernet(key.encode()) if key else None

    @classmethod
    def from_settings(cls, settings: Settings) -> SecretBox:
        if settings.ai_secrets_key:
            try:
                return cls(settings.ai_secrets_key)
            except ValueError as exc:
                raise SecretError(
                    "AI_SECRETS_KEY tidak valid (harus Fernet key: `ai-service llm gen-key`)"
                ) from exc
        if settings.app_env == "local":
            key = base64.urlsafe_b64encode(hashlib.sha256(_DEV_SEED).digest()).decode()
            return cls(key)
        return cls(None)

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    def encrypt(self, plaintext: str) -> str:
        if self._fernet is None:
            raise SecretError("AI_SECRETS_KEY belum diset; API key provider tidak bisa disimpan")
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str | None) -> str:
        if not token:
            return ""
        if self._fernet is None:
            raise SecretError("AI_SECRETS_KEY belum diset; API key provider tidak bisa dibuka")
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise SecretError(
                "API key provider tidak bisa didekripsi (AI_SECRETS_KEY berubah?)"
            ) from exc
