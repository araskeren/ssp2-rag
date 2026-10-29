"""Klien `GET {BE_BASE_URL}/auth/profile` dengan cache per sessionId.

- Bearer pengguna diteruskan apa adanya; IP pengguna diteruskan via X-Forwarded-For /
  X-Real-IP supaya throttler BE (per IP) mengenali pengguna, bukan ai-service.
- 401/403 dari BE = sesi dicabut / user diblokir → `unauthorized` (tidak di-cache).
- BE tidak tersedia (timeout/5xx/429) → pakai cache basi bila ada, selain itu `unavailable`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from ai_service import __version__
from ai_service.logging import get_logger

log = get_logger(__name__)

ProfileStatus = Literal["ok", "unauthorized", "unavailable"]


@dataclass(frozen=True)
class ProfileResult:
    status: ProfileStatus
    profile: dict[str, Any] | None = None
    stale: bool = False


class ProfileClient:
    def __init__(self, base_url: str, timeout_s: float = 3.0, cache_ttl_s: int = 300) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_s,
            headers={"User-Agent": f"ai-service/{__version__}"},
        )
        self._ttl = cache_ttl_s
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    def invalidate(self, session_id: str) -> None:
        self._cache.pop(session_id, None)

    def _cached(self, session_id: str) -> tuple[dict[str, Any] | None, bool]:
        entry = self._cache.get(session_id)
        if entry is None:
            return None, False
        stored_at, profile = entry
        return profile, (time.monotonic() - stored_at) > self._ttl

    def _store(self, session_id: str, profile: dict[str, Any]) -> None:
        if len(self._cache) > 5000:
            now = time.monotonic()
            for key in [k for k, (t, _) in self._cache.items() if now - t > self._ttl]:
                self._cache.pop(key, None)
        self._cache[session_id] = (time.monotonic(), profile)

    async def get_profile(
        self, token: str, session_id: str, client_ip: str | None = None
    ) -> ProfileResult:
        cached, stale = self._cached(session_id)
        if cached is not None and not stale:
            return ProfileResult("ok", cached)

        headers = {"Authorization": f"Bearer {token}"}
        if client_ip:
            headers["X-Forwarded-For"] = client_ip
            headers["X-Real-IP"] = client_ip
        try:
            resp = await self._client.get("/auth/profile", headers=headers)
        except httpx.HTTPError as exc:
            log.warning("BE /auth/profile tidak terjangkau", error=str(exc))
            return self._fallback(session_id, cached)

        if resp.status_code == 200:
            try:
                profile = resp.json()
            except ValueError:
                log.warning("BE /auth/profile membalas bukan JSON")
                return self._fallback(session_id, cached)
            if not isinstance(profile, dict):
                return self._fallback(session_id, cached)
            self._store(session_id, profile)
            return ProfileResult("ok", profile)
        if resp.status_code in (401, 403):
            self.invalidate(session_id)
            return ProfileResult("unauthorized")
        log.warning("BE /auth/profile gagal", status=resp.status_code)
        return self._fallback(session_id, cached)

    @staticmethod
    def _fallback(session_id: str, cached: dict[str, Any] | None) -> ProfileResult:
        if cached is not None:
            log.info("memakai profil cache basi", session_id=session_id)
            return ProfileResult("ok", cached, stale=True)
        return ProfileResult("unavailable")
