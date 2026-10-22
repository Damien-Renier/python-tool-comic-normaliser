"""CBZ validation and transactional archive builders."""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable, Protocol
from xml.etree import ElementTree as ET

from .comicinfo import ComicInfoWriter
from .converters import PdfConverter
from .images import ImageAnalyzer
from .models import BookIdentity, PageInfo, SeriesMetadata
from .naming import BookIdentityResolver
from .text import natural_key


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class BookProcessor(Protocol):
    """Interface implemented by each supported input format."""

    def process(
        self,
        source: Path,
        series_dir: Path,
        output_root: Path,
        metadata: SeriesMetadata,
        force: bool,
    ) -> Path | None:
        """Return the completed output path, whether newly built or current."""
        ...


class ArchiveInspector:
    """Checks whether an existing output satisfies the current format contract."""

    def needs_repair(self, destination: Path) -> bool:
        """Detect malformed ZIPs, folders, stale XML, or incomplete page data."""
        if not destination.exists():
            return True
        try:
            with zipfile.ZipFile(destination) as archive:
                entries = archive.infolist()
                xml_entries = [
                    item for item in entries
                    if item.filename.casefold() == "comicinfo.xml" and not item.is_dir()
                ]
                if len(xml_entries) != 1 or self._contains_directories(entries):
                    return True
                root = ET.fromstring(archive.read(xml_entries[0]))
                image_count = sum(self._is_image(item) for item in entries)
                pages_element = root.find("Pages")
                pages = list(pages_element.findall("Page")) if pages_element is not None else []
                required = {"Image", "ImageWidth", "ImageHeight", "ImageSize", "DoublePage"}
                xml_is_current = (
                    root.find("Day") is None
                    and root.findtext("PageCount") == str(image_count)
                    and len(pages) == image_count
                    and all(required <= set(page.attrib) for page in pages)
                )
                return not xml_is_current or archive.testzip() is not None
        except (OSError, ET.ParseError, zipfile.BadZipFile):
            return True

    @staticmethod
    def _contains_directories(entries: list[zipfile.ZipInfo]) -> bool:
        """Reject both explicit directory flags and nested archive paths."""
        for item in entries:
            unix_mode = (item.external_attr >> 16) & 0o170000
            nested = "/" in item.filename.rstrip("/") or "\\" in item.filename
            if item.is_dir() or unix_mode == 0o040000 or nested:
                return True
        return False

    @staticmethod
    def _is_image(item: zipfile.ZipInfo) -> bool:
        """Return whether a ZIP entry is a supported image file."""
        return not item.is_dir() and Path(item.filename).suffix.casefold() in IMAGE_EXTENSIONS


class ArchiveWriter:
    """Writes normalized pages and metadata to a new CBZ archive."""

    def __init__(self, analyzer: ImageAnalyzer, comicinfo: ComicInfoWriter) -> None:
        """Accept replaceable image-analysis and XML-writing collaborators."""
        self._analyzer = analyzer
        self._comicinfo = comicinfo

    def write(
        self,
        destination: Path,
        book: Path,
        series_dir: Path,
        identity: BookIdentity,
        metadata: SeriesMetadata,
        images: Iterable[tuple[str, bytes]],
        image_count: int,
    ) -> None:
        """Build in a temporary directory, then copy only the completed result."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="cbz-") as temp_name:
            partial = Path(temp_name) / "book.cbz"
            width = max(3, len(str(image_count)))
            pages: list[PageInfo] = []
            with zipfile.ZipFile(
                partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as archive:
                # Consume one page at a time so large books do not live in RAM.
                for position, (extension, data) in enumerate(images):
                    pages.append(self._analyzer.analyze(data, position))
                    archive.writestr(f"{position + 1:0{width}d}{extension}", data)
                xml = self._comicinfo.build(book, series_dir, identity, metadata, pages)
                archive.writestr("ComicInfo.xml", xml)
            shutil.copy2(partial, destination)


class BaseBookProcessor:
    """Shared naming, skip, and destination behavior for input processors."""

    action = "created"

    def __init__(
        self,
        resolver: BookIdentityResolver,
        inspector: ArchiveInspector,
        writer: ArchiveWriter,
    ) -> None:
        """Configure shared naming, inspection, and archive services."""
        self._resolver = resolver
        self._inspector = inspector
        self._writer = writer

    def _target(
        self, source: Path, series_dir: Path, output_root: Path, metadata: SeriesMetadata
    ) -> tuple[Path, BookIdentity]:
        """Resolve identity once and derive its output path."""
        identity = self._resolver.resolve(source, metadata)
        destination = output_root / series_dir.name / self._resolver.output_filename(identity)
        return destination, identity

    def _should_skip(self, source: Path, destination: Path, force: bool) -> bool:
        """Skip only newer outputs that also pass the current format checks."""
        return (
            destination.exists()
            and not force
            and destination.stat().st_mtime >= source.stat().st_mtime
            and not self._inspector.needs_repair(destination)
        )


class CbzBookProcessor(BaseBookProcessor):
    """Flattens an existing CBZ and replaces its metadata."""

    action = "normalized"

    def process(
        self, source: Path, series_dir: Path, output_root: Path,
        metadata: SeriesMetadata, force: bool,
    ) -> Path | None:
        """Stream supported source images into a new flat archive."""
        destination, identity = self._target(source, series_dir, output_root, metadata)
        if self._should_skip(source, destination, force):
            print(f"  skip: {destination.name} is up to date")
            return destination
        with zipfile.ZipFile(source) as original:
            entries = [item for item in original.infolist() if self._is_page(item)]
            entries.sort(key=lambda item: natural_key(Path(item.filename)))
            if not entries:
                raise RuntimeError("source CBZ contains no supported image files")
            # The writer consumes the generator while the source ZIP is open.
            images = (
                (Path(item.filename).suffix.casefold(), original.read(item))
                for item in entries
            )
            self._writer.write(
                destination, source, series_dir, identity, metadata, images, len(entries)
            )
        print(f"  {self.action}: {destination}")
        return destination

    @staticmethod
    def _is_page(item: zipfile.ZipInfo) -> bool:
        """Ignore metadata, explicit folders, and disguised directory entries."""
        unix_mode = (item.external_attr >> 16) & 0o170000
        return (
            not item.is_dir()
            and unix_mode != 0o040000
            and Path(item.filename).suffix.casefold() in IMAGE_EXTENSIONS
        )


class PdfBookProcessor(BaseBookProcessor):
    """Renders PDF pages and packages them with generated metadata."""

    def __init__(
        self,
        resolver: BookIdentityResolver,
        inspector: ArchiveInspector,
        writer: ArchiveWriter,
        converter: PdfConverter,
    ) -> None:
        """Add a PDF renderer to the common book-processing services."""
        super().__init__(resolver, inspector, writer)
        self._converter = converter

    def process(
        self, source: Path, series_dir: Path, output_root: Path,
        metadata: SeriesMetadata, force: bool,
    ) -> Path | None:
        """Render the source PDF and package its measured page images."""
        destination, identity = self._target(source, series_dir, output_root, metadata)
        if self._should_skip(source, destination, force):
            print(f"  skip: {destination.name} is up to date")
            return destination
        with tempfile.TemporaryDirectory(prefix="cbz-") as temp_name:
            images = self._converter.convert(source, Path(temp_name) / "pages")
            payload = ((".jpg", image.read_bytes()) for image in images)
            self._writer.write(
                destination, source, series_dir, identity, metadata, payload, len(images)
            )
        print(f"  {self.action}: {destination}")
        return destination
