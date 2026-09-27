"""Quality assurance: check vivaah against industry references on your own photos.

``vivaah qa <folder>`` grades every photo and checks the result the way a
colourist or a print lab would, then writes an HTML report with before/after
thumbnails and pass / warn / fail for each check.

Engine checks (no photos needed):

* colour accuracy on the X-Rite ColorChecker Classic 24 in CIEDE2000
  (ISO/CIE 11664-6): neutral settings must not change colours
  (ΔE00 < 0.5), the ``true-colour`` look must stay faithful (mean ΔE00 < 3)
* tone response of every look: monotonic grey ramp (no tone inversions)
* skin patches of every look stay on the Rec.709 vectorscope skin-tone
  line (about 123°)
* numeric safety (no NaN / out-of-range values), 3D-LUT accuracy against
  direct rendering, strip rendering identical to whole-image rendering
* Lightroom files: every value in presets and sidecars inside Adobe's slider
  ranges, well-formed tone curves; ``.cube`` files well-formed

Photo checks (per photo, on the finished file):

* file: sRGB ICC profile embedded, EXIF kept, size as requested
* exposure: highlight clipping and crushed blacks (waveform limits)
* skin: vectorscope angle against the skin-tone line, skin luma in IRE
  (waveform), how much the grade moved the skin (ΔE00)
* whites: tint of bright neutral surfaces (L*a*b*)
* banding: gaps in the 8-bit luminance histogram (posterisation)
* consistency across the set (skin angle spread) and speed per photo
"""
from __future__ import annotations

import html
import json
import math
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms

from . import engine, imageio, lut, pipeline, xmp
from .color import delta_e2000, linear_to_lab, linear_to_srgb, srgb_to_linear, ycbcr709
from .engine import RenderContext, render
from .looks import all_looks, get_look
from .settings import DevelopSettings

SKIN_LINE = 123.0  # degrees on a Rec.709 vectorscope (Cb right, Cr up)

# X-Rite ColorChecker Classic 24, sRGB (D65) 8-bit reference values
COLORCHECKER = [
    ("dark skin", (115, 82, 68)), ("light skin", (194, 150, 130)), ("blue sky", (98, 122, 157)),
    ("foliage", (87, 108, 67)), ("blue flower", (133, 128, 177)), ("bluish green", (103, 189, 170)),
    ("orange", (214, 126, 44)), ("purplish blue", (80, 91, 166)), ("moderate red", (193, 90, 99)),
    ("purple", (94, 60, 108)), ("yellow green", (157, 188, 64)), ("orange yellow", (224, 163, 46)),
    ("blue", (56, 61, 150)), ("green", (70, 148, 73)), ("red", (175, 54, 60)),
    ("yellow", (231, 199, 31)), ("magenta", (187, 86, 149)), ("cyan", (8, 133, 161)),
    ("white 9.5", (243, 243, 242)), ("neutral 8", (200, 200, 200)), ("neutral 6.5", (160, 160, 160)),
    ("neutral 5", (122, 122, 121)), ("neutral 3.5", (85, 85, 85)), ("black 2", (52, 52, 52)),
]

# Adobe Camera Raw / Lightroom slider ranges
_RANGES = {
    "Exposure2012": (-5, 5), "Temperature": (2000, 50000), "Tint": (-150, 150),
    "Sharpness": (0, 150), "SharpenRadius": (0.5, 3.0),
}
for _k in ("Contrast2012", "Highlights2012", "Shadows2012", "Whites2012", "Blacks2012", "Texture",
           "Clarity2012", "Dehaze", "Vibrance", "Saturation", "ParametricShadows", "ParametricDarks",
           "ParametricLights", "ParametricHighlights", "ColorGradeShadowLum", "ColorGradeMidtoneLum",
           "ColorGradeHighlightLum", "ColorGradeGlobalLum", "SplitToningBalance", "ShadowTint", "RedHue",
           "RedSaturation", "GreenHue", "GreenSaturation", "BlueHue", "BlueSaturation",
           "PostCropVignetteAmount", "PostCropVignetteRoundness"):
    _RANGES[_k] = (-100, 100)
for _k in ("SplitToningShadowSaturation", "SplitToningHighlightSaturation", "ColorGradeMidtoneSat",
           "ColorGradeGlobalSat", "ColorGradeBlending", "PostCropVignetteMidpoint", "PostCropVignetteFeather",
           "PostCropVignetteHighlightContrast", "GrainAmount", "GrainSize", "GrainFrequency", "SharpenDetail",
           "SharpenEdgeMasking", "ParametricShadowSplit", "ParametricMidtoneSplit", "ParametricHighlightSplit"):
    _RANGES[_k] = (0, 100)
