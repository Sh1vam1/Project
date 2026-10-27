# vivaah — automated premium colour grading for wedding photos

vivaah looks at every photo of a wedding, fixes what is wrong with it (white balance,
exposure, highlights, skin), and then applies a premium look — your **Signature** look
built from your reference edits, or one of eight looks designed for Indian weddings.

It works **with Lightroom**, not instead of it:

- **Lightroom edits (recommended):** vivaah writes a small `.xmp` file next to every raw
  file. Lightroom Classic reads it, and every photo opens already edited — still fully
  adjustable, raw quality untouched.
- **Finished files:** vivaah can also render JPEG / 16-bit TIFF directly (sneak peeks,
  JPEG-only jobs, quick client previews).
- **Lightroom presets and video LUTs:** every look can be exported as a Lightroom
  preset and as a `.cube` LUT for Premiere Pro / DaVinci Resolve / Final Cut.

Everything runs on your Mac. No uploads, no subscriptions, no per-photo fees.
Works with raw files from any camera your photographers use (Canon, Nikon, Sony,
Fujifilm, Panasonic, Olympus/OM, Leica, Pentax, Hasselblad...).

---

## What happens to each photo

1. **Analyse** (about 1 s per photo): finds faces (including profiles and small faces),
   measures skin, brightness, light colour and palette, and works out the scene:
   day, shade, warm indoor (tungsten / candles) or night.
2. **Correct:**
   - **White balance** from white surfaces, skin and edge statistics. Some warmth of
     mandap or candle light is kept on purpose instead of turning everything neutral.
   - **Exposure** from the faces. Faces already in a flattering range keep the
     photographer's intent.
   - **Tone:** highlights, whites and blacks are set to where the tones actually sit.
3. **Apply the look**, choosing the right variant automatically. For example, a night
   first-dance gets the Signature night variant, not the daytime one.
4. **Normalise colour across cameras:** greens, skies and skin are steered to the same
   target in every frame. A Canon shooter and a Sony shooter at the same wedding end up
   matching.
5. **Protect** skin (it stays natural under the look) and outfits: a big emerald lehenga
   or haldi yellow is not muted like foliage.
6. **Keep the gallery consistent:** white balance is matched across each sequence shot
   under the same light, so frames don't flicker warm/cool like camera auto-WB does.

## Install (Mac, once)

1. Download this repository (green **Code** button > Download ZIP) and unzip it,
   e.g. into `Documents/vivaah`.
