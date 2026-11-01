"""Endpoint admin registry LLM: provider (credentials terenkripsi), model, penugasan, uji & resolusi."""

from __future__ import annotations

import re
import time
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from langchain_core.messages import HumanMessage
from sqlalchemy.exc import IntegrityError

from ai_service.api.deps import get_services, require_admin_key
from ai_service.api.schemas import (
    LlmAssignmentIn,
    LlmAssignmentOut,
    LlmModelIn,
    LlmModelOut,
    LlmModelPatch,
    LlmProviderIn,
    LlmProviderOut,
    LlmProviderPatch,
)
from ai_service.auth.identity import normalize_channel_id
from ai_service.errors import ApiError
from ai_service.llm.crypto import SecretError, api_key_hint
from ai_service.llm.factory import LlmConfigError, build_chat_model, build_embeddings
from ai_service.llm.registry import model_spec, provider_spec
from ai_service.llm.repo import LlmRepo
from ai_service.llm.spec import PURPOSE_MODEL_KIND
from ai_service.services import Services

router = APIRouter(
    prefix="/api/v1/admin/llm", tags=["admin-llm"], dependencies=[Depends(require_admin_key)]
)

_SENSITIVE_HEADER = re.compile(r"auth|key|token|secret|cookie", re.I)


def _repo(services: Services) -> LlmRepo:
    if services.llm_repo is None or services.secret_box is None:
        raise ApiError(503, "database belum siap")
    return services.llm_repo


def _invalidate(services: Services) -> None:
    if services.llm_registry is not None:
        services.llm_registry.invalidate()


