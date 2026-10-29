"""Konstruksi LLM (OpenAI-compatible) dan agent LangChain v1."""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from ai_service.agent.context import ChatContext
from ai_service.config import Settings


def build_llm(
    settings: Settings, model: str | None = None, *, temperature: float | None = None
) -> ChatOpenAI:
    return ChatOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or "sk-none",
        model=model or settings.llm_model,
        temperature=settings.llm_temperature if temperature is None else temperature,
        timeout=settings.llm_timeout_s,
        max_retries=2,
    )


def build_agent(llm: BaseChatModel, tools: list[BaseTool], middleware: list[Any]) -> Any:
    return create_agent(
        model=llm,
        tools=tools,
        middleware=middleware,
        context_schema=ChatContext,
    )
