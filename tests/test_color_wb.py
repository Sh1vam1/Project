import numpy as np
import pytest

from vivaah import wb
from vivaah.color import (lab_to_linear, linear_to_lab, linear_to_oklab, linear_to_srgb, oklab_to_linear,
                          srgb_to_linear, wheel_hue_to_oklab_hue)


def test_srgb_roundtrip_and_uint8_path():
    x = np.random.default_rng(1).random((64, 64, 3)).astype(np.float32)
    assert np.allclose(linear_to_srgb(srgb_to_linear(x)), x, atol=1e-5)
    u = (x * 255).astype(np.uint8)
    assert np.allclose(srgb_to_linear(u), srgb_to_linear(u.astype(np.float32) / 255), atol=1e-6)
    assert float(linear_to_srgb(np.float32(0.18))) == pytest.approx(0.4613, abs=1e-3)


def test_lab_and_oklab_roundtrip():
    x = np.random.default_rng(2).random((500, 3)).astype(np.float32)
    assert np.allclose(oklab_to_linear(linear_to_oklab(x)), x, atol=1e-4)
    assert np.allclose(lab_to_linear(linear_to_lab(x)), x, atol=1e-4)
    white = linear_to_lab(np.ones((1, 3), np.float32))[0]
    assert white[0] == pytest.approx(100, abs=0.05) and abs(white[1]) < 0.05 and abs(white[2]) < 0.05


def test_band_hue_order():
    hues = [wheel_hue_to_oklab_hue(h) for h in (0, 30, 60, 120, 180, 240, 270, 300)]
    assert hues == sorted(hues)


def test_known_illuminants():
    t, tint = wb.xy_to_temp_tint(0.44757, 0.40745)  # CIE illuminant A
    assert t == pytest.approx(2856, abs=5)
    assert abs(tint) < 1
    assert wb.D65_TEMP == pytest.approx(6504, abs=5)


@pytest.mark.parametrize("kelvin,tint", [(2500, -20), (3200, 0), (5500, 10), (7500, 25), (12000, -40)])
def test_temp_tint_roundtrip(kelvin, tint):
    x, y = wb.temp_tint_to_xy(kelvin, tint)
    k2, t2 = wb.xy_to_temp_tint(x, y)
    assert k2 == pytest.approx(kelvin, rel=1e-4)
    assert t2 == pytest.approx(tint, abs=1e-3)
    xv, yv = wb.temp_tint_to_xy_vec(np.array([kelvin]), np.array([tint]))
    assert xv[0] == pytest.approx(x) and yv[0] == pytest.approx(y)


def test_neutralize_warm_cast():
    observed = np.array([1.0, 0.85, 0.62])
    ts, tt = wb.shift_to_neutralize(observed)
    assert ts < -20  # needs cooling
    out = wb.shift_matrix(ts, tt) @ observed
    assert np.allclose(out / out[1], 1.0, atol=0.01)


def test_shift_semantics():
    k, t = wb.apply_shift(5000, 5, temp_shift=20, tint_shift=3)
    assert k > 5000 and t == 8  # warmer picture = higher Kelvin, like Lightroom
    assert np.allclose(wb.shift_matrix(0, 0), np.eye(3))


def test_camera_neutral_to_temp_tint_with_srgb_matrix():
    xyz_to_cam = np.linalg.inv(np.array([[0.4124564, 0.3575761, 0.1804375],
                                         [0.2126729, 0.7151522, 0.0721750],
                                         [0.0193339, 0.1191920, 0.9503041]]))
    t, tint = wb.camera_neutral_to_temp_tint(np.ones(3), xyz_to_cam)
    assert t == pytest.approx(wb.D65_TEMP, abs=10)
    assert wb.camera_neutral_to_temp_tint(np.ones(3), np.zeros((3, 3))) is None
