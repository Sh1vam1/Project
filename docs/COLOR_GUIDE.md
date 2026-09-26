# Premium colour for Indian weddings — a working guide

This guide explains what separates a *premium* wedding grade from a preset slapped on a
gallery. It also records the decisions built into vivaah's looks and automation. It
draws on common practice among wedding editors, published Lightroom guidance, and the
analysis of your reference edits ([SIGNATURE_LOOK.md](SIGNATURE_LOOK.md)).

## 1. What "premium" actually means

1. **Skin first, always.** Clients judge a gallery by how they look. Skin must be
   healthy, luminous and true to the person, never orange, grey, green or plastic.
2. **Consistency.** The same light should look the same across 50 frames. This holds
   even when three photographers shot on three camera brands.
3. **Restraint with saturation.** Premium work is rarely the most saturated.
   Backgrounds are calmed (muted greens, soft skies) so that skin, outfits and jewellery
   carry the colour.
4. **Controlled highlights.** Whites are clean but have detail and a gentle roll-off.
   Ivory stays ivory; white never becomes flat paper.
5. **Deep blacks with a colour of their own.** Blacks are rich rather than crushed or
   milky. They often carry a slight warm (or, at night, cool) tint.
6. **Separation.** Subject and background are pulled apart by colour contrast (warm
   skin against olive, slate or teal surroundings), not only by brightness.

