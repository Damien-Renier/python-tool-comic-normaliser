"""Small text and sorting helpers with no infrastructure dependencies."""

from __future__ import annotations

import html
import re
from pathlib import Path


def natural_key(path: Path) -> list[object]:
    """Sort names containing numbers in human reading order."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", path.name)]


def clean_html(value: str | None) -> str:
    """Convert ComicVine's small HTML descriptions into plain text."""
    if not value:
        return ""
    value = re.sub(r"<\s*br\s*/?\s*>", "\n", value, flags=re.I)
    value = re.sub(r"</\s*p\s*>", "\n\n", value, flags=re.I)
    plain = html.unescape(re.sub(r"<[^>]+>", "", value))
    return re.sub(r"\n{3,}", "\n\n", plain).strip()
