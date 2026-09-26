"""Lightroom / Camera Raw XMP: develop presets and per-photo sidecars.

* ``preset_xmp``   -> a Develop preset (.xmp) you import once into Lightroom
                      Classic or Lightroom (Presets panel > Import).  White
                      balance and exposure are left out on purpose: those are
                      decided per photo.
* ``write_sidecar`` -> ``IMG_1234.xmp`` next to ``IMG_1234.CR3`` with the full
                      per-photo settings (absolute Temperature/Tint included).
                      Lightroom Classic picks it up on import or with
                      Metadata > Read Metadata from Files.

Existing sidecars are merged, never clobbered: ratings, colour labels,
keywords, crops, masks, spot removal and the camera profile are kept, and the
original file is backed up once as ``.xmp.vivaah-backup``.
"""
from __future__ import annotations

import shutil
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from . import __version__, wb
from .looks import Look
from .settings import BANDS, DevelopSettings

NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "crs": "http://ns.adobe.com/camera-raw-settings/1.0/",
    "xmp": "http://ns.adobe.com/xap/1.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "tiff": "http://ns.adobe.com/tiff/1.0/",
    "exif": "http://ns.adobe.com/exif/1.0/",
    "exifEX": "http://cipa.jp/exif/1.0/",
    "aux": "http://ns.adobe.com/exif/1.0/aux/",
    "photoshop": "http://ns.adobe.com/photoshop/1.0/",
    "xmpMM": "http://ns.adobe.com/xap/1.0/mm/",
    "stEvt": "http://ns.adobe.com/xap/1.0/sType/ResourceEvent#",
    "stRef": "http://ns.adobe.com/xap/1.0/sType/ResourceRef#",
    "lr": "http://ns.adobe.com/lightroom/1.0/",
    "xmpRights": "http://ns.adobe.com/xap/1.0/rights/",
    "Iptc4xmpCore": "http://iptc.org/std/Iptc4xmpCore/1.0/xmlns/",
    "vivaah": "http://ns.vivaah.app/1.0/",
}
for _p, _u in NS.items():
    ET.register_namespace(_p, _u)

PROCESS_VERSION = "11.0"
CRS_VERSION = "15.4"

_BAND_NAME = {b: b.capitalize() for b in BANDS}

