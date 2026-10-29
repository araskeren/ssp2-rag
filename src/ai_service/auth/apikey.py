"""Pencocokan X-API-Key constant-time (meniru notification-service internal/auth/apikey.go)."""

from __future__ import annotations

import hmac


def match_api_key(presented: str | None, allowed: list[str]) -> bool:
    if not presented:
        return False
    candidate = presented.strip().encode()
    matched = False
    for key in allowed:
        if hmac.compare_digest(candidate, key.encode()):
            matched = True  # tetap lanjut agar waktu tidak bergantung posisi key
    return matched
