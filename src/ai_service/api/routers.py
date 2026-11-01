"""Daftar router fitur yang dipasang ke aplikasi."""

from __future__ import annotations

from fastapi import APIRouter

from ai_service.api import admin, admin_llm, chat, internal, me


def all_routers() -> list[APIRouter]:
    return [me.router, chat.router, internal.router, admin.router, admin_llm.router]
