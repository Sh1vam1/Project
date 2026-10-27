import numpy as np
import pytest

from vivaah import engine
from vivaah.color import lab_to_lch, linear_to_oklab, linear_to_srgb, luminance, srgb_to_linear
from vivaah.engine import RenderContext, compress_gamut, render
from vivaah.imageio import encode_8bit
from vivaah.looks import get_look
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


@pytest.mark.parametrize("rgb8", [(220, 50, 150), (200, 40, 200), (180, 150, 220), (110, 40, 160)])
def test_skin_protection_leaves_pinks_and_purples_alone(rgb8):
    # fuchsia, magenta, lavender, purple: dupattas and flowers at the soft edge of a skin mask
    s = get_look("signature").resolve(None)[0]
    s.skin_protect = 1.0
    patch = np.full((8, 8, 3), srgb_to_linear(np.array(rgb8, np.float32) / 255.0), np.float32)
    ctx = RenderContext(skin_mask=np.ones((4, 4), np.float32))
    assert np.abs(render(patch, s, ctx) - render(patch, s)).max() < 1e-4
    enc = linear_to_srgb(np.clip(render(patch, DevelopSettings()), 0, 1))
    via_safe = engine.apply_lut(enc, engine.build_lut(s, skin_safe=True))
    assert np.abs(via_safe - engine.apply_lut(enc, engine.build_lut(s))).max() < 1e-3


def test_skin_safe_colour_is_only_computed_inside_the_mask_box(scene, monkeypatch):
    seen = []
    real = engine.global_color

    def spy(rgb, s, skin_safe=False):
        if skin_safe:
            seen.append(rgb.shape[0] * rgb.shape[1])
        return real(rgb, s, skin_safe)

    monkeypatch.setattr(engine, "global_color", spy)
    mask = np.zeros((32, 48), np.float32)
    mask[12:20, 4:12] = 1.0  # one face
    render(scene, DevelopSettings(skin_protect=0.8, vibrance=20), RenderContext(skin_mask=mask))
    assert seen and sum(seen) < 0.15 * scene.shape[0] * scene.shape[1]


@pytest.mark.parametrize("shape", [(6, 33000), (33000, 6)])
def test_panoramas_beyond_the_opencv_remap_limit(shape):
    rng = np.random.default_rng(5)
    img = (rng.random(shape + (3,), dtype=np.float32) * 0.8).astype(np.float32)
    s = DevelopSettings(contrast=20, highlights=-30, clarity=10, vignette_amount=-15, skin_protect=0.8)
    out = render(img, s, RenderContext(skin_mask=np.ones((3, 3), np.float32)))
    assert out.shape == img.shape and np.isfinite(out).all()


def test_wide_map_upsampling_is_exact_bilinear():
    lo = np.random.default_rng(2).random((7, 9), dtype=np.float32)
    big = engine._upsample_rows(lo, 0, 50, 40000, 50)
    xs = np.clip((np.arange(40000) + 0.5) * 9 / 40000 - 0.5, 0, 8)
    ys = np.clip((np.arange(50) + 0.5) * 7 / 50 - 0.5, 0, 6)
    x0, y0 = np.minimum(xs.astype(int), 7), np.minimum(ys.astype(int), 5)
    fx, fy = xs - x0, (ys - y0)[:, None]
    ref = ((lo[y0][:, x0] * (1 - fx) + lo[y0][:, x0 + 1] * fx) * (1 - fy)
           + (lo[y0 + 1][:, x0] * (1 - fx) + lo[y0 + 1][:, x0 + 1] * fx) * fy)
    assert np.abs(big - ref).max() < 1e-5


def test_apply_lut_takes_colour_lists_and_wide_images():
    lut = engine.build_lut(DevelopSettings(vibrance=30, saturation=-10), size=17)
    cols = np.random.default_rng(1).random((40000, 3), dtype=np.float32)
    as_list = engine.apply_lut(cols, lut)
    assert as_list.shape == (40000, 3)
    assert np.array_equal(as_list, engine.apply_lut(cols.reshape(200, 200, 3), lut).reshape(-1, 3))
    wide = cols[:33000].reshape(1, 33000, 3)
    assert np.array_equal(engine.apply_lut(wide, lut).reshape(-1, 3), as_list[:33000])
    assert engine.apply_lut(cols[0], lut).shape == (3,)


def test_lut_texture_cache_holds_the_look_and_skin_luts():
    s = DevelopSettings(saturation=10, skin_protect=1.0)
    a, b = engine.build_lut(s, size=9), engine.build_lut(s, size=9, skin_safe=True)
    ta, tb = engine._lut_texture(a), engine._lut_texture(b)
    assert engine._lut_texture(a) is ta and engine._lut_texture(b) is tb
