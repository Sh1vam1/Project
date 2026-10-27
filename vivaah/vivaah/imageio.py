"""Reading and writing photos.

* Raw files from any camera LibRaw supports (Canon, Nikon, Sony, Fujifilm,
  Panasonic, Olympus/OM, Pentax, Leica, Hasselblad, DNG, ...) are decoded to
  scene-linear sRGB with the camera's as-shot white balance.
* JPEG / TIFF / PNG (and HEIC with pillow-heif installed) are decoded and
  linearised; EXIF orientation is applied.
* Output is sRGB with an embedded ICC profile: dithered 8-bit JPEG carrying the
  original EXIF, or 16-bit TIFF carrying make, model and capture time.
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageCms, ImageOps

from . import wb
from .color import linear_to_srgb, srgb_to_linear

RAW_EXTENSIONS = {
    ".3fr", ".ari", ".arw", ".bay", ".cr2", ".cr3", ".crw", ".dcr", ".dng", ".erf", ".fff",
    ".iiq", ".k25", ".kdc", ".mef", ".mos", ".mrw", ".nef", ".nrw", ".orf", ".ori", ".pef",
    ".raf", ".raw", ".rw2", ".rwl", ".sr2", ".srf", ".srw", ".x3f",
}
RASTER_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff", ".png", ".heic", ".heif", ".webp"}
SUPPORTED = RAW_EXTENSIONS | RASTER_EXTENSIONS

_EXIF_DATETIME_ORIGINAL = 0x9003
_EXIF_DATETIME = 0x0132
_EXIF_ORIENTATION = 0x0112


@dataclass
class Photo:
    path: Path
    rgb: np.ndarray  # float32 HxWx3, linear sRGB (scene-linear for raw)
    is_raw: bool
    wb_as_shot: tuple[float, float] | None = None  # (Kelvin, tint) for raw files
    exif: bytes | None = None
    capture_time: datetime | None = None
    camera: str = ""
    full_size: tuple[int, int] = (0, 0)  # original (width, height)


def is_raw(path: str | os.PathLike) -> bool:
    return Path(path).suffix.lower() in RAW_EXTENSIONS


OUTPUT_MARKER = ".vivaah-output"
OUTPUT_DIR_NAMES = {"vivaah-graded", "vivaah_report", "vivaah_qa"}


def mark_output_dir(folder: str | os.PathLike) -> Path:
    """Create ``folder`` and tag it so later runs never treat vivaah's outputs as photos."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / OUTPUT_MARKER
    if not marker.exists():
        marker.write_text("Created by vivaah. Photos in this folder are outputs and are skipped as inputs.\n")
    return folder


def _is_output_dir(folder: Path) -> bool:
    return folder.name in OUTPUT_DIR_NAMES or (folder / OUTPUT_MARKER).exists()


def find_photos(inputs: list[str | os.PathLike], recursive: bool = True) -> list[Path]:
    """Expand files/folders into a sorted list of supported photos.

    Hidden folders and vivaah's own output folders (export, report, QA) are
    skipped, as are ``*_looks.jpg`` preview sheets, so re-running on the same
    folder never grades vivaah's outputs again.
    """
    out: list[Path] = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            for root, dirs, files in os.walk(p):
                rp = Path(root)
                dirs[:] = sorted(d for d in dirs if recursive and not d.startswith(".") and not _is_output_dir(rp / d))
                out.extend(rp / f for f in files if Path(f).suffix.lower() in SUPPORTED
                           and not f.startswith(".") and not f.lower().endswith("_looks.jpg"))
        elif p.is_file() and p.suffix.lower() in SUPPORTED:
            out.append(p)
    # When RAW+JPEG pairs exist, keep the raw (better data, and the XMP belongs to it).
    by_stem: dict[tuple[Path, str], Path] = {}
    for p in sorted(set(out)):
        key = (p.parent, p.stem.lower())
        if key in by_stem and not is_raw(p):
            continue
        by_stem[key] = p
    return sorted(by_stem.values())


def _resize_long_edge(img: np.ndarray, long_edge: int | None) -> np.ndarray:
    if not long_edge:
        return img
    h, w = img.shape[:2]
    if max(h, w) <= long_edge:
        return img
    f = long_edge / max(h, w)
    return cv2.resize(img, (max(1, round(w * f)), max(1, round(h * f))), interpolation=cv2.INTER_AREA)


