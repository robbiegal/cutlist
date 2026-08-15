---
name: build-graphics
description: Build static on-screen text for a cut - title and section cards, lower thirds, captions and label chips - rendered with Pillow to PNG with alpha and composited as a normal layer. Use when a video needs a title card, a name strip, a caption, a label on a subject, or any words on screen; when asked why drawtext is unavailable; or when text is illegible, mispositioned, or gone before it can be read. Animated motion graphics are not in this version.
allowed-tools: Bash, Read, Write, Glob
---

# On-screen text

**This is band 2, and band 2 is static text only.** Cards, lower thirds,
captions and label chips. Animated motion graphics - anything that moves,
counts, wipes, draws itself or transitions on - are band 3 and **not in
v0.1**. Say that plainly when someone asks for one, rather than approximating.

Do not fake motion by emitting a still per frame and layering them: it defeats
the content-addressed cache, it drifts against its own segment, and the result
is worse than the static version it replaced.

## 1. Install the band, and only this band

```bash
pip install 'cutlist[text]'    # Pillow, about 3 MB, no system libraries
cutlist doctor                 # reports whether this config's bands are satisfied
```

Bands install on demand and never all at once. A project with no text scene
never needs this one.

## 2. Get the contract, do not recall it

```bash
cutlist prompt text
```

Follow what that prints for the scene schema. Unknown keys are a hard error,
never an ignored line, so a remembered field name fails the build rather than
silently doing nothing.

## 3. What exists

| `kind` | Is | Default placement |
|---|---|---|
| `card` | A full-frame slate with kicker, title and subtitle centred on an opaque ground. There is nothing behind it, so it is not transparent. | Centre of canvas |
| `lower_third` | A name strip: title over subtitle, on a scrim, with an accent rule down its left edge. | `lower_left` |
| `caption` | A line of text on a scrim, sized to its content. | `bottom_center` |
| `chip` | A short label, same construction at label size. | `lower_left` |

A scene is declared once under `scenes:` and used by any number of segments as
a `video_layers` entry with `kind: scene`, its `name`, a `z`, an `opacity` and
a `time_fit` of `trim`, `loop`, `stretch` or `once`. The rendered PNG is cached
by scene content plus theme, so reusing a lower third across ten segments costs
one render.

**Segment duration is declared once** - either `duration_s` or derived from a
clip layer's in/out - and a graphic is fitted to its segment by `time_fit`. A
graphic and its shot therefore cannot drift out of sync. That whole class of
bug is removed rather than validated against, so do not hand-author a duration
for a scene.

## 4. Why Pillow, and not the two obvious alternatives

**Not `drawtext`.** It requires ffmpeg to have been built with libfreetype, and
that is not something you can ask a user to check. One widely distributed build
ships 490 filters and `drawtext` is not among them - measured, not assumed. A
text feature that is absent on a large share of installs is not a text
feature, and the failure arrives as an unknown-filter error in the middle of a
render.

**Not a browser.** Animated graphics genuinely need one; a title card does not.
Asking someone to download 150 MB of Chromium to put a word on screen is the
kind of dependency that decides a tool is not worth installing.

Pillow bundles FreeType on all three platforms, needs no system libraries, and
renders to PNG with alpha that composites like any other layer.

## 5. Anchor to named layout anchors, never to a pixel pair

`cutlist ingest` probes every source and writes `_cut/geometry.json`: where the
picture lands in the canvas, and the rectangles that are **not** picture. Those
leftover rails are not waste. When a portrait source sits in a landscape
canvas, the side rails are the only part of the frame where a label can sit
without covering the subject, and a video that uses them deliberately looks
composed rather than cropped.

| Anchor family | Points |
|---|---|
| Canvas | `canvas_center`, `canvas_tl`, `canvas_tr`, `canvas_bl`, `canvas_br` |
| Picture | `picture_center`, `picture_top`, `picture_bottom` |
| Convention | `lower_third` |
| Rails | `rail_left`, `rail_right`, `rail_top`, `rail_bottom`, each with a `_top` and `_bottom` variant |

Text kinds take an `anchor` of `lower_left`, `bottom_center`, `top_left` or
`top_right` plus a `margin`, and place themselves. When a layer genuinely needs
a position, take it from the anchors in `_cut/geometry.json` and re-read it
whenever the source set changes.

A rail narrower than one eighth of the canvas width (and never under 160 px) is
not offered as somewhere to put a label, because it cannot hold a legible line
without hyphenating. If no rail is offered, the picture fills the frame and
text must go over it - which is what the scrim is for.