for _k in ("SplitToningShadowHue", "SplitToningHighlightHue", "ColorGradeMidtoneHue", "ColorGradeGlobalHue"):
    _RANGES[_k] = (0, 359)
for _grp in ("Hue", "Saturation", "Luminance"):
    for _b in ("Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta"):
        _RANGES[f"{_grp}Adjustment{_b}"] = (-100, 100)


@dataclass
class Check:
    area: str
    name: str
    status: str  # pass / warn / fail / info
    value: str
    target: str
    photo: str = ""


def _grade(value: float, ok: float, warn: float, higher_is_worse: bool = True) -> str:
    v = value if higher_is_worse else -value
    ok, warn = (ok, warn) if higher_is_worse else (-ok, -warn)
    return "pass" if v <= ok else ("warn" if v <= warn else "fail")


# ------------------------------------------------------------------ engine


def _chart(size: int = 48) -> np.ndarray:
    """ColorChecker as a 4x6 linear image, one flat patch per swatch."""
    img = np.zeros((4 * size, 6 * size, 3), np.float32)
    for i, (_, rgb) in enumerate(COLORCHECKER):
        r, c = divmod(i, 6)
        img[r * size:(r + 1) * size, c * size:(c + 1) * size] = np.array(rgb, np.float32) / 255.0
    return srgb_to_linear(img)


def _patch_labs(img: np.ndarray, size: int = 48) -> np.ndarray:
    lab = linear_to_lab(np.clip(img, 0, 1))
    out = []
    for i in range(24):
        r, c = divmod(i, 6)
        m = size // 4
        out.append(np.median(lab[r * size + m:(r + 1) * size - m, c * size + m:(c + 1) * size - m].reshape(-1, 3), axis=0))
    return np.array(out)


def _skin_angle(enc: np.ndarray) -> float:
    _, cb, cr = np.moveaxis(ycbcr709(enc), -1, 0)
    return float(np.degrees(np.arctan2(np.median(cr), np.median(cb))))


