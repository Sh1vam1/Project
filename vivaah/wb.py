"""White-balance maths compatible with Lightroom / Camera Raw.

Lightroom expresses white balance as a correlated colour temperature (Kelvin)
plus a green-magenta *tint*.  The conversion between those numbers and CIE xy
chromaticity follows the public DNG SDK (Robertson's method with the DNG tint
scale), so the Temperature/Tint values vivaah writes into XMP sidecars mean the
same thing to Lightroom that they mean to us.

vivaah stores white balance *relative* to the "as shot" rendering:

* ``temp_shift`` in mireds, positive = warmer picture (higher Kelvin value)
* ``tint_shift`` in Lightroom tint units, positive = more magenta picture
"""
from __future__ import annotations

import math

import numpy as np

from .color import BRADFORD, BRADFORD_INV, D65_XY, SRGB_TO_XYZ, XYZ_TO_SRGB, xy_to_xyz

_TINT_SCALE = -3000.0

# (mired, u, v, slope) -- Wyszecki & Stiles table as used by the DNG SDK,
# including the well known correction of the 325 mired entry.
_TEMP_TABLE = [
    (0, 0.18006, 0.26352, -0.24341),
    (10, 0.18066, 0.26589, -0.25479),
    (20, 0.18133, 0.26846, -0.26876),
    (30, 0.18208, 0.27119, -0.28539),
    (40, 0.18293, 0.27407, -0.30470),
    (50, 0.18388, 0.27709, -0.32675),
    (60, 0.18494, 0.28021, -0.35156),
    (70, 0.18611, 0.28342, -0.37915),
    (80, 0.18740, 0.28668, -0.40955),
    (90, 0.18880, 0.28997, -0.44278),
    (100, 0.19032, 0.29326, -0.47888),
    (125, 0.19462, 0.30141, -0.58204),
    (150, 0.19962, 0.30921, -0.70471),
    (175, 0.20525, 0.31647, -0.84901),
    (200, 0.21142, 0.32312, -1.0182),
    (225, 0.21807, 0.32909, -1.2168),
    (250, 0.22511, 0.33439, -1.4512),
    (275, 0.23247, 0.33904, -1.7298),
    (300, 0.24010, 0.34308, -2.0637),
    (325, 0.24792, 0.34655, -2.4681),
    (350, 0.25591, 0.34951, -2.9641),
    (375, 0.26400, 0.35200, -3.5814),
    (400, 0.27218, 0.35407, -4.3633),
    (425, 0.28039, 0.35577, -5.3762),
    (450, 0.28863, 0.35714, -6.7262),
    (475, 0.29685, 0.35823, -8.5955),
    (500, 0.30505, 0.35907, -11.324),
    (525, 0.31320, 0.35968, -15.628),
    (550, 0.32129, 0.36011, -23.325),
    (575, 0.32931, 0.36038, -40.770),
    (600, 0.33724, 0.36051, -116.45),
]

MIN_KELVIN = 2000.0
MAX_KELVIN = 50000.0


def xy_to_temp_tint(x: float, y: float) -> tuple[float, float]:
    """CIE xy chromaticity -> (Kelvin, tint) exactly as the DNG SDK computes it."""
    u = 2.0 * x / (1.5 - x + 6.0 * y)
    v = 3.0 * y / (1.5 - x + 6.0 * y)
    last_dt = last_du = last_dv = 0.0
    for i in range(1, len(_TEMP_TABLE)):
        r0, u0, v0, t0 = _TEMP_TABLE[i - 1]
        r1, u1, v1, t1 = _TEMP_TABLE[i]
        du, dv = 1.0, t1
        ln = math.hypot(du, dv)
        du, dv = du / ln, dv / ln
        uu, vv = u - u1, v - v1
        dt = -uu * dv + vv * du
        if dt <= 0.0 or i == len(_TEMP_TABLE) - 1:
            dt = -min(dt, 0.0)
            f = 0.0 if i == 1 else dt / (last_dt + dt)
            temperature = 1.0e6 / (r0 * f + r1 * (1.0 - f))
            uu = u - (u0 * f + u1 * (1.0 - f))
            vv = v - (v0 * f + v1 * (1.0 - f))
            du = du * (1.0 - f) + last_du * f
            dv = dv * (1.0 - f) + last_dv * f
            ln = math.hypot(du, dv)
            du, dv = du / ln, dv / ln
            tint = (uu * du + vv * dv) * _TINT_SCALE
            return temperature, tint
        last_dt, last_du, last_dv = dt, du, dv
    raise AssertionError("unreachable")