# setting -> (crs attribute, kind)
_FIELDS = [
    ("contrast", "Contrast2012", "int"),
    ("highlights", "Highlights2012", "int"),
    ("shadows", "Shadows2012", "int"),
    ("whites", "Whites2012", "int"),
    ("blacks", "Blacks2012", "int"),
    ("texture", "Texture", "int"),
    ("clarity", "Clarity2012", "int"),
    ("dehaze", "Dehaze", "int"),
    ("vibrance", "Vibrance", "int"),
    ("saturation", "Saturation", "int"),
    ("parametric_shadows", "ParametricShadows", "int"),
    ("parametric_darks", "ParametricDarks", "int"),
    ("parametric_lights", "ParametricLights", "int"),
    ("parametric_highlights", "ParametricHighlights", "int"),
    ("parametric_shadow_split", "ParametricShadowSplit", "uint"),
    ("parametric_midtone_split", "ParametricMidtoneSplit", "uint"),
    ("parametric_highlight_split", "ParametricHighlightSplit", "uint"),
    ("grade_shadow_hue", "SplitToningShadowHue", "uint"),
    ("grade_shadow_sat", "SplitToningShadowSaturation", "uint"),
    ("grade_shadow_lum", "ColorGradeShadowLum", "int"),
    ("grade_mid_hue", "ColorGradeMidtoneHue", "uint"),
    ("grade_mid_sat", "ColorGradeMidtoneSat", "uint"),
    ("grade_mid_lum", "ColorGradeMidtoneLum", "int"),
    ("grade_high_hue", "SplitToningHighlightHue", "uint"),
    ("grade_high_sat", "SplitToningHighlightSaturation", "uint"),
    ("grade_high_lum", "ColorGradeHighlightLum", "int"),
    ("grade_global_hue", "ColorGradeGlobalHue", "uint"),
    ("grade_global_sat", "ColorGradeGlobalSat", "uint"),
    ("grade_global_lum", "ColorGradeGlobalLum", "int"),
    ("grade_blending", "ColorGradeBlending", "uint"),
    ("grade_balance", "SplitToningBalance", "int"),
    ("cal_shadow_tint", "ShadowTint", "int"),
    ("cal_red_hue", "RedHue", "int"),
    ("cal_red_sat", "RedSaturation", "int"),
    ("cal_green_hue", "GreenHue", "int"),
    ("cal_green_sat", "GreenSaturation", "int"),
    ("cal_blue_hue", "BlueHue", "int"),
    ("cal_blue_sat", "BlueSaturation", "int"),
    ("vignette_amount", "PostCropVignetteAmount", "int"),
    ("vignette_midpoint", "PostCropVignetteMidpoint", "uint"),
    ("vignette_feather", "PostCropVignetteFeather", "uint"),
    ("vignette_roundness", "PostCropVignetteRoundness", "int"),
    ("vignette_highlights", "PostCropVignetteHighlightContrast", "uint"),
    ("grain_amount", "GrainAmount", "uint"),
    ("grain_size", "GrainSize", "uint"),
    ("grain_roughness", "GrainFrequency", "uint"),
    ("sharpen_amount", "Sharpness", "uint"),
    ("sharpen_radius", "SharpenRadius", "radius"),
    ("sharpen_detail", "SharpenDetail", "uint"),
    ("sharpen_masking", "SharpenEdgeMasking", "uint"),
]
_CURVES = [
    ("curve", "ToneCurvePV2012"),
    ("curve_red", "ToneCurvePV2012Red"),
    ("curve_green", "ToneCurvePV2012Green"),
    ("curve_blue", "ToneCurvePV2012Blue"),
]
# every crs attribute/element vivaah owns (others in an existing sidecar are left alone)
MANAGED = (
    {f[1] for f in _FIELDS}
    | {c[1] for c in _CURVES}
    | {f"{k}Adjustment{_BAND_NAME[b]}" for k in ("Hue", "Saturation", "Luminance") for b in BANDS}
    | {"Exposure2012", "WhiteBalance", "Temperature", "Tint", "ToneCurveName2012", "PostCropVignetteStyle",
       "ProcessVersion", "Version", "HasSettings", "AlreadyApplied"}
)


def _fmt(v: float, kind: str) -> str:
    if kind == "int":
        i = int(round(v))
        return f"{i:+d}" if i else "0"
    if kind == "uint":
        return str(int(round(v)))
    if kind == "ev":
        return f"{v:+.2f}" if abs(v) >= 0.005 else "0.00"
    if kind == "radius":
        return f"{v:+.1f}"
    raise ValueError(kind)


def develop_attributes(s: DevelopSettings, include_exposure: bool = True) -> dict[str, str]:
    attrs: dict[str, str] = {}
    if include_exposure:
        attrs["Exposure2012"] = _fmt(s.exposure, "ev")
    for name, crs, kind in _FIELDS:
        attrs[crs] = _fmt(getattr(s, name), kind)
    attrs["PostCropVignetteStyle"] = "1"
    for k, group in (("Hue", s.hue), ("Saturation", s.sat), ("Luminance", s.lum)):
        for b in BANDS:
            attrs[f"{k}Adjustment{_BAND_NAME[b]}"] = _fmt(group[b], "int")
    custom = any(getattr(s, c) != [[0, 0], [255, 255]] for c, _ in _CURVES)
    attrs["ToneCurveName2012"] = "Custom" if custom else "Linear"
    return attrs


def _curve_points(points: list) -> list[str]:
    pts = sorted((int(round(x)), int(round(y))) for x, y in points)
    pts[0] = (0, pts[0][1])
    pts[-1] = (255, pts[-1][1])
    return [f"{x}, {max(0, min(255, y))}" for x, y in pts]