def engine_checks(tmp: Path) -> list[Check]:
    checks: list[Check] = []
    chart = _chart()
    ref = _patch_labs(chart)

    out = render(chart, DevelopSettings())
    de = delta_e2000(ref, _patch_labs(out))
    checks.append(Check("engine", "neutral settings leave colours unchanged (ColorChecker ΔE00 max)",
                        _grade(de.max(), 0.5, 1.0), f"{de.max():.2f}", "< 0.5"))

    look = get_look("true-colour")
    s, _, _ = look.resolve()
    de = delta_e2000(ref, _patch_labs(render(chart, s, RenderContext())))
    worst = COLORCHECKER[int(np.argmax(de))][0]
    checks.append(Check("engine", "true-colour look stays faithful (ColorChecker ΔE00 mean)",
                        _grade(de.mean(), 3.0, 6.0), f"{de.mean():.2f} (worst {worst} {de.max():.1f})", "mean < 3"))

    ramp = srgb_to_linear(np.repeat(np.linspace(0.02, 0.98, 48, dtype=np.float32)[None, :, None], 3, axis=2)
                          .repeat(8, axis=0))
    skin = srgb_to_linear(np.array([[COLORCHECKER[0][1], COLORCHECKER[1][1]]], np.float32).repeat(16, 0)
                          .repeat(16, 1) / 255.0)
    for lk in all_looks().values():
        for variant in [None, *lk.variants]:
            s, _, used = lk.resolve(variant)
            if variant and used == "base":
                continue
            name = lk.id if variant is None else f"{lk.id} ({variant})"
            L = linear_to_lab(render(ramp, s, RenderContext(), finishing=False))[4, :, 0]
            inversions = int((np.diff(L) < -0.05).sum())
            checks.append(Check("engine", f"{name}: grey ramp is monotonic (no tone inversions)",
                                "pass" if inversions == 0 else "fail", f"{inversions} inversions", "0"))
            enc = linear_to_srgb(render(skin, s, RenderContext(skin_mask=np.ones((4, 4), np.float32)),
                                        finishing=False))
            angles = [_skin_angle(enc[:, :16]), _skin_angle(enc[:, 16:])]
            dev = max(abs(a - SKIN_LINE) for a in angles)
            checks.append(Check("engine", f"{name}: ColorChecker skin stays on the skin-tone line",
                                _grade(dev, 8.0, 15.0), f"{angles[0]:.0f}° / {angles[1]:.0f}°",
                                f"{SKIN_LINE:.0f}° ± 8"))

    rng = np.random.default_rng(7)
    wild = rng.random((64, 96, 3)).astype(np.float32) ** 2.2
    extreme = DevelopSettings(exposure=3, contrast=100, highlights=-100, shadows=100, whites=100, blacks=-100,
                              clarity=100, texture=100, dehaze=100, vibrance=100, saturation=100,
                              vignette_amount=-100, grain_amount=100, sharpen_amount=150)
    for b in extreme.hue:
        extreme.hue[b], extreme.sat[b], extreme.lum[b] = 100, 100, -100
    bad = 0
    for s in (extreme, extreme.scaled(-1.0)):
        o = render(wild, s.clamp(), RenderContext(seed=1))
        bad += int((~np.isfinite(o)).sum() + (o < 0).sum() + (o > 1).sum())
    checks.append(Check("engine", "numeric safety at extreme slider values (NaN / out of range)",
                        "pass" if bad == 0 else "fail", f"{bad} bad values", "0"))

    s, _, _ = get_look("signature").resolve()
    disp = np.clip(wild, 0, 1)
    direct = linear_to_srgb(engine.compress_gamut(engine.global_color(disp, s)))
    via = engine.apply_lut(linear_to_srgb(disp), engine.build_lut(s))
    err = float(np.abs(direct - via).mean() * 255)
    checks.append(Check("engine", "3D LUT fast path matches direct rendering (mean error, 8-bit levels)",
                        _grade(err, 0.5, 1.5), f"{err:.2f}", "< 0.5"))

    s2 = DevelopSettings(contrast=20, highlights=-40, shadows=30, clarity=15, texture=-10, dehaze=10,
                         vignette_amount=-20, sharpen_amount=40)
    whole = render(wild.repeat(4, 0).repeat(4, 1), s2, RenderContext(seed=3))
    old = engine.STRIP_PIXELS
    engine.STRIP_PIXELS = 10_000
    try:
        strips = render(wild.repeat(4, 0).repeat(4, 1), s2, RenderContext(seed=3))
    finally:
        engine.STRIP_PIXELS = old
    diff = float(np.abs(whole - strips).max() * 255)
    checks.append(Check("engine", "large-image strip rendering equals whole-image rendering (max diff, levels)",
                        _grade(diff, 0.05, 0.5), f"{diff:.3f}", "< 0.05"))

    checks += lightroom_checks(tmp)
    return checks


def _check_xmp_text(text: str) -> list[str]:
    problems = []
    root = ET.fromstring(text)
    crs = "{http://ns.adobe.com/camera-raw-settings/1.0/}"
    for desc in root.iter("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description"):
        for key, val in desc.attrib.items():
            if not key.startswith(crs):
                continue
            name = key[len(crs):]
            if name in _RANGES:
                try:
                    v = float(val)
                except ValueError:
                    problems.append(f"{name}={val!r} is not a number")
                    continue
                lo, hi = _RANGES[name]
                if not lo <= v <= hi:
                    problems.append(f"{name}={val} outside {lo}..{hi}")
        for curve in desc:
            if curve.tag.startswith(crs + "ToneCurvePV2012"):
                pts = [tuple(int(x) for x in li.text.split(",")) for li in curve.iter("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}li")]
                xs = [p[0] for p in pts]
                if len(pts) < 2 or xs[0] != 0 or xs[-1] != 255 or xs != sorted(set(xs)) or \
                        any(not 0 <= p[1] <= 255 for p in pts):
                    problems.append(f"bad {curve.tag[len(crs):]}: {pts}")
    return problems


