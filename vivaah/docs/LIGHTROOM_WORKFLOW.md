# Lightroom workflow

vivaah writes a Lightroom sidecar (`IMG_1234.xmp`) next to each raw file
(`IMG_1234.CR3`). The sidecar holds every develop setting for that photo:

- white balance as real Temperature/Tint values
- exposure, tone, curves, HSL, colour grading, calibration, grain, vignette and
  sharpening
- lens corrections switched on

Lightroom treats these settings as if you had made them yourself.

## Lightroom Classic — new wedding (fastest)

1. Copy the cards to your drive as usual, e.g. `Weddings/2026-11 Sharma/`.
2. Run vivaah on that folder before importing. Either double-click
   `mac/Vivaah Grade.command` and choose **Lightroom edits**, or run:
   ```bash
   vivaah grade "/Volumes/Work/Weddings/2026-11 Sharma" --look signature --xmp
   ```
3. Import the folder into Lightroom Classic with **Add** (not Copy/Move, so the
   sidecars stay next to their raws). Every photo arrives already graded.
4. Review in Library or Develop: flag, tweak and sync as normal. Hero shots get your
   personal finishing (masks, retouching, crop).
5. Export as usual.

## Lightroom Classic — photos already in the catalog

1. Select the photos in Library and press **Cmd+S** (*Metadata > Save Metadata to
   File*). Lightroom writes its own as-shot white balance into the sidecars, and
   vivaah then uses Lightroom's exact numbers as its starting point. Optional, but it
   makes white balance match most closely.
2. Run vivaah with `--xmp` on the folder.
3. Back in Lightroom, select the same photos, then choose **Metadata > Read Metadata
   from Files**. You can also click the small "metadata changed on disk" badge on a
   thumbnail and choose *Import Settings from Disk*.

Culling is safe. Existing sidecars are merged, not replaced:

- **Kept:** star ratings, colour labels, keywords, crops, masks, spot removal and the
  camera profile.
- **Backed up:** a sidecar written by Lightroom (or any other app) is copied to
  `*.xmp.vivaah-backup` before vivaah first changes it. Running vivaah again, even
  with a different look, never replaces that backup. `vivaah restore <folder>` puts
  it back.

## Lightroom (cloud / desktop "Lightroom")

Lightroom applies XMP sidecar settings when raw files are added from a folder, so run
vivaah **before** adding the photos. For photos already in Lightroom, remove and add
them again after running vivaah, or apply a vivaah preset (below) instead.

## Using the looks as presets

```bash
vivaah presets --out ~/Desktop/vivaah-presets
```

Import the presets once:

- **Lightroom Classic:** Develop > Presets panel > **+** > *Import Presets...* >
  choose the `.xmp` files.
- **Lightroom:** Edit > Presets > **...** > *Import Presets*.

They appear in a **vivaah** group. Presets deliberately leave white balance and
exposure alone, because those are per-photo decisions. You still get the full look,
including the night and warm-indoor variants as separate presets.

A preset applies the same settings to every photo. `vivaah grade --xmp` also does the
per-photo work:

- white balance, exposure and tone per photo
- greens, sky and skin steered to the look's targets
- outfit protection
- sequence consistency

## One-time exposure check (recommended)

vivaah's brightness decisions are calibrated on its own renderer, which is modelled on
Lightroom's default rendering. Some camera profiles are a little brighter or darker.
Check once with a handful of photos:

```bash
vivaah grade test-folder --look signature --xmp --export test-folder/vivaah-jpeg --size 1600
```

Import `test-folder` into Lightroom and compare with the JPEGs. If Lightroom looks
consistently darker (or brighter), add `--exposure-bias 0.3` (or `-0.3`) to future
runs.

## Files Lightroom won't take sidecars for

Lightroom stores settings *inside* JPEG, TIFF, PSD and DNG files and ignores sidecars
for them. For those files, use `--export FOLDER` to get finished files from vivaah.

## Undo

```bash
vivaah restore "/path/to/wedding"
```

This restores the pre-vivaah sidecars from the backups and deletes sidecars that vivaah
created from scratch. In Lightroom, run *Read Metadata from Files* again.
