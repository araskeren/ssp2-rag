"""Suite eval berjalan dengan model palsu: memastikan runner & metrik tidak rusak (bukan nilai kualitas)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from ai_service.evaluation import run_access_eval, run_rag_eval, run_sql_eval
from ai_service.rag.embeddings import EmbeddingService
from ai_service.rag.ingest import IngestOptions, UploadedFile
from ai_service.services import build_services

pytestmark = pytest.mark.pg

ANALYTICS_ADMIN_DSN = os.environ.get(
    "AI_TEST_ANALYTICS_ADMIN_DSN", "postgresql://postgres@127.0.0.1:5432/sdp_fixture"
)
SAMPLE_DOC = Path("docs/samples/panduan-pelaporan-sari.md").read_bytes()
ANALYSIS_SQL = "SELECT COUNT(DISTINCT record_id) AS jumlah_kasus FROM dn_sari_surveilans WHERE epi_year_record = 2025"


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        return self


async def test_eval_runners_with_fake_models(pg_settings, pg_db, analytics_dsn, tmp_path):
    settings = pg_settings.model_copy(update={"analytics_pg_dsn": analytics_dsn})
    agent_script = []
    for _ in range(12):  # tiap pertanyaan: panggil analisis_data lalu jawab
        agent_script += [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "analisis_data", "args": {"pertanyaan": "jumlah kasus"}, "id": "c"}
                ],
            ),
            AIMessage(content="Jumlah kasus sesuai data."),
        ]
    sql_writer = FakeMessagesListChatModel(
        responses=[AIMessage(content=json.dumps({"sql": ANALYSIS_SQL}))] * 12
    )
    services = await build_services(
        settings,
        pg_db,
        embeddings=EmbeddingService(settings, DeterministicFakeEmbedding(size=64)),
        llm=ToolModel(responses=agent_script),
        sql_llm=sql_writer,
    )
    try:
        await services.ingest.submit(
            [
                UploadedFile(
                    "panduan-pelaporan-sari.md",
                    Path("docs/samples/panduan-pelaporan-sari.md").read_bytes(),
                )
            ],
            IngestOptions(),
            sync=True,
        )

        rag = await run_rag_eval(services, Path("eval/golden_rag.jsonl"), k=5)
        assert rag.summary["cases"] == 10 and rag.summary["recall_at_k"] is not None

        access_path = tmp_path / "acc.jsonl"
        access_path.write_text(
            '{"id":"a1","question":"Berapa kasus?","role":"public","expect":"denied"}\n'
        )
        access = await run_access_eval(services, access_path)
        assert (
            access.summary["denied_rate"] == 1.0
            and access.summary["zero_leakage"] is True
            and access.summary["access_denied_logged"] == 1
        )

        sql_path = tmp_path / "sql.jsonl"
        sql_path.write_text(
            '{"id":"s1","question":"Berapa kasus SARI 2025?","scope":{"level":"GLOBAL"},"expected":{"truth_sql":"SELECT count(*) FROM sari.records WHERE EXTRACT(ISOYEAR FROM \\"createdAt\\") = 2025"}}\n'
            '{"id":"s2","question":"Berapa kasus SARI 2025?","scope":{"level":"FASKES","facility_id":3},"expected":{"truth_sql":"SELECT count(*) FROM sari.records WHERE \\"facilityId\\" = 3 AND EXTRACT(ISOYEAR FROM \\"createdAt\\") = 2025"}}\n'
        )
        sql = await run_sql_eval(services, sql_path, ANALYTICS_ADMIN_DSN)
        assert (
            sql.summary["validity_rate"] == 1.0
            and sql.summary["exact_result_rate"] == 1.0
            and sql.summary["scope_mismatch_cases"] == 0
        )
        report_path = sql.write()
        assert report_path.exists()
        report_path.unlink()
    finally:
        await services.aclose()
