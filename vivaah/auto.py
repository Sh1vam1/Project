"""Per-photo decisions: turn an Analysis + a Look into final develop settings.

This is where "check each photo and make it beautiful" happens:

1. pick the look variant for the scene (day / night / warm indoor / shade)
2. white balance: neutral + skin cues, keep some warmth of candle/tungsten light,
   then add the look's own warmth
3. exposure from the faces (or the scene when there are none)
4. highlights / whites / blacks / shadows from where the tones actually sit
5. palette normalisation: greens, sky and skin land on the look's targets no
   matter which camera or photographer shot the frame
6. outfit protection: a big emerald lehenga or haldi yellow is not treated as
   foliage to be muted
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .analysis import Analysis, display_to_scene_luminance, lstar, lstar_to_y
from .color import lab_to_lch, lch_to_lab, linear_to_oklab, oklab_to_linear
from .engine import base_curve, global_color
from .looks import AutoTargets, Look
from .settings import DevelopSettings


@dataclass
class Plan:
    settings: DevelopSettings
    variant: str
    notes: list = field(default_factory=list)
    deltas: dict = field(default_factory=dict)


def _predict_L(L: float, ev: float, is_raw: bool) -> float:
    """Where a tone at display L* lands after an exposure change (neutral settings)."""
    Yd = lstar_to_y(L)
    Ys = display_to_scene_luminance(Yd, is_raw) * 2.0 ** ev
    Yd2 = float(base_curve(np.array([Ys], np.float32))[0]) if is_raw else min(Ys, 1.0)
    return float(lstar(Yd2))


def _scene_ev(a: Analysis, t: AutoTargets, white_anchor: bool = True) -> float:
    """Exposure from the frame itself (no faces).

    Mid tones aim at the look's median target (small differences are treated
    as intent and only half corrected); a frame whose brightest tones are dim
    is lifted toward a clean white point, like setting levels by hand.
    """
    def ev_to(target_L: float, cur_L: float) -> float:
        ys_t = display_to_scene_luminance(lstar_to_y(target_L), a.is_raw)
        ys_c = display_to_scene_luminance(lstar_to_y(max(cur_L, 3.0)), a.is_raw)
        return math.log2(ys_t / ys_c)

    ev = ev_to(t.scene_mid_L, a.lum.get("p50", 50.0))
    soft = float(np.clip(ev, -0.5, 0.5))
    ev_mid = soft * 0.5 + (ev - soft) * 0.8
    if a.scene == "night" or not white_anchor:
        return ev_mid
    ev_high = ev_to(min(t.white_L, 92.0), a.lum.get("p99", 90.0))
    if ev_high > ev_mid:
        ev_mid += 0.5 * (min(ev_high, ev_mid + 1.2) - ev_mid)
    return ev_mid


def _exposure(a: Analysis, t: AutoTargets) -> tuple[float, str]:
    lim = 2.0 if a.is_raw else 1.2
    if a.skin_L is not None:
        ev_scene = _scene_ev(a, t, white_anchor=False)
        cur = max(a.skin_L, 5.0)
        # Comfort zone: faces already in a flattering range keep the photographer's intent
        # (bright & airy or moody) and only drift gently toward the look's target.
        lo, hi = t.skin_L + t.skin_zone[0], t.skin_L + t.skin_zone[1]
        if cur < lo:
            target = lo + (t.skin_L - lo) * 0.3
            if cur < lo - 18:  # deep skin / dramatic light: meet it part way
                target = cur + (target - cur) * 0.7
        elif cur > hi:
            target = hi + (t.skin_L - hi) * 0.3
        else:
            target = cur + (t.skin_L - cur) * 0.3
        ys_t = display_to_scene_luminance(lstar_to_y(target), a.is_raw)
        ys_c = display_to_scene_luminance(lstar_to_y(cur), a.is_raw)
        ev_face = math.log2(ys_t / ys_c)
        # small faces (group shots, wide frames) share the decision with the whole scene
        w_face = 0.35 + 0.65 * float(np.clip((a.face_area - 0.002) / 0.028, 0.0, 1.0))
        ev = (w_face * ev_face + (1.0 - w_face) * ev_scene) * t.exposure_strength
        why = f"faces L*{cur:.0f}->{target:.0f}, weight {w_face:.2f}"
    else:
        ev = _scene_ev(a, t) * t.exposure_strength
        why = f"scene L*{a.lum.get('p50', 50.0):.0f}->{t.scene_mid_L:.0f}"
    # don't brighten into blown highlights: cap by the bright end of the frame
    if ev > 0:
        p99 = a.lum.get("p99", 90.0)
        while ev > 0.05 and _predict_L(p99, ev, a.is_raw) > 99.0 and a.clip_high < 0.02:
            ev -= 0.1
    return float(np.clip(ev, -lim, lim)), why


def _white_balance(a: Analysis, t: AutoTargets, scene: str) -> tuple[float, float, str]:
    ts, tt = a.wb_shift
    ts *= t.wb_strength
    tt *= t.wb_strength
    note = f"neutral {ts:+.0f} mired {tt:+.0f} tint"
    if ts < 0:  # cooling = the light was warm: keep some of that mood
        keep = t.ambience_keep * {"warm_indoor": 1.6, "night": 1.4}.get(scene, 1.0)
        ts *= 1.0 - min(keep, 0.8)
    lim_t, lim_tint = (150.0, 40.0) if a.is_raw else (70.0, 25.0)
    ts = float(np.clip(ts, -lim_t, lim_t)) + t.warmth
    tt = float(np.clip(tt, -lim_tint, lim_tint)) + t.tint
    return ts, tt, note


def _render_lch(L: float, C: float, h: float, s: DevelopSettings) -> tuple[float, float, float]:
    rgb = oklab_to_linear(lch_to_lab(np.array([L], np.float32), np.array([C], np.float32), np.array([h], np.float32)))
    out = global_color(np.clip(rgb, 0, 1).reshape(1, 3), s)
    Lo, Co, ho = lab_to_lch(linear_to_oklab(np.clip(out, 1e-6, None)))
    return float(Lo[0]), float(Co[0]), float(ho[0])


def _hue_err(a: float, b: float) -> float:
    return ((a - b + 180.0) % 360.0) - 180.0


def _steer(s: DevelopSettings, colour: tuple[float, float, float], kind: str, target: float,
           knobs: list[tuple[str, str]], limit: float, tolerance: float) -> float:
    """Nudge HSL sliders so ``colour`` renders at ``target`` hue or chroma. Returns total move."""
    moved = 0.0
    for _ in range(3):
        Lo, Co, ho = _render_lch(*colour, s)
        err = _hue_err(target, ho) if kind == "hue" else math.log(target / max(Co, 1e-4))
        if abs(err) < tolerance:
            break
        best = None
        for group, band in knobs:
            d = getattr(s, group)
            old = d[band]
            d[band] = old + 10.0
            Lo2, Co2, ho2 = _render_lch(*colour, s)
            d[band] = old
            slope = (_hue_err(ho2, ho) if kind == "hue" else math.log(max(Co2, 1e-4) / max(Co, 1e-4))) / 10.0
            if best is None or abs(slope) > abs(best[2]):
                best = (group, band, slope)
        if best is None or abs(best[2]) < 1e-4:
            break
        group, band, slope = best
        step = float(np.clip(err / slope, -limit - moved, limit - moved))
        step = float(np.clip(step, -limit, limit))
        getattr(s, group)[band] = float(np.clip(getattr(s, group)[band] + step, -100, 100))
        moved += abs(step)
        if moved >= limit:
            break
    return moved


def plan(a: Analysis, look: Look, strength: float = 1.0, auto: bool = True) -> Plan:
    settings, t, variant = look.resolve(a.scene)
    if a.scene == "night" and variant == "base":
        # looks without a night variant still must not turn a night frame into day
        t.skin_L = min(t.skin_L, 52.0)
        t.skin_zone = (-16.0, 10.0)
        t.scene_mid_L = min(t.scene_mid_L, 26.0)
        t.white_L = min(t.white_L, 88.0)
        t.ambience_keep = max(t.ambience_keep, 0.35)
    s = settings.scaled(strength) if strength != 1.0 else settings
    p = Plan(settings=s, variant=variant)
    if not auto:
        return p
    before = s.copy()

    # 1-2. white balance
    ts, tt, note = _white_balance(a, t, a.scene)
    s.temp_shift += ts
    s.tint_shift += tt
    p.notes.append(f"scene {a.scene} ({variant}); {note}; final {ts:+.0f}/{tt:+.0f}")

    # 3. exposure
    ev, why = _exposure(a, t)
    s.exposure += ev
    p.notes.append(f"exposure {ev:+.2f} EV ({why})")

    # 4. tone placement after the exposure change
    white_now = _predict_L(a.lum.get("p99.5", 95.0), ev, a.is_raw)
    black_now = _predict_L(a.lum.get("p0.5", 2.0), ev, a.is_raw)
    clip_after = a.clip_high * (2.0 ** (2.0 * ev))
    if clip_after > 0.002 or white_now > 99.2:
        cap = 55.0 if a.is_raw else 35.0  # rendered files have no highlight data to recover
        s.highlights -= float(np.clip(15.0 + 900.0 * clip_after, 15.0, cap))
    s.whites += float(np.clip((t.white_L - white_now) * 1.5, -20.0, 20.0))
    s.blacks += float(np.clip((t.black_L - black_now) * 2.5, -25.0, 20.0))
    p5 = _predict_L(a.lum.get("p5", 10.0), ev, a.is_raw)
    if a.scene not in ("night",) and p5 < 7.0 and a.lum.get("p25", 30) > 12:
        s.shadows += float(np.clip((7.0 - p5) * 3.0, 0.0, 25.0))

    # 5. palette normalisation
    outfit_bands = {b for b, f in a.outfit.items() if f > 0.02}
    steer_greens = (a.foliage and a.foliage["frac"] > 0.05 and a.scene != "night"
                    and t.foliage_hue is not None and "green" not in outfit_bands)
    if steer_greens:
        col = (a.foliage["L"], a.foliage["C"], a.foliage["h"])
        m = _steer(s, col, "hue", t.foliage_hue, [("hue", "green"), ("hue", "yellow")], 25.0, 3.0)
        if t.foliage_chroma:
            _, c_now, _ = _render_lch(*col, s)
            limit = 25.0 if c_now > t.foliage_chroma else 10.0  # mute freely, boost gently
            m += _steer(s, col, "chroma", t.foliage_chroma, [("sat", "green"), ("sat", "yellow")], limit, 0.08)
        if m:
            p.notes.append(f"greens steered ({m:.0f} slider units)")
    if a.sky and t.sky_chroma and a.scene != "night":
        col = (a.sky["L"], a.sky["C"], 250.0)
        if _steer(s, col, "chroma", t.sky_chroma, [("sat", "blue"), ("sat", "aqua")], 20.0, 0.1):
            p.notes.append("sky saturation steered")
    if a.skin_oklab:
        col = tuple(a.skin_oklab)
        Lo, Co, ho = _render_lch(*col, s)
        if abs(_hue_err(t.skin_hue, ho)) > 4.0:
            _steer(s, col, "hue", t.skin_hue, [("hue", "orange")], 10.0, 2.5)
        lo, hi = t.skin_chroma
        if Co < lo:
            _steer(s, col, "chroma", lo, [("sat", "orange")], 15.0, 0.05)
        elif Co > hi:
            _steer(s, col, "chroma", hi, [("sat", "orange")], 15.0, 0.05)

    # 6. outfit protection: very saturated clothing is not scenery
    for band in outfit_bands:
        frac = a.outfit[band]
        keep = float(np.clip(1.0 - frac / 0.06, 0.35, 1.0))
        if s.hue[band] and band not in ("orange",):
            s.hue[band] *= keep
        if s.sat[band] < 0:
            s.sat[band] *= keep
        s.outfit_protect = max(s.outfit_protect, 0.8)
    if outfit_bands:
        p.notes.append("outfit protection: " + ", ".join(sorted(outfit_bands)))

    s.clamp()
    after = s.to_dict()
    ref = before.to_dict()
    p.deltas = {k: (round(after[k] - ref[k], 2) if not isinstance(after[k], (dict, list)) else None)
                for k in after if after[k] != ref[k]}
    return p
