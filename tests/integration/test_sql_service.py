"""Backend Postgres + guard + SqlService terhadap fixture BE (role ai_ro) — tanpa LLM nyata."""

from __future__ import annotations

import json
import os
from datetime import date

import asyncpg
import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from ai_service.agent.context import ChatContext, RequestTrace
from ai_service.auth.identity import Identity
from ai_service.auth.scope import UserScope
from ai_service.db.repositories import AuditRepo
from ai_service.sql.backends.base import SqlExecutionError
from ai_service.sql.backends.postgres import PostgresBackend
from ai_service.sql.catalog import load_catalog
from ai_service.sql.guard import validate_sql
from ai_service.sql.service import SqlService, parse_sql_draft

pytestmark = pytest.mark.pg

Q_PROV = "SELECT facility_provinsi_name AS provinsi, COUNT(DISTINCT record_id) AS jumlah_kasus FROM dn_sari_surveilans WHERE epi_year_record = 2025 GROUP BY facility_provinsi_name ORDER BY jumlah_kasus DESC"


def _ctx(identity: Identity, message_id="019a0000-0000-7000-8000-000000000001"):
    return ChatContext(
        identity=identity,
        session_id="web_tes",
        message_id=message_id,
        today=date(2026, 10, 28),
        trace=RequestTrace(),
    )


ANALYST_GLOBAL = Identity("42", "web", "analyst", UserScope("GLOBAL"), "role")
ANALYST_PROV = Identity("44", "web", "analyst", UserScope("PROVINCE", provinsi_kode="33"), "role")
ANALYST_FASKES = Identity(
    "43", "web", "analyst", UserScope("FASKES", facility_id=3), "allowed_channels"
)


async def _truth(admin_dsn: str, sql: str, *args):
    conn = await asyncpg.connect(admin_dsn)
    try:
        return await conn.fetchval(sql, *args)
    finally:
        await conn.close()


ANALYTICS_ADMIN_DSN = os.environ.get(
    "AI_TEST_ANALYTICS_ADMIN_DSN", "postgresql://postgres@127.0.0.1:5432/sdp_fixture"
)


async def test_backend_scope_filters_match_ground_truth(pg_settings, analytics_dsn):
    catalog = load_catalog()
    backend = PostgresBackend(analytics_dsn, pool_size=2, statement_timeout_ms=5000)
    await backend.connect()
    try:
        validated = validate_sql(Q_PROV, "postgres", catalog.names, max_rows=200)
        views = [catalog.views[n] for n in validated.views]

        total_truth = await _truth(
            ANALYTICS_ADMIN_DSN,
            'SELECT count(*) FROM sari.records WHERE EXTRACT(ISOYEAR FROM "createdAt") = 2025',
        )
        res = await backend.execute(
            backend.render(views, ANALYST_GLOBAL.scope, validated), 200, 5000
        )
        assert res.columns == ["provinsi", "jumlah_kasus"]
        assert sum(r[1] for r in res.rows) == total_truth and total_truth > 0

        prov_truth = await _truth(
            ANALYTICS_ADMIN_DSN,
            'SELECT count(*) FROM sari.records r JOIN master.facilities f ON f.id = r."facilityId" WHERE f."provinsiKode" = \'33\' AND EXTRACT(ISOYEAR FROM r."createdAt") = 2025',
        )
        rendered = backend.render(views, ANALYST_PROV.scope, validated)
        assert rendered.params == ["33"] and '"provinsiKode" = $1' in rendered.sql
        res_prov = await backend.execute(rendered, 200, 5000)
        assert sum(r[1] for r in res_prov.rows) == prov_truth and 0 < prov_truth < total_truth
        assert all(r[0] == "Jawa Tengah" for r in res_prov.rows)

        fas_truth = await _truth(
            ANALYTICS_ADMIN_DSN,
            'SELECT count(*) FROM sari.records WHERE "facilityId" = 3 AND EXTRACT(ISOYEAR FROM "createdAt") = 2025',
        )
        rendered_f = backend.render(views, ANALYST_FASKES.scope, validated)
        assert rendered_f.params == [3] and "$1::int" in rendered_f.sql
        res_f = await backend.execute(rendered_f, 200, 5000)
        assert sum(r[1] for r in res_f.rows) == fas_truth and 0 < fas_truth < prov_truth

        # referensi tak ber-scope: dim_regions tetap lengkap untuk user ber-scope
        v2 = validate_sql(
            "SELECT code, name FROM dim_regions WHERE level = 'PROVINCE' ORDER BY code",
            "postgres",
            catalog.names,
            200,
        )
        res_dim = await backend.execute(
            backend.render([catalog.views["dim_regions"]], ANALYST_FASKES.scope, v2), 200, 5000
        )
        assert [r[0] for r in res_dim.rows] == ["31", "33"]
        assert await backend.ping() is True
    finally:
        await backend.aclose()