2. Open **Terminal**, type `cd ` (with a space), drag the vivaah folder into the
   window, press Enter, then run:

   ```bash
   bash setup_mac.sh
   ```

   It needs Python 3.9+ (macOS usually has it; otherwise get it from
   [python.org](https://www.python.org/downloads/macos/)).

## Use it

**Easiest:** double-click **`mac/Vivaah Grade.command`**, choose the wedding folder,
choose a look, and choose *Lightroom edits*, *Finished JPEGs* or *Both*.

The first time, macOS may say it cannot open the file. Right-click it, choose
**Open**, then click **Open** again.

**Terminal** (after `source .venv/bin/activate` in the vivaah folder):

```bash
vivaah grade  "/Volumes/Card/Sharma Wedding" --look signature --xmp        # Lightroom edits
vivaah grade  "/path/to/wedding" --look signature --export ~/Desktop/graded --size 2048
vivaah preview photo.CR3                     # one photo through every look, side by side
vivaah looks -v                              # list the looks
vivaah presets --out ~/Desktop/vivaah-presets   # Lightroom presets (.xmp) for every look
vivaah lut --out ~/Desktop/vivaah-luts           # .cube LUTs for video
vivaah learn ~/Desktop/refs --name "Aanya Style"  # create a look from reference edits
vivaah analyze photo.NEF                     # what vivaah sees and what it would change
vivaah qa ~/Downloads/test                   # quality check against industry references (HTML report)
vivaah restore "/path/to/wedding"            # undo: original sidecars back, vivaah's own removed
```

**Quality check.** `vivaah qa <folder>` grades the photos and writes
`vivaah_qa/report.html`. The report shows before/after thumbnails and pass/warn/fail
for:

- colour accuracy on the ColorChecker (CIEDE2000)
- skin on the vectorscope skin-tone line, and skin brightness on the waveform (IRE)
- clipping and crushed blacks, banding, white-balance tint
- sRGB profile and EXIF in the finished files
- every Lightroom value inside Adobe's slider ranges
- consistency across the set, and speed

**Safe to re-run.** vivaah never writes finished files into the folder of the
originals and never overwrites a photo. It skips its own output folders, so running it
again on the same wedding does not grade its own JPEGs. Photos with the same name from
different photographers' folders get separate files.

Every `grade` run writes `vivaah_report/` containing:

- `summary.csv`: per photo, the scene detected and the corrections made
- `details.json`: the full analysis
- `before_after.jpg`: a contact sheet, when exporting

Step-by-step Lightroom Classic / Lightroom instructions:
[docs/LIGHTROOM_WORKFLOW.md](docs/LIGHTROOM_WORKFLOW.md)

## Looks

| id | look | made for |
|---|---|---|
| `signature` | **Signature — Warm Editorial.** Built from your references: ivory whites that never go paper-white, olive-muted greens, soft slate blues, peach-golden skin, deep crimson reds, deep warm blacks. Auto night / warm-indoor / shade variants. | everything; your house style |
| `royal-heritage` | Rich true reds, glowing gold, deep contrast | pheras, palaces, red lehengas |
| `pastel-fine-art` | Bright, airy, creamy, pastel greens | day functions, destination, pastel outfits |
| `cinematic-editorial` | Moody, dark olive greens, teal shadows, warm skin | couple shoots, sangeet, editorial |
| `portra-film` | Kodak Portra-style soft warmth and fine grain | candids, golden hour, family |
| `true-colour` | Natural, accurate outfit colours | rituals, family formals, albums |
| `haldi-sunshine` | Golden glow; keeps the haldi yellow | haldi, marigold decor |
| `mehendi-emerald` | Jewel-tone emeralds instead of lime | mehendi, green outfits |
| `night-glam` | Tames LED / stage casts; clean skin, deep blacks | sangeet, reception |

`--strength 0.7` softens the creative look; the automatic corrections still apply.

Why the looks are built the way they are:
[docs/COLOR_GUIDE.md](docs/COLOR_GUIDE.md) (Indian wedding colour guide) and
[docs/SIGNATURE_LOOK.md](docs/SIGNATURE_LOOK.md) (the analysis of your references).

## Teach it your style

Put 10–30 finished photos you love into a folder (your own work or references) and run:

```bash
vivaah learn ~/Desktop/my-refs --name "My Style"
vivaah grade "/path/to/wedding" --look my-style --xmp
```

vivaah measures how the references treat skin, whites, blacks, greens, skies and
shadows. It saves a new look in `~/.vivaah/looks/` and grades every new photo toward
those numbers.

- Dark reference frames become the look's night variant.
- With only a few references, the new look stays close to the base look.
- The more references you add, the more closely it follows them.

## Speed

- **Lightroom-edit mode** only analyses a small copy of each photo. This is about 1 s per
  photo per CPU core, and all cores are used.
- **Finished JPEGs** are rendered at full size: about 10 s for a 24 MP photo on the
  4-core cloud machine vivaah was developed on. A recent Mac should be faster; this has
  not been measured yet. `--size 2048` makes exports much faster.

## Limits (honest list)

- The direct JPEG renderer is modelled on Lightroom's sliders but is not
  pixel-identical to Lightroom. For final delivery, use the Lightroom-edit route.
- If Lightroom renders consistently brighter or darker than vivaah's preview, set
  `--exposure-bias` once (see the workflow guide).
- Lightroom reads sidecars only for raw files. JPEG, TIFF and DNG files keep their
  settings inside the file, so use `--export` for those.
- Retouching (skin smoothing, blemish or object removal) and cropping are not
  automated yet. Hero shots still deserve your final touch.

## For developers

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
pytest
python tools/roundtrip.py references/ --look signature   # how close a look gets to your references
```

`CLAUDE.md` has the full project context for Claude Code sessions.

Code map:

- `vivaah/engine.py`: Lightroom-style renderer. Tone, HSL, colour grading and
  calibration, with a 3D-LUT fast path, processed in strips.
- `analysis.py`: faces, skin model, white-balance cues, scene type.
- `auto.py`: per-photo decisions.
- `looks/*.json`: the looks.
- `xmp.py`: Lightroom presets and sidecars.
- `lut.py`: `.cube` export.
- `learn.py`: reference learning.
- `batch.py`: parallel runs and gallery consistency.

Roadmap: [docs/ROADMAP.md](docs/ROADMAP.md).
