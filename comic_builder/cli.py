"""Command-line composition root for the application."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .archives import ArchiveInspector, ArchiveWriter, CbzBookProcessor, PdfBookProcessor
from .bedetheque import BedethequeClient
from .comicinfo import ComicInfoWriter
from .config import DotEnvLoader, SeriesConfigReader
from .converters import Pdf24Converter
from .images import JpegAnalyzer
from .metadata import CachedMetadataProvider, ComicVineClient
from .models import PipelineOptions
from .naming import BookIdentityResolver
from .pipeline import BuildPipeline


PDF24_DEFAULT = Path(r"C:\Program Files\PDF24\pdf24-DocTool.exe")


def build_parser() -> argparse.ArgumentParser:
    """Define the stable public command-line interface."""
    parser = argparse.ArgumentParser(
        description="Build metadata-rich CBZ files from PDF and existing CBZ input trees."
    )
    parser.add_argument("--source", type=Path, default=Path("PDFs"))
    parser.add_argument("--cbz-source", type=Path, default=Path("CBZs"))
    parser.add_argument("--output", type=Path, default=Path("Output"))
    parser.add_argument("--pdf24", type=Path, default=PDF24_DEFAULT)
    parser.add_argument("--api-key")
    parser.add_argument("--refresh-metadata", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--jobs", type=int, default=1,
        help="Number of series processed in parallel (default: 1)",
    )
    return parser


def create_pipeline(options: PipelineOptions) -> BuildPipeline:
    """Wire concrete adapters to domain-facing interfaces in one place."""
    config_reader = SeriesConfigReader()
    client = ComicVineClient(options.api_key) if options.api_key else None
    metadata_provider = CachedMetadataProvider(config_reader, client, BedethequeClient())
    resolver = BookIdentityResolver()
    inspector = ArchiveInspector()
    writer = ArchiveWriter(JpegAnalyzer(), ComicInfoWriter())
    cbz_processor = CbzBookProcessor(resolver, inspector, writer)
    pdf_processor = PdfBookProcessor(
        resolver, inspector, writer, Pdf24Converter(options.pdf24)
    )
    return BuildPipeline(metadata_provider, pdf_processor, cbz_processor)


def main(argv: list[str] | None = None) -> int:
    """Load configuration, parse arguments, and execute the build pipeline."""
    # A legacy Windows code page must not turn valid filenames into failures.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")

    project_root = Path(__file__).resolve().parent.parent
    DotEnvLoader().load(project_root / ".env")
    parser = build_parser()
    args = parser.parse_args(argv)
    api_key = (
        args.api_key
        or os.environ.get("COMICVINE_API_KEY")
        or os.environ.get("COMIC_VINE_API_KEY")
    )
    options = PipelineOptions(
        pdf_source=args.source,
        cbz_source=args.cbz_source,
        output=args.output,
        pdf24=args.pdf24,
        api_key=api_key,
        refresh_metadata=args.refresh_metadata,
        force=args.force,
        jobs=args.jobs,
    )
    if options.jobs < 1:
        parser.error("--jobs must be at least 1")
    try:
        return create_pipeline(options).run(options)
    except ValueError as exc:
        parser.error(str(exc))
        return 2  # Unreachable, but keeps the return type explicit.
