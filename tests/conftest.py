"""Fixture bersama: env tes deterministik (tanpa .env), app tanpa lifespan DB."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("AUTO_MIGRATE", "false")
os.environ.setdefault("LLM_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def _reset_settings():
    from ai_service.config import reset_settings_cache

    reset_settings_cache()
    yield
    reset_settings_cache()
