# The Signature look — built from your references

You shared 13 reference edits:

- couple portraits in a field, at a lake, on a balcony and by ivy walls
- two bridal portraits at a stone colonnade and by ivy
- a night first-dance with bokeh and haze
- two warm reception/bar portraits under chandeliers
- a proposal on a dock
- a studio family portrait with sage drapes

I analysed all 13 by eye, and measured 5 of them pixel by pixel. The measurements use
the same analysis vivaah runs on every photo.

The reference images themselves are **not** stored in this repository: they are other
photographers' work and show real people. Only the numbers below are.

## What the references have in common

| Trait | Seen in | What it looks like |
|---|---|---|
| **Olive greens** | field, balcony, studio drapes, lake reeds, proposal | greens pushed toward yellow-olive, muted and darker. In the measured frames *no* pixels were left in Lightroom's true Green band; foliage sits at OKLab hue 100–113°, chroma ≈ 0.05–0.066 |
| **Soft, slate skies and water** | field, lake, tower | blue desaturated toward grey-slate; teal-grey water |
| **Ivory whites with a cap** | all day frames | brightest whites at about 231–242 instead of 255. They are neutral under neutral light (field sky 236/236/236) and ivory under warm light (239/225/216) |
| **Deep, warm blacks** | sandstone, studio, balcony | shadows are deep (L\* 0.4–2.4 at the darkest 0.5%) but tinted warm: black floor ≈ RGB 13/7/2 |
| **Peach-golden skin** | all | facial skin at OKLab hue 41–56° (peachy, slightly red of "yellow-golden"), chroma 0.06–0.072 in daylight |
| **Deep crimson reds** | red dress and awning, bow tie, bouquet | red stays red (not orange), deep and rich but not neon |
| **Night: moody, capped, warm practicals** | first dance | frame median L\* ≈ 7; highlights capped around 215–242; cool haze and neutral-cool shadows against warm bulbs |
| **Warm interiors: amber** | bar and chandelier portraits | strong golden-amber ambience kept (not neutralised), creamy highlights |

## Measurements (5 references with pixel data)

| ref | scene | face L\* | skin hue / chroma | foliage hue / chroma | whites L\* (p99.5) | blacks L\* (p0.5) | white cap RGB | black floor RGB |
|---|---|---|---|---|---|---|---|---|
| field couple | day | 60.1 | 50.0 / 0.072 | 112.6 / 0.066 | 93.1 | 0.4 | 236, 236, 236 | 2, 1, 0 |
| night first dance | night | 28.2 | 28.6 / 0.057 | – | 78.7 | 0.0 | 242, 227, 225 | 0, 0, 0 |
| sandstone, red dress | day | 48.9 | 47.6 / 0.072 | – | 69.5 | 2.4 | 231, 208, 205 | 13, 7, 2 |
| balcony, golden | day | 59.3 | 56.0 / 0.070 | 105.5 / 0.066 | 87.9 | 1.2 | 239, 225, 216 | 6, 4, 2 |
| studio family | day | 73.4 | 41.4 / 0.060 | 100.0 / 0.051 | 90.6 | 2.4 | 239, 228, 227 | 13, 7, 2 |

## How that became settings (`vivaah/looks/signature.json`)

| Trait | Lightroom-style settings | Per-photo target (automation) |
|---|---|---|
| Olive greens | Green hue −52, Yellow hue −14; Green sat −34, Yellow sat −20; Green lum −12, Yellow lum −8 | foliage steered to hue 108°, chroma 0.06 in every photo |
| Slate skies | Blue hue −16, sat −34; Aqua hue −18, sat −38 | sky chroma 0.035 |
| Ivory white cap | tone curve top 246, Blue curve top 253, Highlights −30, Whites −4; Highlights grade 48° / 2 | brightest tones to L\* 93 |
| Warm deep blacks | Red curve 0→9, Green curve 0→4 (shadows only); Shadows grade 38° / 8; Blacks −6 | darkest tones to L\* 2.5 |
| Peach-golden skin | Orange hue +2, sat −5, lum +7; Calibration red hue +4, blue sat +10; skin protection 0.7 | skin steered to hue 53°, chroma 0.055–0.085; faces exposed toward L\* 64 (comfort zone keeps the photographer's intent) |
| Crimson reds | Red hue −6, sat +4, lum −8 | outfit protection when a big saturated red/green/yellow area is found |
| Soft film finish | Contrast +8, Texture −6, grain 8, vignette −8 | – |
| Night variant | contrast +16, highlights −55, whites −22, curve top 228, teal shadows 205° / 9, warm highlights 40° / 12 | faces L\* 46 (wide comfort zone), whites L\* 82, 35% of warm light kept |
| Warm-indoor variant | amber grade 30–42°, curve top 236, Orange/Yellow sat −10/−24 | 45% of warm light kept, extra warmth +8 |

## Validation

Round-trip test on the 5 measurable references:

1. Each finished reference was turned back into a plausible camera original: cooler,
   greener white balance; neon camera greens; stretched whites; exposure off by 0.2–0.5
   EV.
2. That "original" was graded fully automatically with Signature.

| | average colour difference to the real reference (OKLab ΔE × 100) |
|---|---|
| camera "original" | 3.9 |
| after vivaah Signature | 3.1 |

The day frames improved the most: the field couple went from 3.5 to 2.3 and the studio
family from 6.2 to 4.4. White balance was recovered almost exactly (+15 mired / +3 tint
estimated for a +18 / +6 error). The night frame stayed moody instead of being
brightened into day.

`vivaah learn` run on the same 5 references creates a comparable look automatically
(3.9 → 3.4). That is the path for any new style you want: send references, run
`learn`, grade.

## Tuning it

Everything is in `vivaah/looks/signature.json`:

- `settings` holds the Lightroom-style sliders.
- `auto` holds the targets each photo is steered to.
- `variants` holds the night / warm-indoor / shade overrides.

Changes apply on the next run. They also flow into `vivaah presets` and `vivaah lut`.
