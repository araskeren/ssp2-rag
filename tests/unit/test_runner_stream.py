"""Stream chat: hanya jawaban model agen yang mengalir, bukan LLM di dalam tool."""

from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool

from ai_service.agent.runner import is_answer_event, is_provider_auth_error, message_text

SQL_INTERNAL = '{"sql": "SELECT 1"}'


class ScriptedStreamModel(BaseChatModel):
    """GenericFakeChatModel tidak bisa mengalirkan tool call, jadi skripnya ditulis sendiri."""

    script: Any

    @property
    def _llm_type(self) -> str:
        return "scripted-stream"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001
        return ChatResult(generations=[ChatGeneration(message=next(self.script))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001
        message = next(self.script)
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content=message.content,
                tool_call_chunks=[
                    {
                        "name": call["name"],
                        "args": '{"pertanyaan": "x"}',
                        "id": call["id"],
                        "index": 0,
                        "type": "tool_call_chunk",
                    }
                    for call in message.tool_calls
                ],
            )
        )


async def test_llm_inside_tool_does_not_leak_into_answer_tokens():
    nested_llm = GenericFakeChatModel(messages=iter([AIMessage(content=SQL_INTERNAL)]))

    @tool("analisis_data")
    async def analisis_data(pertanyaan: str) -> str:
        """Menyusun SQL lewat LLM lain, seperti SqlService."""
        return message_text(await nested_llm.ainvoke([HumanMessage(content=pertanyaan)]))

    agent = create_agent(
        ScriptedStreamModel(
            script=iter(
                [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"name": "analisis_data", "args": {"pertanyaan": "x"}, "id": "c1"}
                        ],
                    ),
                    AIMessage(content="Jawaban akhir"),
                ]
            )
        ),
        tools=[analisis_data],
    )

    all_tokens: list[str] = []
    answer_tokens: list[str] = []
    async for event in agent.astream_events(
        {"messages": [HumanMessage(content="x")]}, version="v2"
    ):
        if event["event"] == "on_chat_model_stream":
            text = message_text(event["data"]["chunk"])
            all_tokens.append(text)
            if is_answer_event(event):
                answer_tokens.append(text)

    # Tanpa saringan, SQL internal memang muncul; ini yang mencegah tes lolos percuma.
    assert "SELECT" in "".join(all_tokens)
    assert "".join(answer_tokens) == "Jawaban akhir"


class AuthenticationError(Exception):
    """Nama sama dengan galat SDK openai/anthropic; yang dicocokkan adalah namanya."""


def test_provider_auth_error_detected_directly_and_when_wrapped():
    assert is_provider_auth_error(AuthenticationError("401"))

    pembungkus = RuntimeError("gagal memanggil model")
    pembungkus.__cause__ = AuthenticationError("401")
    assert is_provider_auth_error(pembungkus)

    assert not is_provider_auth_error(RuntimeError("timeout"))
    assert not is_provider_auth_error(None)
