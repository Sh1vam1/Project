"""Grade a whole wedding: parallel analysis, gallery consistency, outputs.

Pass 1 analyses every photo (small half-size decodes, all CPU cores).
Between the passes, white balance is smoothed across photos taken under the
same light, so a sequence doesn't flicker warm/cool from frame to frame the
way camera auto-WB does.  Pass 2 writes Lightroom sidecars and/or renders
finished JPEG/TIFF files.
"""
from __future__ import annotations

import csv
import json
import multiprocessing
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

from . import imageio, pipeline, sheet, xmp
from .looks import Look, get_look


# "spawn" everywhere: LibRaw uses OpenMP, which is not fork-safe (macOS already defaults to spawn)
_CTX = multiprocessing.get_context("spawn")


@dataclass
class BatchOptions:
    look: str = "signature"
    looks_dirs: list = field(default_factory=list)
    export_dir: str | None = None  # render finished files here
    write_xmp: bool = False  # write Lightroom sidecars next to raw files
    long_edge: int | None = None  # resize exports (None = full size)
    fmt: str = "jpg"
    quality: int = 95
    strength: float = 1.0
    auto: bool = True
    workers: int = 0  # 0 = all cores but one
    consistency: bool = True
    exposure_bias: float = 0.0  # added to Lightroom exposure (calibrate once if needed)
    sheet: bool = True


@dataclass
class Item:
    path: Path
    result: pipeline.Result | None = None
    error: str | None = None
    outputs: list = field(default_factory=list)


def _analyze(args):
    path, look, strength, use_auto = args
    try:
        return pipeline.analyze_path(path, look, strength, use_auto), None
    except Exception as exc:  # noqa: BLE001 - one bad file must not stop a wedding
        return None, f"{type(exc).__name__}: {exc}"


def _output(args):
    item_path, result, look_id, opts_dict, rel = args
    outs = []
    try:
        if opts_dict["write_xmp"] and imageio.is_raw(item_path) and item_path.suffix.lower() != ".dng":
            side = xmp.write_sidecar(item_path, result.plan.settings, result.analysis.wb_as_shot and
                                     tuple(result.analysis.wb_as_shot), look_id, result.plan.variant,
                                     opts_dict["exposure_bias"])
            outs.append(str(side))
        if opts_dict["export_dir"]:
            out = Path(opts_dict["export_dir"]) / rel.with_suffix("." + opts_dict["fmt"])
            photo = imageio.load(item_path, long_edge=opts_dict["long_edge"])
            enc = pipeline.render_photo(photo, result.plan.settings, result.skin_mask, encoded=True)
            imageio.save(enc, out, quality=opts_dict["quality"], exif=photo.exif, encoded=True)
            outs.append(str(out))
        return outs, None
    except Exception as exc:  # noqa: BLE001
        return outs, f"{type(exc).__name__}: {exc}"


def smooth_white_balance(items: list[Item], gap_minutes: float = 5.0, weight: float = 0.6) -> int:
    """Pull raw files shot under the same light toward a shared white balance."""
    from . import wb

    raws = [it for it in items if it.result and it.result.analysis.is_raw and it.result.analysis.wb_as_shot
            and it.result.analysis.capture_time]
    raws.sort(key=lambda it: it.result.analysis.capture_time)
    groups: list[list[Item]] = []
    last_t, last_key = None, None
    for it in raws:
        a = it.result.analysis
        t = datetime.fromisoformat(a.capture_time)
        key = (a.camera, a.scene)
        if last_t is None or (t - last_t).total_seconds() > gap_minutes * 60 or key != last_key:
            groups.append([])
        groups[-1].append(it)
        last_t, last_key = t, key
    changed = 0
    for g in groups:
        if len(g) < 3:
            continue
        targets = []
        for it in g:
            s = it.result.plan.settings
            base = it.result.analysis.wb_as_shot
            k, tint = wb.apply_shift(base[0], base[1], s.temp_shift, s.tint_shift)
            targets.append((1e6 / k, tint))
        med_m, med_t = np.median(np.array(targets), axis=0)
        for it, (m, tint) in zip(g, targets):
            s = it.result.plan.settings
            base = it.result.analysis.wb_as_shot
            new_m = m + (med_m - m) * weight
            new_t = tint + (med_t - tint) * weight
            s.temp_shift = 1e6 / base[0] - new_m
            s.tint_shift = new_t - base[1]
            it.result.plan.notes.append(f"white balance matched to its {len(g)}-photo sequence")
            changed += 1
    return changed


