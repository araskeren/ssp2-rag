"""Ingest dokumen: dedup sha256 → simpan asli → parse → chunk → embed → simpan; status job di DB."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_service.config import Settings
from ai_service.db.models import Document, IngestJob
from ai_service.db.repositories import DocumentRepo, JobRepo
from ai_service.logging import get_logger
from ai_service.rag.chunking import chunk_document
from ai_service.rag.embeddings import EmbeddingService
from ai_service.rag.parsers import (
    ParsedDocument,
    ParseError,
    extension_of,
    is_supported,
    parse_bytes,
)
from ai_service.rag.store import ChunkStore

log = get_logger(__name__)


class IngestError(Exception):
    pass


@dataclass
class UploadedFile:
    filename: str
    data: bytes


@dataclass
class IngestOptions:
    title: str | None = None
    source: str | None = None
    tags: list[str] = field(default_factory=list)
    replace: bool = False
    requested_by: str | None = None


class IngestService:
    def __init__(
        self,
        settings: Settings,
        documents: DocumentRepo,
        jobs: JobRepo,
        store: ChunkStore,
        embeddings: EmbeddingService,
    ) -> None:
        self._settings = settings
        self._documents = documents
        self._jobs = jobs
        self._store = store
        self._embeddings = embeddings
        self._tasks: set[asyncio.Task[Any]] = set()
        self._upload_dir = Path(settings.upload_dir)

    # ------------------------------------------------------------------ util
    def validate_file(self, file: UploadedFile) -> None:
        if not is_supported(file.filename):
            raise IngestError(f"{file.filename}: format tidak didukung (pdf, txt, md, docx)")
        limit = self._settings.ingest_max_file_mb * 1024 * 1024
        if len(file.data) > limit:
            raise IngestError(f"{file.filename}: melebihi {self._settings.ingest_max_file_mb} MB")
        if not file.data:
            raise IngestError(f"{file.filename}: berkas kosong")

    async def _save_original(self, sha256: str, filename: str, data: bytes) -> str:
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        path = self._upload_dir / f"{sha256}{extension_of(filename)}"
        await asyncio.to_thread(path.write_bytes, data)
        return str(path)

    # ------------------------------------------------------------------ submit
    async def submit(
        self, files: list[UploadedFile], options: IngestOptions, *, sync: bool = False
    ) -> IngestJob:
        for file in files:
            self.validate_file(file)

        items: list[dict[str, Any]] = []
        plan: list[tuple[uuid.UUID, UploadedFile]] = []
        for file in files:
            sha = hashlib.sha256(file.data).hexdigest()
            existing = await self._documents.get_by_sha(sha)
            if existing is not None and existing.status != "deleted" and not options.replace:
                items.append(
                    {
                        "filename": file.filename,
                        "document_id": str(existing.id),
                        "status": "duplicate",
                    }
                )
                continue
            storage_path = await self._save_original(sha, file.filename, file.data)
            fields = {
                "filename": file.filename,
                "mime_type": _mime_of(file.filename),
                "size_bytes": len(file.data),
                "title": options.title if len(files) == 1 else None,
                "source": options.source,
                "tags": list(options.tags),
                "status": "pending",
                "error": None,
                "storage_path": storage_path,
                "uploaded_by": options.requested_by,
            }
            if existing is not None:
                doc = await self._documents.update(existing.id, **fields)
            else:
                doc = await self._documents.create(sha256=sha, content="", **fields)
            assert doc is not None
            items.append(
                {"filename": file.filename, "document_id": str(doc.id), "status": "queued"}
            )
            plan.append((doc.id, file))

        job = await self._jobs.create(
            total_files=len(files), items=items, requested_by=options.requested_by
        )
        if not plan:
            await self._jobs.update(job.id, status="done", finished_at=datetime.now(UTC))
        elif sync:
            await self._run_job(job.id, plan, options)
        else:
            task = asyncio.create_task(self._run_job(job.id, plan, options))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        refreshed = await self._jobs.get(job.id)
        assert refreshed is not None
        return refreshed

    async def _run_job(
        self, job_id: uuid.UUID, plan: list[tuple[uuid.UUID, UploadedFile]], options: IngestOptions
    ) -> None:
        await self._jobs.update(job_id, status="running", started_at=datetime.now(UTC))
        job = await self._jobs.get(job_id)
        items = list(job.items) if job else []
        done = failed = 0
        for doc_id, file in plan:
            try:
                await self.process_document(doc_id, file.filename, file.data, title=options.title)
                status, error = "indexed", None
                done += 1
            except Exception as exc:  # noqa: BLE001 — satu berkas gagal tidak menghentikan job
                status, error = "failed", str(exc)[:500]
                failed += 1
                log.warning("ingest berkas gagal", filename=file.filename, error=error)
                await self._documents.update(doc_id, status="failed", error=error)
            for item in items:
                if item.get("document_id") == str(doc_id):
                    item["status"] = status
                    if error:
                        item["error"] = error
            await self._jobs.update(job_id, items=items, done_files=done, failed_files=failed)
        await self._jobs.update(
            job_id,
            status="done" if failed == 0 else ("failed" if done == 0 else "done"),
            finished_at=datetime.now(UTC),
        )

    # ------------------------------------------------------------------ proses
    async def process_document(
        self, document_id: uuid.UUID, filename: str, data: bytes, *, title: str | None = None
    ) -> int:
        try:
            parsed = await asyncio.to_thread(parse_bytes, filename, data)
        except ParseError as exc:
            raise IngestError(str(exc)) from exc
        count = await self._index_parsed(document_id, parsed, title or parsed.title or filename)
        await self._documents.update(
            document_id, content=parsed.full_text, page_count=parsed.page_count
        )
        return count

    async def _index_parsed(
        self, document_id: uuid.UUID, parsed: ParsedDocument, title: str
    ) -> int:
        chunks = chunk_document(
            parsed, self._settings.rag_chunk_size, self._settings.rag_chunk_overlap
        )
        if not chunks:
            raise IngestError("tidak ada teks yang bisa dipotong")
        vectors = await self._embeddings.embed_texts([c.content for c in chunks])
        count = await self._store.replace_document_chunks(document_id, chunks, vectors)
        await self._documents.update(
            document_id,
            title=title,
            chunk_count=count,
            status="indexed",
            error=None,
            indexed_at=datetime.now(UTC),
        )
        return count

    async def reindex_document(self, document_id: uuid.UUID) -> int:
        doc = await self._documents.get(document_id)
        if doc is None or doc.status == "deleted":
            raise IngestError("dokumen tidak ditemukan")
        if not doc.content:
            raise IngestError("dokumen belum memiliki teks tersimpan; unggah ulang")
        parsed = ParsedDocument.from_full_text(doc.content, doc.mime_type, doc.title)
        return await self._index_parsed(document_id, parsed, doc.title or doc.filename)

    async def reindex_all(self) -> dict[str, int]:
        ids = await self._documents.active_ids()
        ok = failed = 0
        for doc_id in ids:
            try:
                await self.reindex_document(doc_id)
                ok += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                log.warning("reindex gagal", document_id=str(doc_id), error=str(exc))
                await self._documents.update(doc_id, status="failed", error=str(exc)[:500])
        return {"ok": ok, "failed": failed}

    async def delete_document(self, document_id: uuid.UUID) -> bool:
        doc = await self._documents.get(document_id)
        if doc is None or doc.status == "deleted":
            return False
        await self._store.delete_document_chunks(document_id)
        await self._documents.update(document_id, status="deleted", chunk_count=0)
        if doc.storage_path:
            path = Path(doc.storage_path)
            try:
                await asyncio.to_thread(path.unlink, True)
            except OSError:
                pass
        return True

    async def sweep_stale_jobs(self) -> int:
        return await self._jobs.sweep_stale()


def _mime_of(filename: str) -> str:
    from ai_service.rag.parsers import SUPPORTED_EXTENSIONS

    return SUPPORTED_EXTENSIONS.get(extension_of(filename), "application/octet-stream")


def document_to_dict(doc: Document) -> dict[str, Any]:
    return {
        "id": str(doc.id),
        "filename": doc.filename,
        "mime_type": doc.mime_type,
        "sha256": doc.sha256,
        "size_bytes": doc.size_bytes,
        "title": doc.title,
        "source": doc.source,
        "tags": list(doc.tags or []),
        "page_count": doc.page_count,
        "chunk_count": doc.chunk_count,
        "status": doc.status,
        "error": doc.error,
        "uploaded_by": doc.uploaded_by,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
        "indexed_at": doc.indexed_at.isoformat() if doc.indexed_at else None,
    }


def job_to_dict(job: IngestJob) -> dict[str, Any]:
    return {
        "job_id": str(job.id),
        "status": job.status,
        "total_files": job.total_files,
        "done_files": job.done_files,
        "failed_files": job.failed_files,
        "items": list(job.items or []),
        "requested_by": job.requested_by,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
