import numpy as np
import pytest

from vivaah import analysis, auto
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
