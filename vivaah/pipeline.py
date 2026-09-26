"""High-level helpers: analyse, plan and render single photos."""
from __future__ import annotations

import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import analysis as analysis_mod
from . import auto, imageio
from .engine import RenderContext, render
from .looks import Look
from .settings import DevelopSettings


@dataclass
class Result:
    path: Path
    analysis: analysis_mod.Analysis
    plan: auto.Plan
    skin_mask: np.ndarray


def seed_for(path: Path) -> int:
    return zlib.crc32(path.name.encode()) & 0xFFFFFFFF


def analyze_path(path: str | Path, look: Look, strength: float = 1.0, use_auto: bool = True) -> Result:
    photo = imageio.load(path, long_edge=analysis_mod.ANALYSIS_EDGE, fast=True)
    a, mask = analysis_mod.analyze(photo)
    return Result(Path(path), a, auto.plan(a, look, strength, auto=use_auto), mask)


def render_photo(photo: imageio.Photo, settings: DevelopSettings, skin_mask: np.ndarray | None = None,
                 encoded: bool = False) -> np.ndarray:
    ctx = RenderContext(
        is_raw=photo.is_raw,
        wb_base=photo.wb_as_shot if photo.is_raw else None,
        skin_mask=None if skin_mask is None else skin_mask.astype(np.float32) / 255.0,
        seed=seed_for(photo.path),
    )
    return render(photo.rgb, settings, ctx, encoded=encoded)


def develop(path: str | Path, look: Look, out_path: str | Path | None = None, long_edge: int | None = None,
            strength: float = 1.0, use_auto: bool = True, quality: int = 95,
            result: Result | None = None) -> tuple[np.ndarray, Result]:
    """Analyse + plan + render one photo; optionally save it. Returns (image, result)."""
    result = result or analyze_path(path, look, strength, use_auto)
    photo = imageio.load(path, long_edge=long_edge)
    enc = render_photo(photo, result.plan.settings, result.skin_mask, encoded=True)
    if out_path:
        imageio.save(enc, out_path, quality=quality, exif=photo.exif, encoded=True)
    return imageio.srgb_to_linear(enc), result
