import numpy as np
import pytest

from vivaah import analysis, auto
from vivaah.color import lab_to_lch, lch_to_lab, linear_to_oklab, oklab_to_linear
from vivaah.engine import RenderContext, render
from vivaah.faces import Face
from vivaah.imageio import Photo
from vivaah.looks import all_looks, get_look
from vivaah.settings import BANDS, DevelopSettings


def photo(rgb, raw=False):
    return Photo(path=__import__("pathlib").Path("x.jpg"), rgb=rgb, is_raw=raw)


def test_every_builtin_look_resolves():
    looks = all_looks()
    assert {"signature", "royal-heritage", "pastel-fine-art", "cinematic-editorial", "portra-film",
            "true-colour", "haldi-sunshine", "mehendi-emerald", "night-glam"} <= set(looks)
    for look in looks.values():
        for scene in (None, "day", "night", "warm_indoor", "shade"):
            s, t, _ = look.resolve(scene)
            assert isinstance(s, DevelopSettings)
            assert t.white_L > t.black_L


def test_signature_has_scene_variants():
    look = get_look("signature")
    assert look.resolve("night")[2] == "night"
    assert look.resolve("warm_indoor")[2] == "warm_indoor"
    assert look.resolve("day")[2] == "base"


def test_warm_cast_gets_cooled(scene):
    warm = scene * np.array([1.0, 0.8, 0.55], np.float32)
    a, _ = analysis.analyze(photo(warm))
    b, _ = analysis.analyze(photo(scene))
    assert a.wb_shift[0] < b.wb_shift[0] - 15


def test_dark_photo_gets_brighter(scene):
    dark = auto.plan(analysis.analyze(photo(scene * 0.3))[0], get_look("true-colour")).settings.exposure
    normal = auto.plan(analysis.analyze(photo(scene))[0], get_look("true-colour")).settings.exposure
    assert dark > 0.3
    assert dark > normal + 0.4


def test_night_frame_is_classified_and_not_forced_bright(scene):
    night = scene * 0.02
    night[100:120, 200:230] = 0.9  # a few practical lights
    a, _ = analysis.analyze(photo(night))
    assert a.scene == "night"
    p = auto.plan(a, get_look("signature"))
    assert p.variant == "night"
    p2 = auto.plan(a, get_look("true-colour"))  # look without a night variant
    assert p2.settings.exposure < 2.0


def test_plan_settings_stay_in_range(scene):
    a, mask = analysis.analyze(photo(scene))
    for look in all_looks().values():
        s = auto.plan(a, look).settings
        assert -5 <= s.exposure <= 5
        for b in BANDS:
            assert -100 <= s.hue[b] <= 100 and -100 <= s.sat[b] <= 100
    assert mask.dtype == np.uint8 and max(mask.shape) <= analysis.MASK_EDGE


def test_strength_zero_keeps_only_corrections(scene):
    a, _ = analysis.analyze(photo(scene))
    s = auto.plan(a, get_look("signature"), strength=0.0).settings
    assert s.hue["green"] == pytest.approx(0, abs=30)  # only palette steering may remain
    assert s.grade_shadow_sat == 0


def test_generic_skin_mask_without_faces(scene):
    a, mask = analysis.analyze(photo(scene))
    assert len(a.faces) == 0
    # the skin patch (x 40..130) should score higher than the foliage band
    h, w = mask.shape
    skin_score = mask[int(h * 0.45):int(h * 0.85), int(w * 45 / 480):int(w * 125 / 480)].mean()
    foliage_score = mask[int(h * 0.4):int(h * 0.6), int(w * 430 / 480):].mean()
    assert skin_score > foliage_score


def test_planner_colour_model_matches_the_renderer_for_raws():
    # white balance on raws is relative to the camera's as-shot; the planner must predict exactly that
    base = (3200.0, 10.0)
    s = get_look("signature").resolve("warm_indoor")[0]
    for k in ("contrast", "highlights", "shadows", "whites", "blacks", "clarity", "texture", "dehaze", "exposure"):
        setattr(s, k, 0.0)
    s.temp_shift, s.tint_shift = -12.0, 15.0
    L, C, h = 0.70, 0.08, 58.0
    rgb = oklab_to_linear(lch_to_lab(np.array([L], np.float32), np.array([C], np.float32), np.array([h], np.float32)))
    patch = np.full((8, 8, 3), rgb.reshape(3), np.float32)
    rendered = lab_to_lch(linear_to_oklab(render(patch, s, RenderContext(wb_base=base), finishing=False)))[2].mean()
    assert abs(auto._render_lch(L, C, h, s, base)[2] - rendered) < 0.5
    assert abs(auto._render_lch(L, C, h, s)[2] - rendered) > 3  # the old D65-relative model was this far off


def test_steering_never_moves_a_slider_beyond_its_budget(monkeypatch):
    # the colour responds less and less: each step under-delivers and the next asks for more
    monkeypatch.setattr(auto, "_render_lch",
                        lambda L, C, h, s, base=None, skin=0.0: (L, C, 120.0 + 30.0 * np.tanh(s.hue["green"] / 10.0)))
    s = DevelopSettings()
    moved = auto._steer(s, (0.5, 0.06, 120.0), "hue", 80.0, [("hue", "green")], 25.0, 1.0)
    assert moved <= 25.0 + 1e-6 and abs(s.hue["green"]) <= 25.0 + 1e-6


def test_tiny_face_does_not_hide_white_surfaces_from_white_balance(scene, monkeypatch):
    warm = scene * np.array([1.0, 0.8, 0.55], np.float32)  # tungsten-lit: the white dress looks skin-coloured
    no_face, _ = analysis.analyze(photo(warm))
    marks = np.array([[22, 152], [25, 152], [23.5, 154], [22, 156], [25, 156]], np.float32)
    tiny = Face(20, 150, 6, 7, 0.9, marks)  # too small for a face-seeded skin model
    monkeypatch.setattr(analysis.facemod, "detect", lambda rgb8, *a, **k: [tiny])
    with_face, _ = analysis.analyze(photo(warm))
    assert with_face.faces
    assert abs(with_face.wb_shift[0] - no_face.wb_shift[0]) <= 5
    assert abs(with_face.wb_shift[1] - no_face.wb_shift[1]) <= 3