**Never write a literal coordinate pair measured once by hand.** A layout
anchored to `rail_left` follows the canvas and survives a source being
replaced. One anchored to `(330, 250)` silently stops being true the moment a
source of a different aspect arrives, and every graphic is then wrong with no
error anywhere.

## 6. Legibility

Text sits over footage nobody controls, so legibility is a construction
problem, not a styling one.

1. **A semi-opaque neutral scrim behind the text beats a coloured glow over
   the picture.** A glow is a decoration that competes with whatever is behind
   it; a scrim replaces what is behind it. Every non-card kind gets one.
2. **Contrast is checked against the scrim, not against the footage.** The
   footage may be anything, including a frame that has not been shot yet, so a
   contrast figure measured against it is a figure about one frame. The scrim
   is the only surface whose colour is known.
3. **Defaults are achromatic** so the plugin imposes no colour identity. Set
   `theme` wholesale for a project: `ink`, `ink_muted`, `scrim`, `accent`,
   `pad`, `radius`, `base_px`.
4. **Pin a font file when output must be reproducible.** The system stack
   renders correctly on a fresh machine with no download and no licence
   question, which is the right default - but it is not deterministic across
   machines. Different metrics mean different line breaks, so the same config
   produces a different-looking card on a colleague's laptop.

## 7. The readability policy: text accumulates and holds

**"It disappeared before I could read it" is the single most common review note
on annotated video.** Design against that first and everything else second.

| Rule | Prevents |
|---|---|
| A line stays up until the thing it annotates is over. | A viewer choosing between reading the label and watching the picture, and losing both. |
| When a second point arrives, add it - do not swap the first one out. | A reader who was mid-sentence when the sentence was replaced. |
| Nothing appears and vanishes inside one beat. If it is worth showing, it is worth holding to the end of its segment. | Flashed text, which reads as a rendering glitch rather than as information. |
| One idea per line, and the shortest wording that survives paraphrase. | A three-line caption nobody finishes before the cut. |
| A card gets its own segment. | A title fighting the footage underneath it for attention. |

Use `time_fit: trim` for a graphic that should live exactly as long as its shot
and `loop` only for a graphic whose cycle divides the segment cleanly.

## 8. Two composition facts that surprise people

**Layers composite in ascending `z` and the last one is on top.** There is no
depth model underneath - the order of the composite chain alone decides what
covers what. A full-frame card added at a high `z` covers the caption it was
meant to sit behind.

**Redaction runs before the layers composite.** The shot graph is grade, then
redaction, then composite. A box therefore covers the picture underneath it and
nothing a graphic layer paints on top of it: text over a redaction box stays
sharp, and a string that must not be readable cannot be hidden by moving a box
over it, because the box ran before that text existed. Wording is controlled
where the wording lives, in the spec - section 9.

## 9. Every on-screen string lives in the spec

Put text in the config, never baked into an asset that has to be edited by
hand. On-screen content restrictions are the single most likely requirement to
reverse late, and a late reversal should be a spec edit plus one re-render.
Gate on it:

```bash
cutlist scan
```

driven by `policy.forbidden_strings` and `policy.forbidden_patterns`. State the
gate's limits out loud when you report it: it reads the serialized spec, so
text baked into an asset, a font fallback or a second config is invisible to
it.

## 10. Then look at it

Text is exactly the thing that is correct in the graph and wrong on screen: a
substituted font, a line break that moved, a scrim clipped by a rail edge, a
chip anchored to a rail that no longer exists because the source was replaced.

```bash
cutlist measure SEGMENT_ID --at 3.0 --crop 0,760,960,320 --zoom 2
```

Read the image. Then run the `verify` skill and read its images too - a build
is not finished on an exit code.

## Rules that govern

1. **Say "not in this version" for animation rather than approximating it.**
   Prevents a per-frame still sequence that defeats the cache and drifts
   against its own segment.
2. **Never emit `drawtext`.** Prevents a render that dies with an unknown
   filter on the substantial share of installs whose ffmpeg has no libfreetype.
3. **Anchor to a named anchor, never to a measured pixel pair.** Prevents a
   graphics layout silently locked to one source set.
4. **Put a scrim behind every line over footage, and check contrast against the
   scrim.** Prevents legibility that depends on a frame you have not seen.
5. **Hold text; never flash it.** Prevents the most common review note there
   is.
6. **Never expect a redaction box to hide what a graphic layer draws.**
   Prevents a string that must not ship being composited on top of the box that
   was meant to cover it.
7. **Keep every on-screen string in the config and run `cutlist scan`.**
   Prevents a late content reversal turning into hand edits of rendered assets
   under deadline.
8. **Pin a font when the output must match across machines.** Prevents a line
   break, and therefore a layout, that differs per machine with nothing to
   point at.
