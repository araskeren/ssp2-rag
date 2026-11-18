"""Pastikan prompt sistem web dan WA/TG berbeda: aturan tabel/agregat hanya ada di web."""

from __future__ import annotations

from datetime import date

from ai_service.agent.context import ChatContext
from ai_service.agent.prompting import render_system_prompt
from ai_service.auth.identity import Identity


def _ctx(platform: str) -> ChatContext:
    identity = Identity(
        principal_id="42",
        platform=platform,  # type: ignore[arg-type]
        ai_role="analyst",  # type: ignore[arg-type]
    )
    return ChatContext(
        identity=identity,
        session_id="s",
        message_id="m",
        today=date(2026, 1, 1),
    )


def test_web_prompt_memiliki_aturan_tabel_dan_agregat() -> None:
    prompt = render_system_prompt(_ctx("web"))
    assert "mencantumkan" in prompt  # aturan tanpa angka per baris
    assert "antarmuka sudah menampilkan sumber" in prompt  # sumber via UI, bukan ditulis LLM


def test_wa_prompt_tidak_memiliki_aturan_tabel() -> None:
    prompt = render_system_prompt(_ctx("wa"))
    assert "mencantumkan" not in prompt
    assert "rincian nilai secara ringkas" in prompt  # WA bebas menulis rincian


def test_tg_prompt_tidak_memiliki_aturan_tabel() -> None:
    prompt = render_system_prompt(_ctx("tg"))
    assert "mencantumkan" not in prompt
    assert "rincian nilai secara ringkas" in prompt
