"""Ekstraksi teks dokumen: PDF (pypdf), DOCX (python-docx), TXT/MD.

Hasil: ParsedDocument berisi halaman (PDF) atau satu "halaman" tanpa nomor (docx/txt/md).
Teks lengkap disimpan di documents.content dengan pemisah halaman \\f agar reindex
dapat memulihkan nomor halaman tanpa berkas asli.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath

SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
PAGE_SEPARATOR = "\f"


class ParseError(Exception):
    """Dokumen tidak dapat diekstrak; pesan aman ditampilkan ke admin."""


@dataclass
class PageText:
    page: int | None
    text: str


@dataclass
class ParsedDocument:
    pages: list[PageText]
    mime_type: str
    page_count: int | None = None
    title: str | None = None

    @property
    def full_text(self) -> str:
        return PAGE_SEPARATOR.join(p.text for p in self.pages)

    @classmethod
    def from_full_text(
        cls, content: str, mime_type: str, title: str | None = None
    ) -> ParsedDocument:
        parts = content.split(PAGE_SEPARATOR)
        paged = len(parts) > 1
        pages = [PageText(i + 1 if paged else None, t) for i, t in enumerate(parts) if t.strip()]
        return cls(
            pages=pages, mime_type=mime_type, page_count=len(parts) if paged else None, title=title
        )


def extension_of(filename: str) -> str:
    return PurePosixPath(filename.lower()).suffix


def is_supported(filename: str) -> bool:
    return extension_of(filename) in SUPPORTED_EXTENSIONS


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x0c", "\n")
    text = text.replace(" ", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_repeated_lines(pages: list[PageText], threshold: float = 0.6) -> list[PageText]:
    """Buang baris pendek yang muncul di > threshold halaman (header/footer berulang)."""
    if len(pages) < 3:
        return pages
    counts: Counter[str] = Counter()
    for page in pages:
        unique = {ln.strip() for ln in page.text.splitlines() if 0 < len(ln.strip()) <= 80}
        counts.update(unique)
    repeated = {line for line, n in counts.items() if n / len(pages) > threshold}
    if not repeated:
        return pages
    cleaned: list[PageText] = []
    for page in pages:
        kept = [ln for ln in page.text.splitlines() if ln.strip() not in repeated]
        cleaned.append(PageText(page.page, normalize_text("\n".join(kept))))
    return cleaned


def parse_bytes(filename: str, data: bytes) -> ParsedDocument:
    ext = extension_of(filename)
    mime = SUPPORTED_EXTENSIONS.get(ext)
    if mime is None:
        raise ParseError(f"format {ext or '(tanpa ekstensi)'} tidak didukung (pdf, txt, md, docx)")
    if not data:
        raise ParseError("berkas kosong")
    if ext == ".pdf":
        return _parse_pdf(data, mime)
    if ext == ".docx":
        return _parse_docx(data, mime)
    return _parse_text(data, mime)


def _parse_pdf(data: bytes, mime: str) -> ParsedDocument:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # noqa: BLE001
                raise ParseError("PDF terenkripsi dengan kata sandi") from exc
        total = len(reader.pages)
    except ParseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"PDF tidak dapat dibaca: {exc}") from exc

    pages: list[PageText] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 — halaman rusak dilewati
            text = ""
        pages.append(PageText(number, normalize_text(text)))
    if not any(p.text for p in pages):
        raise ParseError(
            "PDF tampaknya hasil pindaian (tanpa lapisan teks) — perlu OCR terlebih dahulu"
        )
    pages = [p for p in strip_repeated_lines(pages) if p.text]

    title: str | None = None
    try:
        meta = reader.metadata
        if meta is not None and meta.title:
            title = str(meta.title).strip() or None
    except Exception:  # noqa: BLE001
        title = None
    return ParsedDocument(pages=pages, mime_type=mime, page_count=total, title=title)


def _parse_docx(data: bytes, mime: str) -> ParsedDocument:
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"DOCX tidak dapat dibaca: {exc}") from exc

    parts: list[str] = []
    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name or "") if para.style is not None else ""
        match = re.match(r"Heading (\d)", style)
        if match:
            parts.append("#" * int(match.group(1)) + " " + text)
        else:
            parts.append(text)
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                parts.append("\t".join(cells))
    text = normalize_text("\n\n".join(parts))
    if not text:
        raise ParseError("dokumen kosong")
    title = (document.core_properties.title or "").strip() or None
    return ParsedDocument(
        pages=[PageText(None, text)], mime_type=mime, page_count=None, title=title
    )


def _parse_text(data: bytes, mime: str) -> ParsedDocument:
    text = ""
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    text = normalize_text(text.lstrip("﻿"))
    if not text:
        raise ParseError("dokumen kosong")
    title: str | None = None
    match = re.search(r"^#\s+(.+?)\s*$", text, re.M)
    if match:
        title = match.group(1).strip()
    return ParsedDocument(
        pages=[PageText(None, text)], mime_type=mime, page_count=None, title=title
    )
