#!/usr/bin/env python3
"""Scrape https://lokality.geology.cz/<id> pages into ./localities/<page_name>.

- page_name: 4-digit id + '-' + slug(title)
- Writes textual content to content.md (markdown-ish)
- Saves static map snapshot to locality-map.png
- Downloads Fotoarchiv images to images/ and stores caption into EXIF UserComment

Designed to be resumable: if content.md already exists for a page_dir, the page is skipped.
"""

from __future__ import annotations

# Suppress urllib3 LibreSSL warning early (must be before importing requests/urllib3).
import warnings

warnings.filterwarnings(
    "ignore",
    message=r"urllib3 v2 only supports OpenSSL 1\.1\.1\+.*",
)

try:
    from urllib3.exceptions import NotOpenSSLWarning  # type: ignore

    warnings.filterwarnings("ignore", category=NotOpenSSLWarning)
except Exception:
    pass

import argparse
import concurrent.futures
import json
import os
import re
import socket
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple
from urllib.parse import urlencode, urljoin, urlparse

import piexif
from piexif.helper import UserComment
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from PIL import Image




BASE_URL = "https://lokality.geology.cz/"
MAPSERVER_URL = "https://mapy.geology.cz/arcgis/rest/services/Popularizace/geologicke_lokality/MapServer"
BASEMAPSERVER_URL = "https://mapy.geology.cz/arcgis/rest/services/Topografie/ZABAGED_komplet/MapServer"
DEFAULT_OUTDIR = Path("localities")


@dataclass(frozen=True)
class PhotoRef:
    photo_page_url: str
    caption: str


