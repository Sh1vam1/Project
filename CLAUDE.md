# CLAUDE.md — vivaah

Context for Claude Code sessions on this repository. The project was started in
a Claude Code cloud session; this file carries over what was learned there.

## The project and the user

- **vivaah** is local, automated premium colour grading for wedding photos,
  focused on Indian weddings. It works *with* Lightroom:
  - per-photo XMP sidecars that Lightroom Classic reads
  - Lightroom Develop presets
  - `.cube` LUTs for the video phase
  - finished JPEG/TIFF renders
- The user is a wedding photo and video editor in India, and not a developer:
  - Explain in plain language and give exact commands to paste.
  - Photos come from many photographers, so every camera brand and raw format shows
    up. Code must stay camera-agnostic (LibRaw via rawpy).
  - Works on a **Mac** with **both Lightroom Classic and Lightroom (cloud)**.
  - Has ChatGPT Plus, but that has no API, so it is not used. Grading stays local
    and deterministic: never use generative models that redraw faces.
- **Goal, in the user's words:** fast, automated, premium look; "check each photo and
  enhance it in the style of my references, even better". Photo first; video later.
- The user shared 13 reference edits (Instagram screenshots):
  - warm editorial style: olive-muted greens, ivory whites capped below 255,
    peach-golden skin, deep warm-tinted blacks, crimson reds
  - moody night frames with capped highlights
  - amber reception interiors

  The references are **not in the repo** (other photographers' work, real people).
  Only measurements are: see `docs/SIGNATURE_LOOK.md`. If the user supplies them
  again, keep them in `references/`, which is gitignored.

## Commands

```bash
bash setup_mac.sh                      # user-facing install (.venv + pip install -e ".[heic]")
python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"   # dev install
pytest                                 # 50 tests, ~6 s
vivaah grade <folder> --look signature --xmp               # Lightroom sidecars next to raws
vivaah grade <folder> --look signature --export out --size 2048
vivaah preview photo.jpg --looks all   # one photo through every look
vivaah analyze photo.NEF               # analysis + per-photo plan as JSON
vivaah qa ~/Downloads/test --look signature   # industry-reference QA -> vivaah_qa/report.html
vivaah learn references/ --name "My Style"   # new look from reference edits
python tools/roundtrip.py references/ --look signature --sheet rt.jpg      # look vs references
```

`mac/Vivaah Grade.command` is the double-click launcher: folder picker, look
picker and output choice via `osascript`.

## Architecture (vivaah/)

| module | role |
|---|---|
| `color.py` | sRGB/linear (cv2.pow for big arrays, uint8 LUT), CIE Lab, OKLab/LCh, band hue mapping |
| `wb.py` | DNG SDK temperature/tint ↔ xy (Robertson table, tint scale −3000), Bradford shift matrices, camera-neutral → Kelvin |
| `settings.py` | `DevelopSettings` mirroring Lightroom sliders; `skin_protect`/`outfit_protect` exist only in vivaah |
| `engine.py` | renderer. **A** WB/calibration matrix + exposure → **B** raw base curve / JPEG knee + local tone on a guided-filter base/detail split (contrast, highlights, shadows, whites, blacks, clarity, texture), dehaze → **C** pure per-pixel colour (curves, vibrance/sat, HSL in OKLCh bands, colour grading, skin-safe variant), direct or via 49³ LUT (`apply_lut` = two `cv2.remap` + blend) → **D** vignette, grain, sharpen. Big images go in strips with blur margins (strip output = whole-image output) |
| `faces.py` | YuNet ONNX (bundled, MIT, `models/NOTICE.md`), cheek/forehead sampling masks |
| `analysis.py` | neutral view at 1024 px, faces, face-seeded skin model (generic fallback), WB cues (white-patch, least-colourful, gray-edge) + skin hue/chroma solved on a mired/tint grid, L\* percentiles, palette, foliage/sky, outfit (very saturated) bands, scene class |
| `auto.py` | per-photo plan: look variant by scene, WB (keeps part of warm ambience), exposure (face comfort zone, small faces blended with scene), tone placement, HSL steering of foliage/sky/skin to look targets, outfit protection |
| `looks.py` + `looks/*.json` | `settings` (sliders), `auto` (targets: `AutoTargets`), `variants` (night / warm_indoor / shade) |
| `xmp.py` | presets (no WB or exposure) and sidecars: absolute Temperature/Tint from as-shot + shift; merges only `MANAGED` crs fields, keeps ratings/crops/masks, backs up once, stores as-shot in `vivaah:` attributes so re-runs stay idempotent; `restore_sidecar` |
| `lut.py` | `.cube` export (red fastest), `read_cube` |
| `learn.py` | fingerprints references → curves/grade/targets with shrinkage toward the base look (n/(n+2)) |
| `batch.py` | spawn process pool, two passes, WB smoothing across raw sequences (same camera + scene, ≤5 min gaps), report CSV/JSON + contact sheet |
| `pipeline.py`, `sheet.py`, `imageio.py`, `cli.py` | glue, contact sheets, I/O (rawpy half-size for analysis, JPEG draft decode, EXIF passthrough, dithered 8-bit, sRGB ICC) |

## Conventions and gotchas

- Internal images are float32 linear sRGB (D65). Edits happen in OKLab; reporting
  uses CIE L\*.
- WB is stored relative to as-shot:
  - `temp_shift` in mireds, **+ = warmer** (higher Kelvin, like Lightroom)
  - `tint_shift` in Lightroom tint units, + = magenta
  - Absolute Kelvin only appears in sidecars.
- The renderer must be (near) **identity at default settings** for rendered files.
  There is a test for it.
- Stage C must stay a **pure per-pixel function**; LUTs and `.cube` depend on it.
- numpy 2 / NEP 50: an `np.float64` scalar upcasts float32 arrays. Wrap with `float()`.
  This caused a real bug in `grain()`.
- OpenCV: keep arrays float32 and contiguous. `cv2.remap` maps must be < 32767 px
  wide. Numpy reductions over the length-3 channel axis are slow, so use
  `_chmax`/`_chmin`.
- Multiprocessing uses **spawn**: LibRaw/OpenMP is not fork-safe.
- Lightroom reads sidecars only for proprietary raws, not for JPEG/TIFF/DNG.
- The generic (no-face) skin mask must not exclude pixels from the white search:
  under tungsten a white dress looks like skin.

## Validation so far (and what is NOT verified)

- `pytest`: 50 tests pass (colour/WB maths incl. CIEDE2000 against Sharma's
  reference data, renderer properties, strips = whole image, LUT vs direct,
  analysis/auto behaviour, XMP merge/restore, LUT format, QA, CLI end to end).
- `vivaah qa` (`vivaah/qa.py`) checks against industry references:
  - ColorChecker ΔE00: 0.00 at neutral settings, 0.85 mean for `true-colour`
  - every look keeps ColorChecker skin on the Rec.709 skin-tone line (123° ± 8)
  - monotonic tone
  - no NaN at extreme sliders
  - LUT and strips exact
  - Adobe slider ranges in presets and sidecars
  - per photo: clipping, crushed blacks, skin angle/IRE, whites, banding, sRGB ICC,
    EXIF
- Skin calibration: OKLab hue 45.5 is the 123° vectorscope skin line. Looks target
  45.5–49 (`true-colour` on the line, `signature` 48 ≈ 125°). An earlier version aimed
  at 53–58 (129–134°, sallow); QA caught it. `auto._refine_skin_balance` nudges
  temp/tint (±12–15) so faces land on the look's skin hue after white balance.
- Round trip on 5 measurable references (hand-cropped): mean OKLab ΔE×100 went
  from 3.9 to 3.1 with `signature` (`docs/SIGNATURE_LOOK.md`). `tools/roundtrip.py`
  randomises the degradation per image, so its absolute numbers differ.
- Not verified yet:
  - Sidecars inside real Lightroom: import, look, brightness parity; `--exposure-bias`
    exists for calibration.
  - Real wedding raws. Only a NASA NEF and JPEGs were available in the cloud.
  - Speed on a Mac. The cloud VM measured ~1 s/photo analysis and ~10 s per 24 MP JPEG
    render.
- Known weak spot: warm monochrome scenes (sandstone, wood interiors) fool the
  neutral/white-patch WB cues into over-cooling (`ref3` in the round trip). Candidates:
  - weight the skin cue more when faces exist
  - detect "no true neutrals" (lowest chroma is still high and aligned with the
    dominant hue)

## Next steps (agreed direction)

1. **On the Mac:**
   - Run on a few real raws from one wedding, import into Lightroom Classic and
     compare with vivaah's JPEG preview. Fix any sidecar field Lightroom rejects;
     calibrate the exposure bias.
   - Tune `looks/signature.json` with `tools/roundtrip.py` and the user's references
     in `references/`.
2. **Learn the user's own edits** from their Lightroom Classic catalog (`.lrcat` is
   SQLite; develop settings per image are stored as Lua-style text in
   `Adobe_imageDevelopSettings`; verify the schema before relying on it). Predict
   per-photo sliders from analysis features.
3. See `docs/ROADMAP.md`: event detection, masks, desktop UI, video (LUTs exist).

Docs: `README.md` (user guide), `docs/LIGHTROOM_WORKFLOW.md`,
`docs/COLOR_GUIDE.md`, `docs/SIGNATURE_LOOK.md`, `docs/ROADMAP.md`.
