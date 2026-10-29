"""CLI `ai-service` (typer): serve | migrate | (ingest, reindex, channel, catalog, eval menyusul)."""

from __future__ import annotations

import typer

from ai_service.config import get_settings
from ai_service.logging import configure_logging, get_logger

app = typer.Typer(
    help="ai-service — Agentic RAG + Text-to-SQL Satu Data Penyakit",
    no_args_is_help=True,
    add_completion=False,
)
log = get_logger(__name__)


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Alamat bind."),
    port: int | None = typer.Option(None, help="Port (default: env PORT)."),
    reload: bool = typer.Option(False, help="Auto-reload (dev saja)."),
    workers: int = typer.Option(1, help="Jumlah worker uvicorn."),
) -> None:
    """Jalankan server HTTP (migrasi otomatis saat boot bila AUTO_MIGRATE=true)."""
    import uvicorn

    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        "ai_service.main:app",
        host=host,
        port=port or settings.port,
        reload=reload,
        workers=workers if not reload else 1,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_config=None,
        access_log=False,
    )


@app.command()
def migrate(revision: str = typer.Argument("head", help="Revisi alembic tujuan.")) -> None:
    """Jalankan migrasi alembic pada database ai-service."""
    from ai_service.db.migrate import run_migrations

    settings = get_settings()
    configure_logging(settings.log_level)
    run_migrations(settings, revision)
    typer.echo(f"migrasi selesai → {revision}")


from ai_service.cli_ext import register  # noqa: E402

register(app)

if __name__ == "__main__":
    app()
