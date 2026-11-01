"""Akses data registry LLM (llm_providers, llm_models, llm_assignments)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select

from ai_service.db.engine import Database
from ai_service.db.models import AllowedChannel, LlmAssignment, LlmModel, LlmProvider


@dataclass
class RegistryRows:
    providers: list[LlmProvider]
    models: list[LlmModel]
    assignments: list[LlmAssignment]


class LlmRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------ snapshot
    async def snapshot(self) -> RegistryRows:
        async with self._db.session() as session:
            providers = list((await session.execute(select(LlmProvider))).scalars())
            models = list((await session.execute(select(LlmModel))).scalars())
            assignments = list((await session.execute(select(LlmAssignment))).scalars())
            return RegistryRows(providers, models, assignments)

    # ------------------------------------------------------------------ generik
    async def _get(self, cls: Any, pk: int) -> Any:
        async with self._db.session() as session:
            return await session.get(cls, pk)

    async def _create(self, cls: Any, data: dict[str, Any]) -> Any:
        async with self._db.session() as session:
            row = cls(**data)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def _update(self, cls: Any, pk: int, data: dict[str, Any]) -> Any:
        async with self._db.session() as session:
            row = await session.get(cls, pk)
            if row is None:
                return None
            for key, value in data.items():
                setattr(row, key, value)
            row.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(row)
            return row

    async def _delete(self, cls: Any, pk: int) -> bool:
        async with self._db.session() as session:
            row = await session.get(cls, pk)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def _by_name(self, cls: Any, name: str) -> Any:
        async with self._db.session() as session:
            stmt = select(cls).where(cls.name == name)
            return (await session.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------------ provider
    async def list_providers(self) -> list[LlmProvider]:
        async with self._db.session() as session:
            rows = await session.execute(select(LlmProvider).order_by(LlmProvider.id))
            return list(rows.scalars())

    async def get_provider(self, provider_id: int) -> LlmProvider | None:
        return await self._get(LlmProvider, provider_id)

    async def get_provider_by_name(self, name: str) -> LlmProvider | None:
        return await self._by_name(LlmProvider, name)

    async def create_provider(self, data: dict[str, Any]) -> LlmProvider:
        return await self._create(LlmProvider, data)

    async def update_provider(self, provider_id: int, data: dict[str, Any]) -> LlmProvider | None:
        return await self._update(LlmProvider, provider_id, data)

    async def delete_provider(self, provider_id: int) -> bool:
        return await self._delete(LlmProvider, provider_id)

    async def provider_model_count(self, provider_id: int) -> int:
        async with self._db.session() as session:
            stmt = (
                select(func.count())
                .select_from(LlmModel)
                .where(LlmModel.provider_id == provider_id)
            )
            return int((await session.execute(stmt)).scalar_one())

    # ------------------------------------------------------------------ model
    async def list_models(
        self, provider_id: int | None = None, kind: str | None = None
    ) -> list[LlmModel]:
        async with self._db.session() as session:
            stmt = select(LlmModel).order_by(LlmModel.id)
            if provider_id is not None:
                stmt = stmt.where(LlmModel.provider_id == provider_id)
            if kind:
                stmt = stmt.where(LlmModel.kind == kind)
            return list((await session.execute(stmt)).scalars())

    async def get_model(self, model_id: int) -> LlmModel | None:
        return await self._get(LlmModel, model_id)

    async def get_model_by_name(self, name: str) -> LlmModel | None:
        return await self._by_name(LlmModel, name)

    async def create_model(self, data: dict[str, Any]) -> LlmModel:
        return await self._create(LlmModel, data)

    async def update_model(self, model_id: int, data: dict[str, Any]) -> LlmModel | None:
        return await self._update(LlmModel, model_id, data)

    async def delete_model(self, model_id: int) -> bool:
        return await self._delete(LlmModel, model_id)

    async def model_usage(self, model_id: int) -> dict[str, int]:
        async with self._db.session() as session:
            assignments = (
                await session.execute(
                    select(func.count())
                    .select_from(LlmAssignment)
                    .where(LlmAssignment.model_id == model_id)
                )
            ).scalar_one()
            channels = (
                await session.execute(
                    select(func.count())
                    .select_from(AllowedChannel)
                    .where(AllowedChannel.llm_model_id == model_id)
                )
            ).scalar_one()
            return {"assignments": int(assignments), "channels": int(channels)}

    # ------------------------------------------------------------------ assignment
    async def list_assignments(self) -> list[LlmAssignment]:
        async with self._db.session() as session:
            stmt = select(LlmAssignment).order_by(LlmAssignment.purpose, LlmAssignment.ai_role)
            return list((await session.execute(stmt)).scalars())

    @staticmethod
    def _target(purpose: str, ai_role: str | None) -> Any:
        cond = LlmAssignment.purpose == purpose
        if ai_role is None:
            return cond & LlmAssignment.ai_role.is_(None)
        return cond & (LlmAssignment.ai_role == ai_role)

    async def upsert_assignment(
        self, purpose: str, ai_role: str | None, model_id: int
    ) -> LlmAssignment:
        async with self._db.session() as session:
            row = (
                await session.execute(select(LlmAssignment).where(self._target(purpose, ai_role)))
            ).scalar_one_or_none()
            if row is None:
                row = LlmAssignment(purpose=purpose, ai_role=ai_role, model_id=model_id)
                session.add(row)
            else:
                row.model_id = model_id
                row.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(row)
            return row

    async def delete_assignment(self, purpose: str, ai_role: str | None) -> bool:
        async with self._db.session() as session:
            result = await session.execute(
                delete(LlmAssignment).where(self._target(purpose, ai_role))
            )
            await session.commit()
            return bool(result.rowcount)