def slugify_title(title: str) -> str:
    t = title.casefold().strip()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.encode("ascii", "ignore").decode("ascii")
    # Keep letters only; treat everything else as a separator.
    t = re.sub(r"[^a-z]+", "-", t)
    t = t.strip("-")
    return t or "untitled"


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def http_get(session: requests.Session, url: str, *, timeout: int = 30) -> requests.Response:
    r = session.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": "lokality-scrape/1.0 (+https://lokality.geology.cz/)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    r.raise_for_status()
    return r


def parse_lokality_page(html: str, page_url: str) -> Tuple[str, str, list[PhotoRef], Optional[int]]:
    """Return (title, content_markdown, photo_refs, preferred_map_layer_id).

    Raises ValueError if the locality page does not exist.
    """
    soup = BeautifulSoup(html, "lxml")

    # Non-existent pages return a warning alert.
    alert = soup.select_one("div.alert.alert-warning")
    if alert and "neexistuje" in alert.get_text(" ", strip=True).casefold():
        raise ValueError("Locality does not exist")
    h2 = soup.find("h2")
    if not h2 or not h2.get_text(strip=True):
        raise ValueError("Missing title <h2>")
    title = h2.get_text(" ", strip=True)

    # Extract map layer hint (vrstva='0' or '2') from inline JS.
    preferred_layer: Optional[int] = None
    scripts_text = "\n".join(s.get_text("\n") for s in soup.find_all("script") if s.get_text())
    m = re.search(r"var\s+vrstva\s*=\s*'(?P<layer>\d+)'", scripts_text)
    if m:
        try:
            preferred_layer = int(m.group("layer"))
        except ValueError:
            preferred_layer = None

    # Main content column: the page uses multiple col-sm-9 blocks; the first often contains only the title.
    main_col = None
    char_h5 = soup.find("h5", string=re.compile(r"^V\s*seobecn\w+\s+charakteristika$", re.I))
    if char_h5:
        main_col = char_h5.find_parent("div", class_=re.compile(r"\bcol-sm-9\b"))
    if not main_col:
        candidates = soup.select("div.col-sm-9")
        scored = []
        for div in candidates:
            scored.append((len(div.find_all("h5")), len(div.get_text(" ", strip=True)), div))
        scored.sort(reverse=True)
        if scored:
            main_col = scored[0][2]
    if not main_col:
        raise ValueError("Missing main content column")

    # Remove the title inside the fragment (we add our own H1).
    inner_h2 = main_col.find("h2")
    if inner_h2:
        inner_h2.decompose()

    # Remove map div so markdownify doesn't include it.
    map_div = main_col.find("div", id="viewDiv")
    if map_div:
        map_div.decompose()

    # The page has multiple <hr> separators; keep them as section breaks.
    html_fragment = str(main_col)
    content_md = md(
        html_fragment,
        heading_style="ATX",
        bullets="-",
        strip=['script', 'style'],
    )
    # Clean up: remove excess blank lines, normalize hr to '---'
    content_md = re.sub(r"\n{3,}", "\n\n", content_md).strip() + "\n"

    # Prepend source
    content_md = f"# {title}\n\nSource: {page_url}\n\n" + content_md

    # Fotoarchiv column (right)
    photos: list[PhotoRef] = []
    foto_h5 = soup.find("h5", string=re.compile(r"^Fotoarchiv$", re.I))
    if foto_h5:
        foto_col = foto_h5.find_parent("div", class_=re.compile(r"\bcol-sm-3\b"))
        if foto_col:
            for thumb in foto_col.select("div.thumbnail"):
                a = thumb.find("a", href=True)
                img = thumb.find("img", src=True)
                cap = thumb.select_one("div.caption p")
                if not a or not img:
                    continue
                caption = (cap.get_text(" ", strip=True) if cap else "").strip()
                photo_page_url = a["href"]
                if photo_page_url.startswith("/"):
                    photo_page_url = urljoin("https://fotoarchiv.geology.cz/", photo_page_url)
                photos.append(PhotoRef(photo_page_url=photo_page_url, caption=caption))

    return title, content_md, photos, preferred_layer


def exif_set_user_comment_jpeg(jpeg_path: Path, comment: str) -> None:
    if not comment:
        return
    data = jpeg_path.read_bytes()
    try:
        exif = piexif.load(data)
    except Exception:
        exif = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}

    exif.setdefault("Exif", {})
    exif["Exif"][piexif.ExifIFD.UserComment] = UserComment.dump(comment, encoding="unicode")
    # Also set ImageDescription for better compatibility.
    exif.setdefault("0th", {})
    exif["0th"][piexif.ImageIFD.ImageDescription] = comment.encode("utf-8", "ignore")
    # Windows Explorer tends to surface XPComment/XPSubject rather than ImageDescription.
    # These are UTF-16LE null-terminated byte strings.
    try:
        xp = (comment + "\x00").encode("utf-16le", "ignore")
        exif["0th"][piexif.ImageIFD.XPComment] = xp
        exif["0th"][piexif.ImageIFD.XPSubject] = xp
    except Exception:
        pass
    exif_bytes = piexif.dump(exif)
    piexif.insert(exif_bytes, str(jpeg_path))


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def jpeg_set_xmp_description(jpeg_path: Path, description: str) -> None:
    """Write caption as XMP dc:description (APP1).

    Many viewers surface XMP/"Description" as image metadata more reliably than EXIF UserComment.
    """
    if not description:
        return
    data = jpeg_path.read_bytes()
    if not (len(data) >= 2 and data[0] == 0xFF and data[1] == 0xD8):
        return

    xmp_header = b"http://ns.adobe.com/xap/1.0/\x00"
    desc_xml = _xml_escape(description)
    xmp_xml = (
        "<?xpacket begin='\ufeff' id='W5M0MpCehiHzreSzNTczkc9d'?>"
        "<x:xmpmeta xmlns:x='adobe:ns:meta/'>"
        "<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>"
        "<rdf:Description xmlns:dc='http://purl.org/dc/elements/1.1/'>"
        "<dc:description><rdf:Alt>"
        f"<rdf:li xml:lang='x-default'>{desc_xml}</rdf:li>"
        "</rdf:Alt></dc:description>"
        "</rdf:Description>"
        "</rdf:RDF>"
        "</x:xmpmeta>"
        "<?xpacket end='w'?>"
    ).encode("utf-8")
    payload = xmp_header + xmp_xml
    if len(payload) + 2 > 0xFFFF:
        # JPEG segment length is 16-bit including the length bytes.
        payload = payload[: 0xFFFF - 2]

    def build_segment(marker: int, seg_data: bytes) -> bytes:
        length = len(seg_data) + 2
        return bytes([0xFF, marker]) + length.to_bytes(2, "big") + seg_data

    # Parse segments up to SOS, strip existing XMP APP1 segments.
    segments: list[tuple[int, bytes, bytes]] = []  # (marker, seg_data, raw_segment_bytes)
    i = 2
    remainder: Optional[bytes] = None
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            break
        # Skip fill bytes.
        while i < len(data) and data[i] == 0xFF:
            i += 1
        if i >= len(data):
            break
        marker = data[i]
        i += 1
        if marker in (0xD9,):
            break
        if marker in (0xDA,):
            # Start of scan: remainder is compressed data until EOI.
            sos_start = i - 2
            remainder = data[sos_start:]
            break
        if i + 2 > len(data):
            break
        seg_len = int.from_bytes(data[i : i + 2], "big")
        i += 2
        if seg_len < 2 or i + (seg_len - 2) > len(data):
            break
        seg_data = data[i : i + (seg_len - 2)]
        raw = bytes([0xFF, marker]) + seg_len.to_bytes(2, "big") + seg_data
        i += seg_len - 2

        # Drop existing XMP.
        if marker == 0xE1 and seg_data.startswith(xmp_header):
            continue
        segments.append((marker, seg_data, raw))
    if remainder is None:
        # Unexpected JPEG layout; avoid corrupting the file.
        return

    new_xmp_seg = build_segment(0xE1, payload)

    # Insert after JFIF (APP0) and EXIF (APP1 Exif\0\0) if present.
    insert_at = 0
    for idx, (marker, seg_data, _raw) in enumerate(segments):
        if marker == 0xE0:
            insert_at = idx + 1
            continue
        if marker == 0xE1 and seg_data.startswith(b"Exif\x00\x00"):
            insert_at = idx + 1
            continue
        break

    rebuilt = bytearray()
    rebuilt += b"\xFF\xD8"
    for idx, (_m, _d, raw) in enumerate(segments):
        if idx == insert_at:
            rebuilt += new_xmp_seg
        rebuilt += raw
    if insert_at >= len(segments):
        rebuilt += new_xmp_seg
    rebuilt += remainder
    jpeg_path.write_bytes(bytes(rebuilt))


