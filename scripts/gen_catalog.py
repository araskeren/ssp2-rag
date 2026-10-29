#!/usr/bin/env python
"""Generator katalog semantik: menulis src/ai_service/sql/catalog/*.yaml (jangan edit YAML manual).

Sumber kebenaran pemetaan kolom = entitas TypeORM BE (turborepo/apps/be/src/modules/<modul>/entities)
dan definisi view dn_* data-pipeline (clickhouse/serving). Jalankan: uv run python scripts/gen_catalog.py
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import yaml

CATALOG_DIR = Path(__file__).resolve().parents[1] / "src" / "ai_service" / "sql" / "catalog"


def col(name: str, type_: str, deskripsi: str = "", nilai: list[str] | None = None) -> dict:
    data: dict = {"name": name, "type": type_}
    if deskripsi:
        data["deskripsi"] = deskripsi
    if nilai:
        data["nilai"] = nilai
    return data


SCOPE_RECORDS = {
    "postgres": {
        "facility_id": 'r."facilityId"',
        "provinsi_kode": 'f."provinsiKode"',
        "kabupaten_kota_kode": 'f."kabupatenKotaKode"',
    },
    "clickhouse": {
        "facility_id": "facility_id",
        "provinsi_kode": "facility_provinsi_kode",
        "kabupaten_kota_kode": "facility_kab_kota_kode",
    },
}

RECORD_BASE_COLUMNS = [
    col("record_id", "id", "ID kasus (opaque)"),
    col("record_date", "date", "Tanggal kasus dicatat di aplikasi"),
    col("epi_week_record", "int", "Minggu epidemiologi ISO dari record_date"),
    col("epi_year_record", "int", "Tahun ISO dari record_date"),
]
FACILITY_COLUMNS = [
    col("facility_id", "id", "ID faskes pelapor"),
    col("facility_name", "text", "Nama faskes pelapor"),
    col("facility_provinsi_kode", "text", "Kode provinsi faskes pelapor"),
    col("facility_kab_kota_kode", "text", "Kode kabupaten/kota faskes pelapor"),
    col("facility_provinsi_name", "text", "Nama provinsi faskes pelapor"),
    col("facility_kab_kota_name", "text", "Nama kabupaten/kota faskes pelapor"),
]
PATIENT_COLUMNS = [
    col("patient_sex", "text", "Jenis kelamin pasien", ["MALE", "FEMALE"]),
    col("patient_age_years", "int", "Umur pasien dalam tahun (dihitung hari ini)"),
    col("patient_provinsi_kode", "text", "Kode provinsi domisili pasien"),
    col("patient_kab_kota_kode", "text", "Kode kabupaten/kota domisili pasien"),
    col("patient_provinsi_name", "text", "Nama provinsi domisili pasien"),
    col("patient_kab_kota_name", "text", "Nama kabupaten/kota domisili pasien"),
]
SPECIMEN_COLUMNS = [
    col("specimen_id", "id", "ID spesimen (NULL bila kasus belum punya spesimen)"),
    col("nomor_spesimen", "text", "Nomor label spesimen"),
    col("jenis_spesimen", "text", "Jenis spesimen"),
    col("tujuan_pemeriksaan", "text", "Tujuan pemeriksaan spesimen"),
    col("tanggal_ambil", "date", "Tanggal pengambilan spesimen"),
    col("tanggal_pengiriman", "date", "Tanggal pengiriman spesimen ke laboratorium"),
    col("tanggal_penerimaan", "date", "Tanggal spesimen diterima laboratorium"),
    col("specimen_status", "text", "Status spesimen", ["PENDING", "RECEIVED", "REJECTED"]),
    col("alasan_penolakan", "text", "Alasan spesimen ditolak laboratorium"),
    col("lab_tujuan_id", "id", "ID laboratorium tujuan"),
    col("lab_tujuan_name", "text", "Nama laboratorium tujuan"),
    col("epi_week_ambil", "int", "Minggu epidemiologi ISO tanggal_ambil"),
    col("epi_year_ambil", "int", "Tahun ISO tanggal_ambil"),
]
RESULT_COLUMNS = [
    col("test_result_id", "id", "ID hasil pemeriksaan laboratorium (NULL bila belum ada hasil)"),
    col("jenis_pemeriksaan", "text", "Jenis pemeriksaan laboratorium (mis. RT-PCR)"),
    col("kesimpulan", "text", "Kesimpulan/hasil laboratorium"),
    col("tanggal_periksa", "date", "Tanggal pemeriksaan laboratorium"),
    col("tanggal_hasil", "date", "Tanggal hasil keluar (NULL bila modul tidak mencatat)"),
    col(
        "tanggal_hasil_efektif",
        "date",
        "tanggal_hasil bila ada, bila tidak tanggal_periksa (dipakai laporan)",
    ),
    col("epi_week_hasil", "int", "Minggu epidemiologi ISO tanggal_hasil_efektif"),
    col("epi_year_hasil", "int", "Tahun ISO tanggal_hasil_efektif"),
]

# Ekspresi SQL Postgres default per blok; modul meng-override bila nama kolom entitasnya berbeda.
DEFAULT_SPECIMEN_EXPR = {
    "nomor_spesimen": "s.nomor_spesimen",
    "jenis_spesimen": "s.jenis_spesimen",
    "tujuan_pemeriksaan": "s.tujuan_pemeriksaan",
    "tanggal_ambil": "s.tanggal_ambil",
    "tanggal_pengiriman": "s.tanggal_pengiriman",
    "tanggal_penerimaan": "s.tanggal_penerimaan",
    "specimen_status": "s.status",
    "alasan_penolakan": "s.alasan_penolakan",
    "lab_tujuan_id": "s.lab_tujuan_id",
}
DEFAULT_RESULT_EXPR = {
    "jenis_pemeriksaan": 't."jenisPemeriksaan"',
    "kesimpulan": "t.kesimpulan",
    "tanggal_periksa": "t.tanggal_periksa",
    "tanggal_hasil": "t.tanggal_hasil",
}


def surveilans_sql(
    schema: str, spec: dict[str, str], res: dict[str, str], extra_select: list[tuple[str, str]]
) -> str:
    sp = {**DEFAULT_SPECIMEN_EXPR, **spec}
    rs = {**DEFAULT_RESULT_EXPR, **res}
    efektif = f"COALESCE({rs['tanggal_hasil']}, {rs['tanggal_periksa']})"
    lines = [
        "r.id AS record_id",
        'r."createdAt"::date AS record_date',
        'EXTRACT(WEEK FROM r."createdAt")::int AS epi_week_record',
        'EXTRACT(ISOYEAR FROM r."createdAt")::int AS epi_year_record',
        "r.status AS record_status",
        'r."currentStage" AS current_stage',
        'r."isActive" AS is_active',
        'r."facilityId" AS facility_id',
        "f.name AS facility_name",
        'f."provinsiKode" AS facility_provinsi_kode',
        'f."kabupatenKotaKode" AS facility_kab_kota_kode',
        "fp.name AS facility_provinsi_name",
        "fk.name AS facility_kab_kota_name",
        "p.sex AS patient_sex",
        'EXTRACT(YEAR FROM age(p."birthDate"))::int AS patient_age_years',
        'p."primaryProvinceCode" AS patient_provinsi_kode',
        'p."primaryCityCode" AS patient_kab_kota_kode',
        "pp.name AS patient_provinsi_name",
        "pk.name AS patient_kab_kota_name",
        "s.id AS specimen_id",
        f"{sp['nomor_spesimen']} AS nomor_spesimen",
        f"{sp['jenis_spesimen']} AS jenis_spesimen",
        f"{sp['tujuan_pemeriksaan']} AS tujuan_pemeriksaan",
        f"{sp['tanggal_ambil']} AS tanggal_ambil",
        f"{sp['tanggal_pengiriman']} AS tanggal_pengiriman",
        f"{sp['tanggal_penerimaan']} AS tanggal_penerimaan",
        f"{sp['specimen_status']} AS specimen_status",
        f"{sp['alasan_penolakan']} AS alasan_penolakan",
        f"{sp['lab_tujuan_id']} AS lab_tujuan_id",
        "lg.name AS lab_tujuan_name",
        f"EXTRACT(WEEK FROM {sp['tanggal_ambil']})::int AS epi_week_ambil",
        f"EXTRACT(ISOYEAR FROM {sp['tanggal_ambil']})::int AS epi_year_ambil",
        "t.id AS test_result_id",
        f"{rs['jenis_pemeriksaan']} AS jenis_pemeriksaan",
        f"{rs['kesimpulan']} AS kesimpulan",
        f"{rs['tanggal_periksa']} AS tanggal_periksa",
        f"{rs['tanggal_hasil']} AS tanggal_hasil",
        f"{efektif} AS tanggal_hasil_efektif",
        f"EXTRACT(WEEK FROM {efektif})::int AS epi_week_hasil",
        f"EXTRACT(ISOYEAR FROM {efektif})::int AS epi_year_hasil",
    ] + [f"{expr} AS {alias}" for alias, expr in extra_select]
    select = ",\n  ".join(lines)
    return (
        f"SELECT\n  {select}\n"
        f"FROM {schema}.records r\n"
        f'LEFT JOIN {schema}.specimens s ON s."recordId" = r.id\n'
        f'LEFT JOIN {schema}.test_results t ON t."specimenId" = s.id\n'
        'LEFT JOIN master.patients p ON p.id = r."patientId"\n'
        'LEFT JOIN master.facilities f ON f.id = r."facilityId"\n'
        f"LEFT JOIN master.facilities lg ON lg.id = {sp['lab_tujuan_id']}\n"
        "LEFT JOIN master.regions fp ON fp.code = f.\"provinsiKode\" AND fp.level = 'PROVINCE'\n"
        "LEFT JOIN master.regions fk ON fk.code = f.\"kabupatenKotaKode\" AND fk.level = 'CITY'\n"
        "LEFT JOIN master.regions pp ON pp.code = p.\"primaryProvinceCode\" AND pp.level = 'PROVINCE'\n"
        "LEFT JOIN master.regions pk ON pk.code = p.\"primaryCityCode\" AND pk.level = 'CITY'\n"
        "WHERE {scope_filter}\n"
    )


def surveilans_view(
    *,
    name: str,
    schema: str,
    modul: str,
    deskripsi: str,
    status_values: list[str],
    stage_values: list[str],
    kesimpulan_values: list[str] | None,
    extra_columns: list[dict],
    extra_select: list[tuple[str, str]],
    spec_expr: dict[str, str] | None = None,
    res_expr: dict[str, str] | None = None,
    contoh: list[dict[str, str]],
    catatan: str = "",
) -> dict:
    columns = (
        RECORD_BASE_COLUMNS
        + [
            col("record_status", "text", f"Status kasus {modul}", status_values),
            col("current_stage", "text", "Tahap alur kasus", stage_values),
            col("is_active", "bool", "Kasus aktif (belum dihapus)"),
        ]
        + FACILITY_COLUMNS
        + PATIENT_COLUMNS
        + SPECIMEN_COLUMNS
        + [
            c
            if c["name"] != "kesimpulan" or not kesimpulan_values
            else col("kesimpulan", "text", "Kesimpulan/hasil laboratorium", kesimpulan_values)
            for c in RESULT_COLUMNS
        ]
        + extra_columns
    )
    return {
        "name": name,
        "modul": modul,
        "deskripsi": deskripsi,
        "grain": "satu baris per kasus x spesimen x hasil laboratorium (LEFT JOIN: kasus tanpa spesimen tetap muncul dengan specimen_id NULL)",
        "hitung": {
            "kasus": "COUNT(DISTINCT record_id)",
            "spesimen": "COUNT(DISTINCT specimen_id)",
            "hasil_lab": "COUNT(test_result_id)",
        },
        "columns": columns,
        "scope": SCOPE_RECORDS,
        "contoh": contoh,
        "sql": {
            "postgres": surveilans_sql(schema, spec_expr or {}, res_expr or {}, extra_select),
            "clickhouse": f"SELECT * EXCEPT (patient_name, patient_birth_date) FROM satu_data_penyakit.{name} WHERE {{scope_filter}}\n",
        },
        "catatan": (catatan + " " if catatan else "")
        + "Wilayah pelaporan mengikuti faskes pelapor (facility_*); wilayah domisili pasien ada di patient_*.",
    }


def contoh_standar(view: str, modul: str) -> list[dict[str, str]]:
    return [
        {
            "pertanyaan": f"Berapa jumlah kasus {modul} per provinsi pada tahun 2025?",
            "sql": f"SELECT facility_provinsi_name AS provinsi, COUNT(DISTINCT record_id) AS jumlah_kasus FROM {view} WHERE epi_year_record = 2025 GROUP BY facility_provinsi_name ORDER BY jumlah_kasus DESC",
        },
        {
            "pertanyaan": f"Tren mingguan kasus {modul} tahun 2025",
            "sql": f"SELECT epi_year_record AS tahun, epi_week_record AS minggu_epi, COUNT(DISTINCT record_id) AS jumlah_kasus FROM {view} WHERE epi_year_record = 2025 GROUP BY epi_year_record, epi_week_record ORDER BY tahun, minggu_epi",
        },
    ]


STAGES = ["Pendaftaran", "Laboratorium", "Verifikasi", "Pemulangan", "Selesai"]
KESIMPULAN_PCR = ["POSITIF_COVID", "POSITIF_AGEN_LAIN", "TIDAK_TERIDENTIFIKASI"]

VIEWS: list[dict] = []

VIEWS.append(
    surveilans_view(
        name="dn_sari_surveilans",
        schema="sari",
        modul="SARI (Severe Acute Respiratory Infection)",
        deskripsi="Kasus surveilans SARI (infeksi saluran pernapasan akut berat) beserta spesimen dan hasil laboratoriumnya.",
        status_values=["Suspek SARI", "Probable SARI", "Konfirmasi SARI"],
        stage_values=STAGES,
        kesimpulan_values=KESIMPULAN_PCR,
        extra_columns=[
            col("tanggal_kunjungan", "date", "Tanggal kunjungan/rawat inap pasien"),
            col("tanggal_mulai_gejala", "date", "Tanggal onset gejala"),
            col("suhu_tubuh", "numeric", "Suhu tubuh saat kunjungan (°C)"),
            col(
                "kondisi_akhir",
                "text",
                "Kondisi akhir pasien",
                ["Sembuh / Dipulangkan", "Dirujuk", "Pulang Paksa / APS", "Meninggal Dunia"],
            ),
            col("diagnosa_akhir", "text", "Diagnosis akhir"),
            col("tanggal_keluar", "date", "Tanggal pasien keluar perawatan"),
        ],
        extra_select=[
            ("tanggal_kunjungan", "r.tanggal_kunjungan"),
            ("tanggal_mulai_gejala", "r.tanggal_mulai_gejala"),
            ("suhu_tubuh", "r.suhu_tubuh"),
            ("kondisi_akhir", "r.kondisi_akhir"),
            ("diagnosa_akhir", "r.diagnosa_akhir"),
            ("tanggal_keluar", "r.tanggal_keluar"),
        ],
        contoh=contoh_standar("dn_sari_surveilans", "SARI")
        + [
            {
                "pertanyaan": "Berapa spesimen SARI yang ditolak laboratorium dan apa alasannya?",
                "sql": "SELECT alasan_penolakan, COUNT(DISTINCT specimen_id) AS spesimen_ditolak FROM dn_sari_surveilans WHERE specimen_status = 'REJECTED' GROUP BY alasan_penolakan ORDER BY spesimen_ditolak DESC",
            }
        ],
    )
)
VIEWS.append(
    surveilans_view(
        name="dn_ili_surveilans",
        schema="ili",
        modul="ILI (Influenza-like Illness)",
        deskripsi="Kasus surveilans ILI (penyakit serupa influenza) beserta spesimen dan hasil laboratoriumnya.",
        status_values=["Suspek ILI", "Konfirmasi ILI"],
        stage_values=STAGES,
        kesimpulan_values=KESIMPULAN_PCR,
        extra_columns=[
            col("tanggal_kunjungan", "date", "Tanggal kunjungan pasien"),
            col("tanggal_mulai_gejala", "date", "Tanggal onset gejala"),
            col("suhu_tubuh", "numeric", "Suhu tubuh saat kunjungan (°C)"),
        ],
        extra_select=[
            ("tanggal_kunjungan", "r.tanggal_kunjungan"),
            ("tanggal_mulai_gejala", "r.tanggal_mulai_gejala"),
            ("suhu_tubuh", "r.suhu_tubuh"),
        ],
        contoh=contoh_standar("dn_ili_surveilans", "ILI")
        + [
            {
                "pertanyaan": "Distribusi kesimpulan hasil lab ILI tahun 2025",
                "sql": "SELECT kesimpulan, COUNT(test_result_id) AS jumlah_hasil, COUNT(DISTINCT record_id) AS jumlah_kasus FROM dn_ili_surveilans WHERE test_result_id IS NOT NULL AND epi_year_hasil = 2025 GROUP BY kesimpulan ORDER BY jumlah_hasil DESC",
            }
        ],
    )
)
VIEWS.append(
    surveilans_view(
        name="dn_covid_19_surveilans",
        schema="covid_19",
        modul="COVID-19",
        deskripsi="Kasus surveilans COVID-19 beserta spesimen dan hasil laboratoriumnya.",
        status_values=["Suspek", "Probable", "Konfirmasi", "Pelaku Perjalanan", "Kontak Erat"],
        stage_values=["Pendaftaran", "Laboratorium", "Verifikasi", "PE", "Selesai"],
        kesimpulan_values=KESIMPULAN_PCR,
        extra_columns=[
            col("suhu_tubuh", "numeric", "Suhu tubuh saat skrining (°C)"),
            col("outcome", "text", "Luaran kasus (mis. Sembuh, Meninggal)"),
            col("tanggal_outcome", "date", "Tanggal luaran kasus"),
        ],
        extra_select=[
            ("suhu_tubuh", "r.suhu_tubuh"),
            ("outcome", "r.outcome"),
            ("tanggal_outcome", "r.tanggal_outcome"),
        ],
        contoh=[
            {
                "pertanyaan": "Berapa kasus konfirmasi COVID-19 per provinsi tahun 2025?",
                "sql": "SELECT facility_provinsi_name AS provinsi, COUNT(DISTINCT record_id) AS kasus_konfirmasi FROM dn_covid_19_surveilans WHERE epi_year_record = 2025 AND LOWER(record_status) LIKE '%konfirmasi%' GROUP BY facility_provinsi_name ORDER BY kasus_konfirmasi DESC",
            },
            {
                "pertanyaan": "Berapa kasus COVID-19 yang meninggal tahun ini?",
                "sql": "SELECT COUNT(DISTINCT record_id) AS kasus_meninggal FROM dn_covid_19_surveilans WHERE LOWER(outcome) LIKE '%meninggal%' AND EXTRACT(YEAR FROM tanggal_outcome) = EXTRACT(YEAR FROM CURRENT_DATE)",
            },
        ],
    )
)
VIEWS.append(
    surveilans_view(
        name="dn_ispa_surveilans",
        schema="ispa",
        modul="ISPA/Pneumonia",
        deskripsi="Kasus surveilans ISPA (pneumonia balita dan ISPA berat) beserta spesimen dan hasil laboratoriumnya.",
        status_values=["Suspek", "Probable", "Konfirmasi"],
        stage_values=STAGES,
        kesimpulan_values=KESIMPULAN_PCR,
        extra_columns=[
            col(
                "klasifikasi",
                "text",
                "Klasifikasi ISPA (mis. Pneumonia, Pneumonia Berat, Batuk Bukan Pneumonia)",
            ),
            col("tanggal_kunjungan", "date", "Tanggal kunjungan pasien"),
            col("tanggal_mulai_gejala", "date", "Tanggal onset gejala"),
            col("suhu_tubuh", "numeric", "Suhu tubuh saat kunjungan (°C)"),
            col("kondisi_akhir", "text", "Kondisi akhir pasien"),
            col("diagnosa_akhir", "text", "Diagnosis akhir"),
            col("tanggal_keluar", "date", "Tanggal pasien keluar perawatan"),
        ],
        extra_select=[
            ("klasifikasi", "r.klasifikasi"),
            ("tanggal_kunjungan", "r.tanggal_kunjungan"),
            ("tanggal_mulai_gejala", "r.tanggal_mulai_gejala"),
            ("suhu_tubuh", "r.suhu_tubuh"),
            ("kondisi_akhir", "r.kondisi_akhir"),
            ("diagnosa_akhir", "r.diagnosa_akhir"),
            ("tanggal_keluar", "r.tanggal_keluar"),
        ],
        contoh=contoh_standar("dn_ispa_surveilans", "ISPA")
        + [
            {
                "pertanyaan": "Berapa kasus pneumonia balita per kabupaten/kota tahun 2025?",
                "sql": "SELECT facility_kab_kota_name AS kabupaten_kota, COUNT(DISTINCT record_id) AS jumlah_kasus FROM dn_ispa_surveilans WHERE epi_year_record = 2025 AND LOWER(klasifikasi) LIKE '%pneumonia%' AND patient_age_years < 5 GROUP BY facility_kab_kota_name ORDER BY jumlah_kasus DESC",
            }
        ],
    )
)
VIEWS.append(
    surveilans_view(
        name="dn_monkey_pox_surveilans",
        schema="monkey_pox",
        modul="Mpox (monkeypox)",
        deskripsi="Kasus surveilans Mpox beserta spesimen dan hasil laboratoriumnya. Modul ini tidak mencatat tanggal hasil (dipakai tanggal periksa).",
        status_values=["Suspek", "Probable", "Konfirmasi", "Kontak Erat"],
        stage_values=STAGES,
        kesimpulan_values=None,
        extra_columns=[
            col("suhu_tubuh", "numeric", "Suhu tubuh saat skrining (°C)"),
            col("status_kontak", "text", "Status kontak erat"),
            col("outcome", "text", "Luaran kasus"),
            col("tanggal_outcome", "date", "Tanggal luaran kasus"),
            col("tanggal_selesai_pemantauan", "date", "Tanggal selesai pemantauan"),
        ],
        extra_select=[
            ("suhu_tubuh", "r.suhu_tubuh"),
            ("status_kontak", "r.status_kontak"),
            ("outcome", "r.outcome"),
            ("tanggal_outcome", "r.tanggal_outcome"),
            ("tanggal_selesai_pemantauan", "r.tanggal_selesai_pemantauan"),
        ],
        res_expr={"tanggal_hasil": "CAST(NULL AS date)"},
        contoh=contoh_standar("dn_monkey_pox_surveilans", "Mpox"),
    )
)
VIEWS.append(
    surveilans_view(
        name="dn_pd3i_surveilans",
        schema="pd3i",
        modul="PD3I (Penyakit yang Dapat Dicegah Dengan Imunisasi)",
        deskripsi="Kasus surveilans PD3I (campak-rubela, difteri, pertusis, AFP/polio, tetanus neonatorum, dll.) beserta spesimen dan hasil laboratoriumnya.",
        status_values=["Suspek", "Probable", "Konfirmasi", "Discarded"],
        stage_values=STAGES,
        kesimpulan_values=None,
        extra_columns=[
            col("syndrome_type", "text", "Tipe sindrom/penyakit PD3I yang dilaporkan"),
            col("penyakit", "text", "Nama penyakit PD3I"),
            col("no_epid", "text", "Nomor epidemiologi kasus"),
            col("status_klb", "bool", "Bagian dari KLB"),
            col("verification_status", "text", "Status verifikasi kasus"),
            col("status_imunisasi", "bool", "Riwayat imunisasi lengkap"),
            col("tgl_muncul_ruam", "date", "Tanggal muncul ruam"),
            col("tgl_demam", "date", "Tanggal mulai demam"),
            col("onset_gejala", "date", "Tanggal onset gejala"),
            col("tanggal_lapor_faskes", "date", "Tanggal laporan faskes"),
            col("kondisi_saat_ini", "text", "Kondisi pasien saat ini"),
            col("patient_status", "text", "Status pasien (mis. hidup/meninggal)"),
            col("mortality_event", "bool", "Kasus kematian"),
            col("etiology", "text", "Etiologi hasil laboratorium"),
        ],
        extra_select=[
            ("syndrome_type", 'r."syndromeType"'),
            ("penyakit", "r.penyakit"),
            ("no_epid", 'r."noEpid"'),
            ("status_klb", 'r."statusKlb"'),
            ("verification_status", 'r."verificationStatus"'),
            ("status_imunisasi", 'r."statusImunisasi"'),
            ("tgl_muncul_ruam", 'r."tglMunculRuam"'),
            ("tgl_demam", 'r."tglDemam"'),
            ("onset_gejala", 'r."onsetOfSymptomsDate"'),
            ("tanggal_lapor_faskes", 'r."faskesReportDate"'),
            ("kondisi_saat_ini", 'r."kondisiSaatIni"'),
            ("patient_status", 'r."patientStatus"'),
            ("mortality_event", 'r."mortalityEvent"'),
            ("etiology", "t.etiology"),
        ],
        spec_expr={
            "jenis_spesimen": 's."specimenType"',
            "tujuan_pemeriksaan": 's."examinationPurpose"',
            "tanggal_ambil": 's."dateCollected"',
        },
        res_expr={
            "kesimpulan": "t.result",
            "tanggal_periksa": 't."testDate"',
            "tanggal_hasil": "CAST(NULL AS date)",
        },
        contoh=contoh_standar("dn_pd3i_surveilans", "PD3I")
        + [
            {
                "pertanyaan": "Berapa kasus PD3I per penyakit tahun 2025?",
                "sql": "SELECT penyakit, COUNT(DISTINCT record_id) AS jumlah_kasus FROM dn_pd3i_surveilans WHERE epi_year_record = 2025 GROUP BY penyakit ORDER BY jumlah_kasus DESC",
            }
        ],
    )
)
VIEWS.append(
    surveilans_view(
        name="dn_pie_surveilans",
        schema="pie",
        modul="PIE (Penyakit Infeksi Emerging)",
        deskripsi="Kasus surveilans penyakit infeksi emerging (sindrom demam, pernapasan, neurologis, dsb.) beserta spesimen dan hasil laboratoriumnya.",
        status_values=["Suspek", "Probable", "Konfirmasi", "Discarded"],
        stage_values=STAGES,
        kesimpulan_values=None,
        extra_columns=[
            col("syndrome_type", "text", "Tipe sindrom yang dilaporkan"),
            col("verification_status", "text", "Status verifikasi kasus"),
            col("suhu_tubuh", "numeric", "Suhu tubuh (°C)"),
            col("diagnosis", "text", "Diagnosis"),
            col("decision", "text", "Keputusan verifikasi"),
            col("onset_gejala", "date", "Tanggal onset gejala"),
            col("tanggal_masuk_rs", "date", "Tanggal masuk rumah sakit"),
            col("tanggal_lapor_faskes", "date", "Tanggal laporan faskes"),
            col("patient_status", "text", "Status pasien"),
            col("mortality_event", "bool", "Kasus kematian"),
            col("etiology", "text", "Etiologi hasil laboratorium"),
        ],
        extra_select=[
            ("syndrome_type", 'r."syndromeType"'),
            ("verification_status", 'r."verificationStatus"'),
            ("suhu_tubuh", 'r."bodyTemperature"'),
            ("diagnosis", "r.diagnosis"),
            ("decision", "r.decision"),
            ("onset_gejala", 'r."onsetOfSymptomsDate"'),
            ("tanggal_masuk_rs", 'r."hospitalAdmissionDate"'),
            ("tanggal_lapor_faskes", 'r."faskesReportDate"'),
            ("patient_status", 'r."patientStatus"'),
            ("mortality_event", 'r."mortalityEvent"'),
            ("etiology", "t.etiology"),
        ],
        spec_expr={
            "jenis_spesimen": 'COALESCE(s.jenis_spesimen, s."specimenType")',
            "tanggal_ambil": 'COALESCE(s.tanggal_ambil, s."dateCollected")',
        },
        res_expr={
            "kesimpulan": "t.result",
            "tanggal_periksa": 't."testDate"',
            "tanggal_hasil": "CAST(NULL AS date)",
        },
        contoh=contoh_standar("dn_pie_surveilans", "PIE"),
    )
)
VIEWS.append(
    surveilans_view(
        name="dn_hpv_surveilans",
        schema="hpv",
        modul="HPV / kanker leher rahim",
        deskripsi="Skrining dan surveilans kanker leher rahim (IVA, HPV DNA) beserta spesimen dan hasil laboratoriumnya.",
        status_values=["Skrining", "Positif", "Negatif", "Rujuk"],
        stage_values=STAGES,
        kesimpulan_values=None,
        extra_columns=[
            col("tanggal_skrining", "date", "Tanggal skrining"),
            col("metode_deteksi_dini", "text", "Metode deteksi dini (IVA / HPV DNA)"),
            col("hasil_iva", "text", "Hasil IVA"),
            col("tanggal_pemeriksaan_iva", "date", "Tanggal pemeriksaan IVA"),
            col("tindak_lanjut_iva", "text", "Tindak lanjut IVA"),
            col("status_hiv", "text", "Status HIV"),
            col("riwayat_ims", "bool", "Riwayat IMS"),
            col("kontrasepsi_saat_ini", "text", "Kontrasepsi saat ini"),
            col("riwayat_imunisasi_hpv", "bool", "Riwayat imunisasi HPV"),
        ],
        extra_select=[
            ("tanggal_skrining", 'r."tanggalSkrining"'),
            ("metode_deteksi_dini", 'r."metodeDeteksiDini"'),
            ("hasil_iva", 'r."hasilIva"'),
            ("tanggal_pemeriksaan_iva", 'r."tanggalPemeriksaanIva"'),
            ("tindak_lanjut_iva", 'r."tindakLanjutIva"'),
            ("status_hiv", 'r."statusHiv"'),
            ("riwayat_ims", 'r."riwayatIms"'),
            ("kontrasepsi_saat_ini", 'r."kontrasepsiSaatIni"'),
            ("riwayat_imunisasi_hpv", 'r."riwayatImunisasiHpv"'),
        ],
        spec_expr={
            "nomor_spesimen": 's."nomorSpesimen"',
            "jenis_spesimen": 's."jenisSpesimen"',
            "tujuan_pemeriksaan": "CAST(NULL AS varchar)",
            "tanggal_ambil": 's."tanggalPenarikanSpesimen"',
            "tanggal_pengiriman": 's."tanggalPengiriman"',
            "tanggal_penerimaan": 's."tanggalPenerimaan"',
            "alasan_penolakan": 's."catatanPenerimaan"',
            "lab_tujuan_id": 's."laboratoriumTujuanId"',
        },
        res_expr={
            "kesimpulan": 't."hasilTes"',
            "tanggal_periksa": 't."tanggalPemeriksaan"',
            "tanggal_hasil": 't."tanggalHasil"',
        },
        contoh=[
            {
                "pertanyaan": "Berapa perempuan yang diskrining IVA per provinsi tahun 2025 dan berapa yang positif?",
                "sql": "SELECT facility_provinsi_name AS provinsi, COUNT(DISTINCT record_id) AS jumlah_skrining, COUNT(DISTINCT CASE WHEN LOWER(hasil_iva) LIKE '%positif%' THEN record_id END) AS iva_positif FROM dn_hpv_surveilans WHERE EXTRACT(YEAR FROM tanggal_skrining) = 2025 GROUP BY facility_provinsi_name ORDER BY jumlah_skrining DESC",
            }
        ],
        catatan="Entitas HPV memakai nama kolom camelCase berbeda dari modul lain (sudah dipetakan).",
    )
)


def aggregate_view(
    name: str, schema: str, modul: str, counters: list[tuple[str, str, str]], ch_view: str
) -> dict:
    counter_cols = [col(alias, "int", desc) for alias, _, desc in counters]
    pg_counters = ",\n  ".join(f'ar."{src}" AS {alias}' for alias, src, _ in counters)
    ch_counters = ", ".join(f"{src} AS {alias}" for alias, src, _ in counters)
    return {
        "name": name,
        "modul": modul,
        "deskripsi": f"Laporan agregat mingguan {modul} per faskes sentinel (satu baris per faskes per minggu epidemiologi). Gunakan SUM() atas kolom total_*; jangan COUNT(DISTINCT) untuk angka kasus.",
        "grain": "satu baris per faskes per minggu epidemiologi (epi_year, epi_week)",
        "hitung": {"jumlah_laporan": "COUNT(report_id)", "total_kasus": "SUM(total_cases)"},
        "columns": [
            col("report_id", "id", "ID laporan agregat"),
            col("facility_id", "id", "ID faskes pelapor"),
            col("facility_name", "text", "Nama faskes pelapor"),
            col("facility_provinsi_kode", "text", "Kode provinsi faskes"),
            col("facility_kab_kota_kode", "text", "Kode kabupaten/kota faskes"),
            col("facility_provinsi_name", "text", "Nama provinsi faskes"),
            col("facility_kab_kota_name", "text", "Nama kabupaten/kota faskes"),
            col("epi_week", "int", "Minggu epidemiologi laporan"),
            col("epi_year", "int", "Tahun epidemiologi laporan"),
            col("status", "text", "Status laporan", ["DRAFT", "SUBMITTED", "VERIFIED", "REJECTED"]),
            col("verified_at", "datetime", "Waktu verifikasi"),
            col("rejected_at", "datetime", "Waktu penolakan"),
            col("created_at", "datetime", "Waktu laporan dibuat"),
        ]
        + counter_cols,
        "scope": {
            "postgres": {
                "facility_id": 'ar."facilityId"',
                "provinsi_kode": 'f."provinsiKode"',
                "kabupaten_kota_kode": 'f."kabupatenKotaKode"',
            },
            "clickhouse": {
                "facility_id": "facilityId",
                "provinsi_kode": "facility_provinsi_kode",
                "kabupaten_kota_kode": "facility_kab_kota_kode",
            },
        },
        "contoh": [
            {
                "pertanyaan": f"Total kasus {modul} dari laporan agregat per provinsi tahun 2025",
                "sql": f"SELECT facility_provinsi_name AS provinsi, SUM(total_cases) AS total_kasus, COUNT(report_id) AS jumlah_laporan FROM {name} WHERE epi_year = 2025 GROUP BY facility_provinsi_name ORDER BY total_kasus DESC",
            }
        ],
        "sql": {
            "postgres": textwrap.dedent(f"""\
                SELECT
                  ar.id AS report_id,
                  ar."facilityId" AS facility_id,
                  f.name AS facility_name,
                  f."provinsiKode" AS facility_provinsi_kode,
                  f."kabupatenKotaKode" AS facility_kab_kota_kode,
                  fp.name AS facility_provinsi_name,
                  fk.name AS facility_kab_kota_name,
                  ar."epiWeek"::int AS epi_week,
                  ar."epiYear"::int AS epi_year,
                  ar.status AS status,
                  ar."verifiedAt" AS verified_at,
                  ar."rejectedAt" AS rejected_at,
                  ar."createdAt" AS created_at,
                  {pg_counters}
                FROM {schema}.aggregate_reports ar
                LEFT JOIN master.facilities f ON f.id = ar."facilityId"
                LEFT JOIN master.regions fp ON fp.code = f."provinsiKode" AND fp.level = 'PROVINCE'
                LEFT JOIN master.regions fk ON fk.code = f."kabupatenKotaKode" AND fk.level = 'CITY'
                WHERE {{scope_filter}}
            """),
            "clickhouse": f"SELECT id AS report_id, facilityId AS facility_id, facility_name, facility_provinsi_kode, facility_kab_kota_kode, facility_provinsi_name, facility_kab_kota_name, epiWeek AS epi_week, epiYear AS epi_year, status, verifiedAt AS verified_at, rejectedAt AS rejected_at, createdAt AS created_at, {ch_counters} FROM satu_data_penyakit.{ch_view} WHERE {{scope_filter}}\n",
        },
        "catatan": "Angka laporan agregat adalah input manual faskes, terpisah dari kasus individual (dn_*_surveilans).",
    }


VIEWS.append(
    aggregate_view(
        "dn_sari_aggregate_reports",
        "sari",
        "SARI",
        [
            ("total_cases", "totalSariCases", "Total kasus SARI yang dilaporkan minggu itu"),
            ("total_specimen_cases", "totalSpecimenCases", "Total kasus yang diambil spesimennya"),
            ("total_inpatient", "totalInpatient", "Total pasien rawat inap"),
        ],
        "dn_sari_aggregate_reports",
    )
)
VIEWS.append(
    aggregate_view(
        "dn_ili_aggregate_reports",
        "ili",
        "ILI",
        [
            ("total_cases", "totalIliCases", "Total kasus ILI yang dilaporkan minggu itu"),
            ("total_specimen_cases", "totalSpecimenCases", "Total kasus yang diambil spesimennya"),
            ("total_visits", "totalVisits", "Total kunjungan"),
        ],
        "dn_ili_aggregate_reports",
    )
)

VIEWS.append(
    {
        "name": "dim_facilities",
        "modul": "Master faskes",
        "deskripsi": "Daftar fasilitas pelayanan kesehatan (faskes) beserta wilayahnya. Pakai untuk menghitung jumlah faskes atau mencari nama/jenis faskes.",
        "grain": "satu baris per faskes",
        "columns": [
            col("facility_id", "id", "ID faskes"),
            col("facility_name", "text", "Nama faskes"),
            col(
                "jenis_sarana",
                "text",
                "Jenis sarana (mis. Rumah Sakit, Pusat Kesehatan Masyarakat, Laboratorium Kesehatan, Dinas Kesehatan, Kementerian & Lembaga)",
            ),
            col(
                "sub_jenis",
                "text",
                "Sub jenis (mis. Puskesmas, Dinas Kesehatan Provinsi, Dinas Kesehatan Kota)",
            ),
            col("kategori_fasyankes", "text", "Kategori fasyankes"),
            col("status_sarana", "text", "Status operasional sarana"),
            col("is_active", "bool", "Faskes aktif"),
            col("provinsi_kode", "text", "Kode provinsi"),
            col("kab_kota_kode", "text", "Kode kabupaten/kota"),
            col("kecamatan_kode", "text", "Kode kecamatan"),
            col("provinsi_name", "text", "Nama provinsi"),
            col("kab_kota_name", "text", "Nama kabupaten/kota"),
        ],
        "scope": {
            "postgres": {
                "facility_id": "f.id",
                "provinsi_kode": 'f."provinsiKode"',
                "kabupaten_kota_kode": 'f."kabupatenKotaKode"',
            },
            "clickhouse": {
                "facility_id": "id",
                "provinsi_kode": "provinsiKode",
                "kabupaten_kota_kode": "kabupatenKotaKode",
            },
        },
        "contoh": [
            {
                "pertanyaan": "Berapa jumlah puskesmas per provinsi?",
                "sql": "SELECT provinsi_name AS provinsi, COUNT(facility_id) AS jumlah_puskesmas FROM dim_facilities WHERE sub_jenis = 'Puskesmas' AND is_active GROUP BY provinsi_name ORDER BY jumlah_puskesmas DESC",
            }
        ],
        "sql": {
            "postgres": textwrap.dedent("""\
            SELECT
              f.id AS facility_id,
              f.name AS facility_name,
              f."jenisSarana" AS jenis_sarana,
              f."subJenis" AS sub_jenis,
              f."kategoriFasyankes" AS kategori_fasyankes,
              f."statusSarana" AS status_sarana,
              f."isActive" AS is_active,
              f."provinsiKode" AS provinsi_kode,
              f."kabupatenKotaKode" AS kab_kota_kode,
              f."kecamatanKode" AS kecamatan_kode,
              fp.name AS provinsi_name,
              fk.name AS kab_kota_name
            FROM master.facilities f
            LEFT JOIN master.regions fp ON fp.code = f."provinsiKode" AND fp.level = 'PROVINCE'
            LEFT JOIN master.regions fk ON fk.code = f."kabupatenKotaKode" AND fk.level = 'CITY'
            WHERE {scope_filter}
        """),
            "clickhouse": "SELECT id AS facility_id, name AS facility_name, jenisSarana AS jenis_sarana, subJenis AS sub_jenis, kategoriFasyankes AS kategori_fasyankes, statusSarana AS status_sarana, isActive AS is_active, provinsiKode AS provinsi_kode, kabupatenKotaKode AS kab_kota_kode, kecamatanKode AS kecamatan_kode, provinsi_name, kab_kota_name FROM satu_data_penyakit.facilities_geo WHERE {scope_filter}\n",
        },
    }
)
VIEWS.append(
    {
        "name": "dim_regions",
        "modul": "Master wilayah",
        "deskripsi": "Referensi wilayah administratif (provinsi, kabupaten/kota, kecamatan, desa/kelurahan) dengan kode dan nama. Tidak dibatasi wilayah pengguna.",
        "grain": "satu baris per wilayah",
        "unscoped": True,
        "columns": [
            col("region_id", "id", "ID wilayah"),
            col("code", "text", "Kode wilayah (mis. 33 = provinsi, 33.28 = kabupaten/kota)"),
            col("name", "text", "Nama wilayah"),
            col("level", "text", "Tingkat wilayah", ["PROVINCE", "CITY", "DISTRICT", "VILLAGE"]),
            col("parent_id", "id", "ID wilayah induk"),
            col("is_active", "bool", "Wilayah aktif"),
        ],
        "scope": {"postgres": {}, "clickhouse": {}},
        "contoh": [
            {
                "pertanyaan": "Apa saja kabupaten/kota di provinsi dengan kode 33?",
                "sql": "SELECT code, name FROM dim_regions WHERE level = 'CITY' AND code LIKE '33.%' ORDER BY name",
            }
        ],
        "sql": {
            "postgres": textwrap.dedent("""\
            SELECT
              r.id AS region_id,
              r.code AS code,
              r.name AS name,
              r.level AS level,
              r."parentId" AS parent_id,
              r."isActive" AS is_active
            FROM master.regions r
            WHERE {scope_filter}
        """),
            "clickhouse": "SELECT id AS region_id, code, name, level, parentId AS parent_id, isActive AS is_active FROM master.regions_final WHERE {scope_filter}\n",
        },
    }
)


VIEWS.append(
    {
        "name": "dn_klb_events",
        "modul": "Epi-Intel / KLB keracunan pangan (SKDR)",
        "deskripsi": "Kejadian Luar Biasa (KLB) keracunan pangan dari modul Epidemiologi Intelijen: satu baris per kejadian dengan jumlah terpapar, sakit, dirawat, meninggal. Wilayah tersimpan sebagai nama (bukan kode); scope wilayah memakai faskes pelapor bila ada.",
        "grain": "satu baris per kejadian KLB",
        "hitung": {
            "jumlah_kejadian": "COUNT(event_id)",
            "total_sakit": "SUM(jumlah_sakit)",
            "total_meninggal": "SUM(jumlah_meninggal)",
        },
        "columns": [
            col("event_id", "id", "ID kejadian"),
            col("sumber_data", "text", "Sumber data (mis. SKDR)"),
            col("nomor_epid", "text", "Nomor epidemiologi kejadian"),
            col("minggu_epidemiologi", "int", "Minggu epidemiologi yang dilaporkan"),
            col("tanggal_kejadian", "date", "Tanggal kejadian"),
            col("tanggal_lapor", "date", "Tanggal laporan"),
            col("epi_week_kejadian", "int", "Minggu ISO dari tanggal_kejadian"),
            col("epi_year_kejadian", "int", "Tahun ISO dari tanggal_kejadian"),
            col("provinsi", "text", "Nama provinsi kejadian"),
            col("kabupaten_kota", "text", "Nama kabupaten/kota kejadian"),
            col("kecamatan", "text", "Nama kecamatan"),
            col("desa_kelurahan", "text", "Nama desa/kelurahan"),
            col("nama_puskesmas", "text", "Puskesmas pelapor"),
            col("tempat_kejadian", "text", "Tempat kejadian"),
            col("jenis_pangan_dicurigai", "text", "Jenis pangan yang dicurigai"),
            col("jumlah_terpapar", "int", "Jumlah orang terpapar"),
            col("jumlah_sakit", "int", "Jumlah orang sakit"),
            col("jumlah_dirawat", "int", "Jumlah dirawat"),
            col("jumlah_meninggal", "int", "Jumlah meninggal"),
            col("status", "text", "Status penanganan", ["BARU", "INVESTIGASI", "SELESAI"]),
            col("facility_id", "id", "ID faskes pelapor (bisa NULL)"),
            col("facility_provinsi_kode", "text", "Kode provinsi faskes pelapor"),
            col("facility_kab_kota_kode", "text", "Kode kabupaten/kota faskes pelapor"),
            col("created_at", "datetime", "Waktu dicatat"),
        ],
        "scope": {
            "postgres": {
                "facility_id": 'ke."facilityId"',
                "provinsi_kode": 'f."provinsiKode"',
                "kabupaten_kota_kode": 'f."kabupatenKotaKode"',
            },
            "clickhouse": {
                "facility_id": "facilityId",
                "provinsi_kode": "facility_provinsi_kode",
                "kabupaten_kota_kode": "facility_kab_kota_kode",
            },
        },
        "contoh": [
            {
                "pertanyaan": "Berapa KLB keracunan pangan per provinsi tahun 2025 dan total yang meninggal?",
                "sql": "SELECT provinsi, COUNT(event_id) AS jumlah_kejadian, SUM(jumlah_sakit) AS total_sakit, SUM(jumlah_meninggal) AS total_meninggal FROM dn_klb_events WHERE epi_year_kejadian = 2025 GROUP BY provinsi ORDER BY jumlah_kejadian DESC",
            }
        ],
        "sql": {
            "postgres": textwrap.dedent(
                """\
                SELECT
                  ke.id AS event_id,
                  ke."sumberData" AS sumber_data,
                  ke."nomorEpid" AS nomor_epid,
                  ke."mingguEpidemiologi"::int AS minggu_epidemiologi,
                  ke."tanggalKejadian" AS tanggal_kejadian,
                  ke."tanggalLapor" AS tanggal_lapor,
                  EXTRACT(WEEK FROM ke."tanggalKejadian")::int AS epi_week_kejadian,
                  EXTRACT(ISOYEAR FROM ke."tanggalKejadian")::int AS epi_year_kejadian,
                  ke.provinsi AS provinsi,
                  ke."kabupatenKota" AS kabupaten_kota,
                  ke.kecamatan AS kecamatan,
                  ke."desaKelurahan" AS desa_kelurahan,
                  ke."namaPuskesmas" AS nama_puskesmas,
                  ke."tempatKejadian" AS tempat_kejadian,
                  ke."jenisPanganDicurigai" AS jenis_pangan_dicurigai,
                  ke."jumlahTerpapar" AS jumlah_terpapar,
                  ke."jumlahSakit" AS jumlah_sakit,
                  ke."jumlahDirawat" AS jumlah_dirawat,
                  ke."jumlahMeninggal" AS jumlah_meninggal,
                  ke.status AS status,
                  ke."facilityId" AS facility_id,
                  f."provinsiKode" AS facility_provinsi_kode,
                  f."kabupatenKotaKode" AS facility_kab_kota_kode,
                  ke."createdAt" AS created_at
                FROM epi_intel.klb_events ke
                LEFT JOIN master.facilities f ON f.id = ke."facilityId"
                WHERE {scope_filter}
                """
            ),
            "clickhouse": "SELECT id AS event_id, sumberData AS sumber_data, nomorEpid AS nomor_epid, mingguEpidemiologi AS minggu_epidemiologi, tanggalKejadian AS tanggal_kejadian, tanggalLapor AS tanggal_lapor, epi_week_kejadian, epi_year_kejadian, provinsi, kabupatenKota AS kabupaten_kota, kecamatan, desaKelurahan AS desa_kelurahan, namaPuskesmas AS nama_puskesmas, tempatKejadian AS tempat_kejadian, jenisPanganDicurigai AS jenis_pangan_dicurigai, jumlahTerpapar AS jumlah_terpapar, jumlahSakit AS jumlah_sakit, jumlahDirawat AS jumlah_dirawat, jumlahMeninggal AS jumlah_meninggal, status, facilityId AS facility_id, facility_provinsi_kode, facility_kab_kota_kode, createdAt AS created_at FROM satu_data_penyakit.dn_klb_events WHERE {scope_filter}\n",
        },
        "catatan": "Kejadian tanpa facilityId tidak terlihat oleh pengguna ber-scope wilayah (hanya GLOBAL).",
    }
)


class _Dumper(yaml.SafeDumper):
    pass


def _str_presenter(dumper: yaml.SafeDumper, data: str):  # noqa: ANN202
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(str, _str_presenter)


def main() -> None:
    CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    for old in CATALOG_DIR.glob("*.yaml"):
        old.unlink()
    for view in VIEWS:
        path = CATALOG_DIR / f"{view['name']}.yaml"
        header = (
            f"# Virtual view '{view['name']}' — katalog semantik Text-to-SQL ai-service.\n"
            "# DIHASILKAN scripts/gen_catalog.py — jangan edit manual; ubah generator lalu jalankan ulang.\n"
            "# Placeholder {scope_filter} diisi sistem (filter wilayah pengguna, parameter terikat).\n"
        )
        path.write_text(
            header
            + yaml.dump(view, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=1000),
            encoding="utf-8",
        )
        print(f"{path.name}: {len(view['columns'])} kolom")


if __name__ == "__main__":
    main()