def run(inputs: list[str], opts: BatchOptions, log: Callable[[str], None] = print) -> list[Item]:
    look: Look = get_look(opts.look, opts.looks_dirs)
    paths = imageio.find_photos(inputs)
    if not paths:
        log("No supported photos found.")
        return []
    roots = [Path(i) for i in inputs if Path(i).is_dir()]
    workers = opts.workers or max(1, (os.cpu_count() or 2) - 1)
    log(f"{len(paths)} photos | look: {look.name} | {workers} workers")
    if opts.write_xmp:
        no_sidecar = [p for p in paths if not imageio.is_raw(p) or p.suffix.lower() == ".dng"]
        if no_sidecar:
            log(f"  note: {len(no_sidecar)} JPEG/TIFF/DNG file(s) keep their settings inside the file, so "
                "Lightroom ignores sidecars for them. Use --export to get finished files for those.")
    items = [Item(p) for p in paths]
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers, mp_context=_CTX) as pool:
        futs = {pool.submit(_analyze, (it.path, look, opts.strength, opts.auto)): it for it in items}
        for n, fut in enumerate(as_completed(futs), 1):
            it = futs[fut]
            it.result, it.error = fut.result()
            if n % 25 == 0 or n == len(items):
                log(f"  analysed {n}/{len(items)}  ({time.time() - t0:.0f}s)")
    if opts.consistency:
        n = smooth_white_balance(items)
        if n:
            log(f"  matched white balance across sequences for {n} raw photos")
    opts_dict = {k: getattr(opts, k) for k in ("write_xmp", "export_dir", "long_edge", "fmt", "quality", "exposure_bias")}
    todo = [it for it in items if it.result]
    if opts.write_xmp or opts.export_dir:
        t1 = time.time()
        with ProcessPoolExecutor(max_workers=workers, mp_context=_CTX) as pool:
            futs = {}
            for it in todo:
                root = next((r for r in roots if r in it.path.parents), it.path.parent)
                rel = it.path.relative_to(root)
                futs[pool.submit(_output, (it.path, it.result, look.id, opts_dict, rel))] = it
            for n, fut in enumerate(as_completed(futs), 1):
                it = futs[fut]
                it.outputs, err = fut.result()
                it.error = it.error or err
                if n % 25 == 0 or n == len(todo):
                    log(f"  wrote {n}/{len(todo)}  ({time.time() - t1:.0f}s)")
    report_dir = Path(opts.export_dir) if opts.export_dir else (roots[0] if roots else paths[0].parent)
    write_report(items, report_dir / "vivaah_report", look)
    if opts.sheet and opts.export_dir:
        _sample_sheet(items, report_dir / "vivaah_report" / "before_after.jpg", opts)
    failed = [it for it in items if it.error]
    log(f"Done in {time.time() - t0:.0f}s. {len(items) - len(failed)} ok, {len(failed)} failed."
        f" Report: {report_dir / 'vivaah_report'}")
    for it in failed[:20]:
        log(f"  ! {it.path.name}: {it.error}")
    return items


def write_report(items: list[Item], folder: Path, look: Look) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    full = []
    for it in items:
        r = it.result
        row = {"file": str(it.path), "error": it.error or ""}
        if r:
            a, s = r.analysis, r.plan.settings
            row.update(scene=a.scene, variant=r.plan.variant, faces=len(a.faces),
                       exposure=round(s.exposure, 2), temp_shift=round(s.temp_shift, 1),
                       tint_shift=round(s.tint_shift, 1), highlights=round(s.highlights),
                       whites=round(s.whites), blacks=round(s.blacks), notes=" | ".join(r.plan.notes))
            full.append({"file": str(it.path), "analysis": a.to_dict(), "notes": r.plan.notes,
                         "settings": s.to_dict(only_changed=True), "outputs": it.outputs})
        rows.append(row)
    keys = ["file", "scene", "variant", "faces", "exposure", "temp_shift", "tint_shift", "highlights",
            "whites", "blacks", "notes", "error"]
    with open(folder / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    (folder / "details.json").write_text(json.dumps({"look": look.id, "photos": full}, indent=1, default=str))


def _sample_sheet(items: list[Item], out: Path, opts: BatchOptions, n: int = 8) -> None:
    ok = [it for it in items if it.result and it.outputs]
    if not ok:
        return
    step = max(1, len(ok) // n)
    pairs = []
    for it in ok[::step][:n]:
        photo = imageio.load(it.path, long_edge=700)
        after = pipeline.render_photo(photo, it.result.plan.settings, it.result.skin_mask)
        before = pipeline.render_photo(photo, type(it.result.plan.settings)())
        pairs.append((before, after, it.path.name))
    sheet.before_after(pairs, out, tile=520, title=f"vivaah - {opts.look}")
