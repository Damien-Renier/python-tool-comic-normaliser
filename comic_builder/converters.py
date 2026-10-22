"""PDF conversion interfaces and PDF24 adapter."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol

from .text import natural_key


class PdfConverter(Protocol):
    """Interface for rendering a PDF into an ordered set of page images."""

    @property
    def executable(self) -> Path:
        """Return the external converter executable path."""
        ...

    def convert(self, pdf: Path, output_dir: Path) -> list[Path]:
        """Render a PDF and return image files in reading order."""
        ...


class Pdf24Converter:
    """Adapter for PDF24's command-line document conversion tool."""

    def __init__(self, executable: Path) -> None:
        """Store the explicitly configured PDF24 executable path."""
        self._executable = executable

    @property
    def executable(self) -> Path:
        """Expose the executable for startup validation and diagnostics."""
        return self._executable

    def convert(self, pdf: Path, output_dir: Path) -> list[Path]:
        """Convert to JPEG and reject PDF24's misleading `.jpg` directories."""
        command = [
            str(self._executable), "-convertTo", "-param", "outputFormat", "jpg",
            "-outputDir", str(output_dir), str(pdf),
        ]
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode:
            details = result.stderr or result.stdout
            raise RuntimeError(f"PDF24 failed ({result.returncode}): {details}")
        # PDF24 creates a directory ending in .jpg; only real files are pages.
        images = sorted(
            (path for path in output_dir.rglob("*.jpg") if path.is_file()),
            key=natural_key,
        )
        if not images:
            raise RuntimeError("PDF24 completed but produced no JPG files")
        return images
