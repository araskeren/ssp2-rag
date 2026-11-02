"""Sakelar fitur: snapshot ber-TTL, fail-closed, dan pemblokiran tool di middleware."""

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from ai_service.agent.context import ChatContext
from ai_service.agent.middleware import FEATURE_OFF_MESSAGE, build_middleware
from ai_service.agent.prompting import render_system_prompt
from ai_service.auth.identity import Identity
from ai_service.features import CLOSED_STATE, FeatureFlags, FeatureState


def _rows(**enabled):
    return [SimpleNamespace(key=k, enabled=v) for k, v in enabled.items()]


class FakeFlags(FeatureFlags):
    def __init__(self, ttl_s=30.0):
        self.now = 0.0
        super().__init__(None, ttl_s, clock=lambda: self.now)  # type: ignore[arg-type]
        self.rows = _rows(assistant=True, helpdesk=True, analysis=True, connector=True)
        self.calls = 0
        self.fail = False

    async def list_rows(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("db mati")
        return self.rows


def test_state_disabled_tools_and_availability():
    assert FeatureState().disabled_tools == frozenset()
    assert FeatureState(helpdesk=False, analysis=False).disabled_tools == {
        "cari_dokumen",
        "analisis_data",
    }
    assert FeatureState().available_for(is_analyst=False)
    assert not FeatureState(assistant=False).available_for(is_analyst=True)
    # Helpdesk mati: pengguna umum tidak punya fitur tersisa, analis masih punya analisis.
    only_analysis = FeatureState(helpdesk=False)
    assert not only_analysis.available_for(is_analyst=False)
    assert only_analysis.available_for(is_analyst=True)
    assert not FeatureState(helpdesk=False, analysis=False).available_for(is_analyst=True)


async def test_snapshot_uses_ttl_and_invalidate():
    flags = FakeFlags()
    assert (await flags.current()).analysis
    flags.rows = _rows(assistant=True, helpdesk=True, analysis=False, connector=True)
    assert (await flags.current()).analysis  # masih dalam TTL
    assert flags.calls == 1

    flags.now = 31.0
    assert not (await flags.current()).analysis

    flags.rows = _rows(assistant=False, helpdesk=True, analysis=False, connector=True)
    flags.invalidate()
    assert not (await flags.current()).assistant


async def test_db_failure_keeps_last_snapshot_else_closed():
    flags = FakeFlags()
    flags.fail = True
    assert await flags.current() == CLOSED_STATE

    flags.fail = False
    assert (await flags.current()).assistant
    flags.fail = True
    flags.now = 100.0
    assert (await flags.current()).assistant


async def test_missing_row_counts_as_off():
    flags = FakeFlags()
    flags.rows = _rows(assistant=True)
    state = await flags.current()
    assert state.assistant and not state.helpdesk and not state.analysis and not state.connector


class RecordingModel(BaseChatModel):
    """Selalu memanggil tool pada giliran pertama, walau tool itu tidak ditawarkan."""

    seen_tools: list = []
    tool_name: str = "analisis_data"

    @property
    def _llm_type(self) -> str:
        return "recording"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        type(self).seen_tools.append([t.name for t in tools])
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001
        if isinstance(messages[-1], ToolMessage):
            message = AIMessage(content="selesai")
        else:
            message = AIMessage(
                content="",
                tool_calls=[{"name": self.tool_name, "args": {"pertanyaan": "x"}, "id": "c1"}],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


async def _run(features: FeatureState, tool_name: str, analyst: bool = True) -> dict[str, Any]:
    RecordingModel.seen_tools = []
    executed: list[str] = []

    @tool("analisis_data")
    async def analisis_data(pertanyaan: str) -> str:
        """Analisis."""
        executed.append("analisis_data")
        return "hasil"

    @tool("cari_dokumen")
    async def cari_dokumen(pertanyaan: str) -> str:
        """Cari."""
        executed.append("cari_dokumen")
        return "hasil"

    async def audit_denial(ctx, question):  # noqa: ANN001
        pytest.fail("sakelar mati tidak boleh dicatat sebagai penolakan akses")

    agent = create_agent(
        RecordingModel(tool_name=tool_name),
        tools=[analisis_data, cari_dokumen],
        middleware=build_middleware(audit_denial, force_tool=False),
        context_schema=ChatContext,
    )
    ctx = ChatContext(
        identity=Identity("1", "web", "analyst" if analyst else "public"),
        session_id="web_1",
        message_id="m",
        today=date(2026, 11, 2),
        features=features,
    )
    state = await agent.ainvoke({"messages": [HumanMessage(content="halo")]}, context=ctx)
    tool_messages = [m.content for m in state["messages"] if isinstance(m, ToolMessage)]
    return {
        "executed": executed,
        "tool_messages": tool_messages,
        "offered": RecordingModel.seen_tools[-1],
        "trace": ctx.trace,
    }


async def test_disabled_tool_is_not_offered_and_blocked_when_called_anyway():
    hasil = await _run(FeatureState(analysis=False), "analisis_data")
    assert hasil["executed"] == []
    assert hasil["tool_messages"] == [FEATURE_OFF_MESSAGE]
    assert hasil["offered"] == ["cari_dokumen"]
    assert hasil["trace"].tool_calls[0].status == "disabled"
    assert hasil["trace"].denied is False


async def test_helpdesk_off_blocks_rag_tool_for_analyst():
    hasil = await _run(FeatureState(helpdesk=False), "cari_dokumen")
    assert hasil["executed"] == []
    assert hasil["tool_messages"] == [FEATURE_OFF_MESSAGE]
    assert hasil["offered"] == ["analisis_data"]


async def test_enabled_tool_still_runs():
    hasil = await _run(FeatureState(), "cari_dokumen")
    assert hasil["executed"] == ["cari_dokumen"]
    assert sorted(hasil["offered"]) == ["analisis_data", "cari_dokumen"]


def test_prompt_mentions_only_disabled_features():
    def prompt(features: FeatureState) -> str:
        return render_system_prompt(
            ChatContext(
                identity=Identity("1", "web", "analyst"),
                session_id="web_1",
                message_id="m",
                today=date(2026, 11, 2),
                features=features,
            )
        )

    assert "DINONAKTIFKAN" not in prompt(FeatureState())
    assert "analisis data SEDANG DINONAKTIFKAN" in prompt(FeatureState(analysis=False))
    assert "helpdesk dokumen SEDANG DINONAKTIFKAN" in prompt(FeatureState(helpdesk=False))