def set_jpeg_caption_metadata(jpeg_path: Path, caption: str) -> None:
    """Write caption into common metadata fields.

    - EXIF: UserComment, ImageDescription, XPComment/XPSubject
    - XMP: dc:description (what many apps show as "Description")
    """
    if not caption:
        return
    exif_set_user_comment_jpeg(jpeg_path, caption)
    try:
        normalize_utf8_jpeg_text(jpeg_path)
        rewrite_jpeg_with_exif_utf8(jpeg_path)
    except Exception:
        pass
    try:
        # Insert XMP after Pillow rewrite (Pillow would otherwise drop unknown APP segments).
        jpeg_set_xmp_description(jpeg_path, caption)
    except Exception:
        pass


def rewrite_jpeg_with_exif_utf8(jpeg_path: Path) -> None:
    """Re-encode JPEG so text EXIF is stored as UTF-8 (Pillow), not Latin-1."""
    # Pillow preserves most metadata if we provide the exif bytes.
    data = jpeg_path.read_bytes()
    exif_dict = piexif.load(data)
    exif_bytes = piexif.dump(exif_dict)
    img = Image.open(jpeg_path)
    img.save(jpeg_path, format="JPEG", quality=95, optimize=True, exif=exif_bytes)


def normalize_utf8_jpeg_text(jpeg_path: Path) -> None:
    """Ensure EXIF text (ImageDescription, UserComment) is stored as UTF-8 bytes."""
    data = jpeg_path.read_bytes()
    exif = piexif.load(data)

    def to_utf8_bytes(v):
        if v is None:
            return None
        if isinstance(v, bytes):
            try:
                v.decode("utf-8")
                return v
            except Exception:
                try:
                    return v.decode("latin-1").encode("utf-8")
                except Exception:
                    return v
        if isinstance(v, str):
            return v.encode("utf-8", "ignore")
        return None

    desc = exif.get("0th", {}).get(piexif.ImageIFD.ImageDescription)
    desc_b = to_utf8_bytes(desc)
    if desc_b is not None:
        exif.setdefault("0th", {})
        exif["0th"][piexif.ImageIFD.ImageDescription] = desc_b

    uc = exif.get("Exif", {}).get(piexif.ExifIFD.UserComment)
    if uc:
        try:
            txt = UserComment.load(uc)
            exif.setdefault("Exif", {})
            exif["Exif"][piexif.ExifIFD.UserComment] = UserComment.dump(txt, encoding="unicode")
        except Exception:
            pass

    piexif.insert(piexif.dump(exif), str(jpeg_path))


