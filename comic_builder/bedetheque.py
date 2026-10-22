"""Best-effort Bédéthèque series-page metadata extraction."""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any, Iterable, Protocol

from .models import IssueMetadata, SeriesConfig, SeriesMetadata
from .text import clean_html


ISSUE_TITLE_RE = re.compile(
    r"^\s*(?:(?:tome|volume|album|n[°o]?)\s*)?([0-9]+(?:\.[0-9]+)?)\s*[-.:–—]\s*(.+?)\s*$",
    re.I,
)
ALBUM_URL_RE = re.compile(r"(?:^|/)BD-[^?#]+\.html(?:[?#].*)?$", re.I)
SERIES_ISSUE_RE = re.compile(
    r"(?:^|\s[-–—:]?\s*)(?:tome|volume|album|n[°o]?)\s*0*([0-9]+(?:\.[0-9]+)?)"
    r"(?:\s*[-.:–—]\s*(.+?))?\s*$",
    re.I,
)


def _as_name(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _as_name(value.get("name"))
    return None


def _objects(value: object) -> Iterable[dict[str, Any]]:
    """Walk JSON-LD graphs without depending on one particular page layout."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


class _SeriesPageParser(HTMLParser):
    """Collect JSON-LD and visible album links from a Bédéthèque page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page_title = ""
        self.description = ""
        self.json_ld: list[str] = []
        self.links: list[tuple[str, str, str, str]] = []
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._link_href: str | None = None
        self._link_title = ""
        self._link_image_alt = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and "ld+json" in (values.get("type") or "").casefold():
            self._capture, self._buffer = "json", []
        elif tag == "title":
            self._capture, self._buffer = "title", []
        elif tag == "meta" and (values.get("name") or "").casefold() == "description":
            self.description = (values.get("content") or "").strip()
        elif tag == "a" and values.get("href"):
            self._link_href = values["href"]
            self._link_title = values.get("title") or ""
            self._link_image_alt = ""
            self._buffer = []
        elif tag == "img" and self._link_href:
            self._link_image_alt = (
                values.get("alt") or values.get("title") or ""
            ).strip()

    def handle_data(self, data: str) -> None:
        if self._capture or self._link_href:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._capture == "json":
            self.json_ld.append("".join(self._buffer))
            self._capture, self._buffer = None, []
        elif tag == "title" and self._capture == "title":
            self.page_title = " ".join("".join(self._buffer).split())
            self._capture, self._buffer = None, []
        elif tag == "a" and self._link_href:
            text = " ".join("".join(self._buffer).split())
            self.links.append(
                (self._link_href, text, self._link_title.strip(), self._link_image_alt)
            )
            self._link_href, self._link_title, self._link_image_alt, self._buffer = (
                None, "", "", []
            )


class BedethequeExtractor(Protocol):
    """Interface implemented by each supported Bédéthèque page layout."""

    def extract(self, document: str, url: str, fallback_name: str) -> SeriesMetadata:
        """Extract normalized series metadata from one HTML page."""
        ...


class AlbumsPageExtractor:
    """Extract metadata from Bédéthèque ``albums-*`` listing pages."""

    ISSUE_TYPES = {"book", "comicissue", "comicstory", "product"}

    def extract(self, document: str, url: str, fallback_name: str) -> SeriesMetadata:
        parser = _SeriesPageParser()
        parser.feed(document)
        issues: list[IssueMetadata] = []
        publisher: str | None = None
        start_year: str | None = None

        for raw in parser.json_ld:
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                continue
            for item in _objects(payload):
                item_type = item.get("@type")
                types = item_type if isinstance(item_type, list) else [item_type]
                if not any(str(kind).casefold() in self.ISSUE_TYPES for kind in types):
                    continue
                name = _as_name(item.get("name") or item.get("headline"))
                number = item.get("position") or item.get("volumeNumber") or item.get("issueNumber")
                if not name:
                    continue
                parsed_number, parsed_name = self._split_title(name)
                number = str(number).strip() if number is not None else parsed_number
                name = parsed_name if parsed_number else name
                issue_url = item.get("url")
                if isinstance(issue_url, dict):
                    issue_url = issue_url.get("@id")
                issue_url = urllib.parse.urljoin(url, str(issue_url)) if issue_url else None
                date = str(item.get("datePublished") or "").strip() or None
                author = item.get("author") or item.get("creator")
                authors = author if isinstance(author, list) else [author]
                credits = tuple(
                    {"name": author_name, "role": "writer"}
                    for author_name in (_as_name(value) for value in authors)
                    if author_name
                )
                issues.append(
                    IssueMetadata(
                        issue_number=number or None,
                        name=name,
                        cover_date=date,
                        description=clean_html(_as_name(item.get("description"))),
                        site_detail_url=issue_url,
                        person_credits=credits,
                    )
                )
                publisher = publisher or _as_name(item.get("publisher"))
                if date and not start_year:
                    start_year = date[:4]

        # Many series pages expose album data only in ordinary links. This path
        # deliberately captures only links whose text includes an issue number.
        for href, text, title, image_alt in parser.links:
            absolute = urllib.parse.urljoin(url, href)
            if not ALBUM_URL_RE.search(urllib.parse.urlparse(absolute).path):
                continue
            number, name = self._split_title(title or text or image_alt)
            if number and name:
                issues.append(IssueMetadata(number, name, site_detail_url=absolute))

        issues = self._deduplicate(issues)
        if not issues:
            raise ValueError("Bédéthèque page contained no recognizable album metadata")
        series_name = self._series_name(parser.page_title) or fallback_name
        return SeriesMetadata(
            url=url,
            name=series_name,
            publisher=publisher,
            start_year=start_year,
            description=clean_html(parser.description),
            issues=tuple(issues),
        )

    @staticmethod
    def _split_title(value: str) -> tuple[str | None, str]:
        match = ISSUE_TITLE_RE.match(html.unescape(value).strip())
        return (match.group(1), match.group(2).strip()) if match else (None, value.strip())

    @staticmethod
    def _series_name(title: str) -> str:
        value = re.split(
            r"\s+(?:-|–|—)\s+(?:BD|bande dessinée|Bedetheque)",
            title,
            maxsplit=1,
            flags=re.I,
        )[0]
        return value.strip()

    @staticmethod
    def _deduplicate(issues: list[IssueMetadata]) -> list[IssueMetadata]:
        result: list[IssueMetadata] = []
        seen: set[tuple[str, str]] = set()
        for issue in issues:
            key = ((issue.issue_number or "").casefold(), (issue.name or "").casefold())
            if key in seen:
                continue
            seen.add(key)
            result.append(issue)
        return result


class SeriesPageExtractor(AlbumsPageExtractor):
    """Extract album links embedded in Bédéthèque ``serie-*`` pages."""

    @staticmethod
    def _split_title(value: str) -> tuple[str | None, str]:
        clean = html.unescape(value).strip()
        number, title = AlbumsPageExtractor._split_title(clean)
        if number:
            return number, title
        match = SERIES_ISSUE_RE.search(clean)
        if not match:
            return None, clean
        # Series pages often put the useful label in an image alt attribute,
        # e.g. ``Litteul Kévin - Tome 1 - Litteul Kevin``.
        trailing = (match.group(2) or "").strip()
        if trailing:
            return match.group(1), trailing
        leading = clean[: match.start()].strip(" -–—:")
        return match.group(1), leading or f"Tome {match.group(1)}"


class BedethequePageExtractor:
    """Route Bédéthèque HTML to the extractor matching its URL type."""

    def __init__(
        self,
        albums: BedethequeExtractor | None = None,
        series: BedethequeExtractor | None = None,
    ) -> None:
        self._albums = albums or AlbumsPageExtractor()
        self._series = series or SeriesPageExtractor()

    def extract(self, document: str, url: str, fallback_name: str) -> SeriesMetadata:
        path = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1].casefold()
        extractor = self._series if path.startswith("serie-") else self._albums
        return extractor.extract(document, url, fallback_name)


class BedethequeClient:
    """Retrieve and parse one configured Bédéthèque series page."""

    def __init__(self, timeout: int = 30, extractor: BedethequePageExtractor | None = None) -> None:
        self._timeout = timeout
        self._extractor = extractor or BedethequePageExtractor()

    def fetch(self, config: SeriesConfig, fallback_name: str) -> SeriesMetadata:
        if not config.bedetheque_url:
            return SeriesMetadata(constants=config.constants)
        request = urllib.request.Request(
            config.bedetheque_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
            },
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            document = response.read().decode(charset, errors="replace")
        folded = document.casefold()
        if "cf-chl-" in folded or "enable javascript and cookies to continue" in folded:
            raise RuntimeError("Bédéthèque blocked the automated request with a browser challenge")
        metadata = self._extractor.extract(document, config.bedetheque_url, fallback_name)
        return SeriesMetadata(
            url=metadata.url,
            name=metadata.name,
            publisher=metadata.publisher,
            start_year=metadata.start_year,
            description=metadata.description,
            issues=metadata.issues,
            constants=config.constants,
        )