def lightroom_checks(tmp: Path) -> list[Check]:
    checks = []
    problems = []
    n = 0
    for lk in all_looks().values():
        for variant in [None, *lk.variants]:
            problems += [f"{lk.id}/{variant or 'base'}: {p}" for p in _check_xmp_text(xmp.preset_xmp(lk, variant))]
            n += 1
    checks.append(Check("lightroom", f"all {n} presets: values inside Adobe slider ranges, valid curves",
                        "pass" if not problems else "fail", "; ".join(problems[:5]) or "ok", "no problems"))
    raw = tmp / "SAMPLE_0001.CR3"
    raw.write_bytes(b"")
    extreme = DevelopSettings(exposure=4.9, temp_shift=240, tint_shift=140)
    side = xmp.write_sidecar(raw, extreme.clamp(), as_shot=(3000.0, 20.0))
    problems = _check_xmp_text(side.read_text())
    checks.append(Check("lightroom", "sidecar with extreme corrections stays inside Adobe ranges",
                        "pass" if not problems else "fail", "; ".join(problems) or "ok", "no problems"))
    cube = lut.read_cube(lut.write_cube(get_look("signature"), tmp / "signature.cube", size=17))
    ok = cube.shape == (17, 17, 17, 3) and cube.min() >= 0 and cube.max() <= 1
    checks.append(Check("lightroom", ".cube LUT well-formed (size, 0..1 range)", "pass" if ok else "fail",
                        f"{cube.shape}, {cube.min():.3f}..{cube.max():.3f}", "33/17 grid, 0..1"))
    return checks


# ------------------------------------------------------------------ photos


def _face_pixels(enc: np.ndarray) -> np.ndarray | None:
    """Cheek/forehead pixels of the faces in an sRGB-encoded image (no lips, eyes, hair)."""
    import cv2

    from . import faces as facemod

    h, w = enc.shape[:2]
    f = min(1.0, 1024 / max(h, w))
    small = cv2.resize(enc, (max(1, round(w * f)), max(1, round(h * f))), interpolation=cv2.INTER_AREA) if f < 1 else enc
    enc8 = (np.clip(small, 0, 1) * 255 + 0.5).astype(np.uint8)
    found = facemod.detect(enc8)
    if not found:
        return None
    mask = np.zeros(enc8.shape[:2], bool)
    for face in found:
        mask |= facemod.face_skin_mask(mask.shape, face)
    px = small[mask]
    Y, cb, cr = np.moveaxis(ycbcr709(px), -1, 0)
    ang = np.degrees(np.arctan2(cr, cb))
    keep = (ang > 80) & (ang < 170) & (Y > 0.08) & (Y < 0.97) & (np.hypot(cb, cr) > 0.01)
    return px[keep] if keep.sum() >= 30 else None