def content_type_to_ext(content_type: Optional[str], fallback: str) -> str:
    if not content_type:
        return fallback
    ct = content_type.split(";")[0].strip().lower()
    return {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
    }.get(ct, fallback)


def download_file(
    session: requests.Session,
    url: str,
    dest_path: Path,
    *,
    referer: Optional[str] = None,
) -> Tuple[str, int]:
    with session.get(
        url,
        stream=True,
        timeout=60,
        headers={
            "User-Agent": "lokality-scrape/1.0",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            **({"Referer": referer} if referer else {}),
        },
    ) as r:
        r.raise_for_status()
        ensure_dir(dest_path.parent)
        total = 0
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 128):
                if not chunk:
                    continue
                f.write(chunk)
                total += len(chunk)
        return r.headers.get("Content-Type", ""), total


def parse_photo_id(photo_page_url: str) -> Optional[str]:
    # https://fotoarchiv.geology.cz/cz/foto/25218/
    m = re.search(r"/foto/(?P<id>\d+)/", photo_page_url)
    return m.group("id") if m else None


def fotoarchiv_image_download_url(photo_id: str) -> str:
    # This endpoint is accessible without auth; other variants tend to be blocked.
    return f"https://fotoarchiv.geology.cz/show/photo/{photo_id}/img.jpg"


def fotoarchiv_candidate_image_urls(photo_id: str) -> list[str]:
    # Some photos are forbidden on img.jpg; fall back to preview sizes.
    return [
        f"https://fotoarchiv.geology.cz/show/photo/{photo_id}/img.jpg",
        f"https://fotoarchiv.geology.cz/show/photo/{photo_id}/g800.jpg",
        f"https://fotoarchiv.geology.cz/show/photo/{photo_id}/g200.jpg",
    ]


def fetch_map_bbox(session: requests.Session, locality_id: int, preferred_layer: Optional[int]) -> Optional[Tuple[float, float, float, float]]:
    layers_to_try = []
    if preferred_layer is not None:
        layers_to_try.append(preferred_layer)
    for lid in [0, 1, 2]:
        if lid not in layers_to_try:
            layers_to_try.append(lid)

    for layer_id in layers_to_try:
        url = f"{MAPSERVER_URL}/{layer_id}/query"
        params = {
            "where": f"id = {locality_id}",
            "outFields": "id",
            "returnGeometry": "true",
            "f": "pjson",
        }
        try:
            r = session.get(url, params={**params, **{"key": "CGS 2022"}}, timeout=30)
            r.raise_for_status()
            payload = r.json()
        except Exception:
            continue

        feats = payload.get("features") or []
        if not feats:
            continue
        geom = feats[0].get("geometry") or {}

        xs: list[float] = []
        ys: list[float] = []

        if "rings" in geom:
            for ring in geom.get("rings") or []:
                for x, y in ring:
                    xs.append(float(x))
                    ys.append(float(y))
        elif "paths" in geom:
            for path in geom.get("paths") or []:
                for x, y in path:
                    xs.append(float(x))
                    ys.append(float(y))
        elif "x" in geom and "y" in geom:
            xs.append(float(geom["x"]))
            ys.append(float(geom["y"]))
        else:
            continue

        if not xs or not ys:
            continue

        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)

        # Expand bbox so the map isn't overly tight.
        dx = maxx - minx
        dy = maxy - miny
        pad_x = max(dx * 0.25, 500.0)
        pad_y = max(dy * 0.25, 500.0)
        return (minx - pad_x, miny - pad_y, maxx + pad_x, maxy + pad_y)

    return None


