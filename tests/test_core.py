"""Focused tests for configuration, naming, XML, and archive normalization."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from comic_builder.archives import ArchiveInspector, ArchiveWriter, CbzBookProcessor
from comic_builder.bedetheque import BedethequePageExtractor
from comic_builder.comicinfo import ComicInfoWriter
from comic_builder.config import SeriesConfigReader
from comic_builder.images import JpegAnalyzer
from comic_builder.metadata import CachedMetadataProvider
from comic_builder.models import IssueMetadata, SeriesMetadata
from comic_builder.naming import BookIdentityResolver
from comic_builder.pipeline import BuildPipeline


def jpeg_stub(width: int, height: int) -> bytes:
    """Create the smallest byte sequence needed by the JPEG header parser."""
    # SOI + SOF0 length/precision/height/width + EOI. Pixel decoding is not
    # needed because the application intentionally reads metadata headers only.
    return (
        b"\xff\xd8\xff\xc0\x00\x0b\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x01\x01\x11\x00\xff\xd9"
    )


class SeriesConfigReaderTests(unittest.TestCase):
    """Verify local constants remain separate from remote metadata."""

    def test_reads_url_and_supported_constants(self) -> None:
        """Parse the documented comicvine.txt format."""
        with tempfile.TemporaryDirectory() as temporary:
            series = Path(temporary)
            (series / "comicvine.txt").write_text(
                "url=https://comicvine.gamespot.com/example/4050-123/\n"
                "LanguageISO=fr\nManga=No\nGenre=Aventure\nAgeRating=Everyone\n",
                encoding="utf-8",
            )
            config = SeriesConfigReader().read(series)
            self.assertEqual(config.volume_id, "123")
            self.assertEqual(config.constants["LanguageISO"], "fr")
            self.assertEqual(config.constants["Genre"], "Aventure")

    def test_reads_supported_constants_without_url(self) -> None:
        """Local ComicInfo values do not depend on ComicVine metadata."""
        with tempfile.TemporaryDirectory() as temporary:
            series = Path(temporary)
            config_path = series / "comicvine.txt"
            config_path.write_text(
                "url=\nurlBD=https://www.bedetheque.com/albums-138-BD-Bidochon.html\n"
                "LanguageISO=fr\nManga=No\nGenre=Humour\nAgeRating=Everyone\n",
                encoding="utf-8",
            )

            config = SeriesConfigReader().read(series)

            self.assertEqual(config.source_path, config_path)
            self.assertIsNone(config.comicvine_url)
            self.assertIsNone(config.volume_id)
            self.assertEqual(
                config.bedetheque_url,
                "https://www.bedetheque.com/albums-138-BD-Bidochon.html",
            )
            self.assertEqual(
                config.constants,
                {
                    "LanguageISO": "fr",
                    "Manga": "No",
                    "Genre": "Humour",
                    "AgeRating": "Everyone",
                },
            )


class BedethequeTests(unittest.TestCase):
    """Verify semantic extraction and provider fallback without network access."""

    def test_extracts_json_ld_album_metadata(self) -> None:
        document = """
            <html><head>
            <title>Les Bidochon - BD, informations</title>
            <meta name="description" content="Chronique de la vie des Bidochon">
            <script type="application/ld+json">
            {"@graph": [
              {"@type": "Book", "position": 1, "name": "Roman d'amour",
               "datePublished": "1979-01-01", "url": "/BD-Bidochon-Tome-1.html",
               "author": {"@type": "Person", "name": "Christian Binet"},
               "publisher": {"@type": "Organization", "name": "Fluide Glacial"}},
              {"@type": "Book", "position": 2, "name": "En vacances",
               "url": "/BD-Bidochon-Tome-2.html"}
            ]}
            </script></head></html>
        """

        metadata = BedethequePageExtractor().extract(
            document,
            "https://www.bedetheque.com/albums-138-BD-Bidochon.html",
            "Fallback",
        )

        self.assertEqual(metadata.name, "Les Bidochon")
        self.assertEqual(metadata.publisher, "Fluide Glacial")
        self.assertEqual(metadata.start_year, "1979")
        self.assertEqual([issue.issue_number for issue in metadata.issues], ["1", "2"])
        self.assertEqual(metadata.issues[0].name, "Roman d'amour")
        self.assertEqual(metadata.issues[0].person_credits[0]["name"], "Christian Binet")

    def test_routes_series_page_and_reads_album_image_labels(self) -> None:
        """A ``serie-*`` page uses its dedicated album-link extraction rules."""
        document = """
            <html><head><title>Litteul Kévin - BD</title></head><body>
              <a href="/BD-Litteul-Kevin-Tome-1-Litteul-Kevin.html">
                <img alt="Litteul Kévin - Tome 1 - Litteul Kevin">
              </a>
              <a href="/BD-Litteul-Kevin-Tome-2-Le-Corbeau.html">
                <img alt="Litteul Kévin - Tome 2 - Le Corbeau">
              </a>
            </body></html>
        """

        metadata = BedethequePageExtractor().extract(
            document,
            "https://www.bedetheque.com/serie-560-BD-Litteul-Kevin.html",
            "Litteul Kévin",
        )

        self.assertEqual(
            [(issue.issue_number, issue.name) for issue in metadata.issues],
            [("1", "Litteul Kevin"), ("2", "Le Corbeau")],
        )

    def test_routes_albums_page_to_leading_number_format(self) -> None:
        """An ``albums-*`` page keeps the listing-page title convention."""
        document = """
            <html><head><title>Example - BD</title></head><body>
              <a href="/BD-Example-Tome-1.html" title="1 - Premier album"></a>
            </body></html>
        """

        metadata = BedethequePageExtractor().extract(
            document,
            "https://www.bedetheque.com/albums-1-BD-Example.html",
            "Example",
        )

        self.assertEqual(metadata.issues[0].issue_number, "1")
        self.assertEqual(metadata.issues[0].name, "Premier album")

    def test_uses_bedetheque_when_comicvine_fetch_fails(self) -> None:
        class FailingSource:
            def fetch(self, config, fallback_name):
                raise RuntimeError("unavailable")

        class BedethequeSource:
            def fetch(self, config, fallback_name):
                return SeriesMetadata(
                    url=config.bedetheque_url,
                    name="Les Bidochon",
                    issues=(IssueMetadata("1", "Roman d'amour"),),
                    constants=config.constants,
                )

        with tempfile.TemporaryDirectory() as temporary:
            series = Path(temporary)
            (series / "comicvine.txt").write_text(
                "url=https://comicvine.gamespot.com/example/4050-123/\n"
                "urlBD=https://www.bedetheque.com/albums-138-BD-Bidochon.html\n"
                "LanguageISO=fr\n",
                encoding="utf-8",
            )
            provider = CachedMetadataProvider(
                SeriesConfigReader(), FailingSource(), BedethequeSource()
            )

            metadata = provider.get(series)

            self.assertEqual(metadata.name, "Les Bidochon")
            self.assertEqual(metadata.constants, {"LanguageISO": "fr"})
            self.assertTrue((series / ".bedetheque-cache.json").is_file())


class NamingTests(unittest.TestCase):
    """Protect issue matching and the public output naming convention."""

    def test_uses_metadata_and_pads_numeric_number(self) -> None:
        """Resolved metadata should drive both title and two-digit filename."""
        metadata = SeriesMetadata(
            issues=(IssueMetadata(issue_number="1", name="Canonical title"),)
        )
        resolver = BookIdentityResolver()
        identity = resolver.resolve(Path("01 - Local title.cbz"), metadata)
        self.assertEqual(identity.number, "1")
        self.assertEqual(identity.title, "Canonical title")
        self.assertEqual(resolver.output_filename(identity), "01 - Canonical title.cbz")

    def test_preserves_hors_serie_label(self) -> None:
        """Non-numeric HS identifiers must not be zero padded."""
        resolver = BookIdentityResolver()
        identity = resolver.resolve(Path("HS - Bonus.cbz"), SeriesMetadata())
        self.assertEqual(resolver.output_filename(identity), "HS - Bonus.cbz")

    def test_leading_issue_number_wins_over_tome_in_title(self) -> None:
        """Story-part numbers in a title must not replace the album number."""
        resolver = BookIdentityResolver()
        metadata = SeriesMetadata(
            issues=(IssueMetadata(issue_number="11", name="Canonical title"),)
        )

        identity = resolver.resolve(
            Path("11 - Les 3 Formules du Professeur Sato - Tome 1.pdf"), metadata
        )

        self.assertEqual(identity.number, "11")
        self.assertEqual(identity.title, "Canonical title")
        self.assertEqual(resolver.output_filename(identity), "11 - Canonical title.cbz")

    def test_tome_prefix_is_used_when_no_leading_number_exists(self) -> None:
        """The documented `Tome N` prefix remains supported."""
        resolver = BookIdentityResolver()

        identity = resolver.resolve(
            Path("Tome 02 - Local title.pdf"), SeriesMetadata()
        )

        self.assertEqual(identity.number, "2")
        self.assertEqual(identity.title, "Local title")
        self.assertEqual(resolver.output_filename(identity), "02 - Local title.cbz")

    def test_normalizes_comicvine_story_part_separators(self) -> None:
        """Colons and a missing separator before Tome use spaced dashes."""
        resolver = BookIdentityResolver()
        identity = resolver.resolve(
            Path("11 - Local.pdf"),
            SeriesMetadata(
                issues=(
                    IssueMetadata(
                        issue_number="11",
                        name="Les 3 Formules du Professeur Satō Tome 1: Mortimer à Tokyo",
                    ),
                )
            ),
        )

        self.assertEqual(
            resolver.output_filename(identity),
            "11 - Les 3 Formules du Professeur Satō - Tome 1 - Mortimer à Tokyo.cbz",
        )

    def test_collapses_spaces_around_story_part_without_changing_case(self) -> None:
        """Repeated spaces are removed while capitalization is preserved."""
        resolver = BookIdentityResolver()
        identity = BookIdentityResolver().resolve(
            Path("19 - Local.pdf"),
            SeriesMetadata(
                issues=(
                    IssueMetadata(
                        issue_number="19",
                        name="La Malédiction des trente deniers   -   Tome 1 : Le Manuscrit de Nicodemus",
                    ),
                )
            ),
        )

        self.assertEqual(
            resolver.output_filename(identity),
            "19 - La Malédiction des trente deniers - Tome 1 - Le Manuscrit de Nicodemus.cbz",
        )


class ImageAnalyzerTests(unittest.TestCase):
    """Verify page measurements come from image bytes."""

    def test_measures_page_and_detects_double_page(self) -> None:
        """A landscape first image is both a spread and front cover."""
        data = jpeg_stub(2400, 1200)
        page = JpegAnalyzer().analyze(data, 0)
        self.assertEqual((page.width, page.height, page.size), (2400, 1200, len(data)))
        self.assertTrue(page.is_double_page)
        self.assertEqual(page.xml_attributes()["Type"], "FrontCover")


class ArchiveProcessorTests(unittest.TestCase):
    """Exercise archive flattening and generated ComicInfo as one unit."""

    def test_normalizes_nested_cbz_and_writes_current_xml(self) -> None:
        """Nested source paths and old XML must not survive rebuilding."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            series = root / "Input" / "Series"
            output = root / "Output"
            series.mkdir(parents=True)
            source = series / "01 - Local.cbz"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("nested/", b"")
                archive.writestr("nested/cover.jpg", jpeg_stub(1000, 1500))
                archive.writestr("nested/spread.jpg", jpeg_stub(2000, 1000))
                archive.writestr("ComicInfo.xml", b"<old />")

            metadata = SeriesMetadata(
                name="Series",
                issues=(IssueMetadata(issue_number="1", name="Remote title", cover_date="2020-05-31"),),
                constants={"LanguageISO": "fr", "Manga": "No"},
            )
            resolver = BookIdentityResolver()
            processor = CbzBookProcessor(
                resolver,
                ArchiveInspector(),
                ArchiveWriter(JpegAnalyzer(), ComicInfoWriter()),
            )
            destination = processor.process(source, series, output, metadata, force=False)
            self.assertIsNotNone(destination)
            assert destination is not None
            self.assertEqual(destination.name, "01 - Remote title.cbz")

            with zipfile.ZipFile(destination) as archive:
                self.assertEqual(
                    archive.namelist(), ["001.jpg", "002.jpg", "ComicInfo.xml"]
                )
                xml = ET.fromstring(archive.read("ComicInfo.xml"))
                self.assertIsNone(xml.find("Day"))
                self.assertEqual(xml.findtext("LanguageISO"), "fr")
                self.assertEqual(xml.findtext("PageCount"), "2")
                pages_element = xml.find("Pages")
                pages = list(pages_element) if pages_element is not None else []
                self.assertEqual(pages[0].get("Type"), "FrontCover")
                self.assertEqual(pages[1].get("DoublePage"), "True")
            self.assertFalse(ArchiveInspector().needs_repair(destination))


