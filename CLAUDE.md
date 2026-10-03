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
pytest                                 # 81 tests, ~10 s
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
| `engine.py` | renderer. **A** WB/calibration matrix (`colour_matrix`, shared with `auto`/`lut`) + exposure → **B** raw base curve / JPEG knee + local tone on a guided-filter base/detail split (contrast, highlights, shadows, whites, blacks, clarity, texture), dehaze → **C** pure per-pixel colour (curves, vibrance/sat, HSL in OKLCh bands, colour grading, skin-safe variant only inside the skin mask's bounding box), direct or via 49³ LUT (`apply_lut` = two `cv2.remap` + blend, any shape) → **D** vignette, grain, sharpen. Big images go in strips with blur margins (strip output = whole-image output) |
| `faces.py` | YuNet ONNX (bundled, MIT, `models/NOTICE.md`), cheek/forehead sampling masks |
| `analysis.py` | neutral view at 1024 px, faces, face-seeded skin model (generic fallback), WB cues (white-patch, least-colourful, gray-edge) + skin hue/chroma solved on a mired/tint grid, L\* percentiles, palette, foliage/sky, outfit (very saturated) bands, scene class |
| `auto.py` | per-photo plan: look variant by scene, WB (keeps part of warm ambience), exposure (face comfort zone, small faces blended with scene), tone placement, HSL steering of foliage/sky/skin to look targets, outfit protection. `_render_lch` is a one-colour model of the renderer (raw WB relative to as-shot via `_wb_base`, skin-safe blend for faces): keep it in sync with `engine` |
| `looks.py` + `looks/*.json` | `settings` (sliders), `auto` (targets: `AutoTargets`), `variants` (night / warm_indoor / shade) |
| `xmp.py` | presets (no WB or exposure) and sidecars: absolute Temperature/Tint from as-shot + shift; merges only `MANAGED` crs fields, keeps ratings/crops/masks, backs up only a sidecar someone else wrote (never vivaah's own), stores as-shot in `vivaah:` attributes so re-runs stay idempotent; `restore_sidecar` puts the original back or deletes a sidecar vivaah created |
| `lut.py` | `.cube` export (red fastest), `read_cube` |
| `learn.py` | fingerprints references → curves/grade/targets with shrinkage toward the base look (n/(n+2)) |
| `batch.py` | spawn process pool (in-process for 1 worker; a worker that dies, e.g. LibRaw crashing on a damaged raw, fails only its own file), two passes, WB smoothing across raw sequences (same camera + folder + scene, ≤5 min gaps), export plan (unique names, never an input file, refuses exporting into the originals' folder), export workers capped by RAM, warnings column, report CSV/JSON + contact sheet |
| `pipeline.py`, `sheet.py`, `imageio.py`, `cli.py` | glue, contact sheets, I/O: rawpy half-size for analysis, JPEG draft decode, EXIF orientation for JPEG/TIFF/PNG, embedded ICC → sRGB (8-bit via ImageCms, 16-bit via a LUT), 16-bit TIFF read/write with `tifffile` (sRGB ICC, make/model/time), EXIF passthrough, dithered 8-bit. `find_photos` skips vivaah's output folders (`.vivaah-output` marker, `vivaah-graded`, `vivaah_report`, `vivaah_qa`) and `*_looks.jpg` |

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
- OpenCV: keep arrays float32 and contiguous. `cv2.remap` maps (and output) must
  be < 32767 px in each direction: `_upsample_rows` and `apply_lut` tile anything
  bigger (`engine.REMAP_MAX`). Numpy reductions over the length-3 channel axis are
  slow, so use `_chmax`/`_chmin`.
- Pillow's `im.getexif()` returns the image's cached Exif object. Copy it before
  editing (`_exif_from_pil`); mutating it once made every portrait load sideways.
- Skin-safe colour (stage C) uses circular hue maths and applies only to
  skin-coloured pixels (hue within ~45° of the skin line), so pinks and purples at
  the edge of the skin mask keep the look.
- Multiprocessing uses **spawn**: LibRaw/OpenMP is not fork-safe.
- Lightroom reads sidecars only for proprietary raws, not for JPEG/TIFF/DNG.
- The generic (no-face) skin mask must not exclude pixels from the white search:
  under tungsten a white dress looks like skin. Only the face-seeded model (≥ 40
  facial samples) may exclude pixels, even when a tiny face was detected.
- File safety: outputs never go into an input folder (except sidecars), never
  overwrite an input, and output folders are marked `.vivaah-output` so re-runs
  never grade vivaah's own files.

## Validation so far (and what is NOT verified)

- `pytest`: 81 tests pass (colour/WB maths incl. CIEDE2000 against Sharma's
  reference data, renderer properties, strips = whole image, LUT vs direct,
  analysis/auto behaviour, XMP merge/restore, LUT format, QA, CLI end to end).
  `tests/test_imageio.py`, `test_safety.py`, `test_cli.py` and the newer tests in
  the other files are regression tests for the 32-finding code review (orientation,
  16-bit ICC, TIFF metadata, raw WB base in the planner, skin-safe pinks, sidecar
  undo after two runs, re-runs, name clashes, overwrite refusal, white-search gate,
  panoramas over 32k px, worker memory, preview analysing once). Each one failed on
  the code before the fix.
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
- Known weak spots:
  - Warm monochrome scenes (sandstone, wood interiors) fool the neutral/white-patch
    WB cues into over-cooling (`ref3` in the round trip; `tools/roundtrip.py` mean
    4.42 → 3.95 overall, but ref3 2.60 → 5.31).
  - Strong magenta casts on skin (the Lena test image: skin at 103°) end at
    107–109°, because the skin nudge is limited to ±12–15 and the neutral cues pull
    the other way. QA flags it. Stage LEDs can do the same at sangeets.
  - Candidates for both: weight the skin cue more when faces exist; detect "no true
    neutrals" (lowest chroma is still high and aligned with the dominant hue).
- Reviews: a max-depth code review (32 findings) has been fixed and tested. The user
  also got `vivaah-for-review.zip` + `REVIEW_PROMPT.md` to ask ChatGPT for a second
  review. If they paste ChatGPT's findings, verify each against the code before
  changing anything (some will be wrong or already fixed).

## Next steps (agreed direction)

**Start with `docs/LOCAL_TEST_PLAN.md`.** The user asked for installation, testing
against industry standards, fixes and improvements on their Mac. Test photos are in
`~/Downloads/test`. Always work on a copy.

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
