"""Per-photo analysis: faces, skin, light, exposure and palette.

Everything here runs on a small (~1024 px) copy of the photo, rendered with
neutral settings, so a whole wedding can be analysed in minutes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

from . import faces as facemod
from . import wb
from .color import linear_to_oklab, linear_to_srgb, luminance, lab_to_lch, hue_distance, smoothstep
from .engine import RenderContext, band_weights, base_curve, tone_stage
from .imageio import Photo
from .settings import BANDS, DevelopSettings

ANALYSIS_EDGE = 1024
MASK_EDGE = 384


def lstar(Y: np.ndarray | float) -> np.ndarray | float:
    Y = np.maximum(Y, 0.0)
    return np.where(Y > 0.008856, 116.0 * np.cbrt(Y) - 16.0, 903.3 * Y)


def lstar_to_y(L: float) -> float:
    return float(((L + 16.0) / 116.0) ** 3) if L > 8.0 else float(L / 903.3)


@dataclass
class WhiteBalanceCue:
    temp_shift: float
    tint_shift: float
    weight: float
    source: str


@dataclass
class Analysis:
    width: int
    height: int
    is_raw: bool
    faces: list = field(default_factory=list)
    face_area: float = 0.0  # fraction of frame covered by face boxes
    skin_fraction: float = 0.0
    skin_L: float | None = None  # CIE L* of facial skin (display, neutral settings)
    skin_oklab: list | None = None  # [L, C, hue] median of facial skin
    lum: dict = field(default_factory=dict)  # L* percentiles of the frame
    clip_high: float = 0.0  # fraction of pixels at display white
    clip_low: float = 0.0
    mean_log2: float = 0.0
    wb_as_shot: list | None = None
    wb_cues: list = field(default_factory=list)
    wb_shift: list = field(default_factory=lambda: [0.0, 0.0])  # estimated neutral correction
    illuminant_k: float | None = None  # estimated scene light (Kelvin)
    scene: str = "day"
    palette: dict = field(default_factory=dict)  # band -> {frac, L, C, h}
    foliage: dict | None = None
    sky: dict | None = None
    outfit: dict = field(default_factory=dict)  # band -> fraction of very saturated pixels
    capture_time: str | None = None
    camera: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["wb_cues"] = [asdict(c) if isinstance(c, WhiteBalanceCue) else c for c in self.wb_cues]
        return d


def _neutral_view(photo: Photo) -> tuple[np.ndarray, np.ndarray]:
    """Scene-linear working copy and its neutral display rendering."""
    rgb = photo.rgb
    h, w = rgb.shape[:2]
    if max(h, w) > ANALYSIS_EDGE:
        f = ANALYSIS_EDGE / max(h, w)
        rgb = cv2.resize(rgb, (round(w * f), round(h * f)), interpolation=cv2.INTER_AREA)
    disp = tone_stage(rgb, DevelopSettings(), RenderContext(is_raw=photo.is_raw))
    return rgb, disp


def skin_model_mask(lab: np.ndarray, found: list, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Probabilistic skin mask seeded from detected faces (generic model otherwise).

    Returns (mask 0..1, boolean mask of the facial sample pixels).
    """
    h, w = shape
    L, C, hue = lab_to_lch(lab)
    face_px = np.zeros((h, w), bool)
    for f in found:
        face_px |= facemod.face_skin_mask((h, w), f)
    plausible = (hue_distance(hue, 55.0) < 40) & (C > 0.015) & (C < 0.2) & (L > 0.2) & (L < 0.97)
    samples = face_px & plausible
    if samples.sum() >= 40:
        ab = lab[samples][:, 1:]
        mu = np.median(ab, axis=0)
        cov = np.cov(ab.T) + np.eye(2) * 2.5e-4
        inv = np.linalg.inv(cov)
        d = lab[..., 1:] - mu
        m2 = np.einsum("...i,ij,...j->...", d, inv, d)
        prob = np.exp(-0.5 * m2 / 2.0)
        Ls = L[samples]
        lo, hi = np.percentile(Ls, 2) - 0.12, np.percentile(Ls, 98) + 0.1
        prob *= smoothstep(lo - 0.08, lo, L) * (1.0 - smoothstep(hi, hi + 0.08, L))
        # people extend below their faces: soft spatial prior (walls far away get little weight)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        prior = np.zeros((h, w), np.float32)
        for f in found:
            cx, cy = f.x + f.w / 2, f.y + f.h / 2
            sx, sy = f.w * 3.2, f.h * 5.0
            dy = np.where(yy < cy, (yy - cy) / (f.h * 1.5), (yy - cy) / sy)
            prior = np.maximum(prior, np.exp(-0.5 * (((xx - cx) / sx) ** 2 + dy ** 2)))
        prob *= 0.12 + 0.88 * prior
    else:
        samples = np.zeros((h, w), bool)
        prob = (smoothstep(40.0, 22.0, hue_distance(hue, 55.0)) * smoothstep(0.025, 0.04, C)
                * (1.0 - smoothstep(0.15, 0.19, C)) * smoothstep(0.25, 0.35, L) * 0.45)
    prob = cv2.GaussianBlur(prob.astype(np.float32), (0, 0), max(1.0, 0.004 * max(h, w)))
    return np.clip(prob, 0.0, 1.0), samples


