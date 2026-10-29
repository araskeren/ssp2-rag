"""Verifikasi JWT HS256 yang diterbitkan BE NestJS (payload: sub, email, roleId, sessionId).

Aturan (lihat turborepo apps/be/src/modules/auth/auth.service.ts):
- hanya HS256 dengan JWT_SECRET bersama; `exp` dan `sub` wajib;
- token sementara 2FA (`isTemp: true`, tanpa sessionId) memakai secret yang sama → ditolak;
- `sub` numerik (id user) dinormalkan ke string.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt


class JwtError(Exception):
    """Token tidak dapat diterima; pesan aman ditampilkan ke klien (401)."""


@dataclass(frozen=True)
class JwtClaims:
    sub: str
    email: str | None
    role_id: int | None
    session_id: str
    exp: int | None
    raw: dict[str, Any]


def verify_hs256(token: str, secret: str) -> JwtClaims:
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"require": ["exp", "sub"], "verify_sub": False, "verify_jti": False},
        )
    except jwt.ExpiredSignatureError as exc:
        raise JwtError("token kedaluwarsa") from exc
    except jwt.InvalidTokenError as exc:
        raise JwtError("token tidak valid") from exc

    if payload.get("isTemp"):
        raise JwtError("token sementara 2FA tidak diterima")
    session_id = payload.get("sessionId")
    if session_id in (None, ""):
        raise JwtError("token tanpa sessionId")
    sub = payload.get("sub")
    if sub in (None, ""):
        raise JwtError("token tanpa sub")

    role_id_raw = payload.get("roleId")
    role_id: int | None = None
    if isinstance(role_id_raw, int) and not isinstance(role_id_raw, bool):
        role_id = role_id_raw
    elif isinstance(role_id_raw, str) and role_id_raw.isdigit():
        role_id = int(role_id_raw)

    return JwtClaims(
        sub=str(sub),
        email=payload.get("email"),
        role_id=role_id,
        session_id=str(session_id),
        exp=payload.get("exp"),
        raw=payload,
    )