def photo_checks(path: Path, look, size: int, out_dir: Path) -> tuple[dict, list[Check]]:
    name = path.name
    checks: list[Check] = []
    t0 = time.time()
    res = pipeline.analyze_path(path, look)
    t1 = time.time()
    photo = imageio.load(path, long_edge=size)
    enc = pipeline.render_photo(photo, res.plan.settings, res.skin_mask, encoded=True)
    before = pipeline.render_photo(photo, DevelopSettings(), encoded=True)
    t2 = time.time()
    out = out_dir / "graded" / (path.stem + ".jpg")
    imageio.save(enc, out, exif=photo.exif, encoded=True)
    a = res.analysis
    m = {"photo": name, "scene": a.scene, "variant": res.plan.variant, "faces": len(a.faces),
         "analyse_s": round(t1 - t0, 2), "render_s": round(t2 - t1, 2),
         "exposure": round(res.plan.settings.exposure, 2), "temp_shift": round(res.plan.settings.temp_shift, 1),
         "tint_shift": round(res.plan.settings.tint_shift, 1), "notes": res.plan.notes}

    with Image.open(out) as im:
        icc = im.info.get("icc_profile")
        desc = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(__import__("io").BytesIO(icc))) if icc else ""
        has_exif = bool(im.info.get("exif"))
        arr = np.asarray(im.convert("RGB"))
        dims = im.size
    checks.append(Check("file", "sRGB ICC profile embedded", "pass" if "srgb" in desc.lower() else "fail",
                        desc or "none", "sRGB", name))
    if photo.exif:
        checks.append(Check("file", "camera EXIF kept", "pass" if has_exif else "warn",
                            "yes" if has_exif else "no", "kept", name))
    want = max(photo.rgb.shape[:2])
    checks.append(Check("file", "output size", "pass" if max(dims) == want else "fail",
                        f"{dims[0]}x{dims[1]}", f"long edge {want}", name))

    e8 = arr.astype(np.float32) / 255.0
    clip = float((arr.max(axis=2) >= 254).mean() * 100)
    crush = float((arr.max(axis=2) <= 2).mean() * 100)
    night = a.scene == "night"
    checks.append(Check("exposure", "highlight clipping (pixels at 254-255)", _grade(clip, 0.5, 2.0),
                        f"{clip:.2f}%", "< 0.5%", name))
    checks.append(Check("exposure", "crushed blacks (pixels at 0-2)",
                        _grade(crush, 5.0 if night else 1.0, 15.0 if night else 5.0), f"{crush:.2f}%",
                        "< 5% (night)" if night else "< 1%", name))
    m.update(clip_pct=round(clip, 3), crush_pct=round(crush, 3))

    Y8 = np.round(ycbcr709(e8)[..., 0] * 255).astype(int)
    lo, hi = np.percentile(Y8, [1, 99]).astype(int)
    hist = np.bincount(Y8.ravel(), minlength=256)[lo:hi + 1]
    gaps = int((hist == 0).sum())
    checks.append(Check("quality", "banding: empty levels in the luminance histogram", _grade(gaps, 2, 8),
                        str(gaps), "≤ 2", name))

    px = _face_pixels(e8)
    if px is not None:
        Yf, cb, cr = np.median(ycbcr709(px), axis=0)
        ang = float(np.degrees(np.arctan2(cr, cb)))
        ire = float(Yf * 100)
        dev = abs(ang - SKIN_LINE)
        bpx0 = _face_pixels(before)
        was = f" (was {_skin_angle(bpx0):.0f}°)" if bpx0 is not None else ""
        checks.append(Check("skin", "skin on the vectorscope skin-tone line", _grade(dev, 8.0, 15.0),
                            f"{ang:.0f}°{was}", f"{SKIN_LINE:.0f}° ± 8", name))
        lo_ire = 20 if night else 35
        checks.append(Check("skin", "skin luma on the waveform (IRE)",
                            "pass" if lo_ire <= ire <= 80 else "warn", f"{ire:.0f}", f"{lo_ire}-80", name))
        bpx = _face_pixels(before)
        if bpx is not None:
            lab_a = linear_to_lab(srgb_to_linear(np.median(px, axis=0)[None]))[0]
            lab_b = linear_to_lab(srgb_to_linear(np.median(bpx, axis=0)[None]))[0]
            moved = float(delta_e2000(lab_a, lab_b))
            checks.append(Check("skin", "how much the grade changed skin colour (ΔE00)", "info",
                                f"{moved:.1f}", "look dependent", name))
            m["skin_moved_de"] = round(moved, 2)
        m.update(skin_angle=round(ang, 1), skin_ire=round(ire, 1))

    lab = linear_to_lab(srgb_to_linear(e8))
    C = np.hypot(lab[..., 1], lab[..., 2])
    white = (lab[..., 0] > 80) & (C < 14) & (arr.max(axis=2) < 254)
    if white.mean() > 0.005:
        wa, wb_ = np.median(lab[white][:, 1]), np.median(lab[white][:, 2])
        status = "pass" if abs(wa) <= 4 and -4 <= wb_ <= 12 else "warn"
        checks.append(Check("white balance", "bright neutrals: tint (a*) and warmth (b*)", status,
                            f"a* {wa:+.1f}, b* {wb_:+.1f}", "|a*| ≤ 4, b* -4..+12 (ivory ok)", name))
        m.update(white_a=round(float(wa), 2), white_b=round(float(wb_), 2))

    thumbs = out_dir / "thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)
    for tag, img in (("before", before), ("after", enc)):
        t = Image.fromarray(imageio.dither_8bit(img))
        t.thumbnail((520, 520))
        t.save(thumbs / f"{path.stem}_{tag}.jpg", quality=85)
    return m, checks


def consistency_checks(metrics: list[dict]) -> list[Check]:
    checks = []
    angles = [m["skin_angle"] for m in metrics if "skin_angle" in m and m["scene"] != "night"]
    if len(angles) >= 3:
        sd = float(np.std(angles))
        checks.append(Check("consistency", f"skin angle spread across {len(angles)} day photos (std)",
                            _grade(sd, 4.0, 8.0), f"{sd:.1f}°", "< 4°"))
    whites = [m["white_b"] for m in metrics if "white_b" in m]
    if len(whites) >= 3:
        sd = float(np.std(whites))
        checks.append(Check("consistency", f"white warmth spread across {len(whites)} photos (b* std)",
                            _grade(sd, 3.0, 6.0), f"{sd:.1f}", "< 3"))
    if metrics:
        a = np.mean([m["analyse_s"] for m in metrics])
        r = np.mean([m["render_s"] for m in metrics])
        checks.append(Check("speed", "seconds per photo: analysis (Lightroom mode) / full render", "info",
                            f"{a:.1f} s / {r:.1f} s", "single process"))
    return checks


