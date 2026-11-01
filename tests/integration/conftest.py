"""Fixture Postgres nyata (mark `pg`): butuh env AI_TEST_DATABASE_URL (DB kosong dengan pgvector)."""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import text

from ai_service.config import Settings
from ai_service.db.engine import Database
from ai_service.db.migrate import run_migrations

TEST_DB_URL = os.environ.get("AI_TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def pg_settings() -> Settings:
    if not TEST_DB_URL:
        pytest.skip("AI_TEST_DATABASE_URL belum diset")
    settings = Settings(
        _env_file=None,
        ai_database_url=TEST_DB_URL,
        embedding_dim=64,
        embedding_model="fake-64",
        upload_dir="./data/test-uploads",
    )
    asyncio.run(_reset_schema(settings))
    run_migrations(settings)
    return settings


async def _reset_schema(settings: Settings) -> None:
    db = Database(settings.ai_database_url, 2)
    try:
        async with db.engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await db.dispose()


@pytest.fixture
async def pg_db(pg_settings: Settings):
    db = Database(pg_settings.ai_database_url, 2)
    async with db.engine.begin() as conn:
        for table in (
            "llm_assignments",
            "ingest_jobs",
            "sql_audit_log",
            "chat_messages",
            "chat_sessions",
            "allowed_channels",
            "llm_models",
            "llm_providers",
            "document_chunks",
            "documents",
        ):
            await conn.execute(text(f"TRUNCATE {table} CASCADE"))
    yield db
    await db.dispose()


ANALYTICS_ADMIN_DSN = os.environ.get(
    "AI_TEST_ANALYTICS_ADMIN_DSN", "postgresql://postgres@127.0.0.1:5432/sdp_fixture"
)
ANALYTICS_RO_DSN = os.environ.get(
    "AI_TEST_ANALYTICS_DSN", "postgresql://ai_ro:ai_ro@127.0.0.1:5432/sdp_fixture"
)
FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")


def _fixture_sql() -> list[str]:
    grant_path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "grant-ai-ro.sql")
    with open(grant_path, encoding="utf-8") as fh:
        grant = (
            fh.read().replace(":'ai_ro_password'", "'ai_ro'").replace(':"DBNAME"', '"sdp_fixture"')
        )
    texts = []
    for name in ("be_schema_min.sql", "be_fixture_data.sql"):
        with open(os.path.join(FIXTURE_DIR, name), encoding="utf-8") as fh:
            texts.append(fh.read())
    texts.append(grant)
    return texts


async def _load_fixture(sql_texts: list[str]) -> bool:
    import asyncpg

    try:
        conn = await asyncpg.connect(ANALYTICS_ADMIN_DSN, timeout=5)
    except Exception:  # noqa: BLE001
        return False
    try:
        for text_sql in sql_texts:
            await conn.execute(text_sql)
    finally:
        await conn.close()
    return True


@pytest.fixture(scope="session")
def analytics_dsn() -> str:
    if not TEST_DB_URL:
        pytest.skip("AI_TEST_DATABASE_URL belum diset")
    if not asyncio.run(_load_fixture(_fixture_sql())):
        pytest.skip("DB fixture analitik (AI_TEST_ANALYTICS_ADMIN_DSN) tidak terjangkau")
    return ANALYTICS_RO_DSN
