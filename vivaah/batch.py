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
from concurrent.futures.process import BrokenProcessPool
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
    warnings: list = field(default_factory=list)


def _analyze(args):
    path, look, strength, use_auto = args
    try:
        return pipeline.analyze_path(path, look, strength, use_auto), None
    except Exception as exc:  # noqa: BLE001 - one bad file must not stop a wedding
        return None, f"{type(exc).__name__}: {exc}"


def _output(args):
    item_path, result, look_id, opts_dict, out = args
    outs, warnings = [], []
    try:
        if opts_dict["write_xmp"] and imageio.is_raw(item_path) and item_path.suffix.lower() != ".dng":
            as_shot = tuple(result.analysis.wb_as_shot) if result.analysis.wb_as_shot else None
            side = xmp.write_sidecar(item_path, result.plan.settings, as_shot, look_id, result.plan.variant,
                                     opts_dict["exposure_bias"])
            outs.append(str(side))
            if as_shot is None and xmp.read_as_shot(side) is None:
                warnings.append("camera white balance unknown: Lightroom keeps the camera's white balance "
                                "for this photo (the exported file has vivaah's correction)")
        if out is not None:
            photo = imageio.load(item_path, long_edge=opts_dict["long_edge"])
            enc = pipeline.render_photo(photo, result.plan.settings, result.skin_mask, encoded=True)
            exif = photo.exif
            del photo  # free the full-size linear copy before encoding
            imageio.save(enc, out, quality=opts_dict["quality"], exif=exif, encoded=True)
            outs.append(str(out))
        return outs, None, warnings
    except Exception as exc:  # noqa: BLE001
        return outs, f"{type(exc).__name__}: {exc}", warnings


def _resolved(p: Path) -> str:
    return str(p.expanduser().resolve()).casefold()  # macOS volumes are usually case-insensitive


def check_export_dir(export_dir: str | Path, inputs: list[str]) -> Path:
    """Refuse export folders that would mix outputs with (or overwrite) the original photos."""
    exp = Path(export_dir).expanduser().resolve()
    for i in inputs:
        src = Path(i).expanduser().resolve()
        folder = src if src.is_dir() else src.parent
        if exp == folder:
            raise ValueError(f"the export folder is the same folder as the original photos ({folder}). "
                             "Choose a separate folder, e.g. a 'vivaah-graded' folder inside the wedding folder.")
    return exp


def plan_exports(items: list[Item], roots: list[Path], export_dir: Path, fmt: str) -> dict[Path, Path]:
    """One unique output path per photo; never an input file."""
    inputs = {_resolved(it.path) for it in items}
    multi = len(roots) > 1
    used: set[str] = set()
    plan: dict[Path, Path] = {}
    for it in items:
        root = next((r for r in roots if r == it.path.parent or r in it.path.parents), None)
        if root is not None:
            rel = it.path.relative_to(root)
            if multi:
                rel = Path(root.name) / rel
        else:
            rel = Path(it.path.name)
        out = export_dir / rel.with_suffix("." + fmt)
        n = 2
        while _resolved(out) in used:
            out = out.with_name(f"{rel.stem}-{n}.{fmt}")
            n += 1
        if _resolved(out) in inputs:
            raise ValueError(f"exporting {it.path.name} would overwrite an original photo ({out}).")
        used.add(_resolved(out))
        plan[it.path] = out
    return plan


