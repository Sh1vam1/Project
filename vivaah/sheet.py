"""Contact sheets: before/after pairs and look comparisons."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .imageio import encode_8bit


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def to_image(rgb_display_linear: np.ndarray, tile: int) -> Image.Image:
    im = Image.fromarray(encode_8bit(rgb_display_linear))
    im.thumbnail((tile, tile), Image.LANCZOS)
    return im


def grid(tiles: list[tuple[Image.Image, str]], cols: int, tile: int, title: str | None = None,
         bg: tuple[int, int, int] = (18, 18, 18)) -> Image.Image:
    rows = (len(tiles) + cols - 1) // cols
    label_h = max(18, tile // 22)
    title_h = label_h * 2 if title else 0
    pad = max(6, tile // 60)
    W = cols * (tile + pad) + pad
    H = title_h + rows * (tile + label_h + pad) + pad
    sheet = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(sheet)
    font = _font(int(label_h * 0.7))
    if title:
        draw.text((pad, pad), title, fill=(235, 235, 235), font=_font(int(label_h * 1.1)))
    for i, (im, label) in enumerate(tiles):
        r, c = divmod(i, cols)
        x = pad + c * (tile + pad)
        y = title_h + pad + r * (tile + label_h + pad)
        sheet.paste(im, (x + (tile - im.width) // 2, y + (tile - im.height) // 2))
        draw.text((x, y + tile + 2), label, fill=(200, 200, 200), font=font)
    return sheet


def before_after(pairs: list[tuple[np.ndarray, np.ndarray, str]], out: str | Path, tile: int = 560,
                 title: str | None = None) -> Path:
    tiles = []
    for before, after, label in pairs:
        tiles.append((to_image(before, tile), f"{label} - before"))
        tiles.append((to_image(after, tile), f"{label} - after"))
    img = grid(tiles, cols=4 if len(pairs) > 1 else 2, tile=tile, title=title)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=92)
    return out
