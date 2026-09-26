"""3D LUT export (.cube) so a look can be used on video in Premiere Pro,
DaVinci Resolve, Final Cut Pro or Photoshop.

The LUT carries the look's colour (tone curves, HSL, colour grading,
vibrance/saturation and the basic tone sliders as a global curve). Per-photo
automation, skin masks and other local effects are not part of a LUT.
Input and output are Rec.709 / sRGB display-referred.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .color import linear_to_srgb, srgb_to_linear
from .engine import GREY_ENC, compress_gamut, global_color, tone_curve_lut, calibration_matrix
from .looks import Look
from .settings import DevelopSettings
from . import wb


def look_function(s: DevelopSettings):
    """Global (pixel-wise) version of the look used for LUTs."""
    xs, ys = tone_curve_lut(s)
    m = calibration_matrix(s) @ wb.shift_matrix(s.temp_shift, s.tint_shift)

    def fn(enc: np.ndarray) -> np.ndarray:
        rgb = srgb_to_linear(enc) @ m.T
        Y = np.maximum(rgb @ np.array([0.2126729, 0.7151522, 0.0721750], np.float32), 1e-7)
        p = linear_to_srgb(Y)
        p2 = np.interp(p, xs, ys).astype(np.float32)
        rgb = compress_gamut(rgb * (srgb_to_linear(p2) / Y)[..., None])
        return linear_to_srgb(compress_gamut(global_color(rgb, s)))

    return fn


def cube_text(look: Look, size: int = 33, variant: str | None = None, warmth: bool = True) -> str:
    s, t, used = look.resolve(variant)
    s = s.copy()
    if warmth:
        s.temp_shift += t.warmth
        s.tint_shift += t.tint
    fn = look_function(s)
    g = np.linspace(0.0, 1.0, size, dtype=np.float32)
    # .cube order: red changes fastest, then green, then blue
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    enc = np.stack([r, gg, b], axis=-1).reshape(-1, 3)
    out = np.clip(fn(enc), 0.0, 1.0)
    title = look.name if used == "base" else f"{look.name} ({used})"
    lines = [f'TITLE "{title}"', "# made with vivaah", f"LUT_3D_SIZE {size}",
             "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    lines += [f"{v[0]:.6f} {v[1]:.6f} {v[2]:.6f}" for v in out]
    return "\n".join(lines) + "\n"


def write_cube(look: Look, path: str | Path, size: int = 33, variant: str | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cube_text(look, size, variant))
    return path


def read_cube(path: str | Path) -> np.ndarray:
    """Parse a .cube file into an (N, N, N, 3) array indexed [r, g, b]."""
    size = None
    rows = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("LUT_3D_SIZE"):
            size = int(line.split()[1])
        elif line[0].isdigit() or line[0] in "-.":
            rows.append([float(v) for v in line.split()[:3]])
    if size is None or len(rows) != size ** 3:
        raise ValueError(f"not a valid 3D .cube file: {path}")
    arr = np.asarray(rows, dtype=np.float32).reshape(size, size, size, 3)  # [b, g, r]
    return np.transpose(arr, (2, 1, 0, 3))
