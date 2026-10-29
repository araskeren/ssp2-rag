"""Konversi jawaban markdown → teks polos untuk WhatsApp/Telegram (+ lampiran daftar sumber)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def markdown_to_plain(text: str) -> str:
    lines_out: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if _TABLE_SEP.match(line):
            continue
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            lines_out.append(" — ".join(c for c in cells if c))
            continue
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            lines_out.append(f"*{heading.group(1).strip()}*")
            continue
        line = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"*\1*", line)
        line = re.sub(r"__(.+?)__", r"*\1*", line)
        line = re.sub(r"`([^`]+)`", r"\1", line)
        line = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", line)
        line = re.sub(r"^\s*>\s?", "", line)
        lines_out.append(line)
    plain = "\n".join(lines_out)
    plain = re.sub(r"\n{3,}", "\n\n", plain).strip()
    return plain


def append_sources(text: str, sources: Sequence[Any]) -> str:
    if not sources:
        return text
    items = []
    for src in sources:
        data = src if isinstance(src, dict) else src.to_dict()
        label = data.get("title") or data.get("filename")
        page = data.get("page")
        where = f" hal. {page}" if page else ""
        items.append(f"{data.get('ref')} {label}{where}")
    return f"{text}\n\nSumber:\n" + "\n".join(items)


def to_messaging_text(answer: str, sources: Sequence[Any], limit: int = 4000) -> str:
    plain = append_sources(markdown_to_plain(answer), sources)
    if len(plain) > limit:
        plain = plain[: limit - 1].rstrip() + "…"
    return plain