def download_map_png(session: requests.Session, locality_id: int, bbox: Tuple[float, float, float, float], dest_path: Path) -> None:
    minx, miny, maxx, maxy = bbox
    size = "1024,768"
    common = {
        "bbox": f"{minx},{miny},{maxx},{maxy}",
        "bboxSR": "102067",
        "imageSR": "102067",
        "size": size,
        "format": "png32",
        "f": "image",
    }

    # 1) Download basemap image.
    base_params = {
        **common,
        "transparent": "false",
    }
    base_url = f"{BASEMAPSERVER_URL}/export?{urlencode(base_params)}"
    base_tmp = dest_path.with_suffix(".basemap.png")
    download_file(session, base_url, base_tmp)

    # 2) Download overlay (locality layer) as transparent PNG and composite.
    overlay_params = {
        **common,
        "transparent": "true",
        "layers": "show:0,2",
        "layerDefs": f"0:id={locality_id};2:id={locality_id}",
        "key": "CGS 2022",
    }
    overlay_url = f"{MAPSERVER_URL}/export?{urlencode(overlay_params)}"
    overlay_tmp = dest_path.with_suffix(".overlay.png")
    download_file(session, overlay_url, overlay_tmp)

    base_img = Image.open(base_tmp).convert("RGBA")
    overlay_img = Image.open(overlay_tmp).convert("RGBA")
    composed = Image.alpha_composite(base_img, overlay_img)
    ensure_dir(dest_path.parent)
    composed.save(dest_path, format="PNG", optimize=True)

    base_tmp.unlink(missing_ok=True)
    overlay_tmp.unlink(missing_ok=True)


def scrape_one(session: requests.Session, locality_id: int, outdir: Path, delay_s: float) -> Optional[str]:
    page_url = urljoin(BASE_URL, str(locality_id))
    r = http_get(session, page_url)

    title, content_md, photos, preferred_layer = parse_lokality_page(r.text, page_url)
    slug = slugify_title(title)
    page_name = f"{locality_id:04d}-{slug}"

    page_dir = outdir / page_name
    content_path = page_dir / "content.md"
    map_path = page_dir / "locality-map.png"
    images_dir = page_dir / "images"

    update_meta_only = bool(getattr(scrape_one, "update_image_metadata", False))

    # Allow re-runs without clobbering by default.
    if content_path.exists() and not getattr(scrape_one, "overwrite", False) and not update_meta_only:
        return None

    ensure_dir(page_dir)
    ensure_dir(images_dir)
    if not update_meta_only:
        content_path.write_text(content_md, encoding="utf-8")
        time.sleep(delay_s)

        bbox = fetch_map_bbox(session, locality_id, preferred_layer)
        if bbox:
            try:
                download_map_png(session, locality_id, bbox, map_path)
            except Exception as e:
                print(f"WARN map {locality_id}: {e}", file=sys.stderr)
        else:
            print(f"WARN map {locality_id}: no geometry", file=sys.stderr)
        time.sleep(delay_s)

    # Download photoarchive images
    for pref in photos:
        photo_id = parse_photo_id(pref.photo_page_url)
        if not photo_id:
            continue
        existing = sorted(images_dir.glob(f"{photo_id}-*"))
        if existing and not getattr(scrape_one, "overwrite", False):
            for p in existing:
                if p.suffix.lower() in {".jpg", ".jpeg"}:
                    set_jpeg_caption_metadata(p, pref.caption)
            continue
        # Fotoarchiv does not expose original filenames via headers; keep a stable local name.
        try:
            # Remove legacy filenames from earlier runs (e.g. <id>.jpg).
            for legacy in images_dir.glob(f"{photo_id}.jpg"):
                legacy.unlink(missing_ok=True)
            for img_url in fotoarchiv_candidate_image_urls(photo_id):
                url_basename = Path(urlparse(img_url).path).name
                dest = images_dir / f"{photo_id}-{url_basename}"

                if dest.exists() and not getattr(scrape_one, "overwrite", False):
                    if dest.suffix.lower() in {".jpg", ".jpeg"}:
                        set_jpeg_caption_metadata(dest, pref.caption)
                    break
                if dest.exists():
                    dest.unlink(missing_ok=True)

                try:
                    ct, _ = download_file(session, img_url, dest, referer=pref.photo_page_url)
                except requests.HTTPError as e:
                    # Try next candidate on 403/404.
                    if e.response is not None and e.response.status_code in {403, 404}:
                        continue
                    raise

                ext = content_type_to_ext(ct, dest.suffix.lstrip(".") or "jpg")
                if dest.suffix.lower() != f".{ext}":
                    new_dest = dest.with_suffix(f".{ext}")
                    if new_dest.exists():
                        new_dest.unlink(missing_ok=True)
                    dest.rename(new_dest)
                    dest = new_dest

                if dest.suffix.lower() in {".jpg", ".jpeg"}:
                    set_jpeg_caption_metadata(dest, pref.caption)
                break
        except Exception as e:
            print(f"WARN photo {locality_id} {photo_id}: {e}", file=sys.stderr)
        time.sleep(delay_s)

    return page_name


