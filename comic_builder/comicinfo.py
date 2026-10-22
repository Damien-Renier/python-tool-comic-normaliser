"""ComicInfo.xml generation."""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from .models import BookIdentity, PageInfo, SeriesMetadata
from .text import clean_html


class ComicInfoWriter:
    """Builds one standards-compatible ComicInfo XML document."""

    def build(
        self,
        book: Path,
        series_dir: Path,
        identity: BookIdentity,
        metadata: SeriesMetadata,
        pages: list[PageInfo],
    ) -> bytes:
        """Serialize issue, series, configured constants, and measured pages."""
        issue = identity.issue
        date_parts = ((issue.cover_date if issue else None) or "").split("-")
        credits = self._group_credits(issue.person_credits if issue else ())

        root = ET.Element(
            "ComicInfo", {"xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance"}
        )
        fields: dict[str, object | None] = {
            "Title": identity.title,
            "Series": metadata.name or series_dir.name,
            "Number": identity.number,
            "Summary": clean_html(
                (issue.description or issue.deck) if issue else None
            ) or metadata.description,
            "Year": date_parts[0] if date_parts and date_parts[0] else metadata.start_year,
            "Month": date_parts[1] if len(date_parts) > 1 else None,
            # Day is deliberately omitted because ComicVine often supplies a
            # placeholder value rather than a meaningful publication day.
            "Writer": ", ".join(credits.get("writer", [])),
            "Penciller": ", ".join(credits.get("penciler", []) + credits.get("penciller", [])),
            "Inker": ", ".join(credits.get("inker", [])),
            "Colorist": ", ".join(credits.get("colorist", [])),
            "Letterer": ", ".join(credits.get("letterer", [])),
            "CoverArtist": ", ".join(credits.get("cover", []) + credits.get("cover artist", [])),
            "Editor": ", ".join(credits.get("editor", [])),
            "Publisher": metadata.publisher,
            "Web": (issue.site_detail_url if issue else None) or metadata.url,
            "Format": "Album",
        }
        for key, value in fields.items():
            self._add_text(root, key, value)
        for key, value in metadata.constants.items():
            self._add_text(root, key, value)

        self._add_text(root, "PageCount", len(pages))
        pages_element = ET.SubElement(root, "Pages")
        for page in pages:
            ET.SubElement(pages_element, "Page", page.xml_attributes())
        ET.indent(root, space="  ")
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    @staticmethod
    def _group_credits(credits: tuple[dict[str, object], ...]) -> dict[str, list[str]]:
        """Index contributor names by ComicVine's comma-separated roles."""
        grouped: dict[str, list[str]] = {}
        for credit in credits:
            name = str(credit.get("name") or "")
            for role in str(credit.get("role") or "").split(","):
                grouped.setdefault(role.strip().casefold(), []).append(name)
        return grouped

    @staticmethod
    def _add_text(root: ET.Element, key: str, value: object | None) -> None:
        """Skip empty optional elements instead of emitting meaningless tags."""
        if value is not None and str(value).strip():
            ET.SubElement(root, key).text = str(value).strip()
