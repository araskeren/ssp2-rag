"""Repository akses data (SQLAlchemy async) untuk tabel milik ai-service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm.attributes import flag_modified

from ai_service.db.engine import Database
from ai_service.db.models import (
    AllowedChannel,
    ChatMessage,
    ChatSession,
    Document,
    IngestJob,
    SqlAuditLog,
)


class ChannelRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, platform: str, channel_id: str) -> AllowedChannel | None:
        async with self._db.session() as session:
            stmt = select(AllowedChannel).where(
                AllowedChannel.platform == platform, AllowedChannel.channel_id == channel_id
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(self, channel_pk: int) -> AllowedChannel | None:
        async with self._db.session() as session:
            return await session.get(AllowedChannel, channel_pk)

    async def list(
        self, platform: str | None = None, page: int = 1, limit: int = 50
    ) -> tuple[list[AllowedChannel], int]:
        async with self._db.session() as session:
            base = select(AllowedChannel)
            count = select(func.count()).select_from(AllowedChannel)
            if platform:
                base = base.where(AllowedChannel.platform == platform)
                count = count.where(AllowedChannel.platform == platform)
            total = (await session.execute(count)).scalar_one()
            rows = (
                await session.execute(
                    base.order_by(AllowedChannel.id.desc()).offset((page - 1) * limit).limit(limit)
                )
            ).scalars()
            return list(rows), int(total)

    async def create(self, data: dict[str, Any]) -> AllowedChannel:
        async with self._db.session() as session:
            row = AllowedChannel(**data)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def update(self, channel_pk: int, data: dict[str, Any]) -> AllowedChannel | None:
        async with self._db.session() as session:
            row = await session.get(AllowedChannel, channel_pk)
            if row is None:
                return None
            for key, value in data.items():
                setattr(row, key, value)
            row.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(row)
            return row

    async def delete(self, channel_pk: int) -> bool:
        async with self._db.session() as session:
            row = await session.get(AllowedChannel, channel_pk)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True


class DocumentRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, document_id: uuid.UUID) -> Document | None:
        async with self._db.session() as session:
            return await session.get(Document, document_id)

    async def get_by_sha(self, sha256: str) -> Document | None:
        async with self._db.session() as session:
            stmt = select(Document).where(Document.sha256 == sha256)
            return (await session.execute(stmt)).scalar_one_or_none()

    async def create(self, **fields: Any) -> Document:
        async with self._db.session() as session:
            row = Document(**fields)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def update(self, document_id: uuid.UUID, **fields: Any) -> Document | None:
        async with self._db.session() as session:
            row = await session.get(Document, document_id)
            if row is None:
                return None
            for key, value in fields.items():
                setattr(row, key, value)
            row.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(row)
            return row

    async def list(
        self,
        status: str | None = None,
        q: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> tuple[list[Document], int]:
        async with self._db.session() as session:
            base = select(Document)
            count = select(func.count()).select_from(Document)
            conditions = []
            if status:
                conditions.append(Document.status == status)
            else:
                conditions.append(Document.status != "deleted")
            if q:
                pattern = f"%{q}%"
                conditions.append(Document.filename.ilike(pattern) | Document.title.ilike(pattern))
            for cond in conditions:
                base = base.where(cond)
                count = count.where(cond)
            total = (await session.execute(count)).scalar_one()
            rows = (
                await session.execute(
                    base.order_by(Document.created_at.desc())
                    .offset((page - 1) * limit)
                    .limit(limit)
                )
            ).scalars()
            return list(rows), int(total)

    async def active_ids(self) -> list[uuid.UUID]:
        async with self._db.session() as session:
            stmt = (
                select(Document.id)
                .where(Document.status != "deleted")
                .order_by(Document.created_at)
            )
            return [row[0] for row in (await session.execute(stmt)).all()]


class JobRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self, total_files: int, items: list[dict[str, Any]], requested_by: str | None
    ) -> IngestJob:
        async with self._db.session() as session:
            row = IngestJob(
                status="queued", total_files=total_files, items=items, requested_by=requested_by
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def get(self, job_id: uuid.UUID) -> IngestJob | None:
        async with self._db.session() as session:
            return await session.get(IngestJob, job_id)

    async def update(self, job_id: uuid.UUID, **fields: Any) -> IngestJob | None:
        async with self._db.session() as session:
            row = await session.get(IngestJob, job_id)
            if row is None:
                return None
            for key, value in fields.items():
                setattr(row, key, value)
            if "items" in fields:
                flag_modified(row, "items")
            await session.commit()
            await session.refresh(row)
            return row

    async def sweep_stale(self) -> int:
        async with self._db.session() as session:
            stmt = (
                update(IngestJob)
                .where(IngestJob.status.in_(("queued", "running")))
                .values(
                    status="failed",
                    error="proses terputus saat service restart",
                    finished_at=datetime.now(UTC),
                )
            )
            result = await session.execute(stmt)
            await session.commit()
            return int(result.rowcount or 0)


class ChatRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_session(self, session_id: str) -> ChatSession | None:
        async with self._db.session() as session:
            return await session.get(ChatSession, session_id)

    async def create_session(
        self,
        session_id: str,
        platform: str,
        principal_id: str,
        ai_role: str,
        title: str | None = None,
    ) -> ChatSession:
        async with self._db.session() as session:
            row = ChatSession(
                session_id=session_id,
                platform=platform,
                principal_id=principal_id,
                ai_role=ai_role,
                title=title,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def list_sessions(
        self, platform: str, principal_id: str, limit: int = 50
    ) -> list[ChatSession]:
        async with self._db.session() as session:
            stmt = (
                select(ChatSession)
                .where(ChatSession.platform == platform, ChatSession.principal_id == principal_id)
                .order_by(ChatSession.last_message_at.desc())
                .limit(limit)
            )
            return list((await session.execute(stmt)).scalars())

    async def delete_session(self, session_id: str) -> bool:
        async with self._db.session() as session:
            row = await session.get(ChatSession, session_id)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def list_messages(self, session_id: str, limit: int = 100) -> list[ChatMessage]:
        async with self._db.session() as session:
            stmt = (
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
                .limit(limit)
            )
            return list((await session.execute(stmt)).scalars())

    async def last_messages(self, session_id: str, count: int) -> list[ChatMessage]:
        async with self._db.session() as session:
            stmt = (
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                .limit(count)
            )
            rows = list((await session.execute(stmt)).scalars())
            rows.reverse()
            return rows

    async def add_messages(
        self,
        session_id: str,
        user_content: str,
        assistant: dict[str, Any],
        *,
        title: str | None = None,
    ) -> ChatMessage:
        # Timestamp eksplisit: now() Postgres sama untuk seluruh transaksi, jadi urutan
        # user → asisten dijamin dengan selisih 1 ms.
        now = datetime.now(UTC)
        assistant = {**assistant, "created_at": now + timedelta(milliseconds=1)}
        async with self._db.session() as session:
            session.add(
                ChatMessage(
                    session_id=session_id, role="user", content=user_content, created_at=now
                )
            )
            await session.flush()
            reply = ChatMessage(session_id=session_id, role="assistant", **assistant)
            session.add(reply)
            chat_session = await session.get(ChatSession, session_id)
            if chat_session is not None:
                chat_session.message_count = (chat_session.message_count or 0) + 2
                chat_session.last_message_at = datetime.now(UTC)
                if title and not chat_session.title:
                    chat_session.title = title
            await session.commit()
            await session.refresh(reply)
            return reply


class AuditRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, **fields: Any) -> SqlAuditLog:
        async with self._db.session() as session:
            row = SqlAuditLog(**fields)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def list(
        self,
        *,
        status: str | None = None,
        principal_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> tuple[list[SqlAuditLog], int]:
        async with self._db.session() as session:
            base = select(SqlAuditLog)
            count = select(func.count()).select_from(SqlAuditLog)
            conditions = []
            if status:
                conditions.append(SqlAuditLog.status == status)
            if principal_id:
                conditions.append(SqlAuditLog.principal_id == principal_id)
            if since:
                conditions.append(SqlAuditLog.created_at >= since)
            if until:
                conditions.append(SqlAuditLog.created_at <= until)
            for cond in conditions:
                base = base.where(cond)
                count = count.where(cond)
            total = (await session.execute(count)).scalar_one()
            rows = (
                await session.execute(
                    base.order_by(SqlAuditLog.created_at.desc())
                    .offset((page - 1) * limit)
                    .limit(limit)
                )
            ).scalars()
            return list(rows), int(total)
