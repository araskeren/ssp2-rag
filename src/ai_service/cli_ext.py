"""Perintah CLI fitur: ingest, reindex, catalog, eval, channel, llm (registry provider)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from ai_service.config import get_settings
from ai_service.db.engine import Database
from ai_service.logging import configure_logging


async def _with_services(fn, *, check_embedding: bool = True):  # noqa: ANN001
    from ai_service.services import build_services

    settings = get_settings()
    configure_logging(settings.log_level)
    db = Database(settings.ai_database_url, settings.ai_db_pool_size)
    try:
        services = await build_services(settings, db, check_embedding=check_embedding)
        try:
            return await fn(services)
        finally:
            await services.aclose()
    finally:
        await db.dispose()


def register(app: typer.Typer) -> None:
    @app.command()
    def ingest(
        paths: list[Path] = typer.Argument(
            ..., exists=True, readable=True, help="Berkas pdf/txt/md/docx."
        ),
        title: str | None = typer.Option(None, help="Judul (hanya bila satu berkas)."),
        source: str | None = typer.Option(None, help="Sumber/instansi dokumen."),
        tags: str = typer.Option("", help="Tag dipisah koma."),
        replace: bool = typer.Option(False, help="Indeks ulang bila sha256 sudah ada."),
    ) -> None:
        """Unggah dan indeks dokumen dari path lokal (sinkron)."""
        from ai_service.rag.ingest import IngestOptions, UploadedFile, job_to_dict

        async def run(services):  # noqa: ANN001
            files = [UploadedFile(p.name, p.read_bytes()) for p in paths]
            options = IngestOptions(
                title=title,
                source=source,
                tags=[t.strip() for t in tags.split(",") if t.strip()],
                replace=replace,
                requested_by="cli",
            )
            job = await services.ingest.submit(files, options, sync=True)
            return job_to_dict(job)

        result = asyncio.run(_with_services(run))
        for item in result["items"]:
            typer.echo(
                f"{item['status']:9} {item['filename']} ({item['document_id']}) {item.get('error', '')}"
            )
        typer.echo(f"job {result['job_id']} → {result['status']}")

    @app.command()
    def reindex(
        document_id: str | None = typer.Option(None, help="Hanya satu dokumen (uuid)."),
        confirm: bool = typer.Option(False, "--confirm", help="Wajib untuk reindex semua dokumen."),
    ) -> None:
        """Potong + embed ulang dari teks tersimpan (perlu saat model/dimensi embedding berubah)."""
        import uuid

        from sqlalchemy import text

        if document_id is None and not confirm:
            raise typer.BadParameter("reindex semua dokumen butuh --confirm")

        async def run(services):  # noqa: ANN001
            if document_id:
                count = await services.ingest.reindex_document(uuid.UUID(document_id))
                return {"ok": 1, "failed": 0, "chunks": count}
            # Kosongkan chunk agar penyelarasan embedding_config/tipe kolom diizinkan.
            async with services.db.engine.begin() as conn:
                await conn.execute(text("DELETE FROM document_chunks"))
            from ai_service.rag.embeddings import ensure_embedding_config

            await ensure_embedding_config(services.db, services.settings)
            return await services.ingest.reindex_all()

        result = asyncio.run(_with_services(run, check_embedding=document_id is not None))
        typer.echo(f"reindex selesai: {result}")

    catalog_app = typer.Typer(help="Katalog semantik Text-to-SQL.", no_args_is_help=True)
    app.add_typer(catalog_app, name="catalog")

    @catalog_app.command("check")
    def catalog_check(
        dialect: str = typer.Option("", help="postgres | clickhouse (default: SQL_BACKEND)"),
    ) -> None:
        """Validasi seluruh view katalog (parse SQL, kolom, scope, contoh)."""
        from ai_service.sql.catalog import load_catalog

        settings = get_settings()
        target = dialect or settings.sql_backend
        catalog = load_catalog()
        issues = catalog.validate(target)
        for issue in issues:
            typer.echo(f"MASALAH: {issue}")
        typer.echo(
            f"{len(catalog.views)} view diperiksa untuk dialek {target}: {'OK' if not issues else str(len(issues)) + ' masalah'}"
        )
        if issues:
            raise typer.Exit(code=1)

    @catalog_app.command("drift")
    def catalog_drift(
        dsn: str = typer.Option("", help="DSN analitik (default: ANALYTICS_PG_DSN)"),
    ) -> None:
        """Eksekusi tiap view (scope TRUE, LIMIT 0) pada DB analitik untuk mendeteksi pergeseran skema BE."""
        from ai_service.sql.drift import check_drift

        settings = get_settings()
        if settings.sql_backend != "postgres":
            raise typer.BadParameter("catalog drift saat ini hanya mendukung SQL_BACKEND=postgres")
        results = asyncio.run(check_drift(dsn or settings.analytics_pg_dsn))
        gagal = 0
        for name, error in results:
            typer.echo(
                f"{'OK     ' if error is None else 'DRIFT  '} {name}"
                + (f" → {error}" if error else "")
            )
            gagal += error is not None
        typer.echo(f"{len(results)} view diperiksa, {gagal} bermasalah")
        if gagal:
            raise typer.Exit(code=1)

    @catalog_app.command("show")
    def catalog_show(dialect: str = typer.Option("", help="postgres | clickhouse")) -> None:
        """Cetak katalog seperti yang dilihat LLM penulis SQL."""
        from ai_service.sql.catalog import load_catalog

        settings = get_settings()
        typer.echo(load_catalog().render_prompt(dialect or settings.sql_backend))

    eval_app = typer.Typer(
        help="Evaluasi RAG / Text-to-SQL / akses (laporan di eval/reports/).", no_args_is_help=True
    )
    app.add_typer(eval_app, name="eval")

    def _run_eval(fn, with_llm: bool = True):  # noqa: ANN001
        async def run(services):  # noqa: ANN001
            report = await fn(services)
            path = report.write()
            return report, path

        report, path = asyncio.run(_with_services(run))
        typer.echo(json.dumps(report.summary, ensure_ascii=False, indent=2))
        for case in report.cases:
            mark = "✔" if case.get("ok", case.get("exact", True)) else "✘"
            typer.echo(f"  {mark} {case.get('id')}")
        typer.echo(f"laporan: {path}")

    @eval_app.command("rag")
    def eval_rag(
        path: Path = typer.Option(Path("eval/golden_rag.jsonl")), k: int = typer.Option(5)
    ) -> None:
        """Recall@k / MRR retrieval terhadap golden set (tanpa LLM)."""
        from ai_service.evaluation import run_rag_eval

        _run_eval(lambda s: run_rag_eval(s, path, k))

    @eval_app.command("judge")
    def eval_judge(path: Path = typer.Option(Path("eval/golden_rag.jsonl"))) -> None:
        """Faithfulness & cakupan poin kunci via LLM-judge (memanggil LLM)."""
        from ai_service.evaluation import run_judge_eval

        _run_eval(lambda s: run_judge_eval(s, path))

    @eval_app.command("sql")
    def eval_sql(
        path: Path = typer.Option(Path("eval/golden_sql.jsonl")),
        admin_dsn: str = typer.Option(
            "postgresql://postgres@127.0.0.1:5432/sdp_fixture",
            help="DSN superuser fixture untuk ground truth",
        ),
    ) -> None:
        """Validity, exact-result, reject rate, dan kebocoran scope Text-to-SQL (memanggil LLM)."""
        from ai_service.evaluation import run_sql_eval

        _run_eval(lambda s: run_sql_eval(s, path, admin_dsn))

    @eval_app.command("access")
    def eval_access(path: Path = typer.Option(Path("eval/golden_access.jsonl"))) -> None:
        """Zero-leakage: pengguna public harus selalu ditolak & diaudit (memanggil LLM)."""
        from ai_service.evaluation import run_access_eval

        _run_eval(lambda s: run_access_eval(s, path))

    llm_app = typer.Typer(
        help="Registry provider LLM (OpenAI-/Anthropic-compatible).", no_args_is_help=True
    )
    app.add_typer(llm_app, name="llm")

    @llm_app.command("gen-key")
    def llm_gen_key() -> None:
        """Cetak Fernet key baru untuk AI_SECRETS_KEY (enkripsi API key provider di DB)."""
        from ai_service.llm.crypto import generate_key

        typer.echo(generate_key())

    @llm_app.command("list")
    def llm_list() -> None:
        """Tampilkan provider, model, penugasan, dan model efektif per role."""

        async def run(services):  # noqa: ANN001
            repo, registry = services.llm_repo, services.llm_registry
            providers = await repo.list_providers()
            models = await repo.list_models()
            assignments = await repo.list_assignments()
            resolved = {
                role: (await registry.resolve("chat", role)).to_dict()
                for role in ("public", "analyst")
            }
            resolved["sql"] = (await registry.resolve("sql")).to_dict()
            resolved["judge"] = (await registry.resolve("judge")).to_dict()
            return providers, models, assignments, resolved

        providers, models, assignments, resolved = asyncio.run(
            _with_services(run, check_embedding=False)
        )
        names = {p.id: p.name for p in providers}
        typer.echo("PROVIDER")
        for p in providers:
            key = f"key {p.api_key_hint}" if p.api_key_enc else "tanpa key"
            typer.echo(
                f"  [{p.id}] {p.name:16} {p.kind:22} {p.base_url or '(resmi)'} {key}"
                f"{'' if p.is_active else ' NONAKTIF'}"
            )
        typer.echo("MODEL")
        for m in models:
            typer.echo(
                f"  [{m.id}] {m.name:16} {m.kind:9} {names.get(m.provider_id, '?')}/{m.model}"
                f"{'' if m.is_active else ' NONAKTIF'}"
            )
        typer.echo("PENUGASAN")
        for a in assignments:
            typer.echo(f"  {a.purpose:9} {a.ai_role or '(default)':10} → model [{a.model_id}]")
        typer.echo("EFEKTIF")
        for target, info in resolved.items():
            typer.echo(f"  {target:9} {info['label']} (via {info['via']})")

    @llm_app.command("import-env")
    def llm_import_env(
        provider_name: str = typer.Option("env", help="Nama provider yang dibuat."),
        assign: bool = typer.Option(True, help="Tugaskan sebagai default chat/sql/embedding."),
    ) -> None:
        """Salin konfigurasi LLM_* / EMBEDDING_* saat ini ke registry DB (migrasi dari env)."""
        from ai_service.llm.crypto import api_key_hint

        async def run(services):  # noqa: ANN001
            s, repo, box = services.settings, services.llm_repo, services.secret_box
            if await repo.get_provider_by_name(provider_name) is not None:
                raise typer.BadParameter(f"provider '{provider_name}' sudah ada")

            async def provider(name, base_url, api_key):  # noqa: ANN001
                existing = await repo.get_provider_by_name(name)
                if existing is not None:
                    return existing
                return await repo.create_provider(
                    {
                        "name": name,
                        "kind": "openai_compatible",
                        "base_url": base_url,
                        "api_key_enc": box.encrypt(api_key) if api_key else None,
                        "api_key_hint": api_key_hint(api_key) or None,
                    }
                )

            async def model(prov, name, upstream, **extra):  # noqa: ANN001
                existing = await repo.get_model_by_name(name)
                if existing is not None:
                    return existing
                return await repo.create_model(
                    {"provider_id": prov.id, "name": name, "model": upstream, **extra}
                )

            chat_p = await provider(provider_name, s.llm_base_url, s.llm_api_key)
            chat_m = await model(chat_p, f"{provider_name}-chat", s.llm_model)
            created = [f"provider {chat_p.name}", f"model {chat_m.name}"]
            sql_m = chat_m
            if s.llm_sql_model:
                sql_m = await model(chat_p, f"{provider_name}-sql", s.llm_sql_model, temperature=0)
                created.append(f"model {sql_m.name}")
            same_embedding_endpoint = (
                s.effective_embedding_base_url == s.llm_base_url
                and s.effective_embedding_api_key == s.llm_api_key
            )
            emb_p = (
                chat_p
                if same_embedding_endpoint
                else await provider(
                    f"{provider_name}-embedding",
                    s.effective_embedding_base_url,
                    s.effective_embedding_api_key,
                )
            )
            emb_m = await model(
                emb_p,
                f"{provider_name}-embedding",
                s.embedding_model,
                kind="embedding",
                embedding_dim=s.embedding_dim,
                params={"send_dimensions": s.embedding_send_dimensions},
            )
            created.append(f"model {emb_m.name}")
            if assign:
                await repo.upsert_assignment("chat", None, chat_m.id)
                await repo.upsert_assignment("embedding", None, emb_m.id)
                if s.llm_sql_model:
                    await repo.upsert_assignment("sql", None, sql_m.id)
                created.append("penugasan default")
            return created

        for line in asyncio.run(_with_services(run, check_embedding=False)):
            typer.echo(f"dibuat: {line}")

    channel_app = typer.Typer(help="Kelola allowed_channels (wa/tg/web).", no_args_is_help=True)
    app.add_typer(channel_app, name="channel")

    @channel_app.command("add")
    def channel_add(
        platform: str = typer.Argument(..., help="wa | tg | web"),
        channel_id: str = typer.Argument(..., help="nomor WA / chat id TG / user id web"),
        role: str = typer.Option("analyst", help="public | analyst"),
        scope: str = typer.Option("GLOBAL", help="GLOBAL | PROVINCE | KABKOTA | FASKES"),
        provinsi_kode: str | None = typer.Option(None),
        kabupaten_kota_kode: str | None = typer.Option(None),
        facility_id: int | None = typer.Option(None),
        display_name: str | None = typer.Option(None),
    ) -> None:
        """Daftarkan/perbarui kanal."""
        from ai_service.api.schemas import ChannelIn
        from ai_service.auth.identity import normalize_channel_id

        body = ChannelIn(
            platform=platform,  # type: ignore[arg-type]
            channel_id=channel_id,
            role=role,  # type: ignore[arg-type]
            scope_level=scope,  # type: ignore[arg-type]
            provinsi_kode=provinsi_kode,
            kabupaten_kota_kode=kabupaten_kota_kode,
            facility_id=facility_id,
            display_name=display_name,
        )
        masalah = body.validate_scope()
        if masalah:
            raise typer.BadParameter("; ".join(masalah))

        async def run(services):  # noqa: ANN001
            data = body.model_dump(
                exclude={"llm_model_id"}
            )  # override model dikelola via admin API
            data["channel_id"] = normalize_channel_id(platform, channel_id)
            existing = await services.channels.get(platform, data["channel_id"])
            if existing is not None:
                row = await services.channels.update(existing.id, data)
                return "diperbarui", row
            row = await services.channels.create(data)
            return "dibuat", row

        action, row = asyncio.run(_with_services(run, check_embedding=False))
        typer.echo(
            f"kanal {action}: #{row.id} {row.platform}:{row.channel_id} role={row.role} scope={row.scope_level}"
        )

    @channel_app.command("list")
    def channel_list(platform: str | None = typer.Option(None)) -> None:
        """Tampilkan kanal terdaftar."""

        async def run(services):  # noqa: ANN001
            rows, total = await services.channels.list(platform, 1, 200)
            return rows, total

        rows, total = asyncio.run(_with_services(run, check_embedding=False))
        for row in rows:
            typer.echo(
                f"#{row.id} {row.platform}:{row.channel_id} role={row.role} scope={row.scope_level} aktif={row.is_active}"
            )
        typer.echo(f"total {total}")
