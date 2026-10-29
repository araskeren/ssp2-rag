"""Resolusi identitas → Identity(principal, platform, ai_role, scope).

Web  : JWT HS256 lokal → profil BE (role, permission, faskes) → ai_role + scope;
       baris allowed_channels(web, <user id>) dapat menimpa (grant/deny + mempersempit scope).
Intern: allowed_channels(platform, channel_id) → role + scope; tak terdaftar mengikuti
       INTERNAL_UNKNOWN_CHANNEL_POLICY (public | deny).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from ai_service.auth.jwt import JwtClaims, JwtError, verify_hs256
from ai_service.auth.profile_client import ProfileClient
from ai_service.auth.scope import GLOBAL_SCOPE, UserScope, derive_user_scope
from ai_service.config import Settings
from ai_service.errors import ApiError
from ai_service.logging import get_logger

log = get_logger(__name__)

Platform = Literal["web", "wa", "tg"]
AiRole = Literal["public", "analyst"]

ChannelLookup = Callable[[str, str], Awaitable[Any | None]]


@dataclass(frozen=True)
class Identity:
    principal_id: str
    platform: Platform
    ai_role: AiRole
    scope: UserScope = GLOBAL_SCOPE
    reason: str = ""
    email: str | None = None
    display_name: str | None = None
    role_name: str | None = None
    session_id: str | None = None
    known_channel: bool = True
    permissions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def scope_label(self) -> str:
        return self.scope.label

    @property
    def is_analyst(self) -> bool:
        return self.ai_role == "analyst"

    def to_dict(self) -> dict[str, Any]:
        return {
            "principal_id": self.principal_id,
            "platform": self.platform,
            "ai_role": self.ai_role,
            "analyst_reason": self.reason,
            "scope": self.scope.to_dict(),
            "scope_label": self.scope_label,
            "email": self.email,
            "display_name": self.display_name,
            "role_name": self.role_name,
            "known_channel": self.known_channel,
        }


def normalize_channel_id(platform: str, channel_id: str) -> str:
    value = channel_id.strip()
    if platform == "wa":
        digits = re.sub(r"\D", "", value)
        if digits.startswith("0"):
            digits = "62" + digits[1:]
        return digits
    return value


def _channel_scope(row: Any) -> UserScope:
    return UserScope.from_level(
        getattr(row, "scope_level", "GLOBAL") or "GLOBAL",
        provinsi_kode=getattr(row, "provinsi_kode", None),
        kabupaten_kota_kode=getattr(row, "kabupaten_kota_kode", None),
        facility_id=getattr(row, "facility_id", None),
    )


class IdentityResolver:
    def __init__(
        self,
        settings: Settings,
        profile_client: ProfileClient,
        channel_lookup: ChannelLookup,
    ) -> None:
        self._settings = settings
        self._profiles = profile_client
        self._lookup = channel_lookup

    # ------------------------------------------------------------------ web
    async def resolve_web(self, token: str, client_ip: str | None = None) -> Identity:
        try:
            claims = verify_hs256(token, self._settings.jwt_secret)
        except JwtError as exc:
            raise ApiError(401, str(exc)) from exc

        result = await self._profiles.get_profile(token, claims.session_id, client_ip)
        if result.status == "unauthorized":
            raise ApiError(401, "sesi tidak valid atau telah dicabut")
        if result.status == "unavailable" or result.profile is None:
            log.warning("BE tidak tersedia; pengguna diperlakukan public", sub=claims.sub)
            return Identity(
                principal_id=claims.sub,
                platform="web",
                ai_role="public",
                reason="be_unavailable",
                email=claims.email,
                session_id=claims.session_id,
            )
        return await self._from_profile(claims, result.profile)

    async def _from_profile(self, claims: JwtClaims, profile: dict[str, Any]) -> Identity:
        role = profile.get("role") or {}
        role_name = role.get("name") if isinstance(role, dict) else None
        facility = profile.get("facility") if isinstance(profile.get("facility"), dict) else None
        permissions = tuple(str(p) for p in (profile.get("permissions") or []))

        ai_role: AiRole = "public"
        reason = "role_public"
        if role_name and role_name in self._settings.analyst_role_set:
            ai_role, reason = "analyst", f"role:{role_name}"
        elif self._settings.ai_analyst_permission in permissions:
            ai_role, reason = "analyst", f"permission:{self._settings.ai_analyst_permission}"
        scope = derive_user_scope(role_name, facility)

        row = await self._lookup("web", claims.sub)
        if row is not None and getattr(row, "is_active", True):
            row_role = getattr(row, "role", "public")
            ai_role = "analyst" if row_role == "analyst" else "public"
            reason = f"allowed_channels:{row_role}"
            if (getattr(row, "scope_level", "GLOBAL") or "GLOBAL") != "GLOBAL":
                scope = _channel_scope(row)

        return Identity(
            principal_id=claims.sub,
            platform="web",
            ai_role=ai_role,
            scope=scope,
            reason=reason,
            email=profile.get("email") or claims.email,
            display_name=profile.get("name"),
            role_name=role_name,
            session_id=claims.session_id,
            permissions=permissions,
        )

    # ------------------------------------------------------------------ internal (wa/tg)
    async def resolve_internal(
        self, platform: str, channel_id: str, display_name: str | None = None
    ) -> Identity:
        if platform not in ("wa", "tg"):
            raise ApiError(400, "platform harus 'wa' atau 'tg'")
        normalized = normalize_channel_id(platform, channel_id)
        if not normalized:
            raise ApiError(400, "channel_id wajib diisi")

        row = await self._lookup(platform, normalized)
        if row is not None and getattr(row, "is_active", True):
            row_role = getattr(row, "role", "public")
            return Identity(
                principal_id=normalized,
                platform=platform,  # type: ignore[arg-type]
                ai_role="analyst" if row_role == "analyst" else "public",
                scope=_channel_scope(row),
                reason=f"allowed_channels:{row_role}",
                display_name=display_name or getattr(row, "display_name", None),
                known_channel=True,
            )
        if self._settings.internal_unknown_channel_policy == "deny":
            raise ApiError(403, "kanal tidak terdaftar")
        return Identity(
            principal_id=normalized,
            platform=platform,  # type: ignore[arg-type]
            ai_role="public",
            reason="unknown_channel",
            display_name=display_name,
            known_channel=False,
        )
