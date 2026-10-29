"""Katalog semantik: virtual view (YAML) yang dilihat LLM, divalidasi saat boot."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlglot
import yaml
from sqlglot import exp

CATALOG_DIR = Path(__file__).resolve().parent / "catalog"
SCOPE_KEYS = ("facility_id", "provinsi_kode", "kabupaten_kota_kode")
SCOPE_PLACEHOLDER = "{scope_filter}"


class CatalogError(Exception):
    pass


@dataclass
class ColumnDef:
    name: str
    type: str = "text"
    deskripsi: str = ""
    nilai: list[str] = field(default_factory=list)


@dataclass
class ViewDef:
    name: str
    modul: str
    deskripsi: str
    columns: list[ColumnDef]
    sql: dict[str, str]
    scope: dict[str, dict[str, str]] = field(default_factory=dict)
    unscoped: bool = False
    grain: str = ""
    hitung: dict[str, str] = field(default_factory=dict)
    contoh: list[dict[str, str]] = field(default_factory=list)
    catatan: str = ""

    def sql_for(self, dialect: str) -> str:
        try:
            return self.sql[dialect]
        except KeyError as exc:
            raise CatalogError(f"view {self.name} tidak punya SQL untuk dialek {dialect}") from exc

    def scope_mapping(self, dialect: str) -> dict[str, str]:
        return self.scope.get(dialect, {})

    @property
    def column_names(self) -> set[str]:
        return {c.name for c in self.columns}


@dataclass
class Catalog:
    views: dict[str, ViewDef]

    @property
    def names(self) -> set[str]:
        return set(self.views)

    @property
    def column_map(self) -> dict[str, set[str]]:
        return {name: view.column_names for name, view in self.views.items()}

    def get(self, name: str) -> ViewDef | None:
        return self.views.get(name)

    def validate(self, dialect: str) -> list[str]:
        """Daftar masalah (kosong = valid). Dipanggil saat boot dan `ai-service catalog check`."""
        from ai_service.sql.guard import GuardError, validate_sql

        issues: list[str] = []
        for view in self.views.values():
            prefix = f"view {view.name}"
            if dialect not in view.sql:
                issues.append(f"{prefix}: tidak ada SQL untuk dialek {dialect}")
                continue
            raw = view.sql[dialect]
            if SCOPE_PLACEHOLDER not in raw:
                issues.append(
                    f"{prefix}: SQL {dialect} tidak memuat placeholder {SCOPE_PLACEHOLDER}"
                )
            try:
                parsed = sqlglot.parse_one(raw.replace(SCOPE_PLACEHOLDER, "TRUE"), read=dialect)
            except sqlglot.errors.ParseError as exc:
                issues.append(f"{prefix}: SQL {dialect} tidak dapat diparse: {exc}")
                continue
            if dialect == "postgres" and isinstance(parsed, exp.Select):
                projected = set(parsed.named_selects)
                missing = sorted(view.column_names - projected)
                if missing:
                    issues.append(
                        f"{prefix}: kolom katalog tidak diproyeksikan SQL: {', '.join(missing)}"
                    )
            if not view.unscoped:
                mapping = view.scope_mapping(dialect)
                missing_keys = [k for k in SCOPE_KEYS if k not in mapping]
                if missing_keys:
                    issues.append(
                        f"{prefix}: pemetaan scope {dialect} kurang: {', '.join(missing_keys)}"
                    )
            if not view.columns:
                issues.append(f"{prefix}: daftar kolom kosong")
            for i, contoh in enumerate(view.contoh, start=1):
                try:
                    validate_sql(
                        contoh.get("sql", ""),
                        dialect,
                        self.names,
                        max_rows=200,
                        view_columns=self.column_map,
                    )
                except GuardError as exc:
                    issues.append(f"{prefix}: contoh #{i} ditolak guard: {exc}")
        return issues

    def select_views(self, question: str) -> list[ViewDef]:
        """Pilih view yang relevan dengan pertanyaan berdasarkan kata kunci modul; tanpa kecocokan → semua.

        View tanpa modul penyakit (dim_*) selalu disertakan sebagai referensi.
        """
        text_q = question.lower()
        matched: list[ViewDef] = []
        for view in self.views.values():
            keywords = {k for k in view_keywords(view) if len(k) >= 3}
            if any(k in text_q for k in keywords):
                matched.append(view)
        if not matched:
            return list(self.views.values())
        dims = [v for v in self.views.values() if v.name.startswith("dim_") and v not in matched]
        return matched + dims

    def render_prompt(
        self, dialect: str, max_examples: int = 2, views: list[ViewDef] | None = None
    ) -> str:
        parts: list[str] = []
        for view in views if views is not None else self.views.values():
            lines = [f"### {view.name} — {view.modul}", view.deskripsi.strip()]
            if view.grain:
                lines.append(f"Grain: {view.grain}")
            if view.hitung:
                lines.append(
                    "Cara menghitung: " + "; ".join(f"{k} = {v}" for k, v in view.hitung.items())
                )
            if view.unscoped:
                lines.append("Referensi (tidak dibatasi wilayah).")
            lines.append("Kolom:")
            for col in view.columns:
                desc = f" — {col.deskripsi}" if col.deskripsi else ""
                nilai = f" (nilai: {', '.join(col.nilai)})" if col.nilai else ""
                lines.append(f"- {col.name} ({col.type}){desc}{nilai}")
            if view.catatan:
                lines.append(f"Catatan: {view.catatan.strip()}")
            for contoh in view.contoh[:max_examples]:
                lines.append(
                    f"Contoh: {contoh.get('pertanyaan', '')}\n  {contoh.get('sql', '').strip()}"
                )
            parts.append("\n".join(lines))
        return "\n\n".join(parts)


MODULE_ALIASES: dict[str, tuple[str, ...]] = {
    "sari": ("sari",),
    "ili": ("ili", "influenza"),
    "covid_19": ("covid", "korona", "corona", "sars-cov"),
    "ispa": ("ispa", "pneumonia"),
    "monkey_pox": ("mpox", "monkeypox", "monkey pox", "cacar monyet"),
    "pd3i": (
        "pd3i",
        "campak",
        "rubela",
        "difteri",
        "pertusis",
        "polio",
        "afp",
        "tetanus",
        "imunisasi",
    ),
    "pie": ("pie", "infeksi emerging", "emerging"),
    "hpv": ("hpv", "kanker leher rahim", "serviks", "iva"),
    "klb": ("klb", "keracunan", "kejadian luar biasa", "skdr"),
}


def view_keywords(view: ViewDef) -> set[str]:
    words: set[str] = set()
    for key, aliases in MODULE_ALIASES.items():
        if key in view.name:
            words.update(aliases)
    if "aggregate" in view.name:
        words.update({"agregat", "laporan mingguan"})
    if view.name.startswith("dim_facilities"):
        words.update({"faskes", "puskesmas", "rumah sakit", "fasilitas", "laboratorium"})
    if view.name.startswith("dim_regions"):
        words.update({"provinsi", "kabupaten", "kota", "wilayah", "kecamatan"})
    return words


def _view_from_dict(data: dict[str, Any], source: Path) -> ViewDef:
    try:
        columns = [
            ColumnDef(
                name=str(c["name"]),
                type=str(c.get("type", "text")),
                deskripsi=str(c.get("deskripsi", "") or ""),
                nilai=[str(v) for v in (c.get("nilai") or [])],
            )
            for c in data["columns"]
        ]
        return ViewDef(
            name=str(data["name"]),
            modul=str(data.get("modul", "")),
            deskripsi=str(data.get("deskripsi", "")),
            columns=columns,
            sql={str(k): str(v) for k, v in (data.get("sql") or {}).items()},
            scope={
                str(k): {str(a): str(b) for a, b in (v or {}).items()}
                for k, v in (data.get("scope") or {}).items()
            },
            unscoped=bool(data.get("unscoped", False)),
            grain=str(data.get("grain", "") or ""),
            hitung={str(k): str(v) for k, v in (data.get("hitung") or {}).items()},
            contoh=[{str(a): str(b) for a, b in c.items()} for c in (data.get("contoh") or [])],
            catatan=str(data.get("catatan", "") or ""),
        )
    except KeyError as exc:
        raise CatalogError(f"{source.name}: field wajib hilang: {exc}") from exc


def load_catalog(directory: Path | None = None) -> Catalog:
    directory = directory or CATALOG_DIR
    views: dict[str, ViewDef] = {}
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise CatalogError(f"{path.name}: isi bukan mapping YAML")
        view = _view_from_dict(data, path)
        if view.name in views:
            raise CatalogError(f"view {view.name} terdefinisi ganda ({path.name})")
        views[view.name] = view
    if not views:
        raise CatalogError(f"katalog kosong di {directory}")
    return Catalog(views)