def _q(tag: str) -> str:
    p, local = tag.split(":")
    return f"{{{NS[p]}}}{local}"


def _alt(parent: ET.Element, tag: str, text: str) -> None:
    el = ET.SubElement(parent, _q(tag))
    alt = ET.SubElement(el, _q("rdf:Alt"))
    li = ET.SubElement(alt, _q("rdf:li"))
    li.set("{http://www.w3.org/XML/1998/namespace}lang", "x-default")
    li.text = text


def _seq(parent: ET.Element, tag: str, items: list[str]) -> None:
    el = ET.SubElement(parent, _q(tag))
    seq = ET.SubElement(el, _q("rdf:Seq"))
    for it in items:
        ET.SubElement(seq, _q("rdf:li")).text = it


def _document() -> tuple[ET.Element, ET.Element]:
    root = ET.Element(_q("x:xmpmeta"))
    root.set(_q("x:xmptk"), f"vivaah {__version__}")
    rdf = ET.SubElement(root, _q("rdf:RDF"))
    desc = ET.SubElement(rdf, _q("rdf:Description"))
    desc.set(_q("rdf:about"), "")
    return root, desc


def _serialize(root: ET.Element) -> str:
    ET.indent(root, space=" ")
    return ET.tostring(root, encoding="unicode") + "\n"


def preset_xmp(look: Look, variant: str | None = None, group: str = "vivaah") -> str:
    s, _, used = look.resolve(variant)
    root, desc = _document()
    name = look.name if used == "base" else f"{look.name} ({used.replace('_', ' ')})"
    header = {
        "PresetType": "Normal",
        "Cluster": "",
        "UUID": uuid.uuid5(uuid.NAMESPACE_URL, f"vivaah/{look.id}/{used}").hex.upper(),
        "SupportsAmount": "True",
        "SupportsColor": "True",
        "SupportsMonochrome": "True",
        "SupportsHighDynamicRange": "True",
        "SupportsNormalDynamicRange": "True",
        "SupportsSceneReferred": "True",
        "SupportsOutputReferred": "True",
        "RequiresRGBTables": "False",
        "CameraModelRestriction": "",
        "Copyright": "",
        "ContactInfo": "",
        "Version": CRS_VERSION,
        "ProcessVersion": PROCESS_VERSION,
    }
    for k, v in header.items():
        desc.set(_q(f"crs:{k}"), v)
    for k, v in develop_attributes(s, include_exposure=False).items():
        desc.set(_q(f"crs:{k}"), v)
    desc.set(_q("crs:LensProfileEnable"), "1")
    desc.set(_q("crs:AutoLateralCA"), "1")
    desc.set(_q("crs:HasSettings"), "True")
    _alt(desc, "crs:Name", name)
    _alt(desc, "crs:ShortName", "")
    _alt(desc, "crs:SortName", "")
    _alt(desc, "crs:Group", group)
    _alt(desc, "crs:Description", look.description)
    for attr, crs in _CURVES:
        _seq(desc, f"crs:{crs}", _curve_points(getattr(s, attr)))
    return _serialize(root)


def read_as_shot(sidecar: Path) -> tuple[float, float] | None:
    """As-shot Temperature/Tint recorded by Lightroom (or by an earlier vivaah run)."""
    if not sidecar.is_file():
        return None
    try:
        tree = ET.parse(sidecar)
    except ET.ParseError:
        return None
    for desc in tree.getroot().iter(_q("rdf:Description")):
        t = desc.get(_q("vivaah:AsShotTemperature"))
        ti = desc.get(_q("vivaah:AsShotTint"))
        if t and ti:
            return float(t), float(ti)
        if desc.get(_q("crs:WhiteBalance")) == "As Shot":
            t, ti = desc.get(_q("crs:Temperature")), desc.get(_q("crs:Tint"))
            if t and ti:
                return float(t), float(ti)
    return None