# ------------------------------------------------------------------ report


def write_report(out_dir: Path, look_id: str, checks: list[Check], metrics: list[dict]) -> Path:
    counts = {s: sum(c.status == s for c in checks) for s in ("pass", "warn", "fail", "info")}
    (out_dir / "report.json").write_text(json.dumps(
        {"look": look_id, "counts": counts, "checks": [asdict(c) for c in checks], "photos": metrics},
        indent=1, default=str))
    colour = {"pass": "#2e7d32", "warn": "#b26a00", "fail": "#c62828", "info": "#546e7a"}
    rows = "".join(
        f"<tr><td>{html.escape(c.area)}</td><td>{html.escape(c.photo)}</td><td>{html.escape(c.name)}</td>"
        f"<td><b style='color:{colour[c.status]}'>{c.status.upper()}</b></td><td>{html.escape(c.value)}</td>"
        f"<td>{html.escape(c.target)}</td></tr>" for c in sorted(checks, key=lambda c: ("fail", "warn", "info", "pass").index(c.status)))
    cards = "".join(
        f"<figure><div><img src='thumbs/{Path(m['photo']).stem}_before.jpg'><img src='thumbs/{Path(m['photo']).stem}_after.jpg'></div>"
        f"<figcaption><b>{html.escape(m['photo'])}</b> · {m['scene']} → {m['variant']} · faces {m['faces']} · "
        f"exposure {m['exposure']:+.2f} · WB {m['temp_shift']:+.0f}/{m['tint_shift']:+.0f}</figcaption></figure>"
        for m in metrics)
    page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>vivaah QA report</title><style>
:root{{color-scheme:light dark}} body{{font:14px -apple-system,Segoe UI,sans-serif;margin:16px;max-width:1300px}}
table{{border-collapse:collapse;width:100%}} td,th{{border-bottom:1px solid #8884;padding:5px 8px;text-align:left;vertical-align:top}}
figure{{margin:0 0 18px}} figure div{{display:flex;gap:6px;flex-wrap:wrap}} img{{max-width:49%;height:auto}}
.sum span{{display:inline-block;margin-right:14px;font-size:16px}}</style></head><body>
<h1>vivaah QA report — look: {html.escape(look_id)}</h1>
<p class="sum"><span style="color:{colour['pass']}">{counts['pass']} pass</span><span style="color:{colour['warn']}">{counts['warn']} warn</span>
<span style="color:{colour['fail']}">{counts['fail']} fail</span><span>{counts['info']} info</span></p>
<p>References: ColorChecker Classic 24 (sRGB), CIEDE2000 colour difference, Rec.709 vectorscope skin-tone line ≈ 123°,
waveform limits (clipping, crushed blacks, skin IRE), Adobe Camera Raw slider ranges.</p>
<h2>Checks</h2><table><tr><th>area</th><th>photo</th><th>check</th><th>result</th><th>value</th><th>target</th></tr>{rows}</table>
<h2>Before / after</h2>{cards}</body></html>"""
    path = out_dir / "report.html"
    path.write_text(page, encoding="utf-8")
    return path


def run(inputs: list[str], look_id: str = "signature", out_dir: str = "vivaah_qa", size: int = 2048,
        limit: int | None = None, log=print) -> tuple[Path, list[Check]]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    look = get_look(look_id)
    log("engine and Lightroom-file checks ...")
    checks = engine_checks(out)
    paths = imageio.find_photos(inputs)[:limit] if inputs else []
    metrics = []
    for i, p in enumerate(paths, 1):
        log(f"photo {i}/{len(paths)}: {p.name}")
        try:
            m, c = photo_checks(p, look, size, out)
            metrics.append(m)
            checks += c
        except Exception as exc:  # noqa: BLE001 - report and keep going
            checks.append(Check("file", "photo could be processed", "fail", f"{type(exc).__name__}: {exc}", "no error", p.name))
    checks += consistency_checks(metrics)
    report = write_report(out, look.id, checks, metrics)
    counts = {s: sum(c.status == s for c in checks) for s in ("pass", "warn", "fail")}
    log(f"{counts['pass']} pass, {counts['warn']} warn, {counts['fail']} fail. Report: {report}")
    for c in checks:
        if c.status == "fail":
            log(f"  FAIL {c.photo + ': ' if c.photo else ''}{c.name}: {c.value} (target {c.target})")
    return report, checks
