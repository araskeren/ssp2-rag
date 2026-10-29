"""Deteksi pergeseran skema: jalankan SQL tiap view katalog dengan LIMIT 0 pada DB analitik."""

from __future__ import annotations

import asyncpg

from ai_service.sql.catalog import SCOPE_PLACEHOLDER, load_catalog


async def check_drift(dsn: str, dialect: str = "postgres") -> list[tuple[str, str | None]]:
    catalog = load_catalog()
    conn = await asyncpg.connect(dsn, timeout=10)
    results: list[tuple[str, str | None]] = []
    try:
        for view in catalog.views.values():
            sql = view.sql_for(dialect).replace(SCOPE_PLACEHOLDER, "TRUE")
            probe = f"WITH {view.name} AS (\n{sql}\n) SELECT * FROM {view.name} LIMIT 0"
            try:
                async with conn.transaction(readonly=True):
                    statement = await conn.prepare(probe)
                    actual = {attr.name for attr in statement.get_attributes()}
                    await statement.fetch()
                missing = sorted(view.column_names - actual)
                results.append(
                    (view.name, f"kolom hilang: {', '.join(missing)}" if missing else None)
                )
            except asyncpg.PostgresError as exc:
                results.append((view.name, str(getattr(exc, "message", exc)).splitlines()[0][:200]))
    finally:
        await conn.close()
    return results
