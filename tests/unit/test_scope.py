"""Tabel paritas dengan deriveUserScope BE (surveillance-report.base.ts)."""

import pytest

from ai_service.auth.scope import UserScope, derive_user_scope

KEMENKES = {
    "id": 1,
    "jenisSarana": "Kementerian & Lembaga",
    "subJenis": None,
    "provinsiKode": "31",
    "kabupatenKotaKode": "31.71",
}
DINKES_PROV = {
    "id": 2,
    "jenisSarana": "Dinas Kesehatan",
    "subJenis": "Dinas Kesehatan Provinsi",
    "provinsiKode": "33",
    "kabupatenKotaKode": "33.74",
}
DINKES_KAB = {
    "id": 4,
    "jenisSarana": "Dinas Kesehatan",
    "subJenis": "Dinas Kesehatan Kabupaten",
    "provinsiKode": "33",
    "kabupatenKotaKode": "33.28",
}
DINKES_KOTA = {
    "id": 5,
    "jenisSarana": "Dinas Kesehatan",
    "subJenis": "Dinas Kesehatan Kota",
    "provinsiKode": "33",
    "kabupatenKotaKode": "33.74",
}
PUSKESMAS = {
    "id": 3,
    "jenisSarana": "Pusat Kesehatan Masyarakat",
    "subJenis": "Puskesmas",
    "provinsiKode": "33",
    "kabupatenKotaKode": "33.28",
}
TANPA_KODE = {
    "id": 9,
    "jenisSarana": "Dinas Kesehatan",
    "subJenis": "Dinas Kesehatan Provinsi",
    "provinsiKode": None,
}


@pytest.mark.parametrize(
    ("role", "facility", "expected"),
    [
        ("Administrator", PUSKESMAS, UserScope("GLOBAL")),
        ("Developer", None, UserScope("GLOBAL")),
        ("Admin Pusdatin", DINKES_PROV, UserScope("GLOBAL")),
        ("Admin Kemenkes", PUSKESMAS, UserScope("GLOBAL")),
        ("Operator Kemenkes", None, UserScope("GLOBAL")),
        ("Viewer", KEMENKES, UserScope("GLOBAL")),
        ("Operator Sari", KEMENKES, UserScope("GLOBAL")),
        ("Admin Provinsi", DINKES_PROV, UserScope("PROVINCE", provinsi_kode="33")),
        ("Operator Provinsi", PUSKESMAS, UserScope("PROVINCE", provinsi_kode="33")),
        ("Viewer", DINKES_PROV, UserScope("PROVINCE", provinsi_kode="33")),
        ("Admin Provinsi", TANPA_KODE, UserScope("GLOBAL")),
        ("Admin KabKota", DINKES_KAB, UserScope("KABKOTA", kabupaten_kota_kode="33.28")),
        ("Operator KabKota", PUSKESMAS, UserScope("KABKOTA", kabupaten_kota_kode="33.28")),
        ("Eksekutif KabKota", DINKES_KOTA, UserScope("KABKOTA", kabupaten_kota_kode="33.74")),
        ("Admin Faskes", PUSKESMAS, UserScope("FASKES", facility_id=3)),
        ("Operator Faskes", PUSKESMAS, UserScope("FASKES", facility_id=3)),
        ("Eksekutif Faskes", PUSKESMAS, UserScope("FASKES", facility_id=3)),
        ("Viewer", PUSKESMAS, UserScope("FASKES", facility_id=3)),
        ("Admin Faskes", None, UserScope("GLOBAL")),
        ("Viewer", None, UserScope("GLOBAL")),
        (None, PUSKESMAS, UserScope("FASKES", facility_id=3)),
    ],
)
def test_derive_user_scope_parity(role, facility, expected):
    assert derive_user_scope(role, facility) == expected


def test_scope_label_and_dict():
    assert UserScope("GLOBAL").label == "Nasional (GLOBAL)"
    assert UserScope("PROVINCE", provinsi_kode="33").to_dict() == {
        "level": "PROVINCE",
        "provinsi_kode": "33",
    }
    assert UserScope.from_level("FASKES", facility_id=7) == UserScope("FASKES", facility_id=7)
    assert UserScope.from_level("FASKES") == UserScope("GLOBAL")
