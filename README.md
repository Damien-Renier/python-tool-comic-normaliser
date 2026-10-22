# ComicVine PDF/CBZ Builder

Build clean, metadata-rich CBZ archives from PDFs or existing CBZ files. The
tool uses a manually selected ComicVine volume URL, so it does not depend on
unreliable series-name search results.

## What it does

The program processes two independent input trees:

- `PDFs/<series>/*.pdf`: converts PDF pages to JPEG with PDF24.
- `CBZs/<series>/*.cbz`: rebuilds existing CBZ archives without converting
  their images.

For every book it:

1. Reads the series configuration from `comicvine.txt`.
2. Retrieves issue metadata from ComicVine and caches it locally.
3. Matches the book to an issue using the number in its filename.
4. Generates a new `ComicInfo.xml`.
5. Examines every JPEG to record its real dimensions and uncompressed size.
6. Removes directory entries and nested paths from existing CBZ files.
7. Writes a clean archive to `Output/<series>`.

Output filenames use resolved ComicVine metadata:

```text
<Number> - <Title>.cbz
```

Numeric issue numbers are padded to at least two digits:

```text
01 - Astérix le Gaulois.cbz
02 - La Serpe d'or.cbz
HS - Special title.cbz
```

The XML number itself remains unpadded (`<Number>1</Number>`).

## Requirements

- Python 3.10 or newer
- A ComicVine API key
- Internet access for the first metadata retrieval
- PDF24 Creator when processing PDFs on Windows

The project uses only Python's standard library. No `pip install` is required.

## Project structure

The implementation is split by responsibility:

```text
build_cbz.py                 Compatibility command-line entry point
comic_builder/
├── cli.py                   Argument parsing and dependency composition
├── pipeline.py              Input discovery and series orchestration
├── models.py                Immutable domain models
├── config.py                .env and comicvine.txt parsing
├── metadata.py              ComicVine client and metadata cache
├── naming.py                Issue matching and output naming
├── images.py                Image-analysis interface and JPEG analyzer
├── comicinfo.py             ComicInfo.xml generation
├── converters.py            PDF-converter interface and PDF24 adapter
└── archives.py              Archive validation and PDF/CBZ processors
tests/                       Focused standard-library regression tests
```

External boundaries are represented by typed protocols, allowing metadata,
image, PDF, or book-processing implementations to be replaced independently.

### Windows installation

