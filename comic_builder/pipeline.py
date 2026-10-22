"""Application orchestration independent from command-line parsing."""

from __future__ import annotations

import json
import urllib.error
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .archives import BookProcessor
from .metadata import MetadataProvider
from .models import PipelineOptions, SeriesMetadata


class BuildPipeline:
    """Discovers series, resolves metadata once, and processes each book."""

    COMPLETE_MARKER = ".series-complete.json"
    COMPLETE_MARKER_VERSION = 3

    def __init__(
        self,
        metadata_provider: MetadataProvider,
        pdf_processor: BookProcessor,
        cbz_processor: BookProcessor,
    ) -> None:
        """Inject metadata and format-specific processing strategies."""
        self._metadata_provider = metadata_provider
        self._pdf_processor = pdf_processor
        self._cbz_processor = cbz_processor

    def run(self, options: PipelineOptions) -> int:
        """Process both input roots and return a shell-friendly exit status."""
        pdfs = self._discover(options.pdf_source, ".pdf")
        cbzs = self._discover(options.cbz_source, ".cbz")
        if not pdfs and not cbzs:
            raise ValueError(
                f"No PDFs below {options.pdf_source} and no CBZs below {options.cbz_source}"
            )
        if pdfs and not options.pdf24.is_file():
            raise ValueError(f"PDF input was found, but PDF24 was not found at {options.pdf24}")

        work: dict[str, list[tuple[str, Path, list[Path], BookProcessor]]] = defaultdict(list)
        for kind, books, processor in (
            ("PDF", pdfs, self._pdf_processor),
            ("CBZ", cbzs, self._cbz_processor),
        ):
            for series_dir, series_books in self._group_by_parent(books).items():
                work[series_dir.name.casefold()].append(
                    (kind, series_dir, series_books, processor)
                )

        ordered_work = [work[key] for key in sorted(work)]
        if options.jobs == 1:
            failures = sum(self._process_series(units, options) for units in ordered_work)
        else:
            print(f"Parallel series workers: {options.jobs}")
            with ThreadPoolExecutor(max_workers=options.jobs) as executor:
                failures = sum(
                    executor.map(
                        lambda units: self._process_series(units, options), ordered_work
                    )
                )
        return 1 if failures else 0

    def _process_series(
        self,
        units: list[tuple[str, Path, list[Path], BookProcessor]],
        options: PipelineOptions,
    ) -> int:
        """Process all input formats for one output series without collisions."""
        failures = 0
        for kind, series_dir, series_books, processor in units:
            print(f"Series ({kind}): {series_dir.name}")
            state = self._load_completion_state(series_dir)
            source_names = {book.name for book in series_books}
            if any(name not in source_names for name in state):
                state = {
                    name: record
                    for name, record in state.items()
                    if name in source_names
                }
                self._write_completion_state(series_dir, series_books, state)
            series_output = options.output / series_dir.name
            pending = [
                book
                for book in series_books
                if options.force
                or options.refresh_metadata
                or not self._album_is_complete(book, state, series_output)
            ]
            if not pending:
                print(f"  skip series: {series_dir.name} is complete")
                continue

            metadata, metadata_ok = self._safe_metadata(
                series_dir, options.refresh_metadata
            )
            for book in series_books:
                if book not in pending:
                    print(f"  skip: {book.name} is marked complete")
                    continue
                try:
                    destination = processor.process(
                        book, series_dir, options.output, metadata, options.force
                    )
                    if destination is None:
                        raise RuntimeError("processor returned no completed output")
                    if metadata_ok:
                        state[book.name] = self._album_record(book, destination)
                        self._write_completion_state(series_dir, series_books, state)
                    else:
                        state.pop(book.name, None)
                        self._write_completion_state(series_dir, series_books, state)
                except Exception as exc:
                    failures += 1
                    state.pop(book.name, None)
                    self._write_completion_state(series_dir, series_books, state)
                    print(f"  ERROR {book.name}: {exc}", file=sys.stderr)
        return failures

    def _safe_metadata(
        self, series_dir: Path, refresh: bool
    ) -> tuple[SeriesMetadata, bool]:
        """Keep local conversion useful when remote metadata is unavailable."""
        try:
            metadata = self._metadata_provider.get(series_dir, refresh)
            # A configured remote source returning no issues means all remote
            # attempts failed. Local fallback output remains useful, but must
            # not be recorded as final or it would never receive metadata.
            return metadata, not (metadata.url and not metadata.issues)
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
            print(f"  warning: metadata retrieval failed: {exc}", file=sys.stderr)
            return SeriesMetadata(), False

    def _load_completion_state(self, series_dir: Path) -> dict[str, dict[str, object]]:
        """Load album completion records from the series' single marker file."""
        marker = series_dir / self.COMPLETE_MARKER
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
            albums = payload.get("albums")
            if (
                payload.get("version") != self.COMPLETE_MARKER_VERSION
                or not isinstance(albums, list)
            ):
                return {}
            return {
                album["source_name"]: album
                for album in albums
                if isinstance(album, dict)
                and isinstance(album.get("source_name"), str)
            }
        except (OSError, ValueError, TypeError):
            return {}

    def _album_is_complete(
        self, book: Path, state: dict[str, dict[str, object]], series_output: Path
    ) -> bool:
        """Return whether one unchanged source has its recorded completed output."""
        record = state.get(book.name)
        if not isinstance(record, dict) or record.get("completed") is not True:
            return False
        output_name = record.get("output")
        return (
            record.get("source") == self._source_record(book)
            and isinstance(output_name, str)
            and (series_output / output_name).is_file()
        )

    def _write_completion_state(
        self,
        series_dir: Path,
        books: list[Path],
        state: dict[str, dict[str, object]],
    ) -> None:
        """Atomically record every finished album in one file for the series."""
        source_names = {book.name for book in books}
        albums = [
            state[name]
            for name in sorted(state, key=str.casefold)
            if name in source_names
        ]
        payload = {
            "version": self.COMPLETE_MARKER_VERSION,
            "albums": albums,
        }
        marker = series_dir / self.COMPLETE_MARKER
        temporary = marker.with_suffix(marker.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(marker)

    @staticmethod
    def _source_record(book: Path) -> dict[str, int]:
        """Fingerprint one source without hashing a large PDF or CBZ."""
        stat = book.stat()
        return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}

    def _album_record(self, book: Path, destination: Path) -> dict[str, object]:
        """Describe one album that completed successfully."""
        return {
            "source_name": book.name,
            "completed": True,
            "source": self._source_record(book),
            "output": destination.name,
        }

    @staticmethod
    def _discover(root: Path, extension: str) -> list[Path]:
        """Return matching inputs deterministically, or none for a missing root."""
        if not root.exists():
            return []
        return sorted(root.rglob(f"*{extension}"), key=lambda path: str(path).casefold())

    @staticmethod
    def _group_by_parent(books: list[Path]) -> dict[Path, list[Path]]:
        """Group inputs so metadata is loaded once per series directory."""
        grouped: dict[Path, list[Path]] = defaultdict(list)
        for book in books:
            grouped[book.parent].append(book)
        return dict(sorted(grouped.items(), key=lambda item: str(item[0]).casefold()))
