import xml.etree.ElementTree as ET

import numpy as np
import pytest

from vivaah import cli, lut, wb, xmp
from vivaah.looks import get_look
from vivaah.settings import DevelopSettings

CRS = "{http://ns.adobe.com/camera-raw-settings/1.0/}"


def test_preset_xmp_is_valid_and_has_no_white_balance():
    text = xmp.preset_xmp(get_look("signature"))
    root = ET.fromstring(text)
    desc = next(root.iter("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description"))
    assert desc.get(CRS + "PresetType") == "Normal"
    assert desc.get(CRS + "HueAdjustmentGreen") == "-52"
    assert desc.get(CRS + "Temperature") is None and desc.get(CRS + "Exposure2012") is None
    tone = next(root.iter(CRS + "ToneCurvePV2012"))
    curve = [li.text for li in tone.iter("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li")]
    assert curve[0].startswith("0,") and any(c.startswith("255,") for c in curve)


def _sidecar_desc(path):
    root = ET.parse(path).getroot()
    return next(root.iter("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description"))


def test_sidecar_absolute_white_balance(tmp_path):
    raw = tmp_path / "IMG_0001.CR3"
    raw.write_bytes(b"")
    s = DevelopSettings(exposure=0.4, temp_shift=20, tint_shift=4)
    side = xmp.write_sidecar(raw, s, as_shot=(5000.0, 5.0), look_id="signature")
    d = _sidecar_desc(side)
    kelvin, tint = wb.apply_shift(5000, 5, 20, 4)
    assert d.get(CRS + "WhiteBalance") == "Custom"
    assert int(d.get(CRS + "Temperature")) == round(kelvin)
    assert d.get(CRS + "Tint") == f"{round(tint):+d}"
    assert d.get(CRS + "Exposure2012") == "+0.40"
    assert d.get(CRS + "RawFileName") == "IMG_0001.CR3"


def test_sidecar_merge_keeps_ratings_and_crop_and_is_idempotent(tmp_path):
    raw = tmp_path / "DSC_0042.NEF"
    raw.write_bytes(b"")
    existing = """<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
    xmp:Rating="4" crs:WhiteBalance="As Shot" crs:Temperature="4300" crs:Tint="+12"
    crs:CropTop="0.1" crs:HasCrop="True" crs:Contrast2012="+50"/>
 </rdf:RDF>
</x:xmpmeta>"""
    raw.with_suffix(".xmp").write_text(existing)
    s = DevelopSettings(temp_shift=10)
    xmp.write_sidecar(raw, s, as_shot=(9999.0, 0.0))  # the Lightroom as-shot wins over our estimate
    d = _sidecar_desc(raw.with_suffix(".xmp"))
    assert d.get("{http://ns.adobe.com/xap/1.0/}Rating") == "4"
    assert d.get(CRS + "CropTop") == "0.1"
    assert d.get(CRS + "Contrast2012") == "0"
    first = d.get(CRS + "Temperature")
    assert int(first) == round(wb.apply_shift(4300, 12, 10, 0)[0])
    xmp.write_sidecar(raw, s, as_shot=(9999.0, 0.0))  # re-run: still relative to the original as-shot
    assert _sidecar_desc(raw.with_suffix(".xmp")).get(CRS + "Temperature") == first
    assert raw.with_suffix(".xmp.vivaah-backup").exists()
    assert xmp.restore_sidecar(raw)
    assert "Contrast2012=\"+50\"" in raw.with_suffix(".xmp").read_text()


def test_cube_lut_roundtrip(tmp_path):
    path = lut.write_cube(get_look("signature"), tmp_path / "sig.cube", size=17)
    text = path.read_text().splitlines()
    assert "LUT_3D_SIZE 17" in text
    table = lut.read_cube(path)
    assert table.shape == (17, 17, 17, 3)
    assert table.min() >= 0 and table.max() <= 1
    # red changes fastest in the file: the second data row is the next red step
    assert table[1, 0, 0, 0] >= table[0, 0, 0, 0]


def test_neutral_look_lut_is_identity(tmp_path):
    look = get_look("true-colour")
    look.settings = {}
    look.auto = {}
    table = lut.read_cube(lut.write_cube(look, tmp_path / "id.cube", size=9))
    g = np.linspace(0, 1, 9)
    assert np.allclose(table[:, 0, 0, 0], g, atol=2e-3)
    assert np.allclose(table[4, 4, 4], 0.5, atol=2e-3)


def test_cli_end_to_end(jpeg_folder, tmp_path, capsys):
    out = tmp_path / "graded"
    assert cli.main(["grade", str(jpeg_folder), "--look", "signature", "--export", str(out),
                     "--size", "400", "--workers", "1"]) == 0
    files = sorted(p.name for p in out.glob("*.jpg"))
    assert files == ["a_day.jpg", "b_dark.jpg", "c_warm.jpg"]
    assert (out / "vivaah_report" / "summary.csv").exists()
    assert (out / "vivaah_report" / "before_after.jpg").exists()
    assert cli.main(["presets", "--out", str(tmp_path / "presets")]) == 0
    assert len(list((tmp_path / "presets").glob("*.xmp"))) >= 9
    assert cli.main(["lut", "--look", "signature", "--out", str(tmp_path / "s.cube"), "--size", "9"]) == 0
    assert cli.main(["learn", str(jpeg_folder), "--name", "Test Style", "--out", str(tmp_path / "looks")]) == 0
    assert (tmp_path / "looks" / "test-style.json").exists()
    assert cli.main(["--looks-dir", str(tmp_path / "looks"), "looks"]) == 0
    assert "test-style" in capsys.readouterr().out
