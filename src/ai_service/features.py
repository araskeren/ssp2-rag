"""Sakelar fitur asisten: disimpan di DB, dibaca lewat snapshot ber-TTL seperti LlmRegistry.

Perubahan berlaku langsung di replika penerima dan paling lama FEATURE_FLAGS_TTL_S di replika lain.
Gagal membaca DB memakai snapshot terakhir; tanpa snapshot, semua fitur dianggap mati (fail-closed).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from ai_service.db.engine import Database
from ai_service.db.models import FeatureFlag
from ai_service.logging import get_logger

log = get_logger(__name__)

TOOL_HELPDESK = "cari_dokumen"
TOOL_ANALYSIS = "analisis_data"


@dataclass(frozen=True)
class FeatureState:
    """Nilai bawaan terbuka untuk jalur tanpa DB (test, eval); jalur nyata memakai FeatureFlags."""

    assistant: bool = True
    helpdesk: bool = True
    analysis: bool = True
    connector: bool = True

    @property
    def disabled_tools(self) -> frozenset[str]:
        off = set()
        if not self.helpdesk:
            off.add(TOOL_HELPDESK)
        if not self.analysis:
            off.add(TOOL_ANALYSIS)
        return frozenset(off)

    def available_for(self, *, is_analyst: bool) -> bool:
        """Ada fitur yang bisa dipakai pengguna ini; analisis hanya berarti bagi analis."""
        return self.assistant and (self.helpdesk or (self.analysis and is_analyst))


CLOSED_STATE = FeatureState(assistant=False, helpdesk=False, analysis=False, connector=False)


class FeatureFlags:
    def __init__(
        self, db: Database, ttl_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._db = db
        self._ttl = max(0.0, float(ttl_s))
        self._clock = clock
        self._state: FeatureState | None = None
        self._loaded_at = 0.0
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        self._state = None

    def _fresh(self) -> FeatureState | None:
        if self._state is not None and self._clock() - self._loaded_at < self._ttl:
            return self._state
        return None

    async def list_rows(self) -> list[FeatureFlag]:
        async with self._db.session() as session:
            rows = await session.execute(select(FeatureFlag).order_by(FeatureFlag.key))
            return list(rows.scalars())

    async def current(self) -> FeatureState:
        state = self._fresh()
        if state is not None:
            return state
        async with self._lock:
            state = self._fresh()
            if state is not None:
                return state
            try:
                rows = await self.list_rows()
            except Exception as exc:  # noqa: BLE001 — DB gangguan tidak boleh membuka fitur yang mati
                log.error("sakelar fitur gagal dibaca", error=str(exc))
                return self._state or CLOSED_STATE
            # Baris yang tidak ada dianggap mati: migrasi selalu mengisi keempatnya.
            enabled = {row.key: row.enabled for row in rows}
            self._state = FeatureState(
                assistant=enabled.get("assistant", False),
                helpdesk=enabled.get("helpdesk", False),
                analysis=enabled.get("analysis", False),
                connector=enabled.get("connector", False),
            )
            self._loaded_at = self._clock()
            return self._state

    async def update(self, changes: dict[str, bool], actor: str) -> list[FeatureFlag]:
        async with self._db.session() as session:
            for key, enabled in changes.items():
                row = await session.get(FeatureFlag, key)
                if row is None:
                    continue
                if row.enabled != enabled:
                    row.enabled = enabled
                    row.updated_at = datetime.now(UTC)
                    row.updated_by = actor
            await session.commit()
        self.invalidate()
        log.info("sakelar fitur diubah", actor=actor, changes=changes)
        return await self.list_rows()
