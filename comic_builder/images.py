"""Image inspection abstractions and JPEG implementation."""

from __future__ import annotations

from typing import Protocol

from .models import PageInfo


class ImageAnalyzer(Protocol):
    """Interface for extracting page metadata from encoded image bytes."""

    def analyze(self, data: bytes, index: int) -> PageInfo:
        """Inspect one image and return its ComicInfo page properties."""
        ...


class JpegAnalyzer:
    """Reads JPEG frame headers without requiring a third-party image library."""

    _SOF_MARKERS = {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    }

    def analyze(self, data: bytes, index: int) -> PageInfo:
        """Measure dimensions and byte size; index zero is the front cover."""
        width, height = self.dimensions(data)
        return PageInfo(index, width, height, len(data), is_front_cover=index == 0)

    def dimensions(self, data: bytes) -> tuple[int, int]:
        """Return width and height from the first valid JPEG frame header."""
        if not data.startswith(b"\xff\xd8"):
            raise ValueError("image is not a JPEG")
        offset = 2
        while offset < len(data):
            while offset < len(data) and data[offset] != 0xFF:
                offset += 1
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            # Standalone markers have no length field.
            if marker == 0x01 or 0xD0 <= marker <= 0xD8:
                continue
            if offset + 2 > len(data):
                break
            segment_length = int.from_bytes(data[offset:offset + 2], "big")
            if marker in self._SOF_MARKERS and offset + 7 <= len(data):
                height = int.from_bytes(data[offset + 3:offset + 5], "big")
                width = int.from_bytes(data[offset + 5:offset + 7], "big")
                if width and height:
                    return width, height
            if segment_length < 2:
                break
            offset += segment_length
        raise ValueError("JPEG frame dimensions were not found")
