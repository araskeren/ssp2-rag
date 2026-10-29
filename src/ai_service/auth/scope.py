"""Port 1:1 `deriveUserScope` BE (turborepo apps/be/src/common/report/surveillance-report.base.ts).

Role diutamakan, lalu fallback ke tipe faskes (subJenis/jenisSarana) bila role bukan role
geografis. Aturan ini satu-satunya sumber kebenaran pembatasan wilayah SQL tool.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

ScopeLevel = Literal["GLOBAL", "PROVINCE", "KABKOTA", "FASKES"]

ROLE_SUPERADMIN = frozenset({"Administrator", "Developer", "Admin Pusdatin"})
ROLE_KEMENKES = frozenset({"Admin Kemenkes", "Operator Kemenkes"})
ROLE_PROVINSI = frozenset({"Admin Provinsi", "Operator Provinsi"})
ROLE_KABKOTA = frozenset({"Admin KabKota", "Operator KabKota"})
ROLE_FASKES = frozenset({"Admin Faskes", "Operator Faskes"})

SUBJENIS_DINKES_PROVINSI = "Dinas Kesehatan Provinsi"
SUBJENIS_DINKES_KABKOTA = frozenset({"Dinas Kesehatan Kabupaten", "Dinas Kesehatan Kota"})
JENIS_KEMENTERIAN_LEMBAGA = "Kementerian & Lembaga"


@dataclass(frozen=True)
class UserScope:
    level: ScopeLevel = "GLOBAL"
    provinsi_kode: str | None = None
    kabupaten_kota_kode: str | None = None
    facility_id: int | None = None

    @property
    def label(self) -> str:
        if self.level == "PROVINCE":
            return f"Provinsi {self.provinsi_kode}"
        if self.level == "KABKOTA":
            return f"Kab/Kota {self.kabupaten_kota_kode}"
        if self.level == "FASKES":
            return f"Faskes #{self.facility_id}"
        return "Nasional (GLOBAL)"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"level": self.level}
        if self.provinsi_kode:
            out["provinsi_kode"] = self.provinsi_kode
        if self.kabupaten_kota_kode:
            out["kabupaten_kota_kode"] = self.kabupaten_kota_kode
        if self.facility_id is not None:
            out["facility_id"] = self.facility_id
        return out

    @classmethod
    def from_level(
        cls,
        level: str,
        provinsi_kode: str | None = None,
        kabupaten_kota_kode: str | None = None,
        facility_id: int | None = None,
    ) -> UserScope:
        if level == "PROVINCE" and provinsi_kode:
            return cls("PROVINCE", provinsi_kode=provinsi_kode)
        if level == "KABKOTA" and kabupaten_kota_kode:
            return cls("KABKOTA", kabupaten_kota_kode=kabupaten_kota_kode)
        if level == "FASKES" and facility_id is not None:
            return cls("FASKES", facility_id=int(facility_id))
        return cls("GLOBAL")


GLOBAL_SCOPE = UserScope("GLOBAL")


def is_dinkes_provinsi(facility: Mapping[str, Any] | None) -> bool:
    return bool(facility) and facility.get("subJenis") == SUBJENIS_DINKES_PROVINSI


def is_dinkes_kabkota(facility: Mapping[str, Any] | None) -> bool:
    return bool(facility) and facility.get("subJenis") in SUBJENIS_DINKES_KABKOTA


def is_kementerian_lembaga(facility: Mapping[str, Any] | None) -> bool:
    return bool(facility) and facility.get("jenisSarana") == JENIS_KEMENTERIAN_LEMBAGA


def derive_user_scope(role_name: str | None, facility: Mapping[str, Any] | None) -> UserScope:
    role = role_name or ""
    facility = facility or None

    if role in ROLE_SUPERADMIN or role in ROLE_KEMENKES:
        return GLOBAL_SCOPE
    # Kementerian & Lembaga = institusi pusat, bukan faskes: user pusat non-hirarki
    # (Viewer, Operator modul) tetap berscope nasional.
    if is_kementerian_lembaga(facility):
        return GLOBAL_SCOPE
    if role in ROLE_PROVINSI or is_dinkes_provinsi(facility):
        kode = (facility or {}).get("provinsiKode")
        return UserScope("PROVINCE", provinsi_kode=kode) if kode else GLOBAL_SCOPE
    if role in ROLE_KABKOTA or is_dinkes_kabkota(facility):
        kode = (facility or {}).get("kabupatenKotaKode")
        return UserScope("KABKOTA", kabupaten_kota_kode=kode) if kode else GLOBAL_SCOPE
    if role in ROLE_FASKES or facility:
        fid = (facility or {}).get("id")
        return UserScope("FASKES", facility_id=int(fid)) if fid is not None else GLOBAL_SCOPE
    return GLOBAL_SCOPE