def _export_workers(items: list[Item], workers: int, long_edge: int | None) -> int:
    """Cap parallel full-size renders by RAM (a 24 MP render peaks around 1 GB per worker)."""
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return workers
    biggest = 0
    for it in items:
        if it.result:
            w, h = it.result.analysis.full_size or (6000, 4000)
            px = w * h
            if long_edge and max(w, h) > long_edge:
                px = int(px * (long_edge / max(w, h)) ** 2)
            biggest = max(biggest, px)
    per_worker = biggest * 48 + 300 * 2**20
    return max(1, min(workers, int(total * 0.6 // max(per_worker, 1))))


def smooth_white_balance(items: list[Item], gap_minutes: float = 5.0, weight: float = 0.6) -> int:
    """Pull raw files shot under the same light toward a shared white balance."""
    from . import wb

    raws = [it for it in items if it.result and it.result.analysis.is_raw and it.result.analysis.wb_as_shot
            and it.result.analysis.capture_time]
    # One sequence = one camera in one photographer's folder, in time order; a gap or a change
    # of light (scene type) starts a new one. Sorting by shooter first keeps two photographers
    # with the same camera model from being mixed when their frames interleave in time.
    raws.sort(key=lambda it: (it.result.analysis.camera, str(it.path.parent), it.result.analysis.capture_time))
    groups: list[list[Item]] = []
    last_t, last_key = None, None
    for it in raws:
        a = it.result.analysis
        t = datetime.fromisoformat(a.capture_time)
        key = (a.camera, a.scene, it.path.parent)
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


CRASHED = ("the decoder crashed on this file (corrupt or unsupported raw?) "
           "or the computer ran out of memory")


def _isolated(fn, args, crashed: Callable[[], object]):
    """Run one job in its own process; ``crashed()`` if that process dies too."""
    try:
        with ProcessPoolExecutor(max_workers=1, mp_context=_CTX) as pool:
            return pool.submit(fn, args).result()
    except BrokenProcessPool:
        return crashed()


def _parallel(fn, jobs: dict, workers: int, crashed: Callable[[], object]):
    """Yield (key, result) as jobs finish; one worker runs in-process (no subprocesses).

    A worker process can die outright (LibRaw crashing on a damaged file, or the
    system running out of memory), which breaks the whole pool. The pool hands out
    jobs in order, so the jobs that were running are the first few still pending:
    those are retried one at a time in their own process (``crashed()`` is their
    result if they kill it again) and the rest continue in a fresh pool.
    """
    if workers <= 1 or len(jobs) <= 1:
        for key, args in jobs.items():
            yield key, fn(args)
        return
    pending = dict(jobs)  # insertion order = submission order
    while pending:
        try:
            with ProcessPoolExecutor(max_workers=workers, mp_context=_CTX) as pool:
                futs = {pool.submit(fn, args): key for key, args in pending.items()}
                for fut in as_completed(futs):
                    res = fut.result()
                    key = futs[fut]
                    del pending[key]
                    yield key, res
        except BrokenProcessPool:
            for key in list(pending)[:workers + 1]:
                yield key, _isolated(fn, pending.pop(key), crashed)


def run(inputs: list[str], opts: BatchOptions, log: Callable[[str], None] = print) -> list[Item]:
    look: Look = get_look(opts.look, opts.looks_dirs)
    export_dir = check_export_dir(opts.export_dir, inputs) if opts.export_dir else None
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
    jobs = {i: (it.path, look, opts.strength, opts.auto) for i, it in enumerate(items)}
    for n, (i, (result, error)) in enumerate(_parallel(_analyze, jobs, workers, lambda: (None, CRASHED)), 1):
        items[i].result, items[i].error = result, error
        if n % 25 == 0 or n == len(items):
            log(f"  analysed {n}/{len(items)}  ({time.time() - t0:.0f}s)")
    if opts.consistency:
        n = smooth_white_balance(items)
        if n:
            log(f"  matched white balance across sequences for {n} raw photos")
    opts_dict = {k: getattr(opts, k) for k in ("write_xmp", "long_edge", "fmt", "quality", "exposure_bias")}
    todo = [it for it in items if it.result]
    exports = plan_exports(todo, roots, export_dir, opts.fmt) if export_dir else {}
    if export_dir:
        imageio.mark_output_dir(export_dir)
    if opts.write_xmp or export_dir:
        t1 = time.time()
        out_workers = _export_workers(todo, workers, opts.long_edge) if export_dir else workers
        if out_workers < workers:
            log(f"  using {out_workers} workers for full-size renders (memory)")
        jobs = {i: (it.path, it.result, look.id, opts_dict, exports.get(it.path)) for i, it in enumerate(todo)}
        results = _parallel(_output, jobs, out_workers, lambda: ([], CRASHED, []))
        for n, (i, (outs, err, warnings)) in enumerate(results, 1):
            it = todo[i]
            it.outputs, it.warnings = outs, warnings
            it.error = it.error or err
            if n % 25 == 0 or n == len(todo):
                log(f"  wrote {n}/{len(todo)}  ({time.time() - t1:.0f}s)")
    report_dir = export_dir if export_dir else (roots[0] if roots else paths[0].parent)
    write_report(items, imageio.mark_output_dir(report_dir / "vivaah_report"), look)
    if opts.sheet and opts.export_dir:
        _sample_sheet(items, report_dir / "vivaah_report" / "before_after.jpg", opts)
    failed = [it for it in items if it.error]
    log(f"Done in {time.time() - t0:.0f}s. {len(items) - len(failed)} ok, {len(failed)} failed."
        f" Report: {report_dir / 'vivaah_report'}")
    for it in failed[:20]:
        log(f"  ! {it.path.name}: {it.error}")
    warned = [it for it in items if it.warnings]
    for it in warned[:20]:
        log(f"  note {it.path.name}: {'; '.join(it.warnings)}")
    return items


def write_report(items: list[Item], folder: Path, look: Look) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    full = []
    for it in items:
        r = it.result
        row = {"file": str(it.path), "error": it.error or "", "warnings": "; ".join(it.warnings)}
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
            "whites", "blacks", "notes", "warnings", "error"]
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
