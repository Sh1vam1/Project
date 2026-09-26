"""Develop settings: one place that mirrors Lightroom's sliders.

Every field maps to a Lightroom / Camera Raw setting (see ``xmp.py``) except
the few marked *vivaah only*, which the local renderer uses but Lightroom has
no slider for (skin protection, outfit protection).
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field, fields
from typing import Any

BANDS = ("red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta")

Curve = list  # list of [x, y] points in 0..255


def _bands() -> dict[str, float]:
    return {b: 0.0 for b in BANDS}


def _linear_curve() -> Curve:
    return [[0, 0], [255, 255]]


@dataclass
class DevelopSettings:
    # White balance, relative to as-shot (see wb.py)
    temp_shift: float = 0.0  # mireds, + = warmer
    tint_shift: float = 0.0  # Lightroom tint units, + = magenta

    # Basic panel
    exposure: float = 0.0  # EV, -5..+5
    contrast: float = 0.0  # -100..100
    highlights: float = 0.0
    shadows: float = 0.0
    whites: float = 0.0
    blacks: float = 0.0
    texture: float = 0.0
    clarity: float = 0.0
    dehaze: float = 0.0
    vibrance: float = 0.0
    saturation: float = 0.0

    # Tone curve
    parametric_shadows: float = 0.0
    parametric_darks: float = 0.0
    parametric_lights: float = 0.0
    parametric_highlights: float = 0.0
    parametric_shadow_split: float = 25.0
    parametric_midtone_split: float = 50.0
    parametric_highlight_split: float = 75.0
    curve: Curve = field(default_factory=_linear_curve)
    curve_red: Curve = field(default_factory=_linear_curve)
    curve_green: Curve = field(default_factory=_linear_curve)
    curve_blue: Curve = field(default_factory=_linear_curve)

    # HSL / Color mixer
    hue: dict = field(default_factory=_bands)
    sat: dict = field(default_factory=_bands)
    lum: dict = field(default_factory=_bands)

    # Color grading (hue 0..360, sat 0..100, lum -100..100)
    grade_shadow_hue: float = 0.0
    grade_shadow_sat: float = 0.0
    grade_shadow_lum: float = 0.0
    grade_mid_hue: float = 0.0
    grade_mid_sat: float = 0.0
    grade_mid_lum: float = 0.0
    grade_high_hue: float = 0.0
    grade_high_sat: float = 0.0
    grade_high_lum: float = 0.0
    grade_global_hue: float = 0.0
    grade_global_sat: float = 0.0
    grade_global_lum: float = 0.0
    grade_blending: float = 50.0
    grade_balance: float = 0.0

    # Calibration
    cal_shadow_tint: float = 0.0
    cal_red_hue: float = 0.0
    cal_red_sat: float = 0.0
    cal_green_hue: float = 0.0
    cal_green_sat: float = 0.0
    cal_blue_hue: float = 0.0
    cal_blue_sat: float = 0.0

    # Effects
    vignette_amount: float = 0.0
    vignette_midpoint: float = 50.0
    vignette_feather: float = 50.0
    vignette_roundness: float = 0.0
    vignette_highlights: float = 0.0
    grain_amount: float = 0.0
    grain_size: float = 25.0
    grain_roughness: float = 50.0

    # Detail (output sharpening)
    sharpen_amount: float = 0.0
    sharpen_radius: float = 1.0
    sharpen_detail: float = 25.0
    sharpen_masking: float = 0.0

    # vivaah only
    skin_protect: float = 0.0  # 0..1, keeps skin hue/chroma natural under the look
    outfit_protect: float = 0.0  # 0..1, keeps very saturated outfit colours out of big hue shifts

    def copy(self) -> "DevelopSettings":
        return copy.deepcopy(self)

    def to_dict(self, only_changed: bool = False) -> dict[str, Any]:
        d = asdict(self)
        if not only_changed:
            return d
        default = asdict(DevelopSettings())
        out: dict[str, Any] = {}
        for k, v in d.items():
            if isinstance(v, dict):
                changed = {b: x for b, x in v.items() if abs(x - default[k][b]) > 1e-9}
                if changed:
                    out[k] = changed
            elif v != default[k]:
                out[k] = v
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "DevelopSettings":
        s = cls()
        s.update(d or {})
        return s

    def update(self, d: dict[str, Any]) -> "DevelopSettings":
        """Override fields from a (possibly partial) dict, e.g. a look file."""
        names = {f.name for f in fields(self)}
        for k, v in d.items():
            if k not in names:
                raise KeyError(f"unknown develop setting '{k}'")
            if k in ("hue", "sat", "lum"):
                for band, x in v.items():
                    if band not in BANDS:
                        raise KeyError(f"unknown colour band '{band}'")
                    getattr(self, k)[band] = float(x)
            elif k.startswith("curve"):
                setattr(self, k, [[float(p[0]), float(p[1])] for p in v])
            else:
                setattr(self, k, float(v))
        return self

    def add(self, d: dict[str, Any]) -> "DevelopSettings":
        """Add per-photo deltas on top of the current values (then clamp)."""
        for k, v in d.items():
            if k in ("hue", "sat", "lum"):
                for band, x in v.items():
                    getattr(self, k)[band] += float(x)
            else:
                setattr(self, k, getattr(self, k) + float(v))
        return self.clamp()

    def clamp(self) -> "DevelopSettings":
        c = _clip
        self.exposure = c(self.exposure, -5, 5)
        for name in ("contrast", "highlights", "shadows", "whites", "blacks", "texture", "clarity",
                     "dehaze", "vibrance", "saturation", "parametric_shadows", "parametric_darks",
                     "parametric_lights", "parametric_highlights", "grade_shadow_lum", "grade_mid_lum",
                     "grade_high_lum", "grade_global_lum", "grade_balance", "cal_shadow_tint",
                     "cal_red_hue", "cal_red_sat", "cal_green_hue", "cal_green_sat", "cal_blue_hue",
                     "cal_blue_sat", "vignette_amount", "vignette_roundness"):
            setattr(self, name, c(getattr(self, name), -100, 100))
        for name in ("grade_shadow_sat", "grade_mid_sat", "grade_high_sat", "grade_global_sat",
                     "grade_blending", "vignette_midpoint", "vignette_feather", "vignette_highlights",
                     "grain_amount", "grain_size", "grain_roughness", "sharpen_detail", "sharpen_masking"):
            setattr(self, name, c(getattr(self, name), 0, 100))
        for name in ("grade_shadow_hue", "grade_mid_hue", "grade_high_hue", "grade_global_hue"):
            setattr(self, name, getattr(self, name) % 360.0)
        self.sharpen_amount = c(self.sharpen_amount, 0, 150)
        self.sharpen_radius = c(self.sharpen_radius, 0.5, 3.0)
        self.temp_shift = c(self.temp_shift, -250, 250)
        self.tint_shift = c(self.tint_shift, -150, 150)
        self.skin_protect = c(self.skin_protect, 0, 1)
        self.outfit_protect = c(self.outfit_protect, 0, 1)
        for k in ("hue", "sat", "lum"):
            for band in BANDS:
                getattr(self, k)[band] = c(getattr(self, k)[band], -100, 100)
        return self

    def scaled(self, strength: float) -> "DevelopSettings":
        """Blend between neutral (0) and these settings (1); used for look strength."""
        if abs(strength - 1.0) < 1e-9:
            return self.copy()
        base = DevelopSettings()
        out = self.copy()
        for f in fields(self):
            v, b = getattr(self, f.name), getattr(base, f.name)
            if isinstance(v, dict):
                setattr(out, f.name, {k: b[k] + (x - b[k]) * strength for k, x in v.items()})
            elif isinstance(v, list):
                setattr(out, f.name, [[p[0], p[0] + (p[1] - p[0]) * strength] for p in v])
            elif f.name.endswith("_hue") and f.name.startswith("grade"):
                continue  # hue is an angle; strength acts through the saturation
            else:
                setattr(out, f.name, b + (v - b) * strength)
        return out


def _clip(v: float, lo: float, hi: float) -> float:
    return float(min(max(v, lo), hi))