def iter_ids(start: int, end: int) -> Iterable[int]:
    step = 1 if end >= start else -1
    for i in range(start, end + step, step):
        yield i


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=3968)
    ap.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--delay", type=float, default=0.2, help="Sleep between requests (seconds)")
    ap.add_argument("--timeout-seconds", type=int, default=0, help="Stop after N seconds (0 = no limit)")
    ap.add_argument("--overwrite", action="store_true", help="Re-scrape even if content.md exists")
    ap.add_argument(
        "--update-image-metadata",
        action="store_true",
        help="Update photo captions in image metadata without rewriting content/map",
    )
    ap.add_argument(
        "--proxy",
        type=str,
        default=None,
        help="Proxy URL (e.g., socks5://localhost:9050 for Tor)",
    )
    ap.add_argument(
        "--tor-refresh-interval",
        type=int,
        default=0,
        help="Tor circuit refresh interval in seconds (0 = disabled). Minimum 10 seconds.",
    )
    ap.add_argument(
        "--tor-control-password",
        type=str,
        default=None,
        help="Tor control port password (if set)",
    )
    args = ap.parse_args(argv)

    setattr(scrape_one, "overwrite", bool(args.overwrite))
    setattr(scrape_one, "update_image_metadata", bool(args.update_image_metadata))

    proxy_url = args.proxy
    tor_refresh_interval = args.tor_refresh_interval
    if tor_refresh_interval and tor_refresh_interval < 10:
        print("tor-refresh-interval must be at least 10 seconds (Tor restriction)", file=sys.stderr)
        return 1

    tor_control_password = args.tor_control_password
    tor_socket: Optional[socket.socket] = None
    if tor_refresh_interval > 0:
        try:
            tor_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            tor_socket.connect(("localhost", 9051))
            if tor_control_password:
                tor_socket.sendall(f'AUTHENTICATE "{tor_control_password}"\r\n'.encode())
                resp = tor_socket.recv(1024)
                if not resp.startswith(b"250"):
                    raise RuntimeError(f"Tor auth failed: {resp}")
        except Exception as e:
            print(f"WARN: Could not connect to Tor control port: {e}", file=sys.stderr)
            tor_socket = None

    def refresh_tor_circuit() -> None:
        if tor_socket:
            try:
                tor_socket.sendall(b"SIGNAL NEWNYM\r\n")
                tor_socket.recv(1024)
            except Exception:
                pass

    last_tor_refresh = time.time()

    ensure_dir(args.outdir)
    t0 = time.time()

    proxies: Optional[dict] = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    def worker(locality_id: int) -> Optional[str]:
        nonlocal last_tor_refresh
        if args.timeout_seconds and (time.time() - t0) > args.timeout_seconds:
            return None

        if tor_refresh_interval > 0 and (time.time() - last_tor_refresh) >= tor_refresh_interval:
            refresh_tor_circuit()
            last_tor_refresh = time.time()

        session = requests.Session()
        if proxies:
            session.proxies.update(proxies)
        try:
            return scrape_one(session, locality_id, args.outdir, args.delay)
        except requests.HTTPError as e:
            print(f"WARN page {locality_id}: {e}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"WARN page {locality_id}: {e}", file=sys.stderr)
            return None

    ids = list(iter_ids(args.start, args.end))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = [ex.submit(worker, i) for i in ids]
        for fut in concurrent.futures.as_completed(futs):
            page_name = fut.result()
            if page_name:
                # Continuously print the actually downloaded page_name
                print(page_name, flush=True)

    if tor_socket:
        try:
            tor_socket.close()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
