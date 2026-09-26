import numpy as np
import pytest

from vivaah import engine
from vivaah.color import lab_to_lch, linear_to_oklab, luminance
from vivaah.engine import RenderContext, compress_gamut, render
from vivaah.imageio import encode_8bit
from vivaah.settings import DevelopSettings


def test_default_settings_are_identity_for_rendered_files(scene):
    out = render(scene, DevelopSettings())
    diff = np.abs(encode_8bit(out).astype(int) - encode_8bit(scene).astype(int))
    assert diff.max() <= 1 and (diff > 0).mean() < 1e-3  # float rounding only


def test_raw_base_curve_is_monotone_and_maps_white():
    y = np.geomspace(1e-5, 4, 400).astype(np.float32)
    d = engine.base_curve(y)
    assert np.all(np.diff(d) >= -1e-7)
    assert engine.base_curve(np.array([1.0], np.float32))[0] > 0.9


def test_exposure_contrast_and_highlights(scene):
    base = luminance(render(scene, DevelopSettings()))
    brighter = luminance(render(scene, DevelopSettings(exposure=1.0)))
    assert brighter.mean() > base.mean() * 1.3
    contrasty = luminance(render(scene, DevelopSettings(contrast=60)))
    assert contrasty.std() > base.std()
    recovered = render(scene, DevelopSettings(highlights=-80))
    bright = base > np.percentile(base, 90)
    assert luminance(recovered)[bright].mean() < base[bright].mean()


def test_hsl_green_hue_moves_toward_yellow():
    green = np.array([[[0.10, 0.35, 0.05]]], np.float32)
    h0 = lab_to_lch(linear_to_oklab(render(green, DevelopSettings())))[2].item()
    s = DevelopSettings()
    s.hue["green"] = -60
    h1 = lab_to_lch(linear_to_oklab(render(green, s)))[2].item()
    assert h1 < h0 - 10


def test_saturation_minus_100_is_grey(scene):
    out = render(scene, DevelopSettings(saturation=-100))
    C = lab_to_lch(linear_to_oklab(out))[1]
    assert C.max() < 0.01


def test_temperature_shift_warms(scene):
    warm = render(scene, DevelopSettings(temp_shift=40))
    ref = render(scene, DevelopSettings())
    assert (warm[..., 0] / (warm[..., 2] + 1e-4)).mean() > (ref[..., 0] / (ref[..., 2] + 1e-4)).mean()


def test_compress_gamut_properties():
    rng = np.random.default_rng(3)
    inside = rng.random((1000, 3)).astype(np.float32)
    assert np.array_equal(compress_gamut(inside), inside)
    wild = rng.normal(0.6, 0.6, (1000, 3)).astype(np.float32)
    out = compress_gamut(wild)
    assert out.min() >= 0 and out.max() <= 1
    Y_in = np.clip(wild @ engine.LUMA, 0, 1)
    ok = (wild @ engine.LUMA > 0) & (wild @ engine.LUMA < 1)
    assert np.allclose((out @ engine.LUMA)[ok], Y_in[ok], atol=1e-4)


def test_lut_matches_direct_colour(scene):
    s = DevelopSettings(vibrance=20, saturation=-10, grade_high_hue=45, grade_high_sat=15)
    s.hue["green"] = -40
    s.sat["blue"] = -30
    disp = np.clip(scene, 0, 1)
    direct = engine.linear_to_srgb(compress_gamut(engine.global_color(disp, s)))
    via_lut = engine.apply_lut(engine.linear_to_srgb(disp), engine.build_lut(s))
    assert np.abs(direct - via_lut).mean() * 255 < 0.5


def test_strips_match_whole_image(scene, monkeypatch):
    s = DevelopSettings(contrast=20, highlights=-40, shadows=30, clarity=15, texture=-10, dehaze=10,
                        vignette_amount=-20, sharpen_amount=40)
    whole = render(scene, s, RenderContext(seed=1))
    monkeypatch.setattr(engine, "STRIP_PIXELS", 20_000)
    strips = render(scene, s, RenderContext(seed=1))
    assert np.abs(whole - strips).max() < 1e-4


def test_skin_mask_limits_hue_change():
    skin = np.full((40, 40, 3), [0.55, 0.30, 0.20], np.float32)
    s = DevelopSettings(skin_protect=1.0)
    s.hue["orange"] = 60
    free = lab_to_lch(linear_to_oklab(render(skin, s)))[2].mean()
    protected = lab_to_lch(linear_to_oklab(render(skin, s, RenderContext(skin_mask=np.ones((10, 10), np.float32)))))[2].mean()
    original = lab_to_lch(linear_to_oklab(skin))[2].mean()
    assert abs(protected - original) < abs(free - original)


@pytest.mark.parametrize("name", ["contrast", "highlights", "shadows", "whites", "blacks"])
def test_tone_curves_monotone(name):
    for v in (-100, 100):
        xs, ys = engine.tone_curve_lut(DevelopSettings(**{name: v}))
        assert np.all(np.diff(ys) >= 0)