def _parse_exif_time(exif: Image.Exif) -> datetime | None:
    raw = None
    try:
        raw = exif.get_ifd(0x8769).get(_EXIF_DATETIME_ORIGINAL)
    except Exception:  # noqa: BLE001 - malformed EXIF is common, never fatal
        raw = None
    raw = raw or exif.get(_EXIF_DATETIME)
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw).strip("\x00 "), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def _exif_from_pil(im: Image.Image) -> tuple[bytes | None, datetime | None, str, int]:
    """EXIF bytes for the output (orientation reset to 1), capture time, camera, orientation.

    Works on a *copy*: Pillow caches the image's Exif object, and changing it
    would stop ``ImageOps.exif_transpose`` from rotating portrait photos.
    """
    try:
        src = im.getexif()
    except Exception:  # noqa: BLE001 - malformed EXIF is common, never fatal
        return None, None, "", 1
    if not src:
        return None, None, "", 1
    try:
        orientation = int(src.get(_EXIF_ORIENTATION, 1) or 1)
    except (TypeError, ValueError):
        orientation = 1
    exif = Image.Exif()
    try:
        exif.load(src.tobytes())
    except Exception:  # noqa: BLE001
        return None, _parse_exif_time(src), "", orientation
    when = _parse_exif_time(exif)
    camera = " ".join(str(exif.get(t, "")).strip("\x00 ") for t in (0x010F, 0x0110)).strip()
    exif[_EXIF_ORIENTATION] = 1  # pixels are written upright
    try:
        return exif.tobytes(), when, camera, orientation
    except Exception:  # noqa: BLE001
        return None, when, camera, orientation


def _orient(arr: np.ndarray, orientation: int) -> np.ndarray:
    """Apply an EXIF orientation to an (H, W, C) array (same mapping as ImageOps.exif_transpose)."""
    ops = {
        2: lambda a: a[:, ::-1],
        3: lambda a: a[::-1, ::-1],
        4: lambda a: a[::-1],
        5: lambda a: np.swapaxes(a, 0, 1),
        6: lambda a: np.rot90(a, -1),
        7: lambda a: np.swapaxes(a[::-1, ::-1], 0, 1),
        8: lambda a: np.rot90(a, 1),
    }
    op = ops.get(orientation)
    return np.ascontiguousarray(op(arr)) if op else arr


def _icc_to_srgb_lut(icc: bytes | None) -> np.ndarray | None:
    """3D LUT (encoded in -> sRGB encoded out) for an embedded non-sRGB profile, via LittleCMS."""
    if not icc:
        return None
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        if "srgb" in (ImageCms.getProfileDescription(src) or "").lower():
            return None
        n = 52  # levels k*5 = k/51: an exact uniform grid in 8-bit
        lv = (np.arange(n) * 5).astype(np.uint8)
        r, g, b = np.meshgrid(lv, lv, lv, indexing="ij")
        grid = Image.fromarray(np.stack([r, g, b], axis=-1).reshape(n * n, n, 3))
        out = ImageCms.profileToProfile(grid, src, ImageCms.createProfile("sRGB"), outputMode="RGB")
        return np.asarray(out, dtype=np.float32).reshape(n, n, n, 3) / 255.0
    except Exception:  # noqa: BLE001 - unreadable profile: treat pixels as sRGB
        return None


def load(path: str | os.PathLike, long_edge: int | None = None, fast: bool = False) -> Photo:
    """Load a photo as linear RGB.

    ``long_edge`` downsizes (area filter) after decoding; ``fast`` lets raw
    files use LibRaw's half-size decode, which is plenty for analysis/previews.
    """
    path = Path(path)
    if is_raw(path):
        return _load_raw(path, long_edge, fast)
    return _load_raster(path, long_edge)


def _load_raster(path: Path, long_edge: int | None) -> Photo:
    ext = path.suffix.lower()
    if ext in (".heic", ".heif"):
        try:
            import pillow_heif  # type: ignore

            pillow_heif.register_heif_opener()
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("HEIC support needs: pip install pillow-heif") from exc
    if ext in (".tif", ".tiff", ".png"):
        arr, meta = _read_high_bit(path)
        if arr is not None and arr.dtype != np.uint8:
            return _load_high_bit(path, arr, meta, long_edge)
    with Image.open(path) as im:
        exif_bytes, when, camera, orientation = _exif_from_pil(im)
        full = im.size if orientation not in (5, 6, 7, 8) else (im.size[1], im.size[0])
        if long_edge and im.format == "JPEG" and max(im.size) >= 2 * long_edge:
            # decode straight at reduced size (DCT scaling): much faster for previews/analysis
            f = long_edge / max(im.size)
            im.draft("RGB", (max(1, int(im.size[0] * f)), max(1, int(im.size[1] * f))))
        im = ImageOps.exif_transpose(im)
        icc = im.info.get("icc_profile")
        im = im.convert("RGB")
        if icc:
            im = _to_srgb(im, icc)
        rgb = srgb_to_linear(np.asarray(im, dtype=np.uint8))
    rgb = _resize_long_edge(rgb, long_edge)
    return Photo(path, rgb, False, None, exif_bytes, when, camera, full)


