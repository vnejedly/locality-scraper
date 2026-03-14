# lokality-scrape

Scrape geological locality pages from `https://lokality.geology.cz/<id>` into per-locality folders under `localities/`.

Each locality is stored as:

- `localities/<page_name>/content.md` (Markdown-ish text)
- `localities/<page_name>/locality-map.png` (static map snapshot)
- `localities/<page_name>/images/` (Fotoarchiv JPEGs with caption written into image metadata)

`page_name` format: `NNNN-slugified-title` (e.g. `0003-vate-pisky`).

## Requirements

- macOS/Linux
- Python 3.9+

## Install

Create a virtual environment in the project root and install dependencies:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
```

## Run

Basic run (scrape all IDs 1..3968):

```bash
./.venv/bin/python scrape_localities.py --start 1 --end 3968
```

Recommended: be gentle on the server (fewer workers + small delay):

```bash
./.venv/bin/python scrape_localities.py --start 1 --end 3968 --workers 4 --delay 0.25
```

### Resuming

The scraper is resumable. If `content.md` already exists for a locality, it is skipped by default.

### Overwrite

Force re-scrape (re-download content/map/images) even if the locality already exists:

```bash
./.venv/bin/python scrape_localities.py --start 1 --end 3968 --overwrite
```

### Update Image Metadata Only

If you already downloaded images and want to (re)write captions into image metadata (EXIF + XMP) without rewriting `content.md` or `locality-map.png`:

```bash
./.venv/bin/python scrape_localities.py --start 1 --end 3968 --update-image-metadata
```

## Output Layout

Example:

```text
localities/
  0123-example-locality/
    content.md
    locality-map.png
    images/
      25218-img.jpg
      25219-g800.jpg
```

## Notes

- Some IDs do not exist; these are skipped.
- Fotoarchiv images are downloaded from public endpoints; some variants may return 403/404, in which case the scraper falls back to smaller preview sizes.
