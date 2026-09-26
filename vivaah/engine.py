"""The local renderer: a Lightroom-style develop pipeline built on NumPy/OpenCV.

Pipeline (all float32, linear light unless noted)::

    A  scene      white balance + calibration (3x3 matrix), exposure gain
    B  tone       base curve (raw) -> local tone mapping on a base/detail split:
                  contrast, highlights, shadows, whites, blacks, clarity, texture;
                  dehaze
    C  colour     tone curves, vibrance/saturation, HSL, colour grading
                  (pure per-colour function -> evaluated directly or via a 3D LUT)
                  + skin protection blended through a skin mask
    D  finish     vignette, grain, output sharpening

Stage C is a pure function of the pixel colour, which is what lets the same
code build .cube LUTs for video and render 45 MP files quickly.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

from . import wb
from .color import (
    LUMA,
    SRGB_TO_XYZ,
    XYZ_TO_SRGB,
    D65_XY,
    hue_distance,
    lab_to_lch,
    lch_to_lab,
    linear_to_oklab,
    linear_to_srgb,
    luminance,
    oklab_to_linear,
    smoothstep,
    srgb_to_linear,
    wheel_hue_to_oklab_hue,
    xy_to_xyz,
)
from .settings import BANDS, DevelopSettings

GREY_ENC = float(linear_to_srgb(np.float32(0.18)))
LUT_SIZE = 49
DIRECT_COLOR_MAX_PIXELS = 3_000_000

# Lightroom HSL band centres (HSV degrees) expressed as OKLab hue angles.
_BAND_WHEEL = (0.0, 30.0, 60.0, 120.0, 180.0, 240.0, 270.0, 300.0)
BAND_CENTERS = np.array([wheel_hue_to_oklab_hue(h) for h in _BAND_WHEEL], dtype=np.float32)

# Skin sits around the Lightroom "orange" band; this is the OKLab hue window
# in which a correctly balanced face lands for fair to deep complexions.
SKIN_HUE_LO, SKIN_HUE_HI, SKIN_HUE_IDEAL = 38.0, 72.0, 56.0


@dataclass
class RenderContext:
    is_raw: bool = False  # input is scene-linear from a raw decode
    wb_base: tuple[float, float] | None = None  # as-shot (Kelvin, tint); None = display white
    skin_mask: np.ndarray | None = None  # HxW float 0..1 at any resolution
    seed: int = 0


# ---------------------------------------------------------------- stage A


def calibration_matrix(s: DevelopSettings) -> np.ndarray:
    """Move the RGB primaries like Lightroom's Calibration panel (white stays white)."""
    if not any((s.cal_red_hue, s.cal_red_sat, s.cal_green_hue, s.cal_green_sat, s.cal_blue_hue, s.cal_blue_sat)):
        return np.eye(3, dtype=np.float32)
    wx, wy = D65_XY
    prims = []
    for (x, y), hue, sat in (
        ((0.64, 0.33), s.cal_red_hue, s.cal_red_sat),
        ((0.30, 0.60), s.cal_green_hue, s.cal_green_sat),
        ((0.15, 0.06), s.cal_blue_hue, s.cal_blue_sat),
    ):
        dx, dy = x - wx, y - wy
        ang = np.arctan2(dy, dx) + np.radians(hue / 100.0 * 20.0)
        rad = np.hypot(dx, dy) * (1.0 + sat / 100.0 * 0.35)
        prims.append((wx + rad * np.cos(ang), wy + rad * np.sin(ang)))
    P = np.stack([xy_to_xyz(px, py) for px, py in prims], axis=1)
    scale = np.linalg.solve(P, xy_to_xyz(wx, wy))
    m_new = P * scale
    return (XYZ_TO_SRGB @ m_new).astype(np.float32)


def scene_matrix(s: DevelopSettings, ctx: RenderContext) -> np.ndarray:
    m = calibration_matrix(s) @ wb.shift_matrix(s.temp_shift, s.tint_shift, ctx.wb_base)
    return (m * np.float32(2.0 ** s.exposure)).astype(np.float32)


# ---------------------------------------------------------------- stage B


