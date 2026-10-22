"""Environment and per-series configuration readers."""

from __future__ import annotations

import os
import re
from pathlib import Path

from .models import SeriesConfig


VOLUME_URL_RE = re.compile(
    r"https?://(?:www\.)?comicvine\.gamespot\.com/[^\s]+?/4050-(\d+)/?", re.I
)
BEDETHEQUE_URL_RE = re.compile(
    r"https?://(?:www\.)?bedetheque\.com/[^\s]+\.html(?:\?[^\s]*)?", re.I
)
CONSTANT_FIELDS = {
    "languageiso": "LanguageISO",
    "manga": "Manga",
    "genre": "Genre",
    "agerating": "AgeRating",
}


class DotEnvLoader:
    """Loads the simple KEY=VALUE syntax needed by this project."""

    def load(self, path: Path) -> None:
        """Add variables without replacing values already set by the caller."""
        if not path.is_file():
            return
        for raw_line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = (part.strip() for part in line.split("=", 1))
            if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
                value = value[1:-1]
            if key:
                os.environ.setdefault(key, value)


class SeriesConfigReader:
    """Finds and parses the configuration associated with one series."""

    def read(self, series_dir: Path) -> SeriesConfig:
        """Read local constants even when no ComicVine volume URL is configured."""
        preferred = [series_dir / "comicvine.txt", series_dir / "series.txt"]
        candidates = preferred + sorted(path for path in series_dir.glob("*.txt") if path not in preferred)
        fallback: SeriesConfig | None = None
        for path in candidates:
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8-sig", errors="replace")
            constants: dict[str, str] = {}
            for line in content.splitlines():
                if "=" not in line or line.lstrip().startswith("#"):
                    continue
                key, value = (part.strip() for part in line.split("=", 1))
                canonical_name = CONSTANT_FIELDS.get(key.casefold())
                if canonical_name and value:
                    constants[canonical_name] = value
            match = VOLUME_URL_RE.search(content)
            bedetheque_match = BEDETHEQUE_URL_RE.search(content)
            bedetheque_url = bedetheque_match.group(0) if bedetheque_match else None
            if not match:
                if fallback is None:
                    fallback = SeriesConfig(
                        source_path=path,
                        constants=constants,
                        bedetheque_url=bedetheque_url,
                    )
                continue
            return SeriesConfig(
                path,
                match.group(0),
                match.group(1),
                constants,
                bedetheque_url,
            )
        return fallback or SeriesConfig()