1. Install [Python](https://www.python.org/downloads/windows/) and enable
   **Add Python to PATH** during installation.
2. Install PDF24 Creator when PDF conversion is required. The default expected
   executable is:

   ```text
   C:\Program Files\PDF24\pdf24-DocTool.exe
   ```

3. Download or copy this project to a local directory.
4. Create `.env` beside `build_cbz.py` as described below.

The included `Build-CBZ.ps1` wrapper is the easiest way to run the tool on
Windows.

### macOS installation

1. Install Python 3, for example with Homebrew:

   ```bash
   brew install python
   ```

2. Download or copy this project to a local directory.
3. Create `.env` beside `build_cbz.py`.
4. Run `build_cbz.py` directly with Python.

PDF24 is not available for macOS. The existing-CBZ workflow is fully
supported, but PDF conversion currently requires Windows and PDF24. Ensure the
`PDFs` directory is absent or contains no PDFs when running on macOS.

### Linux installation

Install Python using the package manager for your distribution. For Debian or
Ubuntu:

```bash
sudo apt update
sudo apt install python3
```

Then download or copy this project and create `.env` beside `build_cbz.py`.

PDF24 is not available for Linux. The existing-CBZ workflow is fully supported,
but PDF conversion currently requires Windows and PDF24. Ensure the `PDFs`
directory is absent or contains no PDFs when running on Linux.

## ComicVine API key

Create a ComicVine API key, then create a file named `.env` in the project
directory:

```text
COMIC_VINE_API_KEY=your-api-key-here
```

`COMICVINE_API_KEY` without the second underscore is also supported. Do not
commit or share `.env`.

You can use an environment variable instead:

Windows PowerShell:

```powershell
$env:COMIC_VINE_API_KEY = 'your-api-key-here'
```

macOS/Linux:

```bash
export COMIC_VINE_API_KEY='your-api-key-here'
```

## Preparing the input folders

Create one directory per series. PDFs and CBZs may be used in the same run:

```text
project/
├── PDFs/
│   └── De Cape et de Crocs/
│       ├── comicvine.txt
│       ├── Tome 01 - Le secret du Janissaire.pdf
│       └── Tome 02 - Pavillon noir.pdf
├── CBZs/
│   └── Astérix/
│       ├── comicvine.txt
│       ├── 01 - Astérix le gaulois.cbz
│       └── 02 - La serpe d'or.cbz
├── Output/
├── .env
├── build_cbz.py
└── Build-CBZ.ps1
```

The number in each filename is used to match the corresponding ComicVine
issue. Supported examples include:

```text
01 - Title.cbz
Tome 01 - Title.pdf
Volume 1 - Title.pdf
HS - Special title.cbz
```

### Creating `comicvine.txt`

Every series input directory must have its own `comicvine.txt`. Manually find
the correct **volume page** on ComicVine and copy its URL. A volume URL contains
an identifier beginning with `4050-`.

Expected content:

```text
url=https://comicvine.gamespot.com/asterix/4050-74147/
urlBD=https://www.bedetheque.com/albums-1-BD-Example.html
LanguageISO=fr
Manga=No
Genre=Humour,Aventure,Antiquité
AgeRating=Everyone
```

The supported constant fields are:

- `LanguageISO`: ISO 639 language code, such as `fr` or `en`.
- `Manga`: normally `No`, `Yes`, or `YesAndRightToLeft`.
- `Genre`: comma-separated genres applied to every book in the series.
- `AgeRating`: a constant reader age rating such as `Everyone`.

`url` selects the preferred ComicVine volume. `urlBD` optionally selects a
Bédéthèque series page as a fallback. ComicVine is tried first; Bédéthèque is
used when ComicVine is not configured, has no API key, or cannot be reached.
Both URLs may be left empty, in which case folder and filename metadata is
used. The remaining values are still copied into every generated
`ComicInfo.xml` for that series. Keep the API key in `.env`, not in
`comicvine.txt`.

Bédéthèque has two supported URL layouts. `albums-*.html` listing pages and
`serie-*.html` series pages are routed to separate extractors behind the same
interface. Series pages may expose album numbers and titles in linked cover
image labels instead of ordinary link text; the series extractor handles those
labels explicitly.

## How to execute

Run commands from the project directory.

### Windows

Process both default input trees:

```powershell
.\Build-CBZ.ps1
```

Force every book to be rebuilt:

```powershell
.\Build-CBZ.ps1 -Force
```

Fetch ComicVine metadata again and rebuild:

```powershell
.\Build-CBZ.ps1 -RefreshMetadata -Force
```

Use custom directories:

```powershell
.\Build-CBZ.ps1 `
    -Source 'D:\Books\PDFs' `
    -CbzSource 'D:\Books\CBZs' `
    -Output 'D:\Books\Output'
```

Process only existing CBZs by pointing `-Source` at an empty or nonexistent
directory:

```powershell
.\Build-CBZ.ps1 -Source '.\No-PDF-Input'
```

### macOS and Linux

Process existing CBZ files:

```bash
python3 build_cbz.py \
  --source ./No-PDF-Input \
  --cbz-source ./CBZs \
  --output ./Output
```

Refresh metadata and rebuild:

```bash
python3 build_cbz.py \
  --source ./No-PDF-Input \
  --cbz-source ./CBZs \
  --output ./Output \
  --refresh-metadata \
  --force
```

### Python command-line options

```text
--source PATH             PDF input root; default: PDFs
--cbz-source PATH         CBZ input root; default: CBZs
--output PATH             Output root; default: Output
--pdf24 PATH              Custom path to pdf24-DocTool.exe
--api-key KEY             Override the API key from .env
--refresh-metadata        Ignore the cache and query ComicVine again
--force                   Rebuild outputs even when they are up to date
```

## Generated metadata

`ComicInfo.xml` can include:

- Title, series, and issue number
- Summary
- Publication year and month (`Day` is intentionally omitted)
- Writer, penciller, inker, colorist, letterer, cover artist, and editor when
  ComicVine supplies those values
- Publisher and ComicVine issue URL
- Language, manga direction, genre, and age rating from `comicvine.txt`
- Format (`Album`)
- Page count and analyzed page records

Each image receives a zero-based `<Page>` record containing:

- `Image`: its zero-based position
- `ImageWidth` and `ImageHeight`: actual pixel dimensions
- `ImageSize`: actual uncompressed byte count
- `DoublePage`: `True` when width is greater than height
- `Type="FrontCover"`: only on the first image

Example:

```xml
<PageCount>50</PageCount>
<Pages>
  <Page DoublePage="False" Image="0" ImageHeight="2500"
        ImageSize="1487138" ImageWidth="1900" Type="FrontCover" />
  <Page DoublePage="False" Image="1" ImageHeight="2500"
        ImageSize="394905" ImageWidth="1900" />
</Pages>
```

## Results

Generated archives are placed in a separate output tree. Inputs are never
modified:

```text
Output/
├── Astérix/
│   ├── 01 - Astérix le Gaulois.cbz
│   └── 02 - La Serpe d'or.cbz
└── De Cape et de Crocs/
    ├── 01 - Le secret du Janissaire.cbz
    └── 02 - Pavillon noir.cbz
```

Every output CBZ contains only root-level image files and one
`ComicInfo.xml`. Existing nested folders, directory entries, and old metadata
files are removed during rebuilding.

## Metadata cache and reruns

ComicVine responses are cached as `.comicvine-cache.json` and Bédéthèque
responses as `.bedetheque-cache.json` inside each series input directory.
The tool keeps one `.series-complete.json` file in each source series directory.
That single file lists every album that finished successfully and its output.
The entry is written immediately after each album completes, so after an
interruption the next pass processes only unfinished albums without reopening
completed output CBZs. An entry is ignored automatically when its source album
changes or its recorded output is missing. Albums with processing or
metadata-retrieval failures are not recorded as complete.

Use `--refresh-metadata`/`-RefreshMetadata` when ComicVine data has changed.
Use `--force`/`-Force` after changing `comicvine.txt` values or when you want to
rebuild every archive.

If ComicVine cannot be reached, the tool reports a warning and tries the
configured Bédéthèque page. If neither remote source nor a cache is available,
it falls back to the series directory and input filename. Bédéthèque may reject
automated requests with a browser challenge; this is reported as a warning and
does not prevent local conversion.

## Tests

Run the regression suite from the project directory:

```powershell
python -m unittest discover -v
```

The tests use temporary files and do not modify the real `PDFs`, `CBZs`, or
`Output` directories.
