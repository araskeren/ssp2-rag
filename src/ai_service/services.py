"""Kontainer service aplikasi — dibangun sekali (lifespan/CLI) dan disimpan di app.state.services."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel

from ai_service.agent.build import build_agent
from ai_service.agent.middleware import build_middleware
from ai_service.agent.runner import ChatRunner
from ai_service.agent.tools.rag_tool import make_rag_tool
from ai_service.agent.tools.sql_tool import make_sql_tool
from ai_service.api.ratelimit import RateLimiter
from ai_service.auth.identity import IdentityResolver
from ai_service.auth.profile_client import ProfileClient
from ai_service.config import Settings
from ai_service.db.engine import Database
from ai_service.db.repositories import AuditRepo, ChannelRepo, ChatRepo, DocumentRepo, JobRepo
from ai_service.llm.crypto import SecretBox
from ai_service.llm.factory import build_embeddings
from ai_service.llm.registry import LlmRegistry
from ai_service.llm.repo import LlmRepo
from ai_service.logging import get_logger
from ai_service.rag.embeddings import EmbeddingService, ensure_embedding_config
from ai_service.rag.ingest import IngestService
from ai_service.rag.rerank import CrossEncoderReranker
from ai_service.rag.retriever import Retriever
from ai_service.rag.store import ChunkStore

log = get_logger(__name__)


@dataclass
class Services:
    settings: Settings
    db: Database | None = None
    profile_client: ProfileClient | None = None
    channels: ChannelRepo | None = None
    identity: IdentityResolver | None = None
    documents: DocumentRepo | None = None
    jobs: JobRepo | None = None
    chats: ChatRepo | None = None
    audit: AuditRepo | None = None
    embeddings: EmbeddingService | None = None
    store: ChunkStore | None = None
    retriever: Retriever | None = None
    ingest: IngestService | None = None
    llm: BaseChatModel | None = None
    llm_repo: LlmRepo | None = None
    llm_registry: LlmRegistry | None = None
    secret_box: SecretBox | None = None
    agent: Any | None = None
    runner: ChatRunner | None = None
    sql_service: Any | None = None
    sql_backend: Any | None = None
    rate_chat: RateLimiter | None = None
    rate_ip: RateLimiter | None = None

    async def aclose(self) -> None:
        if self.profile_client is not None:
            await self.profile_client.aclose()
        if self.sql_backend is not None and hasattr(self.sql_backend, "aclose"):
            await self.sql_backend.aclose()


async def build_services(
    settings: Settings,
    db: Database,
    *,
    embeddings: EmbeddingService | None = None,
    llm: BaseChatModel | None = None,
    sql_llm: BaseChatModel | None = None,
    check_embedding: bool = True,
    with_agent: bool = True,
    with_sql: bool = True,
) -> Services:
    secret_box = SecretBox.from_settings(settings)
    llm_repo = LlmRepo(db)
    registry = LlmRegistry(
        settings,
        llm_repo,
        secret_box,
        fixed={"chat": llm, "sql": sql_llm},  # type: ignore[dict-item]
    )

    # Embedding: penugasan registry (purpose=embedding) menimpa EMBEDDING_* — dibaca sekali saat boot.
    embedding_spec = await registry.resolve_embedding_spec()
    if embedding_spec.source == "db":
        settings = settings.model_copy(
            update={
                "embedding_model": embedding_spec.model,
                "embedding_dim": int(embedding_spec.embedding_dim or settings.embedding_dim),
            }
        )
    if check_embedding:
        await ensure_embedding_config(db, settings)
    await asyncio.to_thread(Path(settings.upload_dir).mkdir, parents=True, exist_ok=True)

    profile_client = ProfileClient(
        settings.be_base_url, settings.be_timeout_s, settings.profile_cache_ttl_s
    )
    channels = ChannelRepo(db)
    identity = IdentityResolver(settings, profile_client, channels.get)
    documents, jobs, chats, audit = DocumentRepo(db), JobRepo(db), ChatRepo(db), AuditRepo(db)
    embeddings = embeddings or EmbeddingService(
        settings, build_embeddings(embedding_spec, settings)
    )
    store = ChunkStore(db)
    reranker = CrossEncoderReranker(settings.rerank_model) if settings.rerank_enabled else None
    retriever = Retriever(settings, store, embeddings, reranker)
    ingest = IngestService(settings, documents, jobs, store, embeddings)
    swept = await ingest.sweep_stale_jobs()
    if swept:
        log.warning("job ingest terbengkalai ditandai gagal", count=swept)

    services = Services(
        settings=settings,
        db=db,
        profile_client=profile_client,
        channels=channels,
        identity=identity,
        documents=documents,
        jobs=jobs,
        chats=chats,
        audit=audit,
        embeddings=embeddings,
        store=store,
        retriever=retriever,
        ingest=ingest,
        llm_repo=llm_repo,
        llm_registry=registry,
        secret_box=secret_box,
        rate_chat=RateLimiter(settings.rate_limit_chat_per_min),
        rate_ip=RateLimiter(settings.rate_limit_ip_per_min),
    )

    # Backend analitik (M4) — opsional; bila gagal, chat tetap jalan tanpa SQL tool nyata.
    if with_sql:
        try:
            from ai_service.sql.service import build_sql_service

            services.sql_service, services.sql_backend = await build_sql_service(
                settings, audit, llm=sql_llm, registry=registry
            )
        except Exception as exc:  # noqa: BLE001 — chat tetap jalan; tool analisis_data melapor "belum dikonfigurasi"
            log.error("backend analitik gagal diinisialisasi", error=str(exc))
            services.sql_service = None

    if with_agent:
        services.llm = llm
        runner_holder: dict[str, ChatRunner] = {}

        async def audit_denial(ctx, question):  # noqa: ANN001
            await runner_holder["runner"].audit_denial(ctx, question)

        tools = [make_rag_tool(retriever), make_sql_tool(services.sql_service)]
        middleware = build_middleware(audit_denial, force_tool=settings.intent_force_tool)

        def agent_factory(model: BaseChatModel) -> Any:
            return build_agent(model, tools, middleware)

        # Model chat di-resolve per request (override kanal → role → default → env); agent di-cache per model.
        services.agent = agent_factory(llm) if llm is not None else None
        services.runner = ChatRunner(
            settings,
            services.agent,
            chats,
            audit,
            model_name=settings.llm_model,
            backend_name=settings.sql_backend,
            retriever=retriever,
            registry=registry,
            agent_factory=agent_factory,
        )
        runner_holder["runner"] = services.runner

    log.info(
        "services siap",
        sql_backend=settings.sql_backend,
        sql_service="aktif" if services.sql_service else "tidak ada",
        be_base_url=settings.be_base_url,
        llm_model=settings.llm_model,
        llm_registry_models=len((await registry.snapshot()).models),
        embedding_model=settings.embedding_model,
        embedding_dim=settings.embedding_dim,
        rerank=settings.rerank_enabled,
    )
    return services
