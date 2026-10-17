"""Deterministic text utilities used by TheraSkillAgent skills and tools."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class TextChunk:
    """A text window with offsets into the normalized source text."""

    index: int
    start: int
    end: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_text(text: str, *, preserve_newlines: bool = True) -> str:
    """Normalize Unicode, line endings, whitespace and blank lines.

    Normalization is intentionally deterministic so the same document gets
    the same chunks and section boundaries across workers.
    """

    value = unicodedata.normalize("NFKC", str(text)).replace("\r\n", "\n").replace("\r", "\n")
    if not preserve_newlines:
        return re.sub(r"\s+", " ", value).strip()

    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.split("\n")]
    compact: list[str] = []
    previous_blank = False
    for line in lines:
        blank = not line
        if blank and previous_blank:
            continue
        compact.append(line)
        previous_blank = blank
    return "\n".join(compact).strip()


def _window_end(text: str, start: int, limit: int) -> int:
    end = min(len(text), start + limit)
    if end >= len(text):
        return end
    # Prefer a paragraph or word boundary while retaining a hard upper bound.
    floor = start + max(1, int(limit * 0.55))
    candidates = [text.rfind("\n\n", floor, end), text.rfind("\n", floor, end), text.rfind(" ", floor, end)]
    boundary = max(candidates)
    return boundary if boundary > start else end


def chunk_text(text: str, *, max_chars: int = 4000, overlap: int = 200) -> list[TextChunk]:
    """Split normalized text into bounded, overlapping windows."""

    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    if overlap < 0 or overlap >= max_chars:
        raise ValueError("overlap must be >= 0 and smaller than max_chars")
    source = normalize_text(text)
    if not source:
        return []

    chunks: list[TextChunk] = []
    start = 0
    index = 0
    while start < len(source):
        end = _window_end(source, start, max_chars)
        chunks.append(TextChunk(index=index, start=start, end=end, text=source[start:end]))
        index += 1
        if end >= len(source):
            break
        next_start = end - overlap
        # Never let a pathological boundary prevent forward progress.
        start = max(start + 1, next_start)
    return chunks


def extract_sections(text: str) -> list[dict[str, Any]]:
    """Extract Markdown heading sections, retaining heading level and body."""

    source = normalize_text(text)
    heading = re.compile(r"^(#{1,6})[ \t]+(.+?)\s*$", re.MULTILINE)
    matches = [(m.start(), len(m.group(1)), m.group(2).strip()) for m in heading.finditer(source)]
    sections: list[dict[str, Any]] = []
    for idx, (start, level, title) in enumerate(matches):
        line_end = source.find("\n", start)
        body_start = len(source) if line_end < 0 else line_end + 1
        end = matches[idx + 1][0] if idx + 1 < len(matches) else len(source)
        body = source[body_start:end].strip()
        sections.append({"index": idx, "level": level, "title": title, "body": body})
    return sections


_SECRET_PATTERNS = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}"), r"\1[REDACTED_TOKEN]"),
    (re.compile(r"(?i)(api[_ -]?key\s*[:=]\s*)[^\s,;]+"), r"\1[REDACTED]"),
)


def redact_secrets(text: str) -> str:
    """Remove common API-key and bearer-token forms from display/output text."""

    value = str(text)
    for pattern, replacement in _SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value