def temp_tint_to_xy(temperature: float, tint: float) -> tuple[float, float]:
    """(Kelvin, tint) -> CIE xy chromaticity (DNG SDK inverse)."""
    r = 1.0e6 / temperature
    offset = tint / _TINT_SCALE
    n = len(_TEMP_TABLE)
    for i in range(n - 1):
        r0, u0, v0, t0 = _TEMP_TABLE[i]
        r1, u1, v1, t1 = _TEMP_TABLE[i + 1]
        if r < r1 or i == n - 2:
            f = (r1 - r) / (r1 - r0)
            u = u0 * f + u1 * (1.0 - f)
            v = v0 * f + v1 * (1.0 - f)
            l0 = math.hypot(1.0, t0)
            l1 = math.hypot(1.0, t1)
            uu = (1.0 / l0) * f + (1.0 / l1) * (1.0 - f)
            vv = (t0 / l0) * f + (t1 / l1) * (1.0 - f)
            ln = math.hypot(uu, vv)
            u += uu / ln * offset
            v += vv / ln * offset
            x = 1.5 * u / (u - 4.0 * v + 2.0)
            y = v / (u - 4.0 * v + 2.0)
            return x, y
    raise AssertionError("unreachable")


_TT = np.array(_TEMP_TABLE)


def temp_tint_to_xy_vec(temperature: np.ndarray, tint: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Vectorised :func:`temp_tint_to_xy` for arrays of Kelvin/tint values."""
    r = 1.0e6 / np.asarray(temperature, dtype=np.float64)
    offset = np.asarray(tint, dtype=np.float64) / _TINT_SCALE
    i = np.clip(np.searchsorted(_TT[:, 0], r, side="right") - 1, 0, len(_TT) - 2)
    r0, u0, v0, t0 = (_TT[i, k] for k in range(4))
    r1, u1, v1, t1 = (_TT[i + 1, k] for k in range(4))
    f = (r1 - r) / (r1 - r0)
    u = u0 * f + u1 * (1.0 - f)
    v = v0 * f + v1 * (1.0 - f)
    l0 = np.hypot(1.0, t0)
    l1 = np.hypot(1.0, t1)
    uu = (1.0 / l0) * f + (1.0 / l1) * (1.0 - f)
    vv = (t0 / l0) * f + (t1 / l1) * (1.0 - f)
    ln = np.hypot(uu, vv)
    u = u + uu / ln * offset
    v = v + vv / ln * offset
    den = u - 4.0 * v + 2.0
    return 1.5 * u / den, v / den


D65_TEMP, D65_TINT = xy_to_temp_tint(*D65_XY)


def apply_shift(temperature: float, tint: float, temp_shift: float, tint_shift: float) -> tuple[float, float]:
    """Apply a relative (mired, tint) shift to an absolute Temperature/Tint."""
    mired = 1.0e6 / temperature - temp_shift
    kelvin = float(np.clip(1.0e6 / max(mired, 1.0e6 / MAX_KELVIN), MIN_KELVIN, MAX_KELVIN))
    return kelvin, float(np.clip(tint + tint_shift, -150.0, 150.0))


def shift_between(base: tuple[float, float], target: tuple[float, float]) -> tuple[float, float]:
    """Relative (temp_shift, tint_shift) that moves ``base`` to ``target`` (Kelvin, tint)."""
    return 1.0e6 / base[0] - 1.0e6 / target[0], target[1] - base[1]


def _cone(xy: tuple[float, float]) -> np.ndarray:
    return BRADFORD @ xy_to_xyz(xy[0], xy[1])


def adaptation_matrix(src_white_xy: tuple[float, float], dst_white_xy: tuple[float, float] = D65_XY) -> np.ndarray:
    """Linear-sRGB matrix mapping colours seen under ``src`` white to ``dst`` white (Bradford)."""
    scale = _cone(dst_white_xy) / _cone(src_white_xy)
    m = XYZ_TO_SRGB @ BRADFORD_INV @ np.diag(scale) @ BRADFORD @ SRGB_TO_XYZ
    return m.astype(np.float32)


def shift_matrix(temp_shift: float, tint_shift: float, base: tuple[float, float] | None = None) -> np.ndarray:
    """Linear-sRGB matrix that re-balances an image rendered for ``base`` (Kelvin, tint).

    The image is assumed to have been rendered so that the ``base`` illuminant
    maps to display white.  Re-rendering for a new illuminant ``base + shift``
    equals a von Kries scaling by cone(base) / cone(new) in Bradford space.
    """
    if abs(temp_shift) < 1e-6 and abs(tint_shift) < 1e-6:
        return np.eye(3, dtype=np.float32)
    base = base or (D65_TEMP, D65_TINT)
    new = apply_shift(base[0], base[1], temp_shift, tint_shift)
    return adaptation_matrix(temp_tint_to_xy(*new), temp_tint_to_xy(*base))


def shift_to_neutralize(observed_rgb: np.ndarray, base: tuple[float, float] | None = None) -> tuple[float, float]:
    """Relative (temp_shift, tint_shift) that turns ``observed_rgb`` (linear) neutral.

    ``observed_rgb`` is the colour a truly neutral object currently has in an
    image rendered for the ``base`` illuminant.
    """
    base = base or (D65_TEMP, D65_TINT)
    obs = np.maximum(np.asarray(observed_rgb, dtype=np.float64), 1e-6)
    rho_obs = BRADFORD @ (SRGB_TO_XYZ @ obs)
    rho_obs = rho_obs / rho_obs[1]
    rho_d65 = _cone(D65_XY)
    rho_d65 = rho_d65 / rho_d65[1]
    rho_new = _cone(temp_tint_to_xy(*base)) * rho_obs / rho_d65
    xyz_new = BRADFORD_INV @ rho_new
    s = xyz_new.sum()
    new = xy_to_temp_tint(xyz_new[0] / s, xyz_new[1] / s)
    new = (float(np.clip(new[0], MIN_KELVIN, MAX_KELVIN)), float(np.clip(new[1], -150, 150)))
    return shift_between(base, new)


def camera_neutral_to_temp_tint(neutral_cam: np.ndarray, xyz_to_cam: np.ndarray) -> tuple[float, float] | None:
    """As-shot camera neutral (camera RGB) + ColorMatrix (XYZ->camera) -> (Kelvin, tint)."""
    m = np.asarray(xyz_to_cam, dtype=np.float64)[:3, :3]
    if not np.any(m) or abs(np.linalg.det(m)) < 1e-9:
        return None
    xyz = np.linalg.solve(m, np.asarray(neutral_cam, dtype=np.float64)[:3])
    s = xyz.sum()
    if s <= 0 or np.any(~np.isfinite(xyz)):
        return None
    try:
        t, tint = xy_to_temp_tint(xyz[0] / s, xyz[1] / s)
    except (ZeroDivisionError, ValueError):
        return None
    return float(np.clip(t, MIN_KELVIN, MAX_KELVIN)), float(np.clip(tint, -150, 150))