def _pchip(xs: np.ndarray, ys: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Monotone cubic (Fritsch-Carlson) interpolation; x outside the range is clamped."""
    xs = np.asarray(xs, dtype=np.float64)
    ys = np.asarray(ys, dtype=np.float64)
    h = np.diff(xs)
    delta = np.diff(ys) / h
    m = np.zeros_like(ys)
    if len(xs) > 2:
        w1 = 2 * h[1:] + h[:-1]
        w2 = h[1:] + 2 * h[:-1]
        same = delta[:-1] * delta[1:] > 0
        inner = np.zeros(len(xs) - 2)
        inner[same] = (w1 + w2)[same] / (w1[same] / delta[:-1][same] + w2[same] / delta[1:][same])
        m[1:-1] = inner
    m[0], m[-1] = delta[0], delta[-1]
    x = np.clip(np.asarray(x, dtype=np.float64), xs[0], xs[-1])
    i = np.clip(np.searchsorted(xs, x) - 1, 0, len(xs) - 2)
    t = (x - xs[i]) / h[i]
    t2, t3 = t * t, t * t * t
    return (
        (2 * t3 - 3 * t2 + 1) * ys[i]
        + (t3 - 2 * t2 + t) * h[i] * m[i]
        + (-2 * t3 + 3 * t2) * ys[i + 1]
        + (t3 - t2) * h[i] * m[i + 1]
    )


def _oetf_ext(y: np.ndarray) -> np.ndarray:
    """sRGB encoding extended past 1.0 so over-range raw data keeps its order."""
    return np.where(y <= 1.0, linear_to_srgb(y), 1.055 * np.power(np.maximum(y, 1.0), 1 / 2.4) - 0.055)


# Default raw "profile" tone: +0.1 EV mid lift, gentle toe, film-like shoulder
# that rolls sensor white into display white.
_BASE_X = np.array([0.0, 0.08, 0.20, GREY_ENC, 0.70, 0.86, 1.0, 1.15, 1.40, 3.0])
_BASE_Y = np.array([0.0, 0.066, 0.185, GREY_ENC + 0.014, 0.738, 0.884, 0.972, 0.995, 1.0, 1.0])


_LOG_LO, _LOG_HI = -14.0, 2.0
_BASE_TABLE: np.ndarray | None = None


def _base_exact(Y: np.ndarray) -> np.ndarray:
    return srgb_to_linear(_pchip(_BASE_X, _BASE_Y, _oetf_ext(Y)).astype(np.float32))


def base_curve(Y: np.ndarray) -> np.ndarray:
    """Scene-linear luminance -> display-linear luminance for raw files."""
    global _BASE_TABLE
    Y = np.asarray(Y, dtype=np.float32)
    if Y.size < 4096 or Y.ndim != 2:
        return _base_exact(Y)
    if _BASE_TABLE is None:
        _BASE_TABLE = _base_exact(np.exp2(np.linspace(_LOG_LO, _LOG_HI, 4096)).astype(np.float32))
    lo = np.float32(2.0 ** _LOG_LO)
    pos = (np.log2(np.maximum(Y, lo)) - np.float32(_LOG_LO)) * np.float32(1.0 / (_LOG_HI - _LOG_LO))
    d = _interp_uniform(pos, _BASE_TABLE)
    tiny = Y < lo
    if tiny.any():
        d[tiny] = _BASE_TABLE[0] * np.maximum(Y[tiny], 0.0) / lo
    return d


def exposure_knee(Y: np.ndarray, exposure: float) -> np.ndarray:
    """Soft shoulder for brightened rendered files (identity when exposure <= 0)."""
    if exposure <= 0:
        return Y
    k = np.float32(1.0 - 0.3 * (1.0 - 2.0 ** (-exposure)))
    out = Y.astype(np.float32, copy=True)
    hi = out > k
    out[hi] = k + (1.0 - k) * np.tanh((out[hi] - k) / (1.0 - k))
    return out


def tone_curve_lut(s: DevelopSettings, n: int = 4096) -> tuple[np.ndarray, np.ndarray]:
    """Basic-panel tone sliders as a curve on sRGB-encoded luminance."""
    p = np.linspace(0.0, 1.0, n)
    y = p.copy()
    if s.contrast:
        g = 1.0 + s.contrast / 100.0 * (0.75 if s.contrast > 0 else 0.45)
        piv = GREY_ENC
        lo = piv * np.power(np.clip(y / piv, 0, None), g)
        hi = 1.0 - (1.0 - piv) * np.power(np.clip((1.0 - y) / (1.0 - piv), 0, None), g)
        y = np.where(y < piv, lo, hi)
    if s.highlights:
        t = np.clip((y - 0.38) / 0.62, 0.0, 1.0)
        y = y + s.highlights / 100.0 * 0.15 * np.sin(np.pi * t) ** 1.2 * (0.35 + 0.65 * t)
    if s.shadows:
        t = np.clip(y / 0.62, 0.0, 1.0)
        y = y + s.shadows / 100.0 * 0.15 * np.sin(np.pi * t) ** 1.2 * (1.0 - 0.5 * t)
    if s.whites:
        y = y + s.whites / 100.0 * 0.09 * np.clip(y, 0, 1) ** 5
    if s.blacks:
        y = y + s.blacks / 100.0 * 0.07 * np.clip(1.0 - y, 0, 1) ** 6
    y = np.maximum.accumulate(np.clip(y, 0.0, 1.0))
    return p.astype(np.float32), y.astype(np.float32)


def _box(img: np.ndarray, r: int) -> np.ndarray:
    return cv2.boxFilter(img, -1, (2 * r + 1, 2 * r + 1), borderType=cv2.BORDER_REFLECT)


def _guided_coeffs(I: np.ndarray, r: int, eps: float) -> tuple[np.ndarray, np.ndarray]:
    """Coefficient maps of a self-guided filter: smooth(I) = a * I + b."""
    mean_i = _box(I, r)
    var_i = _box(I * I, r) - mean_i * mean_i
    a = var_i / (var_i + eps)
    b = mean_i - a * mean_i
    return _box(a, r), _box(b, r)


def fast_guided_filter(I: np.ndarray, radius: float, eps: float, work_edge: int = 900) -> np.ndarray:
    """Self-guided edge-preserving smoothing (He & Sun's fast guided filter)."""
    h, w = I.shape
    f = max(1.0, max(h, w) / work_edge)
    lo = cv2.resize(I, (max(1, round(w / f)), max(1, round(h / f))), interpolation=cv2.INTER_AREA) if f > 1 else I
    a, b = _guided_coeffs(lo, max(1, int(round(radius / f))), eps)
    return _upsample_rows(a, 0, h, w, h) * I + _upsample_rows(b, 0, h, w, h)


def _upsample_rows(lo: np.ndarray, r0: int, r1: int, W: int, H: int) -> np.ndarray:
    """Rows r0:r1 of a low-res map bilinearly resized to (H, W) (same maths as cv2.resize)."""
    h_lo, w_lo = lo.shape[:2]
    if (h_lo, w_lo) == (H, W):
        return lo[r0:r1]
    xs = (np.arange(W, dtype=np.float32) + 0.5) * np.float32(w_lo / W) - 0.5
    ys = (np.arange(r0, r1, dtype=np.float32) + 0.5) * np.float32(h_lo / H) - 0.5
    mx = np.ascontiguousarray(np.broadcast_to(xs[None, :], (r1 - r0, W)))
    my = np.ascontiguousarray(np.broadcast_to(ys[:, None], (r1 - r0, W)))
    return cv2.remap(lo, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _interp_uniform(x: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """1D lookup table on 0..1 (uniform samples), linear interpolation, multi-threaded."""
    n = len(ys)
    if x.ndim != 2 or x.shape[1] > 32000 or n > 32000:
        return np.interp(x, np.linspace(0.0, 1.0, n), ys).astype(np.float32)
    tex = np.ascontiguousarray(ys, dtype=np.float32).reshape(1, n)
    shape = x.shape
    x2 = x.reshape(-1, shape[-1]) if x.ndim > 1 else x.reshape(1, -1)
    mx = np.ascontiguousarray(np.clip(x2, 0.0, 1.0) * np.float32(n - 1), dtype=np.float32)
    my = np.zeros_like(mx)
    return cv2.remap(tex, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE).reshape(shape)


def _chmax(x: np.ndarray) -> np.ndarray:
    return np.maximum(np.maximum(x[..., 0], x[..., 1]), x[..., 2])


def _chmin(x: np.ndarray) -> np.ndarray:
    return np.minimum(np.minimum(x[..., 0], x[..., 1]), x[..., 2])


def compress_gamut(rgb: np.ndarray, knee: float = 1.0) -> np.ndarray:
    """Roll colours toward their own grey instead of clipping channels.

    Keeps luminance and hue: over-bright saturated colours soften into white
    and negative (out-of-gamut) channels desaturate gently.  With the default
    ``knee=1`` in-gamut colours are untouched; raw renders use a softer knee.
    Only the pixels that need it are processed.
    """
    rgb = np.asarray(rgb, dtype=np.float32)
    mx = _chmax(rgb)
    mn = _chmin(rgb)
    need = (mx > knee) | (mn < 0.0)
    if not need.any():
        return rgb
    out = rgb.copy()
    px = rgb[need]
    Y = px @ LUMA
    Yc = np.clip(Y, 0.0, 1.0)
    pmx, pmn = mx[need], mn[need]
    if knee < 1.0:
        target = np.where(pmx > knee, knee + (1 - knee) * np.tanh((pmx - knee) / (1 - knee)), pmx)
    else:
        target = np.minimum(pmx, 1.0)
    s_hi = np.where(pmx > knee, np.clip((target - Yc) / np.maximum(pmx - Yc, 1e-6), 0.0, 1.0), 1.0)
    s_lo = np.where(pmn < 0.0, np.clip(Yc / np.maximum(Yc - pmn, 1e-6), 0.0, 1.0), 1.0)
    out[need] = np.clip(Yc[:, None] + (px - Y[:, None]) * np.minimum(s_hi, s_lo)[:, None], 0.0, 1.0)
    return out


def _haze_map(disp_lo: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    dark = _chmin(disp_lo)
    r = max(2, int(0.02 * max(disp_lo.shape[:2])))
    dark = cv2.erode(dark, np.ones((2 * r + 1, 2 * r + 1), np.uint8))
    dark = cv2.GaussianBlur(dark, (0, 0), r)
    top = np.argsort(dark.ravel())[-max(1, dark.size // 1000):]
    air = np.clip(disp_lo.reshape(-1, 3)[top].mean(axis=0), 0.3, 1.0).astype(np.float32)
    return dark.astype(np.float32), air


def _dehaze_pixels(rgb: np.ndarray, dark: np.ndarray, air: np.ndarray, amount: float) -> np.ndarray:
    k = amount / 100.0
    dark = dark[..., None]
    if k > 0:
        t = np.clip(1.0 - 0.85 * k * dark / air.max(), 0.25, 1.0)
        return ((rgb - air * (1.0 - t)) / t).astype(np.float32)
    veil = -k * 0.45 * (0.4 + 0.6 * np.clip(dark / air.max(), 0, 1))
    return (rgb * (1.0 - veil) + air * veil).astype(np.float32)


@dataclass
class _Prep:
    H: int
    W: int
    matrix: np.ndarray | None
    local: bool
    coeffs: tuple | None
    curve: np.ndarray | None
    tex_sigma: float
    haze: tuple | None
    knee: float


def _prepare_tone(rgb: np.ndarray, s: DevelopSettings, ctx: RenderContext) -> _Prep:
    """Global (low resolution) part of stages A/B: filter coefficients, haze map."""
    H, W = rgb.shape[:2]
    edge = max(H, W)
    m = scene_matrix(s, ctx)
    matrix = None if np.allclose(m, np.eye(3), atol=1e-7) else m
    local = any((s.contrast, s.highlights, s.shadows, s.whites, s.blacks, s.clarity, s.texture))
    prep = _Prep(H, W, matrix, local, None, None, max(0.8, 0.0012 * edge), None, 0.92 if ctx.is_raw else 1.0)
    if not (local or s.dehaze):
        return prep
    f = max(1.0, edge / 900.0)
    lo = cv2.resize(rgb, (max(1, round(W / f)), max(1, round(H / f))), interpolation=cv2.INTER_AREA) if f > 1 else rgb
    if matrix is not None:
        lo = cv2.transform(np.ascontiguousarray(lo, dtype=np.float32), matrix)
    if local:
        Y = np.maximum(luminance(lo), 1e-7)
        D = base_curve(Y) if ctx.is_raw else exposure_knee(Y, s.exposure)
        prep.coeffs = _guided_coeffs(np.log2(np.maximum(D, 1e-7)).astype(np.float32),
                                     max(1, int(round(0.02 * edge / f))), 0.04)
        prep.curve = tone_curve_lut(s)[1]
    if s.dehaze:
        lo_prep = _Prep(lo.shape[0], lo.shape[1], None, local, prep.coeffs, prep.curve,
                        max(0.8, 0.0012 * max(lo.shape[:2])), None, prep.knee)
        prep.haze = _haze_map(_tone_rows(lo, 0, lo_prep, s, ctx))
    return prep


def _tone_rows(x: np.ndarray, row0: int, prep: _Prep, s: DevelopSettings, ctx: RenderContext) -> np.ndarray:
    """Stages A+B for rows row0:row0+len(x) of the image (display-linear output)."""
    h, W = x.shape[:2]
    x = np.ascontiguousarray(x, dtype=np.float32)
    if prep.matrix is not None:
        x = cv2.transform(x, prep.matrix)
    Y = np.maximum(luminance(x), 1e-7)
    D = base_curve(Y) if ctx.is_raw else exposure_knee(Y, s.exposure)
    if prep.local:
        logD = np.log2(np.maximum(D, 1e-7)).astype(np.float32)
        a = _upsample_rows(prep.coeffs[0], row0, row0 + h, prep.W, prep.H)
        b = _upsample_rows(prep.coeffs[1], row0, row0 + h, prep.W, prep.H)
        base = a * logD + b
        detail = logD - base
        p2 = _interp_uniform(linear_to_srgb(np.exp2(base)), prep.curve)
        base2 = np.log2(np.maximum(srgb_to_linear(p2), 1e-7))
        if s.clarity:
            mid = smoothstep(0.05, 0.35, p2) * (1.0 - smoothstep(0.75, 1.0, p2))
            gain = s.clarity / 100.0 * (0.9 if s.clarity > 0 else 0.7)
            detail = detail * (1.0 + gain * mid)
        if s.texture:
            fine = logD - cv2.GaussianBlur(logD, (0, 0), prep.tex_sigma)
            lift = smoothstep(0.03, 0.2, p2)
            detail = detail + fine * (s.texture / 100.0 * (0.8 if s.texture > 0 else 0.6)) * lift
        D2 = np.exp2(base2 + detail)
    else:
        D2 = D
    x = x * (D2 / Y)[..., None]
    if prep.haze is not None:
        dark = _upsample_rows(prep.haze[0], row0, row0 + h, prep.W, prep.H)
        x = _dehaze_pixels(x, dark, prep.haze[1], s.dehaze)
    return compress_gamut(x, knee=prep.knee)


def tone_stage(rgb: np.ndarray, s: DevelopSettings, ctx: RenderContext) -> np.ndarray:
    """Stages A+B on a whole image (used for analysis and small renders)."""
    return _tone_rows(rgb, 0, _prepare_tone(rgb, s, ctx), s, ctx)


# ---------------------------------------------------------------- stage C


def _curve_lut(points: list, n: int = 1024) -> np.ndarray | None:
    pts = np.asarray(points, dtype=np.float64) / 255.0
    if len(pts) == 2 and np.allclose(pts, [[0, 0], [1, 1]]):
        return None
    pts = pts[np.argsort(pts[:, 0])]
    x = np.linspace(0, 1, n)
    return np.clip(_pchip(pts[:, 0], pts[:, 1], x), 0, 1).astype(np.float32)


def _parametric_lut(s: DevelopSettings, n: int = 1024) -> np.ndarray | None:
    amounts = (s.parametric_shadows, s.parametric_darks, s.parametric_lights, s.parametric_highlights)
    if not any(amounts):
        return None
    x = np.linspace(0, 1, n)
    a, b, c = s.parametric_shadow_split / 100, s.parametric_midtone_split / 100, s.parametric_highlight_split / 100
    edges = [(0.0, a), (a, b), (b, c), (c, 1.0)]
    y = x.copy()
    for amt, (lo, hi) in zip(amounts, edges):
        if not amt:
            continue
        pad = 0.5 * (hi - lo)
        t = np.clip((x - (lo - pad)) / (hi - lo + 2 * pad), 0, 1)
        bump = np.sin(np.pi * t) ** 2
        y = y + amt / 100.0 * 0.12 * bump * np.minimum(1.0, np.minimum(x, 1 - x) * 12)
    return np.maximum.accumulate(np.clip(y, 0, 1)).astype(np.float32)


@lru_cache(maxsize=64)
def _curves_cached(master_pts: tuple, r_pts: tuple, g_pts: tuple, b_pts: tuple, param: tuple):
    s = DevelopSettings()
    (s.parametric_shadows, s.parametric_darks, s.parametric_lights, s.parametric_highlights,
     s.parametric_shadow_split, s.parametric_midtone_split, s.parametric_highlight_split) = param
    master = _curve_lut([list(p) for p in master_pts])
    par = _parametric_lut(s)
    x = np.linspace(0, 1, 1024).astype(np.float32)
    if par is not None and master is not None:
        master = np.interp(par, x, master).astype(np.float32)
    elif par is not None:
        master = par
    chans = [_curve_lut([list(p) for p in pts]) for pts in (r_pts, g_pts, b_pts)]
    return master, chans


def _apply_curves(enc: np.ndarray, s: DevelopSettings) -> np.ndarray:
    param = (s.parametric_shadows, s.parametric_darks, s.parametric_lights, s.parametric_highlights,
             s.parametric_shadow_split, s.parametric_midtone_split, s.parametric_highlight_split)
    master, chans = _curves_cached(
        tuple(tuple(p) for p in s.curve),
        tuple(tuple(p) for p in s.curve_red),
        tuple(tuple(p) for p in s.curve_green),
        tuple(tuple(p) for p in s.curve_blue),
        param,
    )
    if master is None and all(c is None for c in chans):
        return enc
    x = np.linspace(0, 1, 1024).astype(np.float32)
    out = enc.copy()
    for i in range(3):
        v = np.clip(out[..., i], 0, 1)
        if master is not None:
            v = np.interp(v, x, master)
        if chans[i] is not None:
            v = np.interp(v, x, chans[i])
        out[..., i] = v
    return out


def band_weights(h: np.ndarray) -> np.ndarray:
    """Smooth partition of unity over the 8 HSL bands for OKLab hue angles."""
    c = BAND_CENTERS
    n = len(c)
    w = np.zeros(h.shape + (n,), dtype=np.float32)
    for i in range(n):
        c0, c1 = c[i], c[(i + 1) % n]
        span = (c1 - c0) % 360.0
        t = ((h - c0) % 360.0) / span
        inside = t < 1.0
        tt = np.where(inside, t, 0.0)
        wi = 0.5 * (1.0 + np.cos(np.pi * tt))
        w[..., i] += np.where(inside, wi, 0.0)
        w[..., (i + 1) % n] += np.where(inside, 1.0 - wi, 0.0)
    return w


def _band_max_shift() -> tuple[np.ndarray, np.ndarray]:
    c = BAND_CENTERS
    nxt = (np.roll(c, -1) - c) % 360.0
    prv = (c - np.roll(c, 1)) % 360.0
    return 0.55 * nxt, 0.55 * prv


_SHIFT_UP, _SHIFT_DOWN = _band_max_shift()


def _wheel_vector(hue: float) -> np.ndarray:
    ang = np.radians(wheel_hue_to_oklab_hue(hue))
    return np.array([np.cos(ang), np.sin(ang)], dtype=np.float32)


def global_color(rgb: np.ndarray, s: DevelopSettings, skin_safe: bool = False) -> np.ndarray:
    """Stage C on display-linear RGB (any shape (...,3)). Returns display-linear RGB."""
    enc = linear_to_srgb(np.clip(rgb, 0.0, 1.0))
    enc = _apply_curves(enc, s)
    lab = linear_to_oklab(srgb_to_linear(enc))
    L, C, h = lab_to_lch(lab)
    L0, C0, h0 = L.copy(), C.copy(), h.copy()

    skin_hue = smoothstep(18.0, 0.0, hue_distance(h, SKIN_HUE_IDEAL) - 14.0)

    if s.saturation or s.vibrance:
        mult = 1.0 + s.saturation / 100.0
        if s.vibrance:
            room = np.clip(1.0 - C / 0.22, 0.0, 1.0) ** 2
            protect = 1.0 - (0.6 * skin_hue if s.vibrance > 0 else 0.0)
            mult = mult * (1.0 + s.vibrance / 100.0 * 0.9 * room * protect)
        C = C * np.maximum(mult, 0.0)

    hue_vals = np.array([s.hue[b] for b in BANDS], dtype=np.float32)
    sat_vals = np.array([s.sat[b] for b in BANDS], dtype=np.float32)
    lum_vals = np.array([s.lum[b] for b in BANDS], dtype=np.float32)
    if hue_vals.any() or sat_vals.any() or lum_vals.any():
        wts = band_weights(h)
        grey = smoothstep(0.004, 0.03, C)
        outfit = 1.0 - s.outfit_protect * smoothstep(0.11, 0.19, C)
        if hue_vals.any():
            shift = np.where(hue_vals > 0, hue_vals / 100.0 * _SHIFT_UP, hue_vals / 100.0 * _SHIFT_DOWN)
            h = (h + (wts @ shift) * grey * outfit) % 360.0
        if sat_vals.any():
            sm = wts @ (sat_vals / 100.0)
            sm = np.where(sm < 0, sm * outfit, sm)
            C = C * np.maximum(1.0 + sm, 0.0)
        if lum_vals.any():
            lm = (wts @ (lum_vals / 100.0)) * smoothstep(0.01, 0.08, C0)
            L = L * np.exp2(0.45 * lm)

    lab = lch_to_lab(L, C, h)

    zones = (
        (s.grade_shadow_hue, s.grade_shadow_sat, s.grade_shadow_lum),
        (s.grade_mid_hue, s.grade_mid_sat, s.grade_mid_lum),
        (s.grade_high_hue, s.grade_high_sat, s.grade_high_lum),
    )
    if any(z[1] or z[2] for z in zones) or s.grade_global_sat or s.grade_global_lum or s.cal_shadow_tint:
        width = 0.08 + 0.34 * s.grade_blending / 100.0
        piv = 0.56 - s.grade_balance / 100.0 * 0.14
        Lr = L0
        ws = 1.0 - smoothstep(piv - 0.17 - width / 2, piv - 0.17 + width / 2, Lr)
        wh = smoothstep(piv + 0.17 - width / 2, piv + 0.17 + width / 2, Lr)
        wm = np.clip(1.0 - ws - wh, 0.0, 1.0)
        dab = np.zeros(L.shape + (2,), dtype=np.float32)
        dL = np.zeros_like(L)
        for (hue, sat, lum), wz in zip(zones + ((s.grade_global_hue, s.grade_global_sat, s.grade_global_lum),),
                                        (ws, wm, wh, np.ones_like(L))):
            if sat:
                dab += (wz * (sat / 100.0 * 0.11))[..., None] * _wheel_vector(hue)
            if lum:
                dL += wz * lum / 100.0 * 0.12
        if s.cal_shadow_tint:
            dab[..., 0] += ws * s.cal_shadow_tint / 100.0 * 0.03
        lab[..., 1:] += dab
        lab[..., 0] += dL

    if skin_safe:
        Ls, Cs, hs = lab_to_lch(lab)
        dh = ((hs - h0 + 180.0) % 360.0) - 180.0
        hs = h0 + np.clip(dh, -5.0, 5.0)
        # nudge toward the flattering skin window, never across it
        hs = hs + np.clip(SKIN_HUE_LO - hs, 0, None) * 0.5 - np.clip(hs - SKIN_HUE_HI, 0, None) * 0.5
        Cs = np.clip(Cs, C0 * 0.85, np.maximum(C0 * 1.12, 0.02))
        Ls = L0 + (Ls - L0) * 0.85
        lab = lch_to_lab(Ls, Cs, hs)

    return oklab_to_linear(lab)


def build_lut(s: DevelopSettings, size: int = LUT_SIZE, skin_safe: bool = False) -> np.ndarray:
    """3D LUT (size^3 x 3) mapping sRGB-encoded input to sRGB-encoded output."""
    g = np.linspace(0.0, 1.0, size, dtype=np.float32)
    r, gg, b = np.meshgrid(g, g, g, indexing="ij")
    enc = np.stack([r, gg, b], axis=-1).reshape(-1, 3)
    out = compress_gamut(global_color(srgb_to_linear(enc), s, skin_safe=skin_safe))
    return linear_to_srgb(out).reshape(size, size, size, 3)


_TEX_CACHE: dict = {}


def _lut_texture(lut: np.ndarray) -> np.ndarray:
    """Lay the blue slices of a LUT side by side: tex[g, b*N + r] = lut[r, g, b]."""
    hit = _TEX_CACHE.get(id(lut))
    if hit is not None and hit[0] is lut:
        return hit[1]
    n = lut.shape[0]
    tex = np.ascontiguousarray(np.transpose(lut, (1, 2, 0, 3)).reshape(n, n * n, 3), dtype=np.float32)
    _TEX_CACHE.clear()
    _TEX_CACHE[id(lut)] = (lut, tex)
    return tex


def apply_lut(enc: np.ndarray, lut: np.ndarray, chunk_rows: int = 1024) -> np.ndarray:
    """Trilinear 3D-LUT lookup on sRGB-encoded pixels.

    Uses two bilinear ``cv2.remap`` lookups (the neighbouring blue slices) and a
    blend, which is exact trilinear interpolation and runs multi-threaded.
    """
    n = lut.shape[0]
    tex = _lut_texture(lut)
    src = np.asarray(enc, dtype=np.float32)
    shape = src.shape
    img = src.reshape(1, -1, 3) if src.ndim != 3 else src
    out = np.empty_like(img)
    for r0 in range(0, img.shape[0], chunk_rows):
        part = np.clip(img[r0:r0 + chunk_rows], 0.0, 1.0) * np.float32(n - 1)
        fr, fg, fb = part[..., 0], part[..., 1], part[..., 2]
        b0 = np.minimum(fb.astype(np.int32), n - 2)
        wb = (fb - b0)[..., None]
        x0 = np.ascontiguousarray(b0 * n + fr, dtype=np.float32)
        fg = np.ascontiguousarray(fg)
        s0 = cv2.remap(tex, x0, fg, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        s1 = cv2.remap(tex, x0 + np.float32(n), fg, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        out[r0:r0 + chunk_rows] = s0 + (s1 - s0) * wb
    return out.reshape(shape)


def _color_rows(rgb: np.ndarray, s: DevelopSettings, mask: np.ndarray | None, luts: tuple | None) -> np.ndarray:
    """Stage C on display-linear rows; returns sRGB-encoded rows."""
    if luts is None:
        out = compress_gamut(global_color(rgb, s))
        if mask is not None:
            safe = compress_gamut(global_color(rgb, s, skin_safe=True))
            out = out + (safe - out) * mask[..., None]
        return linear_to_srgb(out)
    enc = linear_to_srgb(np.clip(rgb, 0, 1))
    out = apply_lut(enc, luts[0])
    if mask is not None and luts[1] is not None:
        rows = np.flatnonzero(mask.max(axis=1) > 0.004)
        if rows.size:
            cols = np.flatnonzero(mask[rows[0]:rows[-1] + 1].max(axis=0) > 0.004)
            box = (slice(rows[0], rows[-1] + 1), slice(cols[0], cols[-1] + 1))
            safe = apply_lut(np.ascontiguousarray(enc[box]), luts[1])
            out[box] += (safe - out[box]) * mask[box][..., None]
    return out


def color_stage(rgb: np.ndarray, s: DevelopSettings, ctx: RenderContext) -> np.ndarray:
    """Stage C on a whole image. Takes display-linear RGB, returns sRGB-encoded RGB."""
    h, w = rgb.shape[:2]
    mask = None
    if ctx.skin_mask is not None and s.skin_protect > 0:
        mask = np.clip(_upsample_rows(ctx.skin_mask.astype(np.float32), 0, h, w, h) * s.skin_protect, 0.0, 1.0)
    luts = None
    if h * w > DIRECT_COLOR_MAX_PIXELS:
        luts = (build_lut(s), build_lut(s, skin_safe=True) if mask is not None else None)
    return _color_rows(rgb, s, mask, luts)


# ---------------------------------------------------------------- stage D


def _vignette_lo(H: int, W: int, s: DevelopSettings) -> np.ndarray:
    lo_w = min(W, 512)
    lo_h = max(2, round(H * lo_w / W))
    yy, xx = np.mgrid[0:lo_h, 0:lo_w].astype(np.float32)
    x = (xx / max(lo_w - 1, 1) - 0.5) * 2.0
    y = (yy / max(lo_h - 1, 1) - 0.5) * 2.0
    r_frame = np.sqrt(x * x + y * y) / np.sqrt(2.0)
    aw, ah = W / max(W, H), H / max(W, H)
    r_circle = np.sqrt((x * aw) ** 2 + (y * ah) ** 2) / np.sqrt(aw * aw + ah * ah)
    t = (s.vignette_roundness + 100.0) / 200.0
    r = r_frame + (r_circle - r_frame) * t
    m0 = 0.15 + 0.55 * s.vignette_midpoint / 100.0
    m1 = m0 + 0.08 + 0.6 * s.vignette_feather / 100.0
    return smoothstep(m0, m1, r).astype(np.float32)


def vignette(enc: np.ndarray, s: DevelopSettings, mask: np.ndarray | None = None) -> np.ndarray:
    """Post-crop vignette (highlight priority style) on sRGB-encoded pixels."""
    if not s.vignette_amount:
        return enc
    h, w = enc.shape[:2]
    if mask is None:
        mask = _upsample_rows(_vignette_lo(h, w, s), 0, h, w, h)
    k = s.vignette_amount / 100.0
    if k < 0:
        gain = 1.0 + k * 0.85 * mask
        if s.vignette_highlights:
            Y = enc @ LUMA
            gain = 1.0 + (gain - 1.0) * (1.0 - s.vignette_highlights / 100.0 * smoothstep(0.73, 1.0, Y))
        # a linear-light gain g is g^(1/2.2) in the encoded domain
        return enc * cv2.pow(np.maximum(gain, 1e-4).astype(np.float32), 1 / 2.2)[..., None]
    return enc + (1.0 - enc) * (k * 0.6 * mask)[..., None]


def grain_sigma(s: DevelopSettings, edge: int) -> float:
    return max(0.35, (0.5 + s.grain_size / 100.0 * 2.2) * edge / 6000.0)


def grain(enc: np.ndarray, s: DevelopSettings, seed: int = 0, edge: int | None = None) -> np.ndarray:
    if not s.grain_amount:
        return enc
    h, w = enc.shape[:2]
    sigma = grain_sigma(s, edge or max(h, w))
    cv2.setRNGSeed(int(seed) & 0x7FFFFFFF)
    n1 = np.empty((h, w), np.float32)
    n2 = np.empty((h, w), np.float32)
    cv2.randn(n1, 0.0, 1.0)
    cv2.randn(n2, 0.0, 1.0)
    n1 = cv2.GaussianBlur(n1, (0, 0), sigma)
    n2 = cv2.GaussianBlur(n2, (0, 0), sigma * 2.5)
    # std of Gaussian-blurred unit white noise is 1 / (2 sigma sqrt(pi))
    k1 = float(2.0 * sigma * np.sqrt(np.pi))
    k2 = float(2.0 * sigma * 2.5 * np.sqrt(np.pi))
    rough = s.grain_roughness / 100.0
    noise = n1 * (k1 * (0.5 + 0.5 * rough)) + n2 * (k2 * (1.0 - rough) * 0.6)
    Y = enc @ LUMA
    amp = (4.0 * Y * (1.0 - Y)) * (s.grain_amount / 100.0 * 0.045)
    return (enc + (noise * amp)[..., None]).astype(np.float32, copy=False)


def sharpen(enc: np.ndarray, s: DevelopSettings) -> np.ndarray:
    if not s.sharpen_amount:
        return enc
    Y = (enc @ LUMA).astype(np.float32, copy=False)
    blur = cv2.GaussianBlur(Y, (0, 0), s.sharpen_radius)
    detail = Y - blur
    amount = s.sharpen_amount / 100.0 * 0.9
    if s.sharpen_masking:
        gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
        mag = cv2.magnitude(gx, gy)
        t = s.sharpen_masking / 100.0 * 0.25
        amount = amount * smoothstep(t * 0.3, t + 1e-4, mag)
    halo = 0.08 * (1.0 - s.sharpen_detail / 100.0 * 0.5) + 0.02
    detail = np.clip(detail, -halo, halo)
    return enc + (detail * amount)[..., None]


# ---------------------------------------------------------------- public API

STRIP_PIXELS = 2_500_000


def render(rgb_linear: np.ndarray, s: DevelopSettings, ctx: RenderContext | None = None,
           finishing: bool = True, encoded: bool = False) -> np.ndarray:
    """Develop a linear image with ``s``.

    Returns float32 in 0..1: display-linear by default, or sRGB-encoded with
    ``encoded=True`` (saves a conversion when writing files).  Large images are
    processed in horizontal strips (with overlap for the blurs) so memory stays
    small and caches stay warm.
    """
    ctx = ctx or RenderContext()
    rgb = np.asarray(rgb_linear, dtype=np.float32)
    H, W = rgb.shape[:2]
    edge = max(H, W)
    prep = _prepare_tone(rgb, s, ctx)
    skin_lo = None
    if ctx.skin_mask is not None and s.skin_protect > 0:
        skin_lo = np.clip(ctx.skin_mask.astype(np.float32) * s.skin_protect, 0.0, 1.0)
        if skin_lo.max() < 0.02:
            skin_lo = None
    luts = None
    if H * W > DIRECT_COLOR_MAX_PIXELS:
        luts = (build_lut(s), build_lut(s, skin_safe=True) if skin_lo is not None else None)
    vig_lo = _vignette_lo(H, W, s) if (finishing and s.vignette_amount) else None
    blur = 0.0
    if s.texture and prep.local:
        blur = max(blur, prep.tex_sigma)
    if finishing and s.sharpen_amount:
        blur = max(blur, s.sharpen_radius + 1.0)
    if finishing and s.grain_amount:
        blur = max(blur, grain_sigma(s, edge) * 2.5)
    margin = int(np.ceil(3.0 * blur)) + 2 if blur else 0
    rows = H if H * W <= STRIP_PIXELS else max(64, STRIP_PIXELS // W)
    out = np.empty((H, W, 3), np.float32)
    for r0 in range(0, H, rows):
        r1 = min(H, r0 + rows)
        m0, m1 = max(0, r0 - margin), min(H, r1 + margin)
        x = _tone_rows(rgb[m0:m1], m0, prep, s, ctx)
        mask = _upsample_rows(skin_lo, m0, m1, W, H) if skin_lo is not None else None
        enc = _color_rows(x, s, mask, luts)
        if finishing:
            if vig_lo is not None:
                enc = vignette(enc, s, _upsample_rows(vig_lo, m0, m1, W, H))
            enc = grain(enc, s, ctx.seed * 7919 + r0, edge)
            enc = sharpen(enc, s)
        out[r0:r1] = enc[r0 - m0:r0 - m0 + (r1 - r0)]
    np.clip(out, 0.0, 1.0, out=out)
    return out if encoded else srgb_to_linear(out)
