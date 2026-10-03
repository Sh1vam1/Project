"""The user's files come first: undo, re-runs, name clashes, overwrites, worker memory."""
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from vivaah import batch, cli, wb, xmp
from vivaah.analysis import Analysis
from vivaah.auto import Plan
from vivaah.batch import BatchOptions, Item
from vivaah.pipeline import Result
from vivaah.settings import DevelopSettings

FOREIGN = """<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
    xmp:Rating="5" crs:WhiteBalance="As Shot" crs:Temperature="5200" crs:Tint="+4" crs:Contrast2012="+35"/>
 </rdf:RDF>
</x:xmpmeta>"""


def test_restore_after_two_runs_removes_the_sidecar_vivaah_created(tmp_path):
    raw = tmp_path / "DSC_0001.NEF"
    raw.write_bytes(b"")
    xmp.write_sidecar(raw, DevelopSettings(temp_shift=10), (5000.0, 0.0), "signature")
    xmp.write_sidecar(raw, DevelopSettings(temp_shift=-5), (5000.0, 0.0), "royal-heritage")
    assert not raw.with_suffix(".xmp.vivaah-backup").exists()
    assert xmp.restore_sidecar(raw)
    assert not raw.with_suffix(".xmp").exists()  # back to "no sidecar", not to the first run


def test_restore_after_two_runs_returns_the_original_sidecar(tmp_path):
    raw = tmp_path / "DSC_0002.CR3"
    raw.write_bytes(b"")
    raw.with_suffix(".xmp").write_text(FOREIGN)
    for look in ("signature", "portra-film"):
        xmp.write_sidecar(raw, DevelopSettings(temp_shift=8), (6000.0, 0.0), look)
    assert xmp.restore_sidecar(raw)
    assert raw.with_suffix(".xmp").read_text() == FOREIGN
    assert not raw.with_suffix(".xmp.vivaah-backup").exists()


def test_rerun_with_export_inside_the_wedding_folder(jpeg_folder):
    out = jpeg_folder / "vivaah-graded"
    (jpeg_folder / "a_day_looks.jpg").write_bytes((jpeg_folder / "a_day.jpg").read_bytes())  # a preview sheet
    opts = BatchOptions(look="signature", export_dir=str(out), workers=1, long_edge=200, sheet=False)
    for _ in range(2):
        items = batch.run([str(jpeg_folder)], opts, log=lambda m: None)
        assert sorted(it.path.name for it in items) == ["a_day.jpg", "b_dark.jpg", "c_warm.jpg"]
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*.jpg"))
    assert files == ["a_day.jpg", "b_dark.jpg", "c_warm.jpg"]


def _items(*paths) -> list:
    return [Item(Path(p)) for p in paths]


def test_same_file_names_from_two_photographers_do_not_collide(tmp_path):
    a, b, loose1, loose2 = tmp_path / "photogA", tmp_path / "photogB", tmp_path / "x", tmp_path / "y"
    items = _items(a / "DSC_0001.jpg", b / "DSC_0001.jpg", a / "day2" / "DSC_0001.jpg")
    plan = batch.plan_exports(items, [a, b], tmp_path / "out", "jpg")
    got = sorted(p.relative_to(tmp_path / "out").as_posix() for p in plan.values())
    assert got == ["photogA/DSC_0001.jpg", "photogA/day2/DSC_0001.jpg", "photogB/DSC_0001.jpg"]
    loose = batch.plan_exports(_items(loose1 / "IMG_1.jpg", loose2 / "IMG_1.jpg"), [], tmp_path / "out", "jpg")
    assert sorted(p.name for p in loose.values()) == ["IMG_1-2.jpg", "IMG_1.jpg"]


def test_exports_never_overwrite_originals(tmp_path, jpeg_folder):
    with pytest.raises(ValueError):
        batch.check_export_dir(jpeg_folder, [str(jpeg_folder)])
    with pytest.raises(ValueError):  # even when called directly: an output path is never an input
        batch.plan_exports(_items(jpeg_folder / "a_day.jpg"), [jpeg_folder], jpeg_folder, "jpg")
    before = (jpeg_folder / "a_day.jpg").read_bytes()
    assert cli.main(["grade", str(jpeg_folder), "--export", str(jpeg_folder)]) == 2
    assert (jpeg_folder / "a_day.jpg").read_bytes() == before


def _raw_item(folder: Path, name: str, when: datetime, kelvin: float) -> Item:
    a = Analysis(width=600, height=400, is_raw=True, wb_as_shot=[kelvin, 0.0], camera="Sony ILCE-7M4",
                 capture_time=when.isoformat(), scene="day")
    return Item(folder / name, Result(folder / name, a, Plan(DevelopSettings(), "base"), None))


def test_white_balance_smoothing_keeps_two_photographers_apart(tmp_path):
    t0 = datetime(2026, 2, 14, 18, 0)
    # same camera model, frames interleaved in time, very different light at each position
    items = [_raw_item(tmp_path / "A", f"A{i}.ARW", t0 + timedelta(seconds=20 * i), 3200.0) for i in range(3)]
    items += [_raw_item(tmp_path / "B", f"B{i}.ARW", t0 + timedelta(seconds=20 * i + 10), 6500.0) for i in range(3)]
    assert batch.smooth_white_balance(items) == 6
    for it in items:  # each sequence agrees with itself: no pull toward the other shooter's light
        k, _ = wb.apply_shift(*it.result.analysis.wb_as_shot, it.result.plan.settings.temp_shift, 0.0)
        assert abs(k - it.result.analysis.wb_as_shot[0]) < 1.0


def test_unknown_camera_white_balance_is_reported(tmp_path):
    raw = tmp_path / "NEWCAM_0001.CR3"
    raw.write_bytes(b"")
    a = Analysis(width=600, height=400, is_raw=True, wb_as_shot=None)
    res = Result(raw, a, Plan(DevelopSettings(temp_shift=-25.0), "base"), None)
    opts = {"write_xmp": True, "long_edge": None, "fmt": "jpg", "quality": 90, "exposure_bias": 0.0}
    outs, err, warnings = batch._output((raw, res, "signature", opts, None))
    assert err is None and outs
    assert warnings and "white balance" in warnings[0]
    assert 'crs:WhiteBalance="As Shot"' in raw.with_suffix(".xmp").read_text()


def test_export_workers_are_capped_by_memory(monkeypatch):
    def item(w, h):
        return SimpleNamespace(result=SimpleNamespace(analysis=SimpleNamespace(full_size=[w, h])))

    pages = {"SC_PAGE_SIZE": 4096, "SC_PHYS_PAGES": 8 * 2**30 // 4096}  # an 8 GB Mac
    monkeypatch.setattr(batch.os, "sysconf", lambda name: pages[name])
    big = [item(8256, 5504)] * 4  # 45 MP files
    assert batch._export_workers(big, 7, None) == 2
    assert batch._export_workers(big, 7, 2048) == 7  # small exports fit
    assert batch._export_workers([item(400, 300)], 1, None) == 1


def test_a_file_that_crashes_its_worker_does_not_stop_the_wedding():
    from crash_job import crash_on_two

    got = dict(batch._parallel(crash_on_two, {i: i for i in range(8)}, 2, lambda: "crashed"))
    assert got == {0: 0, 1: 10, 2: "crashed", 3: 30, 4: 40, 5: 50, 6: 60, 7: 70}
