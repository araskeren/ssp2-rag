"""GET /api/v1/me — identitas pengguna web: ai_role + scope (FE memakai ini untuk gate mode Analisis)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ai_service.api.deps import get_web_identity
from ai_service.api.schemas import MeOut, ScopeOut
from ai_service.auth.identity import Identity

router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.get("/me", response_model=MeOut, response_model_exclude_none=True)
async def me(identity: Identity = Depends(get_web_identity)) -> MeOut:
    return MeOut(
        user_id=identity.principal_id,
        email=identity.email,
        display_name=identity.display_name,
        role_name=identity.role_name,
        ai_role=identity.ai_role,
        analyst_reason=identity.reason,
        scope=ScopeOut(**identity.scope.to_dict()),
        scope_label=identity.scope_label,
    )
