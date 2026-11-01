"""Endpoint admin (X-API-Key admin): kanal allowed_channels; dokumen/ingest/audit menyusul."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response

from ai_service.api.deps import get_services, require_admin_key
from ai_service.api.schemas import ChannelIn, ChannelOut, ChannelPatch, PageOut, SearchIn
from ai_service.auth.identity import normalize_channel_id
from ai_service.errors import ApiError
from ai_service.rag.ingest import (
    IngestError,
    IngestOptions,
    UploadedFile,
    document_to_dict,
    job_to_dict,
)
from ai_service.services import Services

router = APIRouter(
    prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(require_admin_key)]
)


def _channels(services: Services):
    if services.channels is None:
        raise ApiError(503, "database belum siap")
    return services.channels


async def _check_channel_model(services: Services, model_id: int | None) -> None:
    if model_id is None:
        return
    model = await services.llm_repo.get_model(model_id) if services.llm_repo else None
    if model is None or model.kind != "chat":
        raise ApiError(400, "validasi request gagal", ["llm_model_id: model chat tidak ditemukan"])


@router.get("/channels", response_model=PageOut)
async def list_channels(
    platform: str | None = Query(default=None, pattern="^(wa|tg|web)$"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    services: Services = Depends(get_services),
) -> PageOut:
    rows, total = await _channels(services).list(platform, page, limit)
    return PageOut(
        items=[ChannelOut.model_validate(r) for r in rows], total=total, page=page, limit=limit
    )


@router.post("/channels", response_model=ChannelOut, status_code=201)
async def create_channel(body: ChannelIn, services: Services = Depends(get_services)) -> ChannelOut:
    masalah = body.validate_scope()
    if masalah:
        raise ApiError(400, "validasi request gagal", masalah)
    await _check_channel_model(services, body.llm_model_id)
    data = body.model_dump()
    data["channel_id"] = normalize_channel_id(body.platform, body.channel_id)
    repo = _channels(services)
    if await repo.get(body.platform, data["channel_id"]) is not None:
        raise ApiError(409, "kanal sudah terdaftar")
    row = await repo.create(data)
    return ChannelOut.model_validate(row)


@router.patch("/channels/{channel_pk}", response_model=ChannelOut)
async def patch_channel(
    channel_pk: int, body: ChannelPatch, services: Services = Depends(get_services)
) -> ChannelOut:
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise ApiError(400, "tidak ada field yang diubah")
    repo = _channels(services)
    row = await repo.get_by_id(channel_pk)
    if row is None:
        raise ApiError(404, "kanal tidak ditemukan")
    merged = ChannelIn(
        platform=row.platform,
        channel_id=row.channel_id,
        role=data.get("role", row.role),
        scope_level=data.get("scope_level", row.scope_level),
        provinsi_kode=data.get("provinsi_kode", row.provinsi_kode),
        kabupaten_kota_kode=data.get("kabupaten_kota_kode", row.kabupaten_kota_kode),
        facility_id=data.get("facility_id", row.facility_id),
    )
    masalah = merged.validate_scope()
    if masalah:
        raise ApiError(400, "validasi request gagal", masalah)
    if "llm_model_id" in data:
        await _check_channel_model(services, data["llm_model_id"])
    updated = await repo.update(channel_pk, data)
    return ChannelOut.model_validate(updated)


@router.delete("/channels/{channel_pk}", status_code=204)
async def delete_channel(channel_pk: int, services: Services = Depends(get_services)) -> Response:
    if not await _channels(services).delete(channel_pk):
        raise ApiError(404, "kanal tidak ditemukan")
    return Response(status_code=204)


# ---------------------------------------------------------------------- dokumen & ingest


def _ingest(services: Services):
    if services.ingest is None or services.documents is None or services.jobs is None:
        raise ApiError(503, "layanan ingest belum siap")
    return services.ingest


@router.post("/ingest", status_code=202)
async def ingest_documents(
    files: list[UploadFile] = File(..., description="Berkas pdf/txt/md/docx"),
    title: str | None = Form(default=None),
    source: str | None = Form(default=None),
    tags: str | None = Form(default=None, description="dipisah koma"),
    replace: bool = Form(default=False),
    sync: bool = Form(default=False),
    services: Services = Depends(get_services),
) -> dict:
    ingest = _ingest(services)
    uploaded: list[UploadedFile] = []
    limit = services.settings.ingest_max_file_mb * 1024 * 1024
    for upload in files:
        data = await upload.read()
        if len(data) > limit:
            raise ApiError(
                413, f"{upload.filename}: melebihi {services.settings.ingest_max_file_mb} MB"
            )
        uploaded.append(UploadedFile(upload.filename or "tanpa-nama", data))
    options = IngestOptions(
        title=title or None,
        source=source or None,
        tags=[t.strip() for t in (tags or "").split(",") if t.strip()],
        replace=replace,
        requested_by="admin-api",
    )
    try:
        job = await ingest.submit(uploaded, options, sync=sync)
    except IngestError as exc:
        raise ApiError(400, str(exc)) from exc
    return job_to_dict(job)


@router.get("/ingest/{job_id}")
async def get_ingest_job(job_id: uuid.UUID, services: Services = Depends(get_services)) -> dict:
    _ingest(services)
    job = await services.jobs.get(job_id)  # type: ignore[union-attr]
    if job is None:
        raise ApiError(404, "job tidak ditemukan")
    return job_to_dict(job)


@router.get("/documents", response_model=PageOut)
async def list_documents(
    status: str | None = Query(default=None, pattern="^(pending|indexed|failed|deleted)$"),
    q: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    services: Services = Depends(get_services),
) -> PageOut:
    _ingest(services)
    rows, total = await services.documents.list(status, q, page, limit)  # type: ignore[union-attr]
    return PageOut(items=[document_to_dict(r) for r in rows], total=total, page=page, limit=limit)


@router.get("/documents/{document_id}")
async def get_document(document_id: uuid.UUID, services: Services = Depends(get_services)) -> dict:
    _ingest(services)
    doc = await services.documents.get(document_id)  # type: ignore[union-attr]
    if doc is None:
        raise ApiError(404, "dokumen tidak ditemukan")
    return document_to_dict(doc)


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: uuid.UUID, services: Services = Depends(get_services)
) -> Response:
    if not await _ingest(services).delete_document(document_id):
        raise ApiError(404, "dokumen tidak ditemukan")
    return Response(status_code=204)


@router.post("/documents/{document_id}/reindex")
async def reindex_document(
    document_id: uuid.UUID, services: Services = Depends(get_services)
) -> dict:
    try:
        count = await _ingest(services).reindex_document(document_id)
    except IngestError as exc:
        raise ApiError(404 if "tidak ditemukan" in str(exc) else 400, str(exc)) from exc
    doc = await services.documents.get(document_id)  # type: ignore[union-attr]
    return {"document": document_to_dict(doc), "chunk_count": count}


@router.post("/search")
async def debug_search(body: SearchIn, services: Services = Depends(get_services)) -> dict:
    if services.retriever is None:
        raise ApiError(503, "retriever belum siap")
    chunks = await services.retriever.retrieve(body.query, top_k=body.top_k)
    _, sources = services.retriever.format_context(chunks)
    return {
        "query": body.query,
        "hits": [
            {
                **src.to_dict(),
                "dense_rank": chunk.dense_rank,
                "sparse_rank": chunk.sparse_rank,
                "content": chunk.content,
            }
            for src, chunk in zip(sources, chunks, strict=True)
        ],
    }


# ---------------------------------------------------------------------- audit SQL
@router.get("/sql-audit", response_model=PageOut)
async def list_sql_audit(
    status: str | None = Query(default=None, pattern="^(ok|rejected|error|timeout|access_denied)$"),
    principal_id: str | None = Query(default=None, max_length=100),
    since: datetime | None = Query(default=None, alias="from"),
    until: datetime | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    services: Services = Depends(get_services),
) -> PageOut:
    if services.audit is None:
        raise ApiError(503, "database belum siap")
    rows, total = await services.audit.list(
        status=status, principal_id=principal_id, since=since, until=until, page=page, limit=limit
    )
    items = [
        {
            "id": str(r.id),
            "created_at": r.created_at,
            "session_id": r.session_id,
            "message_id": str(r.message_id) if r.message_id else None,
            "principal_id": r.principal_id,
            "platform": r.platform,
            "ai_role": r.ai_role,
            "scope": r.scope,
            "backend": r.backend,
            "question": r.question,
            "generated_sql": r.generated_sql,
            "views": list(r.views or []),
            "attempt": r.attempt,
            "status": r.status,
            "error": r.error,
            "row_count": r.row_count,
            "elapsed_ms": r.elapsed_ms,
        }
        for r in rows
    ]
    return PageOut(items=items, total=total, page=page, limit=limit)


# ---------------------------------------------------------------------- katalog Text-to-SQL
@router.get("/catalog")
async def get_catalog(
    prompt: bool = Query(default=False, description="sertakan rendering prompt untuk LLM"),
    services: Services = Depends(get_services),
) -> dict:
    from ai_service.sql.catalog import load_catalog

    catalog = load_catalog()
    dialect = services.settings.sql_backend
    body: dict = {
        "backend": dialect,
        "sql_service": "aktif" if services.sql_service is not None else "tidak ada",
        "views": [
            {
                "name": v.name,
                "modul": v.modul,
                "deskripsi": v.deskripsi,
                "grain": v.grain,
                "unscoped": v.unscoped,
                "scope": v.scope_mapping(dialect),
                "columns": [
                    {"name": c.name, "type": c.type, "deskripsi": c.deskripsi, "nilai": c.nilai}
                    for c in v.columns
                ],
                "contoh": v.contoh,
            }
            for v in catalog.views.values()
        ],
        "issues": catalog.validate(dialect),
    }
    if prompt:
        body["prompt"] = catalog.render_prompt(dialect)
    return body