def _wb_candidates(scene_rgb: np.ndarray, lab: np.ndarray, disp_Y: np.ndarray, skin: np.ndarray,
                   base: tuple[float, float] | None) -> list[WhiteBalanceCue]:
    cues: list[WhiteBalanceCue] = []
    L, C, hue = lab_to_lch(lab)
    mx = scene_rgb.max(axis=-1)
    usable = (mx < 0.97) & (lstar(disp_Y) > 22) & (skin < 0.3)  # not clipped, not too dark, not skin
    if usable.sum() > 200:
        # white patch: the brightest unclipped surfaces (dress, shirt, clouds) are usually white;
        # trust depends on how plausible the implied light is (near the daylight/tungsten locus)
        top = usable & (L >= np.percentile(L[usable], 97))
        if top.sum() >= 30:
            obs = np.median(scene_rgb[top], axis=0)
            ts, tt = wb.shift_to_neutralize(obs, base)
            plaus = float(smoothstep(45.0, 12.0, abs(tt)) * smoothstep(200.0, 70.0, abs(ts)))
            cues.append(WhiteBalanceCue(ts, tt, 0.2 + 0.65 * plaus, "white-patch"))
        # least colourful bright surfaces (can be fooled by a blue sky under warm light)
        bright = usable & (L > np.percentile(L[usable], 40))
        if bright.sum() > 100:
            cthr = np.percentile(C[bright], 3)
            pick = bright & (C <= max(cthr, 0.004))
            if pick.sum() >= 30 and cthr < 0.16:
                obs = np.median(scene_rgb[pick], axis=0)
                ts, tt = wb.shift_to_neutralize(obs, base)
                weight = float(0.2 + 0.5 * smoothstep(0.1, 0.02, cthr))
                cues.append(WhiteBalanceCue(ts, tt, weight, "neutral"))
    # gray-edge (Minkowski p=6) on the linear image
    blur = cv2.GaussianBlur(scene_rgb, (0, 0), 1.0)
    edges = []
    for c in range(3):
        gx = cv2.Sobel(blur[..., c], cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(blur[..., c], cv2.CV_32F, 0, 1, ksize=3)
        edges.append(np.sqrt(gx * gx + gy * gy))
    e = np.stack(edges, axis=-1)[usable] if usable.sum() > 200 else np.stack(edges, axis=-1).reshape(-1, 3)
    est = np.power(np.mean(np.power(e + 1e-9, 6), axis=0), 1 / 6)
    if np.all(est > 0):
        ts, tt = wb.shift_to_neutralize(est / est[1], base)
        cues.append(WhiteBalanceCue(ts, tt, 0.45, "gray-edge"))
    return cues


def _solve_wb(cues: list[WhiteBalanceCue], face_rgb: np.ndarray | None, face_conf: float,
              base: tuple[float, float] | None, is_raw: bool) -> tuple[float, float]:
    """Combine cues on a (mired, tint) grid; skin constrains hue, neutrals constrain the rest."""
    dm = np.arange(-160.0, 161.0, 5.0)
    dt = np.arange(-60.0, 61.0, 3.0)
    M, T = np.meshgrid(dm, dt, indexing="ij")
    cost = (M / (80.0 if is_raw else 50.0)) ** 2 + (T / (25.0 if is_raw else 15.0)) ** 2
    for c in cues:
        sm, st = (38.0, 16.0) if c.source == "gray-edge" else (14.0 / c.weight, 7.0 / c.weight)
        cost += ((M - c.temp_shift) / sm) ** 2 + ((T - c.tint_shift) / st) ** 2
    if face_rgb is not None and face_conf > 0:
        b = base or (wb.D65_TEMP, wb.D65_TINT)
        cone_face = wb.BRADFORD @ (wb.SRGB_TO_XYZ @ np.asarray(face_rgb, dtype=np.float64))
        cone_base = wb._cone(wb.temp_tint_to_xy(*b))
        mired = np.maximum(1.0e6 / b[0] - M, 1.0e6 / wb.MAX_KELVIN)
        kelvin = np.clip(1.0e6 / mired, wb.MIN_KELVIN, wb.MAX_KELVIN)
        x, y = wb.temp_tint_to_xy_vec(kelvin, np.clip(b[1] + T, -150.0, 150.0))
        xyz = np.stack([x / y, np.ones_like(x), (1.0 - x - y) / y], axis=-1)
        cones_new = xyz @ wb.BRADFORD.T
        back = wb.XYZ_TO_SRGB @ wb.BRADFORD_INV
        out = ((cone_base * cone_face) / cones_new) @ back.T
        lab = linear_to_oklab(np.maximum(out, 1e-6).astype(np.float32))
        L, Cc, hh = lab_to_lch(lab)
        hue_err = ((hh - 57.0 + 180.0) % 360.0) - 180.0
        chroma_err = np.log(np.maximum(Cc, 1e-4) / (0.078 * np.clip(L / 0.68, 0.6, 1.3)))
        cost += face_conf * ((hue_err / 7.0) ** 2 + (chroma_err / 0.38) ** 2)
    i, j = np.unravel_index(np.argmin(cost), cost.shape)
    return float(M[i, j]), float(T[i, j])


def _palette(lab: np.ndarray, valid: np.ndarray) -> dict:
    L, C, hue = lab_to_lch(lab)
    colourful = valid & (C > 0.03)
    out = {}
    if colourful.sum() < 50:
        return out
    wts = band_weights(hue[colourful])
    for i, b in enumerate(BANDS):
        wi = wts[:, i]
        frac = float(wi.sum() / valid.sum())
        if frac < 0.002:
            continue
        sel = wi > 0.5
        if sel.sum() < 20:
            continue
        out[b] = {
            "frac": round(frac, 4),
            "L": round(float(np.median(L[colourful][sel])), 4),
            "C": round(float(np.median(C[colourful][sel])), 4),
            "h": round(float(np.median(hue[colourful][sel])), 2),
        }
    return out


def analyze(photo: Photo) -> tuple[Analysis, np.ndarray]:
    """Analyse a photo. Returns (Analysis, skin mask at <= MASK_EDGE px, uint8 0..255)."""
    scene_rgb, disp = _neutral_view(photo)
    h, w = disp.shape[:2]
    enc8 = (np.clip(linear_to_srgb(disp), 0, 1) * 255 + 0.5).astype(np.uint8)
    found = facemod.detect(enc8)
    lab = linear_to_oklab(np.clip(disp, 0, 1))
    Y = luminance(np.clip(disp, 0, None))
    Ls = lstar(Y)
    skin, face_px = skin_model_mask(lab, found, (h, w))

    a = Analysis(width=w, height=h, is_raw=photo.is_raw, camera=photo.camera)
    a.capture_time = photo.capture_time.isoformat() if photo.capture_time else None
    a.wb_as_shot = list(photo.wb_as_shot) if photo.wb_as_shot else None
    a.faces = [f.to_dict(w, h) for f in found]
    a.face_area = float(sum(f.w * f.h for f in found) / (w * h))
    a.skin_fraction = float((skin > 0.5).mean())
    pct = np.percentile(Ls, [0.5, 1, 5, 25, 50, 75, 95, 99, 99.5])
    a.lum = {k: round(float(v), 2) for k, v in zip(("p0.5", "p1", "p5", "p25", "p50", "p75", "p95", "p99", "p99.5"), pct)}
    a.clip_high = float((disp.max(axis=-1) >= 0.995).mean())
    a.clip_low = float((Ls < 1.0).mean())
    a.mean_log2 = float(np.mean(np.log2(np.maximum(Y, 1e-5))))

    face_rgb = None
    face_conf = 0.0
    if face_px.sum() >= 40:
        a.skin_L = round(float(np.median(Ls[face_px])), 2)
        L, C, hh = lab_to_lch(lab[face_px])
        a.skin_oklab = [round(float(np.median(L)), 4), round(float(np.median(C)), 4), round(float(np.median(hh)), 2)]
        face_rgb = np.median(scene_rgb[face_px], axis=0)
        face_conf = float(min(1.0, face_px.sum() / 400.0) * max(f.score for f in found))

    base = photo.wb_as_shot if photo.is_raw else None
    # only a face-seeded skin model is specific enough to exclude pixels from the white search
    # (under warm light a white dress matches the generic skin colours)
    cues = _wb_candidates(scene_rgb, lab, Y, skin if found else np.zeros_like(skin), base)
    a.wb_cues = cues
    a.wb_shift = list(_solve_wb(cues, face_rgb, face_conf, base, photo.is_raw))
    if photo.is_raw and photo.wb_as_shot:
        k, _ = wb.apply_shift(photo.wb_as_shot[0], photo.wb_as_shot[1], *a.wb_shift)
    else:
        k, _ = wb.apply_shift(wb.D65_TEMP, wb.D65_TINT, a.wb_shift[0], a.wb_shift[1])
        # for rendered files the camera already balanced once: estimate the light from the residual
    a.illuminant_k = round(float(k), 0)

    valid = (Ls > 8) & (disp.max(axis=-1) < 0.995)
    a.palette = _palette(lab, valid)
    L, C, hh = lab_to_lch(lab)
    foliage = valid & (hh > 95) & (hh < 175) & (C > 0.03) & (C < 0.15) & (skin < 0.3)
    if foliage.mean() > 0.02:
        a.foliage = {"frac": round(float(foliage.mean()), 4), "h": round(float(np.median(hh[foliage])), 2),
                     "C": round(float(np.median(C[foliage])), 4), "L": round(float(np.median(L[foliage])), 4)}
    yy = np.mgrid[0:h, 0:w][0]
    sky = valid & (hh > 215) & (hh < 285) & (L > 0.55) & (yy < h * 0.6)
    if sky.mean() > 0.03:
        a.sky = {"frac": round(float(sky.mean()), 4), "C": round(float(np.median(C[sky])), 4),
                 "L": round(float(np.median(L[sky])), 4)}
    # very saturated fabric (lehenga, dupatta, haldi yellow); lawns and leaves rarely get this saturated
    very_thr = np.where((hh > 90) & (hh < 200), 0.155, 0.13)
    very = valid & (C > very_thr) & (skin < 0.3)
    if very.sum() > 0:
        wts = band_weights(hh[very])
        for i, b in enumerate(BANDS):
            frac = float(wts[:, i].sum() / valid.sum())
            if frac > 0.005:
                a.outfit[b] = round(frac, 4)

    a.scene = classify_scene(a)

    mask = skin
    if max(h, w) > MASK_EDGE:
        f = MASK_EDGE / max(h, w)
        mask = cv2.resize(skin, (round(w * f), round(h * f)), interpolation=cv2.INTER_AREA)
    return a, (np.clip(mask, 0, 1) * 255 + 0.5).astype(np.uint8)


def classify_scene(a: Analysis) -> str:
    p50 = a.lum.get("p50", 50.0)
    p95 = a.lum.get("p95", 90.0)
    dark_frame = p50 < 24 and a.lum.get("p25", 0) < 12
    if dark_frame and (a.skin_L is None or a.skin_L > p50 + 12 or p95 > 55):
        return "night"
    if a.illuminant_k is not None and a.illuminant_k < 3900:
        return "warm_indoor"
    if a.illuminant_k is not None and a.illuminant_k > 7500:
        return "shade"
    return "day"


def display_to_scene_luminance(Yd: float, is_raw: bool) -> float:
    """Invert the neutral display mapping (used to turn a brightness target into EV)."""
    if not is_raw:
        return Yd
    ys = np.geomspace(1e-4, 4.0, 2048).astype(np.float32)
    yd = base_curve(ys)
    return float(np.interp(Yd, yd, ys))
