"""Typed domain objects shared by the application layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SeriesConfig:
    """User-controlled settings loaded from a series' comicvine.txt file."""

    source_path: Path | None = None
    comicvine_url: str | None = None
    volume_id: str | None = None
    constants: dict[str, str] = field(default_factory=dict)
    bedetheque_url: str | None = None


@dataclass(frozen=True)
class IssueMetadata:
    """ComicVine fields used to identify and describe one issue."""

    issue_number: str | None = None
    name: str | None = None
    cover_date: str | None = None
    description: str | None = None
    deck: str | None = None
    site_detail_url: str | None = None
    person_credits: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "IssueMetadata":
        """Create a typed issue while tolerating omitted ComicVine fields."""
        return cls(
            issue_number=value.get("issue_number"),
            name=value.get("name"),
            cover_date=value.get("cover_date"),
            description=value.get("description"),
            deck=value.get("deck"),
            site_detail_url=value.get("site_detail_url"),
            person_credits=tuple(value.get("person_credits") or ()),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize fields in the stable cache format."""
        return {
            "issue_number": self.issue_number,
            "name": self.name,
            "cover_date": self.cover_date,
            "description": self.description,
            "deck": self.deck,
            "site_detail_url": self.site_detail_url,
            "person_credits": list(self.person_credits),
        }


@dataclass(frozen=True)
class SeriesMetadata:
    """Resolved volume metadata plus user-defined series constants."""

    volume_id: str | None = None
    url: str | None = None
    name: str | None = None
    publisher: str | None = None
    start_year: str | int | None = None
    description: str = ""
    issues: tuple[IssueMetadata, ...] = ()
    constants: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls, value: dict[str, Any], constants: dict[str, str] | None = None
    ) -> "SeriesMetadata":
        """Load cached/API-shaped data and overlay current local constants."""
        return cls(
            volume_id=str(value["volume_id"]) if value.get("volume_id") else None,
            url=value.get("url"),
            name=value.get("name"),
            publisher=value.get("publisher"),
            start_year=value.get("start_year"),
            description=value.get("description") or "",
            issues=tuple(IssueMetadata.from_dict(item) for item in value.get("issues") or ()),
            constants=dict(constants if constants is not None else value.get("constants") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serializable data for the on-disk metadata cache."""
        return {
            "volume_id": self.volume_id,
            "url": self.url,
            "name": self.name,
            "publisher": self.publisher,
            "start_year": self.start_year,
            "description": self.description,
            "issues": [issue.to_dict() for issue in self.issues],
            "constants": self.constants,
        }


@dataclass(frozen=True)
class BookIdentity:
    """The issue number and title selected for one input book."""

    number: str | None
    title: str
    issue: IssueMetadata | None = None


@dataclass(frozen=True)
class PageInfo:
    """Measured properties for one image in reading order."""

    index: int
    width: int
    height: int
    size: int
    is_front_cover: bool = False

    @property
    def is_double_page(self) -> bool:
        """Landscape images are treated as two-page spreads."""
        return self.width > self.height

    def xml_attributes(self) -> dict[str, str]:
        """Return ComicInfo page attributes in a predictable order."""
        attributes = {
            "DoublePage": str(self.is_double_page),
            "Image": str(self.index),
            "ImageHeight": str(self.height),
            "ImageSize": str(self.size),
            "ImageWidth": str(self.width),
        }
        if self.is_front_cover:
            attributes["Type"] = "FrontCover"
        return attributes


@dataclass(frozen=True)
class PipelineOptions:
    """Validated command-line settings consumed by the pipeline."""

    pdf_source: Path
    cbz_source: Path
    output: Path
    pdf24: Path
    api_key: str | None
    refresh_metadata: bool = False
    force: bool = False
    jobs: int = 1