def _read_high_bit(path: Path) -> tuple[np.ndarray | None, tuple[int, bytes | None] | None]:
    """Pixels (RGB, native bit depth) of a TIFF/PNG plus (orientation, ICC) when tifffile can read them.

    tifffile reads TIFF tags reliably (OpenCV 5 refuses some TIFFs with an
    orientation tag); OpenCV covers compressions tifffile can't decode alone
    (LZW) and 16-bit PNG.
    """
    meta = None
    if path.suffix.lower() in (".tif", ".tiff"):
        try:
            import tifffile

            with tifffile.TiffFile(path) as tf:
                page = tf.pages[0]
                if int(page.photometric) not in (1, 2):  # MINISBLACK, RGB (works on old tifffile too)
                    raise ValueError("not RGB or grey (e.g. CMYK): let OpenCV / Pillow convert it")
                orientation = int(page.tags[274].value) if 274 in page.tags else 1
                icc = bytes(page.tags[34675].value) if 34675 in page.tags else None
                meta = (orientation, icc)
                arr = page.asarray()
            if arr.ndim == 3 and arr.shape[0] in (3, 4) and arr.shape[-1] not in (3, 4):
                arr = np.moveaxis(arr, 0, -1)  # planar -> interleaved
            if arr.ndim == 2:
                arr = np.repeat(arr[..., None], 3, axis=2)
            return np.ascontiguousarray(arr[..., :3]), meta
        except Exception:  # noqa: BLE001 - fall back to OpenCV / Pillow
            pass
    arr = cv2.imread(str(path), cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH | cv2.IMREAD_ANYCOLOR)
    if arr is None:
        return None, meta
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    return cv2.cvtColor(np.ascontiguousarray(arr[..., :3]), cv2.COLOR_BGR2RGB), meta


def _load_high_bit(path: Path, arr: np.ndarray, meta: tuple | None, long_edge: int | None) -> Photo:
    """16-bit / float TIFF and PNG: full precision, plus orientation and ICC from the header."""
    maxv = 65535.0 if arr.dtype == np.uint16 else (1.0 if arr.dtype.kind == "f" else float(np.iinfo(arr.dtype).max))
    enc = arr.astype(np.float32)
    enc *= np.float32(1.0 / maxv)
    exif_bytes, when, camera = None, None, ""
    orientation, icc = meta if meta else (1, None)
    try:
        with Image.open(path) as im:
            exif_bytes, when, camera, pil_orientation = _exif_from_pil(im)
            if meta is None:
                orientation, icc = pil_orientation, im.info.get("icc_profile")
    except Exception:  # noqa: BLE001 - header unreadable by Pillow: pixels still usable
        pass
    enc = _orient(np.clip(enc, 0.0, 1.0), orientation)
    lut = _icc_to_srgb_lut(icc)
    if lut is not None:
        from .engine import apply_lut

        enc = apply_lut(enc, lut)
    full = (enc.shape[1], enc.shape[0])
    rgb = _resize_long_edge(srgb_to_linear(enc), long_edge)
    return Photo(path, rgb, False, None, exif_bytes, when, camera, full)


def _to_srgb(im: Image.Image, icc: bytes) -> Image.Image:
    """Convert from an embedded profile (e.g. Display P3 / Adobe RGB) to sRGB."""
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        if "srgb" in (ImageCms.getProfileDescription(src) or "").lower():
            return im
        return ImageCms.profileToProfile(im, src, ImageCms.createProfile("sRGB"), outputMode="RGB")
    except Exception:  # noqa: BLE001 - fall back to treating pixels as sRGB
        return im


