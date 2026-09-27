"""Round-trip check of a look against finished reference edits.

Each reference is turned back into a plausible "straight from camera" frame
(cooler/greener white balance, neon camera greens, stretched whites, exposure
off by a few tenths of a stop), graded automatically with the look, and
compared with the real reference (mean OKLab distance x 100, lower is better).

    python tools/roundtrip.py references/ --look signature --sheet roundtrip.jpg

Use it before/after changing a look or the automation to see whether the
change moves the results toward the references. References should be clean
exports, not screenshots with app borders.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vivaah import imageio, pipeline, sheet, wb  # noqa: E402
from vivaah.color import (lab_to_lch, lch_to_lab, linear_to_oklab, linear_to_srgb, oklab_to_linear,  # noqa: E402
                          srgb_to_linear)
from vivaah.looks import get_look  # noqa: E402


def degrade(rgb: np.ndarray, seed: int) -> np.ndarray:
    """Undo a finished grade into a camera-original-like frame (deterministic per seed)."""
    rng = np.random.default_rng(seed)
    L, C, h = lab_to_lch(linear_to_oklab(np.clip(rgb, 0, 1)))
    green = np.clip(1 - np.abs(h - 110) / 45, 0, 1)
    h = h + 22 * green
    C = C * (1 + 0.6 * green)
    blue = np.clip(1 - np.abs(((h - 250 + 180) % 360) - 180) / 50, 0, 1)
    C = C * (1 + 0.8 * blue)
    out = oklab_to_linear(lch_to_lab(L, C, h))
    enc = np.clip((linear_to_srgb(np.clip(out, 0, 1)) - 0.01) / 0.94, 0, 1)
    out = srgb_to_linear(enc)
    temp, tint = rng.uniform(-30, -8), rng.uniform(-8, 0)
    out = (out.reshape(-1, 3) @ wb.shift_matrix(temp, tint).T).reshape(out.shape)
    return np.clip(out * 2 ** rng.uniform(-0.5, 0.3), 0, 1)


def distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(linear_to_oklab(np.clip(a, 0, 1)) - linear_to_oklab(np.clip(b, 0, 1)),
                                axis=-1).mean() * 100)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("references", help="folder of finished reference edits")
    ap.add_argument("--look", default="signature", help="look id or path to a look .json")
    ap.add_argument("--size", type=int, default=700)
    ap.add_argument("--sheet", help="save a camera/graded/reference comparison sheet here")
    args = ap.parse_args()

    look = get_look(args.look)
    refs = imageio.find_photos([args.references])
    if not refs:
        print("no reference photos found")
        return 1
    tiles, rows = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for i, ref in enumerate(refs):
            target = imageio.load(ref, long_edge=args.size).rgb
            camera = degrade(target, seed=i)
            src = Path(tmp) / f"{ref.stem}_camera.jpg"
            imageio.save(camera, src)
            graded, res = pipeline.develop(src, look, long_edge=args.size)
            before, after = distance(camera, target), distance(graded, target)
            rows.append((before, after))
            print(f"{ref.name:32s} before {before:5.2f}  after {after:5.2f}  [{res.plan.variant}] "
                  + " | ".join(res.plan.notes))
            if args.sheet:
                tiles += [(sheet.to_image(camera, 360), f"{ref.stem}: camera (simulated)"),
                          (sheet.to_image(graded, 360), f"{look.id} ({res.plan.variant})"),
                          (sheet.to_image(target, 360), "reference")]
    mean = np.mean(rows, axis=0)
    print(f"\nmean distance to references: camera {mean[0]:.2f} -> graded {mean[1]:.2f}")
    if args.sheet:
        sheet.grid(tiles, cols=3, tile=360, title=f"{look.name}: round trip").save(args.sheet, quality=90)
        print(f"sheet: {args.sheet}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
