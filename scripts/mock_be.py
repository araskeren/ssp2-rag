#!/usr/bin/env python
"""Mock backend NestJS untuk dev/smoke: GET /auth/profile, POST /auth/login|logout (dev-only, set cookie httpOnly).

Memverifikasi Bearer HS256 dengan JWT_SECRET (default dev) dan membalas profil per `sub`:
  42 → Admin Kemenkes @ Kementerian & Lembaga   (analyst, GLOBAL)
  43 → Operator Faskes @ Puskesmas 33.28        (public, FASKES #3)
  44 → Admin Provinsi @ Dinkes Provinsi 33      (public, PROVINCE 33) — analyst bila di-whitelist
  45 → Eksekutif KabKota @ Dinkes Kota 33.74     (analyst, KABKOTA 33.74)
  46 → Viewer @ Kementerian & Lembaga            (public, GLOBAL)
Jalankan: uv run python scripts/mock_be.py --port 3001
"""

from __future__ import annotations

import argparse
import datetime
import os

import jwt
import uvicorn
from fastapi import Body, FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

SECRET = os.environ.get("JWT_SECRET", "dev-jwt-secret-samakan-dengan-backend")

CORS_ORIGIN = os.environ.get("CORS_ORIGIN", "http://localhost:5173")

FACILITY_KEMENKES = {
    "id": 1,
    "name": "Kementerian Kesehatan RI",
    "jenisSarana": "Kementerian & Lembaga",
    "subJenis": None,
    "provinsiKode": "31",
    "kabupatenKotaKode": "31.71",
}
FACILITY_DINKES_PROV = {
    "id": 2,
    "name": "Dinas Kesehatan Provinsi Jawa Tengah",
    "jenisSarana": "Dinas Kesehatan",
    "subJenis": "Dinas Kesehatan Provinsi",
    "provinsiKode": "33",
    "kabupatenKotaKode": "33.74",
}
FACILITY_PUSKESMAS = {
    "id": 3,
    "name": "Puskesmas Pati I",
    "jenisSarana": "Pusat Kesehatan Masyarakat",
    "subJenis": "Puskesmas",
    "provinsiKode": "33",
    "kabupatenKotaKode": "33.28",
}
FACILITY_DINKES_KOTA = {
    "id": 4,
    "name": "Dinas Kesehatan Kota Semarang",
    "jenisSarana": "Dinas Kesehatan",
    "subJenis": "Dinas Kesehatan Kota",
    "provinsiKode": "33",
    "kabupatenKotaKode": "33.74",
}

PROFILES = {
    "42": {
        "name": "Admin Kemenkes",
        "role": {"id": 4, "name": "Admin Kemenkes"},
        "facility": FACILITY_KEMENKES,
        "permissions": ["sari_records:read", "menu_sari:dashboard", "ai_chat:analyze"],
    },
    "43": {
        "name": "Operator Faskes",
        "role": {"id": 9, "name": "Operator Faskes"},
        "facility": FACILITY_PUSKESMAS,
        "permissions": ["sari_records:create"],
    },
    "44": {
        "name": "Admin Provinsi",
        "role": {"id": 5, "name": "Admin Provinsi"},
        "facility": FACILITY_DINKES_PROV,
        "permissions": ["sari_records:read"],
    },
    "45": {
        "name": "Eksekutif KabKota",
        "role": {"id": 14, "name": "Eksekutif KabKota"},
        "facility": FACILITY_DINKES_KOTA,
        "permissions": ["sari_records:read", "ai_chat:analyze"],
    },
    "46": {
        "name": "Viewer Pusat",
        "role": {"id": 16, "name": "Viewer"},
        "facility": FACILITY_KEMENKES,
        "permissions": ["sari_records:read"],
    },
}

app = FastAPI(title="mock-be")
app.add_middleware(
    CORSMiddleware,
    # Dev-only: izinkan origin lokal apa pun (localhost/127.0.0.1, port berapa saja).
    # allow_credentials=True tidak boleh dipasangkan dengan allow_origins=["*"],
    # jadi dibatasi lewat regex origin lokal.
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_origins=[o.strip() for o in CORS_ORIGIN.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/auth/profile")
def profile(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        return JSONResponse(status_code=401, content={"statusCode": 401, "message": "Unauthorized"})
    try:
        payload = jwt.decode(
            authorization[7:], SECRET, algorithms=["HS256"], options={"verify_sub": False}
        )
    except jwt.InvalidTokenError as exc:
        return JSONResponse(status_code=401, content={"statusCode": 401, "message": str(exc)})
    if payload.get("isTemp"):
        return JSONResponse(status_code=401, content={"statusCode": 401, "message": "temp token"})
    sub = str(payload.get("sub"))
    base = PROFILES.get(sub)
    if base is None:
        return JSONResponse(
            status_code=401, content={"statusCode": 401, "message": "User not found"}
        )
    return {
        "id": int(sub),
        "email": payload.get("email", f"user{sub}@example.id"),
        "roleId": base["role"]["id"],
        "facilityId": base["facility"]["id"],
        "isActive": True,
        **base,
        "googleLinked": False,
        "passkeyCount": 0,
    }


DEV_PERSONAS = {"42", "43", "44", "45", "46"}


@app.post("/auth/login")
def login(payload: dict | None = Body(default=None)):
    """DEV-ONLY: login sebagai persona tetap. Di BE asli, ini validasi kredensial."""
    sub = str((payload or {}).get("sub", "42"))
    if sub not in DEV_PERSONAS:
        return JSONResponse(status_code=400, content={"statusCode": 400, "message": f"sub harus salah satu {sorted(DEV_PERSONAS)}"})
    now = datetime.datetime.now(datetime.timezone.utc)
    token = jwt.encode(
        {
            "sub": int(sub),
            "email": f"user{sub}@example.id",
            "sessionId": f"dev-session-{sub}",
            "iat": int(now.timestamp()),
            "exp": int(now.timestamp()) + 3600,
        },
        SECRET,
        algorithm="HS256",
    )
    resp = JSONResponse(content={"ok": True, "sub": sub, "access_token": token})
    resp.set_cookie(key="access_token", value=token, httponly=True, samesite="lax")
    return resp


@app.post("/auth/logout")
def logout():
    resp = JSONResponse(content={"ok": True})
    resp.delete_cookie("access_token")
    return resp


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3001)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
