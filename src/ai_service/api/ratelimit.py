"""Rate limiter token-bucket in-process (per replica) — kunci per principal dan per IP."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    tokens: float
    updated: float


@dataclass
class RateLimiter:
    per_minute: int
    burst: int | None = None
    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    @property
    def capacity(self) -> float:
        return float(self.burst or self.per_minute)

    def allow(self, key: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=self.capacity, updated=now)
            self._buckets[key] = bucket
        refill = (now - bucket.updated) * (self.per_minute / 60.0)
        bucket.tokens = min(self.capacity, bucket.tokens + refill)
        bucket.updated = now
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            self._maybe_gc(now)
            return True
        return False

    def _maybe_gc(self, now: float) -> None:
        if len(self._buckets) > 20000:
            stale = [k for k, b in self._buckets.items() if now - b.updated > 600]
            for key in stale:
                self._buckets.pop(key, None)
