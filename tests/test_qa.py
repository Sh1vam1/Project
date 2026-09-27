import json

import numpy as np
import pytest

from vivaah import qa
from vivaah.color import delta_e2000, ycbcr709

SHARMA = [  # CIEDE2000 reference pairs (Sharma, Wu & Dalal 2005)
    ((50, 2.6772, -79.7751), (50, 0, -82.7485), 2.0425),
    ((50, 0, 0), (50, -1, 2), 2.3669),
    ((50, 2.49, -0.001), (50, -2.49, 0.0009), 7.1792),
    ((50, 2.5, 0), (73, 25, -18), 27.1492),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ((22.7233, 20.0904, -46.6940), (23.0331, 14.9730, -42.5619), 2.0373),
]


@pytest.mark.parametrize("lab1,lab2,expected", SHARMA)
def test_ciede2000_matches_reference_data(lab1, lab2, expected):
    assert float(delta_e2000(np.array(lab1), np.array(lab2))) == pytest.approx(expected, abs=1e-4)


def test_colorchecker_skin_sits_on_the_skin_tone_line():
    for idx in (0, 1):
        rgb = np.array(qa.COLORCHECKER[idx][1], np.float32) / 255
        _, cb, cr = ycbcr709(rgb)
        assert abs(np.degrees(np.arctan2(cr, cb)) - qa.SKIN_LINE) < 3


def test_engine_checks_all_pass(tmp_path):
    checks = qa.engine_checks(tmp_path)
    failed = [(c.name, c.value) for c in checks if c.status == "fail"]
    assert not failed
    assert any("ColorChecker" in c.name for c in checks)


def test_qa_run_writes_report(jpeg_folder, tmp_path):
    report, checks = qa.run([str(jpeg_folder)], out_dir=str(tmp_path / "qa"), size=400, log=lambda *_: None)
    assert report.exists() and "vivaah QA report" in report.read_text()
    data = json.loads((tmp_path / "qa" / "report.json").read_text())
    assert len(data["photos"]) == 3
    assert any(c.area == "file" and "sRGB" in c.name and c.status == "pass" for c in checks)
    assert (tmp_path / "qa" / "graded" / "a_day.jpg").exists()
