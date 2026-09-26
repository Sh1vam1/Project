"""Reading and writing photos.

* Raw files from any camera LibRaw supports (Canon, Nikon, Sony, Fujifilm,
  Panasonic, Olympus/OM, Pentax, Leica, Hasselblad, DNG, ...) are decoded to
  scene-linear sRGB with the camera's as-shot white balance.
* JPEG / TIFF / PNG (and HEIC with pillow-heif installed) are decoded and
  linearised; EXIF orientation is applied.
* Output is sRGB with an embedded ICC profile, dithered to 8 bit (JPEG) or
  16 bit TIFF, carrying the original EXIF where available.
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
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
    meta: dict = field(default_factory=dict)


def is_raw(path: str | os.PathLike) -> bool:
    return Path(path).suffix.lower() in RAW_EXTENSIONS


def find_photos(inputs: list[str | os.PathLike], recursive: bool = True) -> list[Path]:
    """Expand files/folders into a sorted list of supported photos."""
    out: list[Path] = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            out.extend(q for q in it if q.is_file() and q.suffix.lower() in SUPPORTED and not q.name.startswith("."))
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


def _exif_from_pil(im: Image.Image) -> tuple[bytes | None, datetime | None, str]:
    try:
        exif = im.getexif()
    except Exception:  # noqa: BLE001
        return None, None, ""
    if not exif:
        return None, None, ""
    when = _parse_exif_time(exif)
    camera = " ".join(str(exif.get(t, "")).strip("\x00 ") for t in (0x010F, 0x0110)).strip()
    exif[_EXIF_ORIENTATION] = 1  # pixels are written upright
    try:
        return exif.tobytes(), when, camera
    except Exception:  # noqa: BLE001
        return None, when, camera


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
    exif_bytes, when, camera = None, None, ""
    if ext in (".tif", ".tiff", ".png"):
        arr = cv2.imread(str(path), cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH | cv2.IMREAD_ANYCOLOR)
        if arr is None:
            raise ValueError(f"cannot read {path}")
        if arr.ndim == 2:
            arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
        if arr.shape[2] == 4:
            arr = arr[..., :3]
        maxv = 65535.0 if arr.dtype == np.uint16 else (1.0 if arr.dtype.kind == "f" else 255.0)
        enc = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB).astype(np.float32) / maxv
        try:
            with Image.open(path) as im:
                exif_bytes, when, camera = _exif_from_pil(im)
        except Exception:  # noqa: BLE001
            pass
    else:
        with Image.open(path) as im:
            exif_bytes, when, camera = _exif_from_pil(im)
            full = im.size
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
        if full != im.size and max(full) and im.size[0] / im.size[1] != full[0] / full[1]:
            full = (full[1], full[0])  # rotated by EXIF orientation
        rgb = _resize_long_edge(rgb, long_edge)
        return Photo(path, rgb, False, None, exif_bytes, when, camera, full)
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
                    exif_bytes, when, camera = _exif_from_pil(im)
        except Exception:  # noqa: BLE001 - many raws have no usable thumbnail
            pass
    rgb = rgb16.astype(np.float32) / 65535.0
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


def dither_8bit(enc: np.ndarray, seed: int = 0) -> np.ndarray:
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


def encode_8bit(rgb_display_linear: np.ndarray, seed: int = 0) -> np.ndarray:
    """Display-linear float -> 8-bit sRGB with triangular dither."""
    return dither_8bit(linear_to_srgb(np.clip(rgb_display_linear, 0.0, 1.0)), seed)


def save(rgb: np.ndarray, path: str | os.PathLike, quality: int = 95,
         exif: bytes | None = None, encoded: bool = False) -> Path:
    """Write a rendered image (sRGB). Format follows the extension (.jpg/.tif/.png).

    ``rgb`` is display-linear, or already sRGB-encoded when ``encoded=True``.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()
    enc = np.clip(rgb, 0, 1) if encoded else linear_to_srgb(np.clip(rgb, 0, 1))
    if ext in (".tif", ".tiff"):
        arr = np.clip(enc * 65535.0 + 0.5, 0, 65535).astype(np.uint16)
        cv2.imwrite(str(path), cv2.cvtColor(arr, cv2.COLOR_RGB2BGR))
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