async def test_ai_ro_cannot_read_pii_or_write(analytics_dsn):
    conn = await asyncpg.connect(analytics_dsn)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch("SELECT nik FROM master.patients LIMIT 1")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch("SELECT email FROM master.users LIMIT 1")
        assert await conn.fetchval("SELECT count(*) FROM master.patients") == 30
        with pytest.raises(
            (asyncpg.ReadOnlySQLTransactionError, asyncpg.InsufficientPrivilegeError)
        ):
            await conn.execute("UPDATE sari.records SET status = 'x'")
    finally:
        await conn.close()


class ScriptedWriter(FakeMessagesListChatModel):
    pass


async def test_sql_service_retry_audit_and_format(pg_settings, pg_db, analytics_dsn):
    catalog = load_catalog()
    backend = PostgresBackend(analytics_dsn, pool_size=2, statement_timeout_ms=5000)
    await backend.connect()
    audit = AuditRepo(pg_db)
    writer = ScriptedWriter(
        responses=[
            AIMessage(
                content=json.dumps({"sql": "SELECT * FROM sari.records", "penjelasan": "salah"})
            ),
            AIMessage(
                content="```json\n"
                + json.dumps({"sql": Q_PROV, "penjelasan": "per provinsi"})
                + "\n```"
            ),
            AIMessage(
                content=json.dumps(
                    {"sql": "SELECT kolom_tidak_ada FROM dn_sari_surveilans", "penjelasan": "x"}
                )
            ),
            AIMessage(
                content=json.dumps(
                    {
                        "sql": "SELECT record_id FROM dn_sari_surveilans WHERE epi_year_record = 1999",
                        "penjelasan": "kosong",
                    }
                )
            ),
        ]
    )
    service = SqlService(pg_settings, catalog, backend, audit, writer)
    try:
        ctx = _ctx(ANALYST_GLOBAL)
        outcome = await service.answer("Berapa kasus SARI per provinsi 2025?", ctx)
        assert outcome.status == "ok" and outcome.attempts == 2 and outcome.row_count >= 1
        assert (
            outcome.columns == ["provinsi", "jumlah_kasus"]
            and outcome.scope_label == "Nasional (GLOBAL)"
        )
        rows, total = await audit.list(principal_id="42")
        assert total == 2 and [r.status for r in rows] == ["ok", "rejected"]
        assert rows[0].executed_sql and rows[0].executed_sql.startswith(
            "WITH dn_sari_surveilans AS ("
        )
        text = service.format_for_model(outcome)
        assert "| provinsi | jumlah_kasus |" in text and "Total" in text

        ctx2 = _ctx(ANALYST_FASKES, "019a0000-0000-7000-8000-000000000002")
        out2 = await service.answer("Kolom tidak ada?", ctx2)
        assert out2.status == "empty" and out2.attempts == 2
        assert "tidak ada data" in service.format_for_model(out2)
        rows2, _ = await audit.list(principal_id="43")
        assert [r.status for r in rows2] == ["ok", "rejected"] and "kolom_tidak_ada" in (
            rows2[1].error or ""
        )
    finally:
        await backend.aclose()


def test_parse_sql_draft_variants():
    assert parse_sql_draft('{"sql": "SELECT 1", "penjelasan": "p"}') == ("SELECT 1", "p")
    assert parse_sql_draft('Berikut:\n```json\n{"sql": "SELECT 2"}\n```')[0] == "SELECT 2"
    assert parse_sql_draft("```sql\nSELECT 3\n```")[0] == "SELECT 3"
    assert parse_sql_draft("select 4 from x")[0] == "select 4 from x"
    with pytest.raises(Exception, match="tidak mengembalikan"):
        parse_sql_draft("maaf tidak bisa")


async def test_backend_unknown_scope_mapping_is_error(pg_settings, analytics_dsn):
    catalog = load_catalog()
    view = catalog.views["dn_sari_surveilans"]
    stripped = view.__class__(**{**view.__dict__, "scope": {"postgres": {}}})
    backend = PostgresBackend(analytics_dsn, pool_size=1)
    validated = validate_sql(
        "SELECT record_id FROM dn_sari_surveilans", "postgres", catalog.names, 10
    )
    with pytest.raises(SqlExecutionError, match="pemetaan scope"):
        backend.render([stripped], ANALYST_PROV.scope, validated)
