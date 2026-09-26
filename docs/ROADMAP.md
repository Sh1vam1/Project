# Roadmap

## Phase 1 — done in this version

- Per-photo analysis:
  - face detection (bundled YuNet)
  - face-seeded skin model
  - white balance from white surfaces, the least colourful surfaces, gray-edge
    statistics and skin
  - exposure from faces, or from the scene when there are none
  - scene type: day / shade / warm indoor / night
- Lightroom-style renderer in NumPy/OpenCV:
  - tone, curves, HSL, colour grading, calibration, clarity/texture/dehaze, vignette,
    grain, sharpening
  - 3D-LUT fast path, strip processing
  - skin and outfit protection
- Signature look built from your references (auto variants for night, warm indoor and
  shade), plus 8 Indian-wedding looks.
- Lightroom sidecars (merged, backed up, undoable), Lightroom presets, `.cube` LUTs,
  finished JPEG/TIFF output.
- Gallery consistency: white balance matched across sequences.
- `vivaah learn`: new looks from reference edits (brought forward from Phase 2).
- Mac setup script and double-click launcher.

## Phase 2 — learn *your* edits precisely

- **Learn from your Lightroom catalog.** Your `.lrcat` catalog already stores every
  slider you ever set. vivaah can read past weddings (original + your final settings)
  and learn how *you* react to each kind of photo: dark reception, backlit portrait,
  mixed light. Then it predicts your sliders for new photos. This is how services like
  Imagen/Aftershoot work, except it would run on your Mac and stay yours.
- Before/after pairs as an alternative to the catalog (exported originals plus your
  finals), fitted as a 3D LUT plus per-scene targets.
- Per-photographer / per-camera calibration profiles.

## Phase 3 — smarter automation

- Event detection (haldi / mehendi / sangeet / pheras / reception) to pick the look
  per section of the gallery automatically.
- Face-part masks exported as Lightroom masks: eyes, teeth, facial skin, subject
  relight.
- Automatic straighten and crop suggestions.
- Culling assist (closed eyes, blur, duplicates) to go with grading.
- A small desktop app: drop a folder, see a preview grid, adjust strength per look,
  send to Lightroom.

## Phase 4 — video

- The same looks already export as `.cube` LUTs (`vivaah lut`) for Premiere Pro,
  DaVinci Resolve and Final Cut. Photo and film deliverables can match today.
- Per-clip analysis (white balance / exposure per shot) with an XML/EDL or Resolve
  script, so clips arrive pre-balanced before the creative LUT.
- Log-camera input transforms (S-Log3, C-Log, V-Log, N-Log) in front of the looks.
