#!/usr/bin/env python
"""Cetak JWT HS256 ala BE untuk smoke/dev: uv run python scripts/mint_jwt.py --sub 42 [--temp] [--exp -60]."""

from __future__ import annotations

import argparse
import os
import time
import uuid

import jwt


def mint(
    sub: int,
    secret: str,
    email: str | None = None,
    role_id: int = 1,
    temp: bool = False,
    exp_offset: int = 900,
) -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": email or f"user{sub}@example.id",
        "iat": now,
        "exp": now + exp_offset,
    }
    if temp:
        payload["isTemp"] = True
    else:
        payload["roleId"] = role_id
        payload["sessionId"] = str(uuid.uuid4())
    return jwt.encode(payload, secret, algorithm="HS256")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sub", type=int, default=42)
    parser.add_argument(
        "--secret", default=os.environ.get("JWT_SECRET", "dev-jwt-secret-samakan-dengan-backend")
    )
    parser.add_argument("--temp", action="store_true", help="token sementara 2FA (harus ditolak)")
    parser.add_argument(
        "--exp",
        type=int,
        default=900,
        help="detik sampai kedaluwarsa (negatif = sudah kedaluwarsa)",
    )
    args = parser.parse_args()
    print(mint(args.sub, args.secret, temp=args.temp, exp_offset=args.exp))