def _load_raw(path: Path, long_edge: int | None, fast: bool) -> Photo:
    import rawpy

    with rawpy.imread(str(path)) as raw:
        sizes = raw.sizes
        full = (sizes.width, sizes.height)
        if sizes.flip in (5, 6):
            full = (sizes.height, sizes.width)
        half = fast or (long_edge is not None and long_edge * 2 <= max(full))
        rgb16 = raw.postprocess(
            use_camera_wb=True,
            output_color=rawpy.ColorSpace.sRGB,
            gamma=(1, 1),
            no_auto_bright=True,
            output_bps=16,
            half_size=half,
            highlight_mode=rawpy.HighlightMode.Blend,
            demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
        )
        wb_as_shot = None
        try:
            mul = np.asarray(raw.camera_whitebalance, dtype=np.float64)
            if mul[3] == 0:
                mul[3] = mul[1]
            if mul[1] > 0 and np.all(mul[:3] > 0):
                neutral = mul[1] / mul[:3]
                wb_as_shot = wb.camera_neutral_to_temp_tint(neutral, raw.rgb_xyz_matrix)
        except Exception:  # noqa: BLE001 - some formats lack the matrix
            wb_as_shot = None
        exif_bytes, when, camera = None, None, ""
        try:
            thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                with Image.open(io.BytesIO(thumb.data)) as im:
                    exif_bytes, when, camera, _ = _exif_from_pil(im)
        except Exception:  # noqa: BLE001 - many raws have no usable thumbnail
            pass
    rgb = rgb16.astype(np.float32)
    del rgb16
    rgb *= np.float32(1.0 / 65535.0)
    rgb = _resize_long_edge(rgb, long_edge)
    return Photo(path, rgb, True, wb_as_shot, exif_bytes, when, camera, full)


def srgb_icc() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


_DITHER: dict = {}


def _dither_tile(width: int, rows: int = 256) -> np.ndarray:
    tile = _DITHER.get(width)
    if tile is None:
        rng = np.random.default_rng(12345)
        tile = (rng.random((rows, width), dtype=np.float32) - rng.random((rows, width), dtype=np.float32)) * 0.5 + 0.5
        _DITHER.clear()
        _DITHER[width] = tile
    return tile


def dither_8bit(enc: np.ndarray) -> np.ndarray:
    """sRGB-encoded float -> 8 bit with triangular dither (no banding in skies)."""
    h, w = enc.shape[:2]
    tile = _dither_tile(w)
    out = np.empty((h, w, 3), np.uint8)
    rows = tile.shape[0]
    for r0 in range(0, h, rows):
        r1 = min(h, r0 + rows)
        v = enc[r0:r1] * np.float32(255.0) + tile[: r1 - r0, :, None]
        np.clip(v, 0, 255, out=v)
        out[r0:r1] = v.astype(np.uint8)
    return out


def encode_8bit(rgb_display_linear: np.ndarray) -> np.ndarray:
    """Display-linear float -> 8-bit sRGB with triangular dither."""
    return dither_8bit(linear_to_srgb(np.clip(rgb_display_linear, 0.0, 1.0)))


def _save_tiff(enc: np.ndarray, path: Path, exif: bytes | None) -> None:
    """16-bit TIFF with the sRGB profile and the camera make/model/capture time."""
    import tifffile

    arr = np.clip(enc * 65535.0 + 0.5, 0, 65535).astype(np.uint16)
    extratags, when = [], None
    if exif:
        try:
            ex = Image.Exif()
            ex.load(exif)
            for code in (0x010F, 0x0110):  # Make, Model
                v = str(ex.get(code, "")).strip("\x00 ")
                if v:
                    extratags.append((code, "s", 0, v, True))
            when = _parse_exif_time(ex)
        except Exception:  # noqa: BLE001 - metadata is optional
            extratags, when = [], None
    tifffile.imwrite(path, arr, photometric="rgb", compression="zlib", iccprofile=srgb_icc(),
                     datetime=when, software="vivaah", extratags=extratags)


def save(rgb: np.ndarray, path: str | os.PathLike, quality: int = 95,
         exif: bytes | None = None, encoded: bool = False) -> Path:
    """Write a rendered image (sRGB). Format follows the extension (.jpg/.tif/.png).

    ``rgb`` is display-linear, or already sRGB-encoded when ``encoded=True``.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()
    # both writers clip while quantising, so encoded input needs no full-size copy here
    enc = np.asarray(rgb, dtype=np.float32) if encoded else linear_to_srgb(np.clip(rgb, 0, 1))
    if ext in (".tif", ".tiff"):
        _save_tiff(enc, path, exif)
        return path
    im = Image.fromarray(dither_8bit(enc))
    kwargs = {"icc_profile": srgb_icc()}
    if exif:
        kwargs["exif"] = exif
    if ext in (".jpg", ".jpeg"):
        im.save(path, "JPEG", quality=quality, subsampling=0, **kwargs)
    else:
        im.save(path, **kwargs)
    return path
