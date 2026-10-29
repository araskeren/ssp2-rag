"""Suite evaluasi: retrieval (recall@k, MRR), faithfulness (LLM-judge), Text-to-SQL, dan akses.

Semua runner menerima `Services` yang sudah dibangun (lihat cli_ext `eval`). Laporan JSON ditulis ke
eval/reports/<tanggal>-<suite>.json. Identitas dipalsukan langsung (tanpa BE) agar eval mandiri.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import Any

from langchain_core.messages import HumanMessage

from ai_service.agent.prompting import load_prompt
from ai_service.auth.identity import Identity
from ai_service.auth.scope import UserScope
from ai_service.logging import get_logger
from ai_service.services import Services

log = get_logger(__name__)
REPORT_DIR = Path("eval/reports")
NOT_FOUND_PHRASE = "tidak ditemukan"


@dataclass
class EvalReport:
    suite: str
    cases: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def write(self) -> Path:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = REPORT_DIR / f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{self.suite}.json"
        path.write_text(
            json.dumps(
                {"suite": self.suite, "summary": self.summary, "cases": self.cases},
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        return path


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _identity(role: str, scope: dict[str, Any] | None = None, principal: str = "eval") -> Identity:
    user_scope = UserScope.from_level(
        (scope or {}).get("level", "GLOBAL"),
        provinsi_kode=(scope or {}).get("provinsi_kode"),
        kabupaten_kota_kode=(scope or {}).get("kabupaten_kota_kode"),
        facility_id=(scope or {}).get("facility_id"),
    )
    return Identity(
        principal, "web", "analyst" if role == "analyst" else "public", user_scope, f"eval:{role}"
    )


def _session() -> str:
    return f"web_{uuid.uuid4()}"


# ---------------------------------------------------------------------- retrieval
async def run_rag_eval(services: Services, path: Path, k: int = 5) -> EvalReport:
    assert services.retriever is not None
    report = EvalReport("rag")
    hits = rr_total = n_expected = 0
    nf_total = 0
    for case in load_jsonl(path):
        chunks = await services.retriever.retrieve(case["question"], top_k=k)
        files = [c.filename for c in chunks]
        if case.get("must_not_find"):
            nf_total += 1
            report.cases.append(
                {
                    "id": case["id"],
                    "must_not_find": True,
                    "top_score": chunks[0].score if chunks else 0.0,
                    "files": files[:3],
                    "ok": True,
                    "catatan": "dinilai di suite judge (jawaban harus 'tidak ditemukan')",
                }
            )
            continue
        n_expected += 1
        rank = next(
            (
                i
                for i, c in enumerate(chunks, start=1)
                if any(
                    c.filename == e["filename"] and (e.get("page") in (None, c.page))
                    for e in case["expected"]
                )
            ),
            None,
        )
        hits += rank is not None
        rr_total += (1 / rank) if rank else 0
        report.cases.append(
            {"id": case["id"], "rank": rank, "files": files, "ok": rank is not None}
        )
    report.summary = {
        "k": k,
        "recall_at_k": round(hits / n_expected, 3) if n_expected else None,
        "mrr": round(rr_total / n_expected, 3) if n_expected else None,
        "must_not_find_cases": nf_total,
        "cases": len(report.cases),
    }
    return report


# ---------------------------------------------------------------------- faithfulness (LLM-judge)
def _parse_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


async def run_judge_eval(services: Services, path: Path) -> EvalReport:
    assert services.runner is not None and services.llm is not None
    template = Template(load_prompt("judge.md"))
    report = EvalReport("judge")
    faith_total = covered_total = kp_total = citation_ok = n = 0
    identity = _identity("public", principal="eval-judge")
    for case in load_jsonl(path):
        if case.get("must_not_find"):
            result = await services.runner.run(identity, _session(), case["question"])
            ok = NOT_FOUND_PHRASE in result.answer.lower()
            report.cases.append(
                {"id": case["id"], "must_not_find": True, "answer": result.answer[:300], "ok": ok}
            )
            continue
        result = await services.runner.run(identity, _session(), case["question"])
        refs = {s["ref"] for s in result.sources}
        cited = set(re.findall(r"\[\d+\]", result.answer))
        citations_valid = cited <= refs and bool(cited)
        sources_text = (
            "\n\n".join(
                f"{s['ref']} {s.get('title') or s['filename']}: {s['snippet']}"
                for s in result.sources
            )
            or "(tanpa sumber)"
        )
        prompt = template.safe_substitute(
            sources=sources_text,
            question=case["question"],
            answer=result.answer,
            keypoints="\n".join(f"- {kp}" for kp in case.get("keypoints", [])) or "(tidak ada)",
        )
        verdict_msg = await services.llm.ainvoke([HumanMessage(content=prompt)])
        verdict = _parse_json(
            verdict_msg.text
            if isinstance(getattr(verdict_msg, "text", None), str)
            else str(verdict_msg.content)
        )
        faithful = float(verdict.get("faithful", 0) or 0)
        covered = len(verdict.get("keypoints_covered", []) or [])
        n += 1
        faith_total += faithful
        covered_total += covered
        kp_total += len(case.get("keypoints", []))
        citation_ok += citations_valid
        report.cases.append(
            {
                "id": case["id"],
                "answer": result.answer[:500],
                "faithful": faithful,
                "keypoints_covered": covered,
                "keypoints_total": len(case.get("keypoints", [])),
                "unsupported_claims": verdict.get("unsupported_claims", []),
                "citations_valid": citations_valid,
                "latency_ms": result.latency_ms,
            }
        )
    report.summary = {
        "mean_faithfulness": round(faith_total / n, 2) if n else None,
        "keypoint_coverage": round(covered_total / kp_total, 3) if kp_total else None,
        "citation_validity": round(citation_ok / n, 3) if n else None,
        "cases": len(report.cases),
    }
    return report


# ---------------------------------------------------------------------- Text-to-SQL
async def _truth(admin_dsn: str, sql: str) -> list[list[Any]]:
    import asyncpg

    from ai_service.sql.backends.base import jsonable

    conn = await asyncpg.connect(admin_dsn)
    try:
        rows = await conn.fetch(sql)
        return [[jsonable(v) for v in r] for r in rows]
    finally:
        await conn.close()


def _rows_match(actual: list[list[Any]], truth: list[list[Any]]) -> bool:
    if len(truth) == 1 and len(truth[0]) == 1:  # skalar: cari nilai di baris/kolom mana pun
        want = truth[0][0]
        flat = [v for row in actual for v in row]
        return any(_num_eq(v, want) for v in flat)
    norm = lambda rows: sorted(sorted(str(v).lower() for v in row) for row in rows)  # noqa: E731
    return norm(actual) == norm(truth)


def _num_eq(a: Any, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return str(a).lower() == str(b).lower()


async def run_sql_eval(services: Services, path: Path, admin_dsn: str) -> EvalReport:
    assert services.runner is not None
    report = EvalReport("sql")
    valid = exact = rejected_ok = n_valid_cases = n_reject_cases = scope_leak = 0
    for case in load_jsonl(path):
        identity = _identity("analyst", case.get("scope"), principal=f"eval-sql-{case['id']}")
        result = await services.runner.run(identity, _session(), case["question"])
        sql = result.sql or {}
        if case.get("must_reject"):
            n_reject_cases += 1
            ok = result.intent == "denied" or sql.get("status") in ("rejected", "error") or not sql
            # jawaban tidak boleh memuat kolom sensitif
            leak = bool(sql.get("rows")) and any(
                ("nik" in c.lower() or "email" in c.lower()) for c in sql.get("columns", [])
            )
            ok = ok and not leak
            rejected_ok += ok
            report.cases.append(
                {
                    "id": case["id"],
                    "must_reject": True,
                    "status": sql.get("status"),
                    "query": sql.get("query"),
                    "ok": ok,
                }
            )
            continue
        n_valid_cases += 1
        ran = sql.get("status") in ("ok", "empty")
        valid += ran
        truth = await _truth(admin_dsn, case["expected"]["truth_sql"])
        match = ran and _rows_match(sql.get("rows", []), truth)
        exact += match
        if ran and not match and case.get("scope", {}).get("level", "GLOBAL") != "GLOBAL":
            scope_leak += 1
        report.cases.append(
            {
                "id": case["id"],
                "status": sql.get("status") or "tool_not_called",
                "attempts": sql.get("attempts"),
                "query": sql.get("query"),
                "rows": sql.get("rows", [])[:5],
                "truth": truth[:5],
                "exact": match,
                "scope": case.get("scope"),
                "latency_ms": result.latency_ms,
            }
        )
    report.summary = {
        "validity_rate": round(valid / n_valid_cases, 3) if n_valid_cases else None,
        "exact_result_rate": round(exact / n_valid_cases, 3) if n_valid_cases else None,
        "reject_rate": round(rejected_ok / n_reject_cases, 3) if n_reject_cases else None,
        "scope_mismatch_cases": scope_leak,
        "cases": len(report.cases),
    }
    return report


# ---------------------------------------------------------------------- akses
async def run_access_eval(services: Services, path: Path) -> EvalReport:
    assert services.runner is not None and services.audit is not None
    report = EvalReport("access")
    denied = n = 0
    principal = f"eval-access-{uuid.uuid4().hex[:8]}"
    identity = _identity("public", {"level": "FASKES", "facility_id": 3}, principal=principal)
    for case in load_jsonl(path):
        result = await services.runner.run(identity, _session(), case["question"])
        n += 1
        ok = result.intent == "denied" and result.sql is None
        denied += ok
        report.cases.append(
            {"id": case["id"], "intent": result.intent, "answer": result.answer[:300], "ok": ok}
        )
    _, ok_rows = await services.audit.list(status="ok", principal_id=principal)
    _, denied_rows = await services.audit.list(status="access_denied", principal_id=principal)
    report.summary = {
        "denied_rate": round(denied / n, 3) if n else None,
        "sql_executed_by_public": ok_rows,
        "access_denied_logged": denied_rows,
        "zero_leakage": ok_rows == 0,
        "cases": n,
    }
    return report
