"""Command line interface: ``vivaah <command> ...`` (run ``vivaah -h``)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__


def _looks_dirs(args) -> list:
    return args.looks_dir or []


def cmd_looks(args) -> int:
    from .looks import all_looks

    for look in all_looks(_looks_dirs(args)).values():
        variants = f"  [auto variants: {', '.join(look.variants)}]" if look.variants else ""
        print(f"{look.id:18s} {look.name}{variants}")
        if args.verbose:
            print(f"{'':18s} {look.description}")
            if look.best_for:
                print(f"{'':18s} best for: {', '.join(look.best_for)}")
    return 0


def cmd_grade(args) -> int:
    from .batch import BatchOptions, run

    if not args.export and not args.xmp:
        print("Choose an output: --xmp (Lightroom sidecars next to raw files) and/or --export FOLDER.")
        return 2
    opts = BatchOptions(
        look=args.look, looks_dirs=_looks_dirs(args), export_dir=args.export, write_xmp=args.xmp,
        long_edge=args.size, fmt=args.format, quality=args.quality, strength=args.strength,
        auto=not args.no_auto, workers=args.workers, consistency=not args.no_consistency,
        exposure_bias=args.exposure_bias, sheet=not args.no_sheet,
    )
    items = run(args.inputs, opts)
    return 0 if items and not all(it.error for it in items) else 1


def cmd_preview(args) -> int:
    from . import imageio, pipeline, sheet
    from .looks import all_looks, get_look
    from .settings import DevelopSettings

    names = list(all_looks(_looks_dirs(args))) if args.looks == "all" else args.looks.split(",")
    photo = imageio.load(args.input, long_edge=args.size)
    tiles = [(sheet.to_image(pipeline.render_photo(photo, DevelopSettings()), args.size), "original")]
    for name in names:
        look = get_look(name.strip(), _looks_dirs(args))
        res = pipeline.analyze_path(args.input, look, args.strength)
        img = pipeline.render_photo(photo, res.plan.settings, res.skin_mask)
        label = look.id if res.plan.variant == "base" else f"{look.id} ({res.plan.variant})"
        tiles.append((sheet.to_image(img, args.size), label))
        if args.verbose:
            print(f"{look.id}: " + " | ".join(res.plan.notes))
    out = Path(args.out or Path(args.input).with_name(Path(args.input).stem + "_looks.jpg"))
    cols = min(4, len(tiles))
    sheet.grid(tiles, cols=cols, tile=args.size, title=Path(args.input).name).save(out, quality=92)
    print(f"Saved {out}")
    return 0


def cmd_presets(args) -> int:
    from .looks import all_looks
    from .xmp import preset_xmp

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for look in all_looks(_looks_dirs(args)).values():
        for variant in [None, *look.variants]:
            if variant and not look.variants[variant].get("settings"):
                continue
            name = look.id if variant is None else f"{look.id}-{variant}"
            (out / f"vivaah-{name}.xmp").write_text(preset_xmp(look, variant))
            n += 1
    print(f"Wrote {n} Lightroom presets to {out}")
    print("Lightroom Classic: Develop > Presets panel '+' > Import Presets... and pick these .xmp files.")
    return 0


def cmd_lut(args) -> int:
    from .looks import all_looks, get_look
    from .lut import write_cube

    looks = list(all_looks(_looks_dirs(args)).values()) if args.look == "all" else [get_look(args.look, _looks_dirs(args))]
    out = Path(args.out)
    for look in looks:
        for variant in [None, *[v for v in look.variants if look.variants[v].get("settings")]]:
            name = look.id if variant is None else f"{look.id}-{variant}"
            path = out / f"vivaah-{name}.cube" if (out.suffix != ".cube" or len(looks) > 1) else out
            write_cube(look, path, size=args.size, variant=variant)
            print(f"Wrote {path}")
            if out.suffix == ".cube" and len(looks) == 1:
                return 0
    return 0


def cmd_learn(args) -> int:
    from .learn import learn, save_learned

    look, fps = learn(args.references, args.name, base=args.base, look_id=args.id, looks_dirs=_looks_dirs(args))
    path = save_learned(look, args.out)
    print(f"Measured {len(fps)} reference photo(s).")
    print(f"Saved look '{look.id}' to {path}")
    print(f"Use it with: vivaah grade <folder> --look {look.id} --xmp")
    if args.verbose:
        print(json.dumps({"auto": look.auto, "settings": look.settings}, indent=1))
    return 0


def cmd_analyze(args) -> int:
    from . import pipeline
    from .looks import get_look

    look = get_look(args.look, _looks_dirs(args))
    res = pipeline.analyze_path(args.input, look, args.strength)
    print(json.dumps(res.analysis.to_dict(), indent=1, default=str))
    print("\nplan (" + res.plan.variant + "):")
    for n in res.plan.notes:
        print("  - " + n)
    print(json.dumps(res.plan.settings.to_dict(only_changed=True), indent=1))
    return 0


def cmd_restore(args) -> int:
    from . import imageio, xmp

    n = sum(xmp.restore_sidecar(p) for p in imageio.find_photos(args.inputs))
    print(f"Restored/removed {n} sidecar(s).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vivaah", description="Automated premium colour grading for wedding photos.")
    p.add_argument("--version", action="version", version=f"vivaah {__version__}")
    p.add_argument("--looks-dir", action="append", help="extra folder with look .json files")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("looks", help="list available looks")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(func=cmd_looks)

    s = sub.add_parser("grade", help="grade photos/folders (Lightroom sidecars and/or finished files)")
    s.add_argument("inputs", nargs="+", help="photos or folders (searched recursively)")
    s.add_argument("--look", default="signature")
    s.add_argument("--xmp", action="store_true", help="write Lightroom .xmp sidecars next to raw files")
    s.add_argument("--export", metavar="FOLDER", help="render finished files into FOLDER")
    s.add_argument("--size", type=int, help="long edge in pixels for exports (default: full size)")
    s.add_argument("--format", choices=("jpg", "tif"), default="jpg")
    s.add_argument("--quality", type=int, default=95)
    s.add_argument("--strength", type=float, default=1.0, help="look strength 0..1.5 (auto corrections stay)")
    s.add_argument("--no-auto", action="store_true", help="apply the look only, no per-photo corrections")
    s.add_argument("--no-consistency", action="store_true", help="don't match white balance across sequences")
    s.add_argument("--exposure-bias", type=float, default=0.0, help="EV added to Lightroom exposure")
    s.add_argument("--workers", type=int, default=0)
    s.add_argument("--no-sheet", action="store_true", help="skip the before/after contact sheet")
    s.set_defaults(func=cmd_grade)

    s = sub.add_parser("preview", help="one photo through several looks, side by side")
    s.add_argument("input")
    s.add_argument("--looks", default="all", help="comma separated look ids, or 'all'")
    s.add_argument("--out")
    s.add_argument("--size", type=int, default=700)
    s.add_argument("--strength", type=float, default=1.0)
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(func=cmd_preview)

    s = sub.add_parser("presets", help="export looks as Lightroom Develop presets (.xmp)")
    s.add_argument("--out", default="lightroom-presets")
    s.set_defaults(func=cmd_presets)

    s = sub.add_parser("lut", help="export looks as .cube LUTs for video (Premiere, Resolve, FCP)")
    s.add_argument("--look", default="all")
    s.add_argument("--out", default="luts")
    s.add_argument("--size", type=int, default=33)
    s.set_defaults(func=cmd_lut)

    s = sub.add_parser("learn", help="create a look from reference edits you love")
    s.add_argument("references", nargs="+", help="reference photos or a folder")
    s.add_argument("--name", required=True)
    s.add_argument("--id")
    s.add_argument("--base", default="signature", help="look to start from")
    s.add_argument("--out", help="folder to save the look (default ~/.vivaah/looks)")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(func=cmd_learn)

    s = sub.add_parser("analyze", help="show what vivaah sees in a photo and what it would do")
    s.add_argument("input")
    s.add_argument("--look", default="signature")
    s.add_argument("--strength", type=float, default=1.0)
    s.set_defaults(func=cmd_analyze)

    s = sub.add_parser("restore", help="undo: restore/remove the sidecars vivaah wrote")
    s.add_argument("inputs", nargs="+")
    s.set_defaults(func=cmd_restore)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyError as exc:
        print(f"error: {exc.args[0] if exc.args else exc}", file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
