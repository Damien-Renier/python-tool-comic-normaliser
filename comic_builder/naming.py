"""Book identity matching and output filename policy."""

from __future__ import annotations

import re
from pathlib import Path

from .models import BookIdentity, IssueMetadata, SeriesMetadata


NUMBER_RE = re.compile(r"(?:tome|tom|volume|vol\.?|book|issue|#)\s*0*(\d+(?:\.\d+)?)", re.I)
LEADING_NUMBER_RE = re.compile(r"^\s*0*(\d+(?:\.\d+)?)\s*[-–—:]\s*")
LEADING_SPECIAL_RE = re.compile(r"^\s*(HS)\s*[-–—:]\s*", re.I)
INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*]')


class BookIdentityResolver:
    """Matches filename hints to ComicVine issues and formats output names."""

    def resolve(self, book: Path, metadata: SeriesMetadata) -> BookIdentity:
        """Prefer an unambiguous issue match, then fall back to the filename."""
        number = self.number_from_name(book.stem)
        fallback_title = self.title_from_name(book.stem)
        issue = self.match_issue(metadata, number, fallback_title)
        return BookIdentity(
            number=issue.issue_number if issue and issue.issue_number else number,
            title=issue.name if issue and issue.name else fallback_title,
            issue=issue,
        )

    def output_filename(self, identity: BookIdentity) -> str:
        """Build a portable `<Number> - <Title>.cbz` filename."""
        display_number = identity.number
        if display_number and re.fullmatch(r"\d+", display_number):
            display_number = display_number.zfill(2)
        title = self._normalize_title_separators(identity.title)
        stem = f"{display_number} - {title}" if display_number else title
        # Metadata may contain characters forbidden by Windows filesystems.
        safe_stem = INVALID_FILENAME_RE.sub("_", stem).rstrip(" .")
        return f"{safe_stem}.cbz"

    @staticmethod
    def _normalize_title_separators(title: str) -> str:
        """Normalize title separators without changing capitalization."""
        normalized = re.sub(r"\s+", " ", title).strip()
        # Colons cannot appear in Windows filenames. Underscores produced by
        # the former replacement policy represent the same visual separator.
        normalized = re.sub(r"\s*[:_]\s*", " - ", normalized)
        # Normalize only dash characters already used as separators, leaving
        # compound words such as ``Rendez-vous`` untouched.
        normalized = re.sub(r"(?:\s+[-–—]\s*|\s*[-–—]\s+)", " - ", normalized)
        # ComicVine titles sometimes omit the separator before a story part.
        normalized = re.sub(
            r"(?:\s+-)?\s+(Tome\s+\d+\b)", r" - \1", normalized, flags=re.I
        )
        return re.sub(r"\s+", " ", normalized).strip()

    @staticmethod
    def number_from_name(stem: str) -> str | None:
        """Extract common numeric or hors-série prefixes from a filename."""
        # A leading issue number identifies the book itself and must take
        # precedence over story-part text such as "Tome 1" later in the title.
        match = LEADING_NUMBER_RE.search(stem) or LEADING_SPECIAL_RE.search(stem) or NUMBER_RE.search(stem)
        return match.group(1) if match else None

    @staticmethod
    def title_from_name(stem: str) -> str:
        """Remove a recognized issue prefix while preserving the visible title."""
        title = re.sub(
            r"^\s*(?:tome|tom|volume|vol\.?|book|issue|#)\s*0*\d+(?:\.\d+)?\s*[-–—:]?\s*",
            "",
            stem,
            flags=re.I,
        )
        title = LEADING_NUMBER_RE.sub("", title)
        title = LEADING_SPECIAL_RE.sub("", title)
        return title.strip() or stem

    def match_issue(
        self, metadata: SeriesMetadata, number: str | None, title: str
    ) -> IssueMetadata | None:
        """Match uniquely by issue number first and exact title second."""
        if number:
            matches = [
                issue
                for issue in metadata.issues
                if self._normalize_number(issue.issue_number) == self._normalize_number(number)
            ]
            if len(matches) == 1:
                return matches[0]
        folded_title = title.casefold()
        matches = [issue for issue in metadata.issues if (issue.name or "").casefold() == folded_title]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _normalize_number(value: object) -> str:
        """Treat values such as `01`, `1`, and `1.0` as the same issue."""
        text = str(value or "").strip().lstrip("0") or "0"
        try:
            return str(float(text)).rstrip("0").rstrip(".")
        except ValueError:
            return text.casefold()
