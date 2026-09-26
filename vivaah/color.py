"""Colour-space conversions.

Conventions used everywhere in vivaah:

* Images are float32 arrays shaped (..., 3) with channels last.
* "linear" means linear-light sRGB / Rec.709 primaries with a D65 white.
* "encoded" means the sRGB transfer function has been applied (0..1).
* Out-of-range values are allowed while processing and only clipped on output,
  so intermediate steps never lose highlight or gamut information.
"""
from __future__ import annotations

import colorsys

import cv2
import numpy as np

SRGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ]
)
XYZ_TO_SRGB = np.linalg.inv(SRGB_TO_XYZ)
D65_XY = (0.3127, 0.3290)
D65_XYZ = np.array([0.95047, 1.0, 1.08883])
LUMA = SRGB_TO_XYZ[1].astype(np.float32)  # Rec.709 luminance weights

# Bradford cone-response matrix (chromatic adaptation).
BRADFORD = np.array(
    [
        [0.8951, 0.2664, -0.1614],
        [-0.7502, 1.7135, 0.0367],
        [0.0389, -0.0685, 1.0296],
    ]
)
BRADFORD_INV = np.linalg.inv(BRADFORD)

_OK_M1 = np.array(
    [
        [0.4122214708, 0.5363325363, 0.0514459929],
        [0.2119034982, 0.6806995451, 0.1073969566],
        [0.0883024619, 0.2817188376, 0.6299787005],
    ],
    dtype=np.float32,
)
_OK_M2 = np.array(
    [
        [0.2104542553, 0.7936177850, -0.0040720468],
        [1.9779984951, -2.4285922050, 0.4505937099],
        [0.0259040371, 0.7827717662, -0.8086757660],
    ],
    dtype=np.float32,
)
_OK_M2_INV = np.linalg.inv(_OK_M2.astype(np.float64)).astype(np.float32)
_OK_M1_INV = np.linalg.inv(_OK_M1.astype(np.float64)).astype(np.float32)


_ENC_TO_LIN_8 = None


def srgb_to_linear(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v)
    if v.dtype == np.uint8:
        global _ENC_TO_LIN_8
        if _ENC_TO_LIN_8 is None:
            _ENC_TO_LIN_8 = srgb_to_linear(np.arange(256, dtype=np.float32) / 255.0)
        return _ENC_TO_LIN_8[v]
    v = np.asarray(v, dtype=np.float32)
    if v.size < 4096:
        return np.where(v <= 0.04045, v / 12.92,
                        np.power((np.maximum(v, 0.04045) + 0.055) / 1.055, 2.4)).astype(np.float32)
    v = np.ascontiguousarray(v)
    out = cv2.pow((np.maximum(v, 0.04045) + 0.055) * np.float32(1 / 1.055), 2.4)
    low = v <= 0.04045
    out[low] = v[low] * np.float32(1 / 12.92)
    return out


def linear_to_srgb(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    if v.size < 4096:
        return np.where(v <= 0.0031308, v * 12.92,
                        1.055 * np.power(np.maximum(v, 0.0031308), 1 / 2.4) - 0.055).astype(np.float32)
    v = np.ascontiguousarray(v)
    out = cv2.pow(np.maximum(v, 0.0031308), 1 / 2.4) * np.float32(1.055) - np.float32(0.055)
    low = v <= 0.0031308
    out[low] = v[low] * np.float32(12.92)
    return out


def luminance(rgb_linear: np.ndarray) -> np.ndarray:
    if rgb_linear.ndim == 3 and rgb_linear.dtype == np.float32 and rgb_linear.size > 30000:
        return cv2.transform(rgb_linear, LUMA.reshape(1, 3))
    return rgb_linear @ LUMA


def rgb_to_xyz(rgb_linear: np.ndarray) -> np.ndarray:
    return rgb_linear @ SRGB_TO_XYZ.T.astype(np.float32)


def xyz_to_rgb(xyz: np.ndarray) -> np.ndarray:
    return xyz @ XYZ_TO_SRGB.T.astype(np.float32)


def xy_to_xyz(x: float, y: float, Y: float = 1.0) -> np.ndarray:
    return np.array([x * Y / y, Y, (1.0 - x - y) * Y / y])


def xyz_to_xy(xyz: np.ndarray) -> tuple[float, float]:
    s = float(np.sum(xyz))
    if s <= 0:
        return D65_XY
    return float(xyz[0] / s), float(xyz[1] / s)


# --- CIE L*a*b* (used for analysis / reporting, familiar L* 0..100 scale) ---


def _lab_f(t: np.ndarray) -> np.ndarray:
    d = 6.0 / 29.0
    return np.where(t > d**3, np.cbrt(t), t / (3 * d * d) + 4.0 / 29.0)


def linear_to_lab(rgb_linear: np.ndarray) -> np.ndarray:
    xyz = rgb_to_xyz(rgb_linear) / D65_XYZ.astype(np.float32)
    f = _lab_f(xyz)
    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1).astype(np.float32)


def lab_to_linear(lab: np.ndarray) -> np.ndarray:
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (L + 16.0) / 116.0
    fx = fy + a / 500.0
    fz = fy - b / 200.0
    d = 6.0 / 29.0

    def finv(f):
        return np.where(f > d, f**3, 3 * d * d * (f - 4.0 / 29.0))

    xyz = np.stack([finv(fx), finv(fy), finv(fz)], axis=-1) * D65_XYZ.astype(np.float32)
    return xyz_to_rgb(xyz).astype(np.float32)


# --- OKLab (used for all colour edits: perceptually even hue and chroma) ---


def linear_to_oklab(rgb_linear: np.ndarray) -> np.ndarray:
    lms = np.cbrt(rgb_linear.astype(np.float32) @ _OK_M1.T)
    return (lms @ _OK_M2.T).astype(np.float32)


def oklab_to_linear(lab: np.ndarray) -> np.ndarray:
    lms = lab.astype(np.float32) @ _OK_M2_INV.T
    return ((lms * lms * lms) @ _OK_M1_INV.T).astype(np.float32)


def lab_to_lch(lab: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return lightness, chroma and hue (degrees 0..360) of a Lab/OKLab array."""
    L = lab[..., 0]
    C = np.hypot(lab[..., 1], lab[..., 2])
    h = np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360.0
    return L, C, h.astype(np.float32)


def lch_to_lab(L: np.ndarray, C: np.ndarray, h: np.ndarray) -> np.ndarray:
    hr = np.radians(h)
    return np.stack([L, C * np.cos(hr), C * np.sin(hr)], axis=-1).astype(np.float32)


def wheel_hue_to_oklab_hue(hue_deg: float) -> float:
    """Map a Lightroom colour-wheel / HSL hue (HSV degrees) to an OKLab hue angle."""
    r, g, b = colorsys.hsv_to_rgb((hue_deg % 360.0) / 360.0, 1.0, 1.0)
    lab = linear_to_oklab(srgb_to_linear(np.array([r, g, b], dtype=np.float32)))
    return float(np.degrees(np.arctan2(lab[2], lab[1])) % 360.0)


def smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def hue_distance(h1: np.ndarray, h2: float) -> np.ndarray:
    """Absolute circular distance between hue angles in degrees."""
    return np.abs((h1 - h2 + 180.0) % 360.0 - 180.0)
