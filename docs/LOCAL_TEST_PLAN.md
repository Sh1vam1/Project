# Test plan for the Mac (for Claude Code running locally)

The user asked for vivaah to be installed on their Mac, tested in several ways against
industry standards, and then fixed and improved. Test photos are in `~/Downloads/test`.
Other photos on the Mac may also be used.

Work on a **copy** of the photos. Never write sidecars or outputs into the user's
original folders.

## 0. Setup

```bash
cd <this repo>
bash setup_mac.sh                  # creates .venv, installs vivaah
source .venv/bin/activate
pip install -e ".[dev]"
pytest                             # expect all tests to pass
mkdir -p ~/vivaah-testrun && cp -R ~/Downloads/test ~/vivaah-testrun/photos
```

Record the machine (chip, cores, RAM, macOS) and `python3 -V` for the report.

## 1. Industry-reference QA (automated)

```bash
vivaah qa ~/vivaah-testrun/photos --look signature --out ~/vivaah-testrun/qa-signature
vivaah qa ~/vivaah-testrun/photos --look true-colour --out ~/vivaah-testrun/qa-true
open ~/vivaah-testrun/qa-signature/report.html
```

Investigate every **fail** and every repeated **warn**. What the checks mean:

| check | reference / threshold |
|---|---|
| ColorChecker ΔE00 | CIEDE2000. Neutral settings < 0.5; `true-colour` mean < 3 |
| skin angle | Rec.709 vectorscope skin-tone line 123° ± 8 (the "was" value is before grading) |
| skin IRE | waveform: 35–80 by day, 20–80 at night |
| clipping / crushed blacks | < 0.5 % at 254–255; < 1 % at 0–2 (< 5 % at night) |
| banding | ≤ 2 empty levels in the 8-bit luminance histogram |
| whites | bright neutrals: \|a\*\| ≤ 4, b\* −4..+12 (ivory allowed) |
| file | sRGB ICC embedded, EXIF kept, requested size |
| Lightroom | every crs value inside Adobe's slider ranges; valid tone curves |
| consistency | skin angle std < 4° across day photos of one event |

## 2. Real-raw pipeline

1. `vivaah analyze <one raw>`: faces found? scene right? as-shot Kelvin plausible
   compared with the camera's WB setting?
2. `vivaah grade ~/vivaah-testrun/photos --look signature --xmp --export ~/vivaah-testrun/jpeg --size 2048`
   - Every file type decodes: list the extensions that fail.
   - A sidecar is written for every raw.
   - Check the timing: seconds per photo for analysis and for export.
3. Look at `vivaah_report/before_after.jpg` and a few full-size JPEGs at 100 %:
   - halos around people against bright sky (local tone mapping)
   - skin smoothness, noise, banding in skies and walls
   - red / pink / gold outfits: hue stays true, texture not clipped
   - night and indoor frames stay moody, not grey

## 3. Lightroom Classic check (needs the user at the Mac)

1. Import `~/vivaah-testrun/photos` with **Add**.
2. Confirm that the settings arrive: the Basic panel shows Temperature/Tint/Exposure
   from the sidecar, and the HSL, curves and colour grading match the look.
3. Compare Lightroom's rendering with vivaah's JPEGs, side by side:
   - **Brightness:** if Lightroom is consistently brighter or darker, measure the
     offset and set `--exposure-bias`.
   - **Colour:** if greens, skin or whites differ clearly, adjust the renderer (not
     the look) where the model is wrong.
4. `vivaah restore ~/vivaah-testrun/photos` then *Read Metadata from Files*: the
   original settings come back.
5. Import the presets (`vivaah presets --out ~/vivaah-testrun/presets`) and confirm
   they load in both Lightroom Classic and Lightroom.

## 4. Robustness

- Mixed folder: RAW+JPEG pairs, HEIC from phones, a corrupt file (truncate a copy
  with `head -c 100000`), a very small image, a portrait-orientation JPEG.
- Photos with no faces (details, decor), very dark receptions, stage LEDs, haldi
  yellow, a red lehenga close-up, group photos with small faces.
- A folder of 300+ photos: memory stays bounded, all cores used, no worker crashes.

## 5. Improve

- Fix every bug found. Add a regression test for each (`tests/`).
- Tune `vivaah/looks/signature.json` against `references/` with
  `python tools/roundtrip.py references/ --look signature`. Keep the QA passing.
- Known weak spot: warm monochrome scenes (sandstone, wood) over-cool. See
  CLAUDE.md.
- Summarise for the user in plain language:
  - what was tested
  - what passed, and what was fixed
  - before/after examples
  - what still needs their eye in Lightroom
