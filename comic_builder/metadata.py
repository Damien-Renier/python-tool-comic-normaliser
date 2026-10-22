"""ComicVine API access and cache-backed metadata resolution."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from .config import SeriesConfigReader
from .models import IssueMetadata, SeriesConfig, SeriesMetadata
from .text import clean_html


class MetadataProvider(Protocol):
    """Interface used by the pipeline to obtain one series' metadata."""

    def get(self, series_dir: Path, refresh: bool = False) -> SeriesMetadata:
        """Return cached or remote metadata for a series directory."""
        ...


class SeriesMetadataSource(Protocol):
    """Interface implemented by remote series metadata sources."""

    def fetch(self, config: SeriesConfig, fallback_name: str) -> SeriesMetadata:
        """Retrieve normalized metadata for a configured series."""
        ...


class ComicVineClient:
    """Minimal client for the two ComicVine endpoints used by the application."""

    ISSUE_FIELDS = (
        "id,name,issue_number,cover_date,description,deck,"
        "site_detail_url,person_credits"
    )

    def __init__(self, api_key: str, timeout: int = 30) -> None:
        """Configure authentication and a bounded network timeout."""
        self._api_key = api_key
        self._timeout = timeout

    def fetch(self, config: SeriesConfig, fallback_name: str) -> SeriesMetadata:
        """Retrieve volume and all paginated issues for the selected volume."""
        if not config.volume_id:
            return SeriesMetadata(constants=config.constants)
        volume = self._get(f"volume/4050-{config.volume_id}/")["results"]
        issues = self._fetch_issues(config.volume_id)
        return SeriesMetadata(
            volume_id=config.volume_id,
            url=config.comicvine_url,
            name=volume.get("name") or fallback_name,
            publisher=(volume.get("publisher") or {}).get("name", ""),
            start_year=volume.get("start_year"),
            description=clean_html(volume.get("description") or volume.get("deck")),
            issues=tuple(IssueMetadata.from_dict(issue) for issue in issues),
            constants=config.constants,
        )

    def _fetch_issues(self, volume_id: str) -> list[dict[str, Any]]:
        """Read every issue page rather than silently truncating long series."""
        params: dict[str, object] = {
            "filter": f"volume:{volume_id}",
            "field_list": self.ISSUE_FIELDS,
            "limit": 100,
        }
        payload = self._get("issues/", **params)
        issues = list(payload["results"])
        total = payload.get("number_of_total_results", len(issues))
        while len(issues) < total:
            page = self._get("issues/", **params, offset=len(issues))["results"]
            if not page:
                break
            issues.extend(page)
        return issues

    def _get(self, resource: str, **params: object) -> dict[str, Any]:
        """Execute one authenticated API request and validate its status."""
        query = urllib.parse.urlencode({"api_key": self._api_key, "format": "json", **params})
        request = urllib.request.Request(
            f"https://comicvine.gamespot.com/api/{resource}?{query}",
            headers={"User-Agent": "ComicVine-CBZ-Builder/2.0"},
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            payload: dict[str, Any] = json.load(response)
        if payload.get("status_code") != 1:
            raise RuntimeError(payload.get("error", "ComicVine API request failed"))
        return payload


class CachedMetadataProvider:
    """Coordinates configuration, source-specific caches, and fallback order."""

    CACHE_NAME = ".comicvine-cache.json"
    BEDETHEQUE_CACHE_NAME = ".bedetheque-cache.json"

    def __init__(
        self,
        config_reader: SeriesConfigReader,
        client: SeriesMetadataSource | None,
        bedetheque_client: SeriesMetadataSource | None = None,
    ) -> None:
        """Combine configuration parsing with an optional remote client."""
        self._config_reader = config_reader
        self._client = client
        self._bedetheque_client = bedetheque_client

    def get(self, series_dir: Path, refresh: bool = False) -> SeriesMetadata:
        """Use a matching cache when possible and preserve current constants."""
        config = self._config_reader.read(series_dir)
        comicvine_cache = series_dir / self.CACHE_NAME
        if config.volume_id:
            cached = self._read_cache(comicvine_cache, refresh)
            if cached and str(cached.get("volume_id")) == config.volume_id:
                return SeriesMetadata.from_dict(cached, config.constants)
            if self._client is None:
                print(
                    "  warning: ComicVine URL found but COMIC_VINE_API_KEY is not set",
                    file=sys.stderr,
                )
            else:
                metadata = self._try_fetch("ComicVine", self._client, config, series_dir.name)
                if metadata:
                    self._write_cache(comicvine_cache, metadata)
                    return metadata

        if config.bedetheque_url:
            bedetheque_cache = series_dir / self.BEDETHEQUE_CACHE_NAME
            cached = self._read_cache(bedetheque_cache, refresh)
            if cached and cached.get("url") == config.bedetheque_url:
                return SeriesMetadata.from_dict(cached, config.constants)
            if self._bedetheque_client is not None:
                metadata = self._try_fetch(
                    "Bédéthèque", self._bedetheque_client, config, series_dir.name
                )
                if metadata:
                    self._write_cache(bedetheque_cache, metadata)
                    return metadata

        # Preserve compatibility for a series whose URL was removed after a
        # successful ComicVine lookup, unless urlBD explicitly selects a new source.
        if not config.volume_id and not config.bedetheque_url:
            cached = self._read_cache(comicvine_cache, refresh)
            if cached:
                return SeriesMetadata.from_dict(cached, config.constants)
        return SeriesMetadata(
            config.volume_id,
            config.comicvine_url or config.bedetheque_url,
            constants=config.constants,
        )

    @staticmethod
    def _read_cache(path: Path, refresh: bool) -> dict[str, Any] | None:
        if refresh or not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"  warning: ignoring invalid metadata cache {path.name}: {exc}", file=sys.stderr)
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _write_cache(path: Path, metadata: SeriesMetadata) -> None:
        path.write_text(
            json.dumps(metadata.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _try_fetch(
        label: str,
        source: SeriesMetadataSource,
        config: SeriesConfig,
        fallback_name: str,
    ) -> SeriesMetadata | None:
        try:
            return source.fetch(config, fallback_name)
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
            print(f"  warning: {label} metadata retrieval failed: {exc}", file=sys.stderr)
            return None