def sidecar_path(photo_path: Path) -> Path:
    return photo_path.with_suffix(".xmp")


def write_sidecar(photo_path: str | Path, s: DevelopSettings, as_shot: tuple[float, float] | None,
                  look_id: str = "", variant: str = "", exposure_bias: float = 0.0) -> Path:
    """Write/merge ``<photo>.xmp`` with the full per-photo develop settings."""
    photo_path = Path(photo_path)
    side = sidecar_path(photo_path)
    base = read_as_shot(side) or as_shot
    if side.is_file():
        backup = side.with_suffix(".xmp.vivaah-backup")
        if not backup.exists():
            shutil.copy2(side, backup)
        try:
            tree = ET.parse(side)
            root = tree.getroot()
        except ET.ParseError:
            root = None
    else:
        root = None
    if root is None:
        root, desc = _document()
    else:
        descs = list(root.iter(_q("rdf:Description")))
        desc = next((d for d in descs if any(k.startswith(f"{{{NS['crs']}}}") for k in d.attrib)), None)
        if desc is None:
            if not descs:
                rdf = root.find(_q("rdf:RDF"))
                if rdf is None:
                    rdf = ET.SubElement(root, _q("rdf:RDF"))
                desc = ET.SubElement(rdf, _q("rdf:Description"))
                desc.set(_q("rdf:about"), "")
            else:
                desc = descs[0]
    # drop the fields we own; keep crop, masks, profile, lens, retouching, ratings...
    crs_ns = f"{{{NS['crs']}}}"
    for k in list(desc.attrib):
        if k.startswith(crs_ns) and k[len(crs_ns):] in MANAGED:
            del desc.attrib[k]
    for child in list(desc):
        if child.tag.startswith(crs_ns) and child.tag[len(crs_ns):] in MANAGED:
            desc.remove(child)

    s = s.copy()
    s.exposure = s.exposure + exposure_bias
    attrs = {
        "Version": CRS_VERSION,
        "ProcessVersion": PROCESS_VERSION,
    }
    if base:
        kelvin, tint = wb.apply_shift(base[0], base[1], s.temp_shift, s.tint_shift)
        attrs.update(WhiteBalance="Custom", Temperature=str(int(round(kelvin))), Tint=_fmt(tint, "int"))
        desc.set(_q("vivaah:AsShotTemperature"), str(int(round(base[0]))))
        desc.set(_q("vivaah:AsShotTint"), str(int(round(base[1]))))
    else:
        attrs["WhiteBalance"] = "As Shot"
    attrs.update(develop_attributes(s))
    attrs.update(HasSettings="True", AlreadyApplied="False")
    for k, v in attrs.items():
        desc.set(_q(f"crs:{k}"), v)
    if desc.get(_q("crs:LensProfileEnable")) is None:
        desc.set(_q("crs:LensProfileEnable"), "1")
    if desc.get(_q("crs:AutoLateralCA")) is None:
        desc.set(_q("crs:AutoLateralCA"), "1")
    desc.set(_q("crs:RawFileName"), photo_path.name)
    desc.set(_q("vivaah:Look"), look_id)
    desc.set(_q("vivaah:Variant"), variant)
    desc.set(_q("vivaah:Version"), __version__)
    for attr, crs in _CURVES:
        _seq(desc, f"crs:{crs}", _curve_points(getattr(s, attr)))
    side.write_text(_serialize(root), encoding="utf-8")
    return side


def restore_sidecar(photo_path: str | Path) -> bool:
    """Undo: put back the sidecar that existed before vivaah, or remove one vivaah created."""
    side = sidecar_path(Path(photo_path))
    backup = side.with_suffix(".xmp.vivaah-backup")
    if backup.exists():
        shutil.move(backup, side)
        return True
    if not side.exists():
        return False
    try:
        root = ET.parse(side).getroot()
    except ET.ParseError:
        return False
    if any(d.get(_q("vivaah:Version")) for d in root.iter(_q("rdf:Description"))):
        side.unlink()
        return True
    return False
