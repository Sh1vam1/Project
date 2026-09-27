"""Looks: a creative grade plus the targets the per-photo automation aims for.

A look file (JSON) has:

``settings``   Lightroom-style slider values (only the ones that differ from 0)
``auto``       targets for the per-photo corrections (see ``AutoTargets``)
``variants``   optional overrides picked automatically by scene
               (``night``, ``warm_indoor``, ``shade``)

Built-in looks live in ``vivaah/looks``; your own looks (for example the
ones ``vivaah learn`` creates) go in ``~/.vivaah/looks`` or any folder you
pass with ``--looks-dir``.
"""
from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .settings import DevelopSettings

BUILTIN_DIR = Path(__file__).with_name("looks")
USER_DIR = Path(os.environ.get("VIVAAH_LOOKS", Path.home() / ".vivaah" / "looks"))


@dataclass
class AutoTargets:
    skin_L: float = 63.0  # CIE L* the faces are exposed to
    skin_zone: tuple = (-7.0, 9.0)  # faces within skin_L+zone only drift gently (keeps intent)
    scene_mid_L: float = 50.0  # median L* when there are no faces
    white_L: float = 95.0  # where the brightest 0.5% should sit
    black_L: float = 3.0  # where the darkest 0.5% should sit
    warmth: float = 0.0  # mireds added after neutral balance (+ warmer)
    tint: float = 0.0  # tint added after neutral balance (+ magenta)
    ambience_keep: float = 0.25  # share of warm light (tungsten, golden hour) to keep
    wb_strength: float = 1.0  # 0 = keep camera WB, 1 = full correction
    exposure_strength: float = 0.85
    foliage_hue: float | None = None  # OKLab hue greens should land on
    foliage_chroma: float | None = None
    sky_chroma: float | None = None
    skin_hue: float = 46.0  # OKLab hue for skin (45.5 = the 123° vectorscope skin-tone line)
    skin_chroma: tuple = (0.05, 0.10)

    @classmethod
    def from_dict(cls, d: dict | None) -> "AutoTargets":
        names = {f.name for f in fields(cls)}
        unknown = set(d or {}) - names
        if unknown:
            raise KeyError(f"unknown auto target(s): {', '.join(sorted(unknown))}")
        t = cls(**(d or {}))
        if isinstance(t.skin_chroma, list):
            t.skin_chroma = tuple(t.skin_chroma)
        if isinstance(t.skin_zone, list):
            t.skin_zone = tuple(t.skin_zone)
        return t


@dataclass
class Look:
    id: str
    name: str
    description: str = ""
    best_for: list = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    auto: dict = field(default_factory=dict)
    variants: dict = field(default_factory=dict)
    source: str = ""

    def resolve(self, scene: str | None = None) -> tuple[DevelopSettings, AutoTargets, str]:
        """Settings + targets for a scene; returns the variant name actually used."""
        settings = copy.deepcopy(self.settings)
        auto = copy.deepcopy(self.auto)
        used = "base"
        if scene and scene in self.variants:
            v = self.variants[scene]
            for k, val in v.get("settings", {}).items():
                if k in ("hue", "sat", "lum") and k in settings:
                    settings[k] = {**settings[k], **val}
                else:
                    settings[k] = val
            auto.update(v.get("auto", {}))
            used = scene
        return DevelopSettings.from_dict(settings), AutoTargets.from_dict(auto), used

    def to_json(self) -> str:
        d = asdict(self)
        d.pop("source", None)
        return _compact_json(d)

    def save(self, path: str | os.PathLike) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json() + "\n")
        return path


def _compact_json(d: dict) -> str:
    """Indented JSON that keeps numeric lists (curve points, ranges) on one line."""
    s = json.dumps(d, indent=2)
    s = re.sub(r"\[\s+([-\d.,\s]+?)\s+\]", lambda m: "[" + ", ".join(x.strip() for x in m.group(1).split(",")) + "]", s)
    return re.sub(r"\[\s+(\[[-\d., ]+\](?:,\s+\[[-\d., ]+\])*)\s+\]",
                  lambda m: "[" + re.sub(r",\s+\[", ", [", m.group(1)) + "]", s)


def load_look_file(path: str | os.PathLike) -> Look:
    path = Path(path)
    d = json.loads(path.read_text())
    look = Look(**{k: d[k] for k in ("id", "name", "description", "best_for", "settings", "auto", "variants") if k in d})
    look.source = str(path)
    look.resolve()  # validate settings / targets early
    for scene in look.variants:
        look.resolve(scene)
    return look


def all_looks(extra_dirs: list[str | os.PathLike] | None = None) -> dict[str, Look]:
    looks: dict[str, Look] = {}
    dirs = [BUILTIN_DIR, USER_DIR, *(Path(d) for d in (extra_dirs or []))]
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            look = load_look_file(f)
            looks[look.id] = look
    return looks


def get_look(name: str, extra_dirs: list[str | os.PathLike] | None = None) -> Look:
    p = Path(name)
    if p.suffix == ".json" and p.is_file():
        return load_look_file(p)
    looks = all_looks(extra_dirs)
    if name not in looks:
        raise KeyError(f"unknown look '{name}'. Available: {', '.join(sorted(looks))}")
    return looks[name]
