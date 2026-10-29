"""Pemotongan teks: RecursiveCharacterTextSplitter per halaman + pelacakan judul bagian (markdown)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from ai_service.rag.parsers import ParsedDocument

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)
SEPARATORS = ["\n\n", "\n", ". ", " ", ""]


@dataclass
class Chunk:
    index: int
    page: int | None
    section: str | None
    content: str


def make_splitter(chunk_size: int, chunk_overlap: int) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=SEPARATORS,
        length_function=len,
        keep_separator=True,
    )


def chunk_document(parsed: ParsedDocument, chunk_size: int, chunk_overlap: int) -> list[Chunk]:
    splitter = make_splitter(chunk_size, chunk_overlap)
    chunks: list[Chunk] = []
    index = 0
    for page in parsed.pages:
        headings = [(m.start(), m.group(2).strip()) for m in HEADING_RE.finditer(page.text)]
        cursor = 0
        for piece in splitter.split_text(page.text):
            content = piece.strip()
            if not content:
                continue
            probe = content[:40]
            pos = page.text.find(probe, cursor)
            if pos < 0:
                pos = page.text.find(probe)
            if pos >= 0:
                cursor = pos
            section = None
            for start, title in headings:
                if start <= pos:
                    section = title
                else:
                    break
            if section and not content.startswith(("#", section)):
                content = f"{section}\n{content}"
            chunks.append(Chunk(index=index, page=page.page, section=section, content=content))
            index += 1
    return chunks
