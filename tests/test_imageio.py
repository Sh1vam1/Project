"""Loading and saving: orientation, colour profiles, high bit depth, metadata, folder scanning."""
import struct
import zlib

import numpy as np
import tifffile
from PIL import Image

from vivaah import imageio
from vivaah.color import linear_to_srgb

from icc_fixture import DISPLAY_P3


def _enc8(photo) -> np.ndarray:
    return np.round(np.clip(linear_to_srgb(photo.rgb), 0, 1) * 255).astype(int)


def _marked(h: int, w: int) -> np.ndarray:
    """Grey image with a red block in the top-left corner (shows where 'up' went)."""
    a = np.full((h, w, 3), 128, np.uint8)
    a[: h // 4, : w // 4] = (220, 30, 30)
    return a


def _exif(orientation: int, make: str = "", model: str = "", when: str = "") -> bytes:
    ex = Image.Exif()
    ex[0x0112] = orientation
    if make:
        ex[0x010F] = make
    if model:
        ex[0x0110] = model
    if when:
        ex[0x0132] = when
    return ex.tobytes()


def test_portrait_jpeg_loads_upright(tmp_path):
    # cameras store vertical shots landscape + Orientation=6 (rotate 90 degrees clockwise to view)
    p = tmp_path / "portrait.jpg"
    Image.fromarray(_marked(40, 60)).save(p, quality=95, exif=_exif(6))
    photo = imageio.load(p)
    assert photo.rgb.shape[:2] == (60, 40)
    assert photo.full_size == (40, 60)
    enc = _enc8(photo)
    assert enc[2, -2, 0] > 180 and enc[2, -2, 1] < 80  # the stored top-left is now top-right
    assert enc[-2, 2, 0] < 160
    out = imageio.save(photo.rgb, tmp_path / "out.jpg", exif=photo.exif)
    with Image.open(out) as im:
        assert im.size == (40, 60)
        assert im.getexif().get(0x0112, 1) == 1  # pixels are upright: no second rotation in viewers


def test_preview_size_jpeg_keeps_orientation_and_full_size(tmp_path):
    p = tmp_path / "big_portrait.jpg"
    Image.fromarray(_marked(400, 600)).save(p, quality=90, exif=_exif(8))
    photo = imageio.load(p, long_edge=100, fast=True)
    assert max(photo.rgb.shape[:2]) == 100 and photo.rgb.shape[0] > photo.rgb.shape[1]
    assert photo.full_size == (400, 600)


def _p3_reference(tmp_path, colour) -> np.ndarray:
    """The same colour as an 8-bit Display-P3 JPEG (Pillow's colour management path)."""
    block = np.zeros((16, 16, 3), np.uint8)
    block[:] = colour
    p = tmp_path / "p3.jpg"
    Image.fromarray(block).save(p, quality=100, subsampling=0, icc_profile=DISPLAY_P3)
    return _enc8(imageio.load(p))[8, 8]


def _png16_with_icc(path, arr16: np.ndarray, icc: bytes) -> None:
    """16-bit RGB PNG with an embedded profile (OpenCV writes the pixels, we add the iCCP chunk)."""
    import cv2

    assert cv2.imwrite(str(path), cv2.cvtColor(arr16, cv2.COLOR_RGB2BGR))
    data = path.read_bytes()
    body = b"P3\0\0" + zlib.compress(icc)
    chunk = struct.pack(">I", len(body)) + b"iCCP" + body + struct.pack(">I", zlib.crc32(b"iCCP" + body))
    end_ihdr = 8 + 8 + 13 + 4  # signature + IHDR header/data/crc
    path.write_bytes(data[:end_ihdr] + chunk + data[end_ihdr:])


def test_16bit_tiff_and_png_respect_embedded_profile(tmp_path):
    colour = (200, 60, 40)
    ref = _p3_reference(tmp_path, colour)
    assert ref[0] > 205 and ref[1] < 50  # P3 red is more saturated than sRGB red
    arr16 = np.zeros((16, 16, 3), np.uint16)
    arr16[:] = np.array(colour) * 257
    tif = tmp_path / "p3_16.tif"
    tifffile.imwrite(tif, arr16, photometric="rgb", iccprofile=DISPLAY_P3)
    png = tmp_path / "p3_16.png"
    _png16_with_icc(png, arr16, DISPLAY_P3)
    for path in (tif, png):
        got = _enc8(imageio.load(path))[8, 8]
        assert np.abs(got - ref).max() <= 3, (path.name, got, ref)
    plain = tmp_path / "srgb_16.tif"  # untagged = sRGB: unchanged
    tifffile.imwrite(plain, arr16, photometric="rgb")
    assert np.abs(_enc8(imageio.load(plain))[8, 8] - colour).max() <= 1


def test_16bit_tiff_orientation_tag(tmp_path):
    arr16 = (_marked(40, 60).astype(np.uint16) * 257)
    p = tmp_path / "rotated.tif"
    tifffile.imwrite(p, arr16, photometric="rgb", extratags=[(274, "H", 1, 6, True)])
    photo = imageio.load(p)
    assert photo.rgb.shape[:2] == (60, 40)
    enc = _enc8(photo)
    assert enc[2, -2, 0] > 180 and enc[2, -2, 1] < 80


def test_tiff_export_has_profile_and_camera_metadata(tmp_path):
    rgb = np.full((20, 30, 3), 0.18, np.float32)
    exif = _exif(1, make="NIKON CORPORATION", model="NIKON Z 6_2", when="2026:02:14 18:30:05")
    out = imageio.save(rgb, tmp_path / "out.tif", exif=exif)
    with tifffile.TiffFile(out) as tf:
        page = tf.pages[0]
        assert page.dtype == np.uint16 and page.shape == (20, 30, 3)
        assert bytes(page.tags[34675].value) == imageio.srgb_icc()
        assert page.tags[271].value == "NIKON CORPORATION"
        assert page.tags[272].value == "NIKON Z 6_2"
        assert page.tags[306].value.startswith("2026:02:14")
    back = imageio.load(out)
    assert abs(float(back.rgb.mean()) - 0.18) < 0.002
    assert back.camera  # make/model survive a round trip


def test_find_photos_skips_outputs_previews_and_jpeg_twins(tmp_path):
    shoot = tmp_path / "shoot"
    (shoot / "vivaah-graded").mkdir(parents=True)
    (shoot / "custom-out").mkdir()
    (shoot / ".cache").mkdir()
    for name in ("DSC_0001.NEF", "DSC_0001.JPG", "DSC_0002.jpg", "DSC_0002_looks.jpg",
                 "vivaah-graded/DSC_0002.jpg", "custom-out/DSC_0003.jpg", ".cache/x.jpg"):
        (shoot / name).write_bytes(b"")
    imageio.mark_output_dir(shoot / "custom-out")  # any folder vivaah exported into is marked
    found = [p.relative_to(shoot).as_posix() for p in imageio.find_photos([shoot])]
    assert found == ["DSC_0001.NEF", "DSC_0002.jpg"]