class SeriesCompletionMarkerTests(unittest.TestCase):
    """Ensure one series file records completion album by album."""

    def test_marker_records_each_completed_album(self) -> None:
        """Each successful album has its own entry in the series marker."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            series = root / "CBZs" / "Series"
            output = root / "Output" / "Series"
            series.mkdir(parents=True)
            output.mkdir(parents=True)
            books = [series / "01 - One.cbz", series / "02 - Two.cbz"]
            for book in books:
                book.write_bytes(b"source")
            (output / "01 - One.cbz").write_bytes(b"output")
            (output / "02 - Two.cbz").write_bytes(b"output")

            pipeline = BuildPipeline(None, None, None)  # type: ignore[arg-type]
            state = {
                book.name: pipeline._album_record(book, output / book.name)
                for book in books
            }
            pipeline._write_completion_state(series, books, state)
            loaded = pipeline._load_completion_state(series)

            self.assertTrue(pipeline._album_is_complete(books[0], loaded, output))
            self.assertTrue(pipeline._album_is_complete(books[1], loaded, output))
            marker = series / BuildPipeline.COMPLETE_MARKER
            self.assertTrue(marker.is_file())
            payload = json.loads(marker.read_text(encoding="utf-8"))
            self.assertIsInstance(payload["albums"], list)
            self.assertEqual(
                {album["source_name"] for album in payload["albums"]},
                {book.name for book in books},
            )
            self.assertTrue(payload["albums"][0]["completed"])

    def test_album_is_invalidated_by_source_change_or_missing_output(self) -> None:
        """Only a changed or missing album entry must run again."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            series = root / "PDFs" / "Series"
            output = root / "Output" / "Series"
            series.mkdir(parents=True)
            output.mkdir(parents=True)
            book = series / "01 - One.pdf"
            destination = output / "01 - One.cbz"
            book.write_bytes(b"source")
            destination.write_bytes(b"output")

            pipeline = BuildPipeline(None, None, None)  # type: ignore[arg-type]
            state = {book.name: pipeline._album_record(book, destination)}
            pipeline._write_completion_state(series, [book], state)
            loaded = pipeline._load_completion_state(series)
            book.write_bytes(b"changed source")
            self.assertFalse(pipeline._album_is_complete(book, loaded, output))

            state[book.name] = pipeline._album_record(book, destination)
            pipeline._write_completion_state(series, [book], state)
            loaded = pipeline._load_completion_state(series)
            destination.unlink()
            self.assertFalse(pipeline._album_is_complete(book, loaded, output))

    def test_empty_configured_remote_metadata_is_not_complete(self) -> None:
        """A failed configured scraper must not make fallback output final."""
        class EmptyRemoteProvider:
            def get(self, series_dir, refresh=False):
                return SeriesMetadata(url="https://example.invalid/serie-1.html")

        pipeline = BuildPipeline(
            EmptyRemoteProvider(), None, None  # type: ignore[arg-type]
        )
        metadata, metadata_ok = pipeline._safe_metadata(Path("Series"), False)

        self.assertEqual(metadata.issues, ())
        self.assertFalse(metadata_ok)


if __name__ == "__main__":
    unittest.main()