def _provider_out(row: Any) -> LlmProviderOut:
    headers = {
        k: ("***" if _SENSITIVE_HEADER.search(k) else v) for k, v in (row.headers or {}).items()
    }
    return LlmProviderOut(
        id=row.id,
        name=row.name,
        kind=row.kind,
        base_url=row.base_url or "",
        api_key_set=bool(row.api_key_enc),
        api_key_hint=row.api_key_hint,
        headers=headers,
        timeout_s=row.timeout_s,
        is_active=row.is_active,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _encrypt_key(services: Services, api_key: str) -> dict[str, Any]:
    if not api_key:
        return {"api_key_enc": None, "api_key_hint": None}
    try:
        return {
            "api_key_enc": services.secret_box.encrypt(api_key),  # type: ignore[union-attr]
            "api_key_hint": api_key_hint(api_key),
        }
    except SecretError as exc:
        raise ApiError(400, str(exc)) from exc


async def _commit(coro: Any, conflict: str) -> Any:
    try:
        return await coro
    except IntegrityError as exc:
        raise ApiError(409, conflict) from exc


# ---------------------------------------------------------------------- provider
@router.get("/providers")
async def list_providers(services: Services = Depends(get_services)) -> dict:
    rows = await _repo(services).list_providers()
    return {"items": [_provider_out(r) for r in rows]}


@router.post("/providers", response_model=LlmProviderOut, status_code=201)
async def create_provider(
    body: LlmProviderIn, services: Services = Depends(get_services)
) -> LlmProviderOut:
    repo = _repo(services)
    if await repo.get_provider_by_name(body.name) is not None:
        raise ApiError(409, "nama provider sudah dipakai")
    data = body.model_dump(exclude={"api_key"})
    data.update(_encrypt_key(services, (body.api_key or "").strip()))
    row = await _commit(repo.create_provider(data), "nama provider sudah dipakai")
    _invalidate(services)
    return _provider_out(row)


@router.get("/providers/{provider_id}", response_model=LlmProviderOut)
async def get_provider(
    provider_id: int, services: Services = Depends(get_services)
) -> LlmProviderOut:
    row = await _repo(services).get_provider(provider_id)
    if row is None:
        raise ApiError(404, "provider tidak ditemukan")
    return _provider_out(row)


@router.patch("/providers/{provider_id}", response_model=LlmProviderOut)
async def patch_provider(
    provider_id: int, body: LlmProviderPatch, services: Services = Depends(get_services)
) -> LlmProviderOut:
    repo = _repo(services)
    data = body.model_dump(exclude_unset=True, exclude={"api_key"})
    if body.api_key is not None:
        data.update(_encrypt_key(services, body.api_key.strip()))
    if not data:
        raise ApiError(400, "tidak ada field yang diubah")
    if "headers" in data and data["headers"] is not None:
        current = await repo.get_provider(provider_id)
        old = (current.headers or {}) if current is not None else {}
        # Nilai "***" (hasil masking GET) berarti "pertahankan nilai lama".
        data["headers"] = {
            k: (old.get(k, "") if v == "***" else v) for k, v in data["headers"].items()
        }
    row = await _commit(repo.update_provider(provider_id, data), "nama provider sudah dipakai")
    if row is None:
        raise ApiError(404, "provider tidak ditemukan")
    _invalidate(services)
    return _provider_out(row)


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(provider_id: int, services: Services = Depends(get_services)) -> Response:
    repo = _repo(services)
    if await repo.provider_model_count(provider_id):
        raise ApiError(409, "provider masih memiliki model; hapus/pindahkan modelnya dulu")
    if not await _commit(repo.delete_provider(provider_id), "provider masih dipakai"):
        raise ApiError(404, "provider tidak ditemukan")
    _invalidate(services)
    return Response(status_code=204)


# ---------------------------------------------------------------------- model
def _validate_model(provider: Any, kind: str, embedding_dim: int | None) -> None:
    if kind == "embedding":
        if provider.kind != "openai_compatible":
            raise ApiError(
                400,
                "validasi request gagal",
                ["kind: embedding hanya untuk provider openai_compatible"],
            )
        if not embedding_dim:
            raise ApiError(400, "validasi request gagal", ["embedding_dim: wajib untuk embedding"])


@router.get("/models")
async def list_models(
    provider_id: int | None = Query(default=None),
    kind: str | None = Query(default=None, pattern="^(chat|embedding)$"),
    services: Services = Depends(get_services),
) -> dict:
    rows = await _repo(services).list_models(provider_id, kind)
    return {"items": [LlmModelOut.model_validate(r) for r in rows]}


@router.post("/models", response_model=LlmModelOut, status_code=201)
async def create_model(body: LlmModelIn, services: Services = Depends(get_services)) -> LlmModelOut:
    repo = _repo(services)
    provider = await repo.get_provider(body.provider_id)
    if provider is None:
        raise ApiError(400, "validasi request gagal", ["provider_id: provider tidak ditemukan"])
    _validate_model(provider, body.kind, body.embedding_dim)
    if await repo.get_model_by_name(body.name) is not None:
        raise ApiError(409, "nama model sudah dipakai")
    row = await _commit(repo.create_model(body.model_dump()), "nama model sudah dipakai")
    _invalidate(services)
    return LlmModelOut.model_validate(row)


@router.get("/models/{model_id}", response_model=LlmModelOut)
async def get_model(model_id: int, services: Services = Depends(get_services)) -> LlmModelOut:
    row = await _repo(services).get_model(model_id)
    if row is None:
        raise ApiError(404, "model tidak ditemukan")
    return LlmModelOut.model_validate(row)


@router.patch("/models/{model_id}", response_model=LlmModelOut)
async def patch_model(
    model_id: int, body: LlmModelPatch, services: Services = Depends(get_services)
) -> LlmModelOut:
    repo = _repo(services)
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise ApiError(400, "tidak ada field yang diubah")
    current = await repo.get_model(model_id)
    if current is None:
        raise ApiError(404, "model tidak ditemukan")
    provider = await repo.get_provider(current.provider_id)
    _validate_model(provider, current.kind, data.get("embedding_dim", current.embedding_dim))
    if "params" in data and data["params"] is None:
        data["params"] = {}
    row = await _commit(repo.update_model(model_id, data), "nama model sudah dipakai")
    _invalidate(services)
    return LlmModelOut.model_validate(row)


@router.delete("/models/{model_id}", status_code=204)
async def delete_model(model_id: int, services: Services = Depends(get_services)) -> Response:
    repo = _repo(services)
    usage = await repo.model_usage(model_id)
    if usage["assignments"]:
        raise ApiError(409, "model masih ditugaskan; ubah/hapus penugasannya dulu", [str(usage)])
    if not await _commit(repo.delete_model(model_id), "model masih dipakai"):
        raise ApiError(404, "model tidak ditemukan")
    _invalidate(services)
    return Response(status_code=204)


@router.post("/models/{model_id}/test")
async def test_model(model_id: int, services: Services = Depends(get_services)) -> dict:
    """Panggilan nyata ke provider (chat: 1 pesan pendek; embedding: 1 teks) untuk cek credentials."""
    repo = _repo(services)
    row = await repo.get_model(model_id)
    if row is None:
        raise ApiError(404, "model tidak ditemukan")
    provider_row = await repo.get_provider(row.provider_id)
    started = time.perf_counter()
    try:
        spec = model_spec(row, provider_spec(provider_row, services.secret_box))  # type: ignore[arg-type]
        if spec.kind == "embedding":
            vector = await build_embeddings(spec, services.settings).aembed_query("ping")
            detail: dict[str, Any] = {
                "dim": len(vector),
                "dim_ok": len(vector) == spec.embedding_dim,
            }
        else:
            llm = build_chat_model(spec, services.settings)
            reply = await llm.ainvoke([HumanMessage(content="Balas dengan satu kata: ok")])
            text = (
                reply.text if isinstance(getattr(reply, "text", None), str) else str(reply.content)
            )
            detail = {"output": text[:200]}
    except (SecretError, LlmConfigError) as exc:
        return {"ok": False, "latency_ms": 0, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — laporkan apa adanya ke admin
        latency = int((time.perf_counter() - started) * 1000)
        return {"ok": False, "latency_ms": latency, "error": f"{type(exc).__name__}: {exc}"[:500]}
    latency = int((time.perf_counter() - started) * 1000)
    return {"ok": True, "latency_ms": latency, **detail}


# ---------------------------------------------------------------------- penugasan
@router.get("/assignments")
async def list_assignments(services: Services = Depends(get_services)) -> dict:
    rows = await _repo(services).list_assignments()
    return {"items": [LlmAssignmentOut.model_validate(r) for r in rows]}


@router.put("/assignments", response_model=None)
async def put_assignment(body: LlmAssignmentIn, services: Services = Depends(get_services)) -> dict:
    repo = _repo(services)
    if body.ai_role is not None and body.purpose != "chat":
        raise ApiError(400, "validasi request gagal", ["ai_role: hanya berlaku untuk purpose chat"])
    model = await repo.get_model(body.model_id)
    if model is None:
        raise ApiError(400, "validasi request gagal", ["model_id: model tidak ditemukan"])
    expected = PURPOSE_MODEL_KIND[body.purpose]
    if model.kind != expected:
        raise ApiError(
            400,
            "validasi request gagal",
            [f"model_id: purpose {body.purpose} butuh model bertipe {expected}"],
        )
    if not model.is_active:
        raise ApiError(400, "validasi request gagal", ["model_id: model tidak aktif"])
    row = await repo.upsert_assignment(body.purpose, body.ai_role, body.model_id)
    _invalidate(services)
    out: dict[str, Any] = LlmAssignmentOut.model_validate(row).model_dump()
    if body.purpose == "embedding":
        current = services.settings
        out["restart_required"] = True
        out["reindex_required"] = (
            model.model != current.embedding_model or model.embedding_dim != current.embedding_dim
        )
    return out


@router.delete("/assignments", status_code=204)
async def delete_assignment(
    purpose: str = Query(pattern="^(chat|sql|judge|embedding)$"),
    ai_role: str | None = Query(default=None, pattern="^(public|analyst)$"),
    services: Services = Depends(get_services),
) -> Response:
    if not await _repo(services).delete_assignment(purpose, ai_role):
        raise ApiError(404, "penugasan tidak ditemukan")
    _invalidate(services)
    return Response(status_code=204)


# ---------------------------------------------------------------------- resolusi (debug)
@router.get("/resolve")
async def resolve_models(
    ai_role: str = Query(default="public", pattern="^(public|analyst)$"),
    platform: str | None = Query(default=None, pattern="^(wa|tg|web)$"),
    channel_id: str | None = Query(default=None),
    services: Services = Depends(get_services),
) -> dict:
    """Model efektif per keperluan untuk role (atau kanal tertentu, yang sekaligus menentukan role)."""
    registry = services.llm_registry
    if registry is None:
        raise ApiError(503, "registry LLM belum siap")
    model_id: int | None = None
    channel: dict[str, Any] | None = None
    if platform and channel_id and services.channels is not None:
        row = await services.channels.get(platform, normalize_channel_id(platform, channel_id))
        if row is not None and row.is_active:
            ai_role = "analyst" if row.role == "analyst" else "public"
            model_id = row.llm_model_id
            channel = {"id": row.id, "role": row.role, "llm_model_id": row.llm_model_id}
    out: dict[str, Any] = {"ai_role": ai_role, "channel": channel}
    try:
        for purpose in ("chat", "sql", "judge"):
            resolved = await registry.resolve(
                purpose,
                ai_role if purpose == "chat" else None,
                model_id if purpose == "chat" else None,
            )
            out[purpose] = resolved.to_dict()
    except (LlmConfigError, SecretError) as exc:
        raise ApiError(503, str(exc)) from exc
    embedding = await registry.resolve_embedding_spec()
    out["embedding"] = {
        "label": embedding.label,
        "source": embedding.source,
        "model": embedding.model,
        "dim": embedding.embedding_dim,
        "active_in_process": embedding.model == services.settings.embedding_model
        and embedding.embedding_dim == services.settings.embedding_dim,
    }
    return out
