"""Learn a look from reference photos (finished edits you love).

Reference edits are measured with the same analysis used for grading:

* where faces sit (brightness, hue, saturation of skin),
* where whites cap and how deep/tinted the blacks are, per channel,
* the tint of neutral shadows and highlights (the colour grade),
* how greens and skies are rendered (hue / saturation).

Those numbers become (a) curve end points and colour-grade wheels and (b) the
per-photo targets the automation steers every new photo toward.  Night
references (dark frames) feed the look's ``night`` variant.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

import numpy as np

from . import analysis as analysis_mod
from . import imageio
from .color import lab_to_lch, linear_to_oklab, linear_to_srgb, srgb_to_linear, wheel_hue_to_oklab_hue
from .engine import _curve_lut
from .looks import USER_DIR, AutoTargets, Look, get_look

_WHEEL = np.arange(0.0, 360.0, 1.0)
_WHEEL_OK = np.array([wheel_hue_to_oklab_hue(h) for h in _WHEEL])


def _oklab_to_wheel(hue_ok: float) -> float:
    d = np.abs(((_WHEEL_OK - hue_ok + 180.0) % 360.0) - 180.0)
    return float(_WHEEL[np.argmin(d)])


def measure(path: str | Path) -> dict:
    """Fingerprint one reference edit."""
    photo = imageio.load(path, long_edge=analysis_mod.ANALYSIS_EDGE, fast=True)
    photo.is_raw = False  # references are finished renders: measure them as-is
    a, _ = analysis_mod.analyze(photo)
    rgb = np.clip(photo.rgb, 0, 1)
    enc = linear_to_srgb(rgb)
    lab = linear_to_oklab(rgb)
    L, C, h = lab_to_lch(lab)
    fp: dict = {"file": str(path), "scene": a.scene, "skin_L": a.skin_L, "skin_oklab": a.skin_oklab,
                "lum": a.lum, "foliage": a.foliage, "sky": a.sky}
    flat = enc.reshape(-1, 3)
    fp["black_floor"] = np.percentile(flat, 0.5, axis=0).round(4).tolist()
    # only frames that actually contain whites (dress, sky, shirt) say where whites cap
    bright_neutral = (L > max(np.percentile(L, 97), 0.85)) & (C < 0.05)
    if bright_neutral.sum() > 50:
        fp["white_cap"] = np.percentile(enc[bright_neutral], 95, axis=0).round(4).tolist()
    for zone, sel in (("shadow", (L < 0.38) & (L > 0.08) & (C < 0.06)), ("highlight", (L > 0.78) & (C < 0.06))):
        if sel.sum() > 100:
            fp[f"{zone}_tint"] = np.median(lab[sel][:, 1:], axis=0).round(5).tolist()
    fp["chroma_median"] = round(float(np.median(C[L > 0.1])), 4)
    return fp


def _median(values: list, default=None):
    vals = [v for v in values if v is not None]
    return float(np.median(vals)) if vals else default


def _grade_from_tint(tint: list | None, max_sat: float = 18.0) -> tuple[float, float] | None:
    if not tint:
        return None
    a, b = tint
    mag = float(np.hypot(a, b))
    if mag < 0.002:
        return None
    hue_ok = float(np.degrees(np.arctan2(b, a)) % 360.0)
    return _oklab_to_wheel(hue_ok), float(min(max_sat, mag / 0.11 * 100.0 * 0.9))


def learn(paths: list[str | Path], name: str, base: str = "signature", look_id: str | None = None,
          looks_dirs: list | None = None) -> tuple[Look, list[dict]]:
    files = imageio.find_photos(paths)
    if not files:
        raise ValueError("no reference photos found")
    fps = [measure(f) for f in files]
    day = [f for f in fps if f["scene"] != "night"] or fps
    night = [f for f in fps if f["scene"] == "night"]

    look = copy.deepcopy(get_look(base, looks_dirs))
    look.id = look_id or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "my-look"
    look.name = name
    look.description = (f"Learned from {len(fps)} reference photo(s) ({len(day)} day, {len(night)} night) "
                        f"on top of '{base}'.")
    look.source = ""
    s = look.settings
    auto = look.auto

    def shrink(measured: float, prior: float, n: int) -> float:
        """Few references -> stay near the base look; many -> trust the references."""
        return prior + (measured - prior) * n / (n + 2.0)

    def pre_curve_L(L_out: float, curve: list) -> float:
        """Measured (finished) L* -> the L* the auto sliders should aim for before the curve."""
        y = _curve_lut(curve)
        if y is None:
            return L_out
        x = np.linspace(0, 1, len(y))
        enc_out = float(linear_to_srgb(np.float32(analysis_mod.lstar_to_y(L_out))))
        enc_in = float(np.interp(enc_out, y, x))
        return float(analysis_mod.lstar(srgb_to_linear(np.float32(enc_in))))

    def targets(group: list[dict], dst: dict, prior: dict, curve: list) -> None:
        n = len(group)
        p = AutoTargets.from_dict(prior)

        def put(key: str, measured: float | None, lo: float, hi: float, digits: int = 1) -> None:
            if measured is not None:
                dst[key] = round(float(np.clip(shrink(measured, getattr(p, key), n), lo, hi)), digits)

        put("skin_L", _median([f["skin_L"] for f in group]), 40, 78)
        white = _median([f["lum"]["p99.5"] for f in group if f.get("white_cap")])
        if white is not None:
            put("white_L", pre_curve_L(white, curve), 70, 99)
        put("black_L", pre_curve_L(_median([f["lum"]["p0.5"] for f in group], 2.0), curve), 0, 12)
        put("scene_mid_L", _median([f["lum"]["p50"] for f in group]), 15, 70)
        fol = [f["foliage"] for f in group if f["foliage"]]
        if fol and p.foliage_hue is not None:
            put("foliage_hue", _median([x["h"] for x in fol]), 60, 170)
            put("foliage_chroma", _median([x["C"] for x in fol]), 0.02, 0.15, 4)
        elif fol:
            dst["foliage_hue"] = round(_median([x["h"] for x in fol]), 1)
            dst["foliage_chroma"] = round(_median([x["C"] for x in fol]), 4)
        sky = [f["sky"]["C"] for f in group if f["sky"]]
        if sky:
            if p.sky_chroma is not None:
                put("sky_chroma", _median(sky), 0.0, 0.12, 4)
            else:
                dst["sky_chroma"] = round(_median(sky), 4)
        sk = [f["skin_oklab"] for f in group if f["skin_oklab"]]
        if len(sk) >= 2 or (sk and n == 1 and len(fps) == 1):
            put("skin_hue", float(np.median([x[2] for x in sk])), 35, 75)
            cs = [x[1] for x in sk]
            lo = shrink(float(np.min(cs)) * 0.9, p.skin_chroma[0], len(sk))
            hi = shrink(float(np.max(cs)) * 1.1, p.skin_chroma[1], len(sk))
            dst["skin_chroma"] = [round(lo, 4), round(max(hi, lo + 0.01), 4)]

    def curves(group: list[dict], dst: dict) -> None:
        n = len(group)
        master = dst.get("curve", [[0, 0], [255, 255]])
        floor0, cap0 = master[0][1], master[-1][1]
        floors = np.median(np.array([f["black_floor"] for f in group]), axis=0) * 255.0
        caps = [f["white_cap"] for f in group if f.get("white_cap")]
        base_floor = shrink(float(np.clip(floors.min(), 0, 30)), floor0, n)
        dst["curve"] = [[0, round(base_floor)], *[p for p in master if 0 < p[0] < 255], [255, cap0]]
        cap = None
        if caps:
            cap = np.median(np.array(caps), axis=0) * 255.0
            base_cap = shrink(float(np.clip(cap.max(), 215, 255)), cap0, len(caps))
            dst["curve"][-1] = [255, round(base_cap)]
        for i, ch in enumerate(("curve_red", "curve_green", "curve_blue")):
            old = dst.get(ch, [[0, 0], [255, 255]])
            lift = shrink(float(np.clip(floors[i] - floors.min(), 0, 25)), old[0][1], n)
            drop = 255 - old[-1][1]
            if cap is not None:
                drop = shrink(float(np.clip(cap.max() - cap[i], 0, 25)), drop, len(caps))
            # lifts only touch the shadows and drops only the highlights (anchored mid tones)
            dst[ch] = [[0, round(lift)], [64, 64], [192, 192], [255, round(255 - drop)]]

    def grade(group: list[dict], dst: dict) -> None:
        n = len(group)
        for zone, key in (("shadow", "grade_shadow"), ("highlight", "grade_high")):
            tints = [f[f"{zone}_tint"] for f in group if f.get(f"{zone}_tint")]
            g = _grade_from_tint(np.median(np.array(tints), axis=0).tolist() if tints else None, max_sat=12.0)
            if not g:
                continue
            # finished photos carry the light's colour too: learn half of the measured tint
            hue_old, sat_old = dst.get(f"{key}_hue", 0.0), dst.get(f"{key}_sat", 0.0)
            v_old = sat_old * np.array([np.cos(np.radians(hue_old)), np.sin(np.radians(hue_old))])
            v_new = 0.5 * g[1] * np.array([np.cos(np.radians(g[0])), np.sin(np.radians(g[0]))])
            v = v_old + (v_new - v_old) * n / (n + 2.0)
            dst[f"{key}_hue"] = round(float(np.degrees(np.arctan2(v[1], v[0])) % 360.0))
            dst[f"{key}_sat"] = round(float(np.hypot(*v)))

    prior_auto = copy.deepcopy(auto)
    curves(day, s)
    targets(day, auto, prior_auto, s["curve"])
    grade(day, s)
    if night:
        v = look.variants.setdefault("night", {})
        vs, va = v.setdefault("settings", {}), v.setdefault("auto", {})
        for k in ("curve", "curve_red", "curve_green", "curve_blue", "grade_shadow_hue", "grade_shadow_sat",
                  "grade_high_hue", "grade_high_sat"):
            if k not in vs and k in s:
                vs[k] = copy.deepcopy(s[k])
        curves(night, vs)
        targets(night, va, {**prior_auto, **va}, vs["curve"])
        grade(night, vs)
    look.resolve()
    return look, fps


def save_learned(look: Look, folder: str | Path | None = None) -> Path:
    folder = Path(folder) if folder else USER_DIR
    return look.save(folder / f"{look.id}.json")