Current trend context: 2026 wedding film and photo coverage favours true-to-life,
polished skin, refined contrast and warm, earthy, film-inspired palettes (muted greens,
golden highlights). Over-saturated "digital" colour is going out
([Fotober](https://fotober.com/best-wedding-video-trends-2026)). Editorial and moody
grades remain popular for couple sessions.

## 2. Skin tones for South Asian complexions

- **Where skin lives:** almost entirely in Lightroom's **Orange** band, with some Red.
  In vivaah's measurements, well-balanced faces sit at an OKLab hue of about 45–60°,
  from fair to deep complexions. Deeper skin sits slightly redder and is relatively more
  saturated for its brightness.
- **Common problems and fixes:**

  | Problem | Usual cause | Fix |
  |---|---|---|
  | Orange / "tan" skin | uncorrected tungsten or mandap light; too much saturation or vibrance | cool the white balance a little (keep some warmth); Orange saturation −5 to −10 |
  | Yellow / sallow skin | Orange hue pushed too far toward yellow; LED lighting | Orange hue back toward red; a touch of magenta tint |
  | Grey / dull skin | shade, overcast, flat JPEGs | warm up; Orange luminance +5 to +10 for glow; small vibrance boost (vibrance protects skin) |
  | Green skin | bounce light from foliage or grass; fluorescent light | magenta tint +; check skin on a vectorscope for video |
  | Magenta / pink skin | cheap LED stage lights; some camera profiles | green tint −; Red and Magenta saturation down |

- **Don't flatten complexions.** Exposure should make faces flattering, not make every
  person the same brightness. vivaah uses a *comfort zone*: faces already exposed well
  are left close to the photographer's choice, and only clearly under- or over-exposed
  faces are pulled in.
- **Makeup under flash** can look paler or greyer than neck and hands. Keep global
  corrections gentle and fix that locally on hero images.
- **Lightroom guidance often quoted for South Asian skin:** nudge Orange hue slightly
  toward yellow for warmth (+5 to +15 at most), keep Orange saturation in check, and lift
  Orange luminance for glow ([Presetpedia](https://presetpedia.com/indian-wedding-lightroom-presets/)).
  vivaah does this per photo by steering skin to a target instead of using fixed numbers.

## 3. Outfit and decor colours

| Colour | Risk | Treatment |
|---|---|---|
| **Red / maroon lehenga, sindoor** | turns orange; clips into a flat blob | Red hue slightly toward magenta (−5 to −10); Red luminance down a touch; moderate saturation. Never push red saturation to the point where fabric texture disappears. |
| **Pink / magenta / rani** | goes neon; clips under LEDs | Magenta saturation −10 to −25 at night; check texture in the folds |
| **Gold, zari, jewellery** | goes greenish-yellow or dull | gold sits between Orange and Yellow: Yellow hue slightly toward orange; modest Yellow saturation; warm highlights |
| **Emerald / bottle green (mehendi)** | foliage-muting presets turn it olive | protect very saturated greens: vivaah detects large saturated outfit areas and reduces green shifts for that photo |
| **Haldi yellow** | auto white balance neutralises it; clipping on turmeric-covered skin | keep the ambience (don't fully neutralise); highlights down; skin glow via Orange luminance |
| **Ivory, white, pastels, sherwanis** | blown highlights; yellow or blue whites | highlight roll-off; white point slightly below 255; neutral-to-ivory whites |
| **Marigold and orange decor** | competes with skin (same hue band) | slightly reduce Orange saturation where decor dominates; separate skin by luminance |

## 4. Event by event

| Event | Light | Mood | vivaah look |
|---|---|---|---|
| Haldi | bright day, yellow decor | joyful, golden, glowing | `haldi-sunshine` or `signature` |
| Mehendi | afternoon, shade, green decor | fresh, playful | `mehendi-emerald` or `signature` |
| Sangeet | stage LEDs, purple/blue/green spill | glam, energetic, deep blacks | `night-glam` or `signature` (auto night variant) |
| Wedding / pheras | often night: tungsten, fire, mandap lights | warm, sacred, rich reds and gold | `royal-heritage` or `signature` (auto warm-indoor variant) |
| Vidaai | day or night | emotional, soft | `portra-film`, `true-colour` |
| Reception | stage lights, sequins, backdrops | polished, glamorous | `night-glam`, `cinematic-editorial` |
| Pre-wedding | golden hour, locations | editorial, cinematic, creative | `signature`, `cinematic-editorial`, `pastel-fine-art` |
| Family formals, rituals | anything | accurate, clean | `true-colour` |

## 5. Difficult light

- **Mixed tungsten, LED and daylight:** balance for the faces, then accept some colour
  in the background. vivaah weighs white surfaces, skin and edge statistics, and keeps
  part of the warmth of warm light (`ambience_keep`), because fully neutral candle light
  looks lifeless.
- **Stage LEDs:** fix skin first. Reduce purple, blue and green saturation in the
  background and keep blacks deep. `night-glam` is built for this, with strong skin
  protection.
- **Green spill** from lawns and hedges: add magenta tint and lower Green and Yellow
  saturation. vivaah's foliage steering handles the background.
- **Blue shade versus warm sun** in the same sequence: sequence consistency matches
  white balance across frames shot under the same light.

## 6. Consistency across photographers and cameras

Cameras render differently:

- Some brands lean warm/magenta on skin, others greener or yellower.
- Some cameras make greens neon.
- Camera auto white balance drifts from frame to frame.

vivaah handles this in three ways:

1. It **normalises per photo**. Foliage, sky and skin are steered to the look's
   targets, measured on each frame.
2. It **matches white balance across a sequence** of raw files: same camera, same scene
   type, taken within 5 minutes of each other.
3. It uses **one set of targets per look**, so every frame is aimed at the same place.

## 7. What automation should leave to you

- Hero-image retouching: skin smoothing, blemishes, stray hair, flyaways (Lightroom's
  Remove tool, Photoshop, or a retoucher).
- Local light: faces, eyes, subject masks (Lightroom's *Select People* masks are
  excellent for this).
- High-ISO reception photos: run Lightroom's AI Denoise on keepers.
- Crop and straighten.

## 8. Reading the numbers vivaah uses

- **L\*** (0–100): perceived brightness. Faces usually look best around 55–70; bright,
  airy looks go higher and moody ones lower.
- **OKLab hue / chroma:** a modern perceptual colour space. Hue is the colour angle and
  chroma is colourfulness. Examples: olive foliage ≈ hue 105–112°, chroma ≈ 0.06; skin ≈
  hue 50–56°, chroma ≈ 0.06–0.08.
- **Mireds** (white balance): 1,000,000 / Kelvin. A shift of 10 mireds looks equally
  large at any colour temperature, which is why vivaah uses mireds internally.

Sources: [Presetpedia – Indian wedding presets](https://presetpedia.com/indian-wedding-lightroom-presets/),
[Fotober – wedding video trends 2026](https://fotober.com/best-wedding-video-trends-2026),
[Rashika Mittal – 2026 wedding colour trends](https://rashikamittal.com/blogs/journal/2026-wedding-colour-trends-pastels-jewel-tones),
[Imagen – AI wedding editing](https://imagen-ai.com/valuable-tips/ai-wedding-photo-editing/),
[Aftershoot – batch editing 2026](https://aftershoot.com/blog/best-software-for-batch-editing-wedding-photos/).
