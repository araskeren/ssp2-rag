from ai_service.agent.plain_text import markdown_to_plain, to_messaging_text


def test_markdown_to_plain_wa():
    md = "## Alur Pelaporan\n\n**Langkah** pertama: `login`.\n\n- Poin satu\n- Poin [dua](https://x.id/a)\n\n| Peran | Akses |\n| --- | --- |\n| Admin | Semua |\n"
    plain = markdown_to_plain(md)
    assert plain.startswith("*Alur Pelaporan*")
    assert "*Langkah* pertama: login." in plain
    assert "• Poin satu" in plain and "dua (https://x.id/a)" in plain
    assert "Peran — Akses" in plain and "Admin — Semua" in plain and "---" not in plain


def test_to_messaging_text_sources_and_limit():
    text = to_messaging_text(
        "Jawaban [1].", [{"ref": "[1]", "filename": "juknis.pdf", "title": None, "page": 3}]
    )
    assert text.endswith("Sumber:\n[1] juknis.pdf hal. 3")
    long = to_messaging_text("x" * 5000, [], limit=100)
    assert len(long) <= 100 and long.endswith("…")


def test_intent_and_source_selection_with_prefetch():
    from ai_service.agent.context import RequestTrace, SqlOutcome, ToolCallRecord
    from ai_service.agent.runner import ChatRunner
    from ai_service.rag.retriever import Source

    src = [
        Source("[1]", "d", "a.md", None, None, None, "x", 0.1),
        Source("[2]", "d", "b.md", None, None, None, "y", 0.1),
    ]
    trace = RequestTrace(sources=list(src), prefetched=True)
    assert (
        ChatRunner._intent(trace, "Halo!") == "smalltalk"
        and ChatRunner._select_sources(trace, "Halo!") == []
    )
    assert ChatRunner._intent(trace, "Jawaban [2].") == "helpdesk"
    assert [s["ref"] for s in ChatRunner._select_sources(trace, "Jawaban [2].")] == ["[2]"]
    trace2 = RequestTrace(sources=list(src), tool_calls=[ToolCallRecord("cari_dokumen", "ok", "2")])
    assert (
        ChatRunner._intent(trace2, "tanpa sitasi") == "helpdesk"
        and len(ChatRunner._select_sources(trace2, "tanpa sitasi")) == 2
    )
    trace3 = RequestTrace(sql=SqlOutcome(backend="postgres", status="ok"))
    assert ChatRunner._intent(trace3, "x") == "data_analysis"
    assert ChatRunner._intent(RequestTrace(denied=True), "x") == "denied"


def test_intent_router_decision():
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from ai_service.agent.middleware import decide_tool_choice, is_data_intent

    assert is_data_intent("Berapa jumlah kasus SARI per provinsi tahun 2025?")
    assert is_data_intent("Tampilkan tren mingguan kasus ILI")
    assert not is_data_intent("Bagaimana alur pelaporan kasus SARI?")
    assert not is_data_intent("Halo, berapa lama kamu di sini?")
    assert decide_tool_choice([HumanMessage("Berapa total kasus?")], True) == "analisis_data"
    assert decide_tool_choice([HumanMessage("Berapa total kasus?")], False) is None
    assert (
        decide_tool_choice(
            [HumanMessage("Berapa?"), AIMessage(""), ToolMessage("x", tool_call_id="1")], True
        )
        is None
    )
    assert decide_tool_choice([HumanMessage("Apa itu SARI?")], True) is None


def test_intent_router_excludes_duration_questions_and_forces_tool_choice():
    import asyncio
    from datetime import date

    from langchain.agents import create_agent
    from langchain.tools import tool
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage, HumanMessage

    from ai_service.agent.context import ChatContext
    from ai_service.agent.middleware import build_middleware, is_data_intent
    from ai_service.auth.identity import Identity
    from ai_service.auth.scope import UserScope

    assert not is_data_intent("Berapa lama batas pengiriman laporan agregat?")
    assert not is_data_intent("Kapan laporan mingguan harus dikirim?")
    assert is_data_intent("Berapa jumlah laporan agregat yang terverifikasi tahun 2025?")

    captured: dict = {}

    class Recording(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):  # noqa: ANN001
            captured.update(kwargs)
            return self

    @tool("analisis_data")
    def analisis_data(pertanyaan: str) -> str:
        """stub"""
        return "ok"

    async def audit(ctx, question):  # noqa: ANN001
        return None

    agent = create_agent(
        model=Recording(responses=[AIMessage(content="jawab")]),
        tools=[analisis_data],
        middleware=build_middleware(audit, force_tool=True),
        context_schema=ChatContext,
    )
    ctx = ChatContext(
        Identity("42", "web", "analyst", UserScope("GLOBAL"), "r"), "web_x", "m", date(2026, 10, 28)
    )
    asyncio.run(
        agent.ainvoke({"messages": [HumanMessage("Berapa jumlah kasus SARI 2025?")]}, context=ctx)
    )
    assert captured.get("tool_choice") == "analisis_data"
    captured.clear()
    asyncio.run(agent.ainvoke({"messages": [HumanMessage("Apa definisi SARI?")]}, context=ctx))
    assert captured.get("tool_choice") is None
