---
name: redact
description: Obscure part of the picture - a face, a screen, a document, a badge, a name, a passer-by - with mosaic or blur, tracking it while it moves, and prove the coverage frame by frame. Use when asked to blur, mosaic, pixelate, obscure, hide, redact or anonymise something in a video, when a shot cannot ship until part of it is unreadable, or when a subject who did not consent walks through frame.
argument-hint: "<what to obscure> [segment-id]"
allowed-tools: Bash, Read, Edit, Glob
---

# Obscure a region, and prove it stayed covered

A redaction is not finished when it renders. It is finished when the subject is
covered in every frame of the window and you are holding frames that show it.
The failure this work exists to prevent is narrow and specific: coverage that
holds for most of a window and slips for a handful of frames. Nobody watching at
speed sees it. Anyone who pauses does.

## 1. Get the contract, do not recall it

```bash
cutlist prompt redact
```

Follow what that prints, exactly. It is versioned with the engine and names the
exact keys. A remembered copy either sends you to keys the strict schema
rejects - which at least errors - or to keys it accepts with a different
meaning, which does not.

## 2. Measure on the render, never on the source

Build first. There is nothing to measure until a frame exists at the geometry
the deliverable will actually have.

```bash
cutlist build --only <segment-id>
cutlist measure <segment-id> --at 4.20 --grid
```

`measure` extracts that timestamp from the built shot, overlays a labelled pixel
grid and writes it to `_cut/evidence/frames/`. Read `x`, `y`, `w`, `h` off the
grid and write them straight into config - they are already canvas coordinates.

For a tight read, crop in and magnify:

```bash
cutlist measure <segment-id> --at 4.20 --grid --crop 640,280,640,360 --zoom 2
```

**Opening the source clip in a viewer and reading coordinates there is wrong**,
for two independent reasons, either of which is enough on its own:

| Why source coordinates are wrong | What you get instead |
|---|---|
| Variable frame timing. Phone and screen-capture sources carry irregular timestamps. Seeking such a file to `t` lands on whatever frame its timestamps put there, which is not the frame the conformed constant-rate copy shows at `t`. | You measure one frame and cover a different one. The error is invisible on a still subject and dead obvious on a moving one. |
| Placement. The layer is scaled and positioned onto the project canvas by its `fit` policy, and rotation is baked in at conform. Source pixels live in a different grid, often a different aspect, sometimes a different orientation. | Coordinates that are internally consistent and land nowhere near the subject. |

Conform exists to retire both problems: after it, everything is frame-indexed
against the timeline. `measure` reads from that side of the line. Stay on it.

## 3. Write the entry

```yaml
timeline:
  - id: desk-wide
    redact:
      - box: [812, 344, 260, 180]   # x, y, w, h - canvas pixels
        from: 0.00                  # window start, seconds
        to_s: 1.20                  # window end, seconds
        mode: mosaic
        margin_px: 24
```

The key names carry one trap worth stating outright:

| Key | Means | Note |
|---|---|---|
| `box` | `[x, y, w, h]` | Start position, plus the size for the whole entry |
| `to` | `[x, y]` | **End position only.** Two numbers. Never four. |
| `from` / `to_s` | Window start / end, seconds | Times are segment-relative and start at 0 for every segment. `to` is taken by the position, which is why the end time is `to_s` |
| `margin_px` | Grown on all four sides, then clamped to canvas | Default 16 |
| `mode` | `mosaic` or `blur` | |
| `strength` | Cell or radius size | Omit it; derived from the region |

**Size is constant for the life of an entry. Only position interpolates.**
Passing a four-element `to` is a hard error with an explanation attached,
deliberately, because the alternative is worse than an error: `crop` resolves
its width and height once when the filter graph is initialised, so an animated
size does not render slowly or approximately - it does nothing at all, and
reports nothing. You would ship a redaction that silently ignored half of what
you wrote.

For a subject that grows or shrinks in frame - walking toward the camera, a
push-in - use consecutive entries, each with its own constant box, or one box
generous enough to cover the largest it gets.

## 4. Track motion with several short entries

One entry draws a straight line between two points. A real subject does not
travel in a straight line, and a fast one certainly does not. Over a long entry
the cover either drifts off the subject in the middle, or needs a box so large
it obscures half the picture.

Consecutive short entries approximate the real path with chords. `margin_px`
absorbs what is left - the gap between the chord and the curve.

**Worked example.** A subject crosses frame during a 1.2 s pan. As one entry it
is a single chord, and mid-window the subject sits well off it. Split it into
four consecutive ~0.3 s entries, measuring at each junction:

```bash
cutlist measure pan-wide --at 0.00 --grid
cutlist measure pan-wide --at 0.30 --grid
cutlist measure pan-wide --at 0.60 --grid
cutlist measure pan-wide --at 0.90 --grid
cutlist measure pan-wide --at 1.20 --grid
```

```yaml
    redact:
      - {box: [300, 240, 220, 260], to: [610, 250],  from: 0.00, to_s: 0.30, margin_px: 28}
      - {box: [610, 250, 220, 260], to: [930, 268],  from: 0.30, to_s: 0.60, margin_px: 28}
      - {box: [930, 268, 220, 260], to: [1240, 286], from: 0.60, to_s: 0.90, margin_px: 28}
      - {box: [1240, 286, 220, 260], to: [1500, 300], from: 0.90, to_s: 1.20, margin_px: 28}
```

Every entry starts where the previous one ended, in both position and time. A
junction that does not match leaves either a gap in coverage or a visible jump
in the cover.

Faster motion needs more entries, not longer ones:

| Subject | Entry length | Reason |
|---|---|---|
| Static or drifting | The whole window, one entry | Nothing to chase |
| A walk across frame | 0.5 - 1.0 s | Path curvature is small over that span |
| A hand pass, a pan, a turn of the head | 0.2 - 0.3 s | Direction changes inside anything longer |
| A whip pan, a subject crossing in under half a second | Stop. See section 6. | Piecewise tracking has nothing to interpolate between |

**Make boxes generous.** A box sized exactly to the subject is a box that fails
the moment the subject moves, turns, or the camera does anything. Size to the
subject plus the room it needs, then let `margin_px` cover the tracking error.
A margin of roughly a fifth of the box's shorter side is a sound starting point;
raise it rather than adding entries when the subject is slow but the box is
tight. Picture obscured beyond what was needed costs a little framing. Picture
not obscured costs the deliverable.

## 5. Verify. This is mandatory, and it is specific

```bash
cutlist verify --boundaries
```

**Interpolated geometry fails at the edges of its window, not in the middle.**
The middle is where a linear interpolation is at its best and where the box is
by construction closest to where you measured. A mid-window spot check is
therefore close to worthless: it is the one sample guaranteed to look right.

`--boundaries` samples the frame just inside and just outside every window and
stacks them into `_cut/evidence/boundaries/`. Read each stack for two things:

1. The inside frame is covered - the window starts early enough and ends late enough.
2. The outside frame shows the subject where you expect it - which confirms the
   window sits on the frames you think it does, and is not one frame short at
   either end. One exposed frame at 30 fps is 33 ms of video and a permanent
   still to anyone who pauses.

Then read the crop-band montage `cutlist verify` writes to
`_cut/evidence/montage/`: the redacted band alone, sampled across the window and
laid out as a strip. It is the deliberate complement of the boundary stack -
where that shows the two edges, this shows everything between them, and drift
reads instantly as the subject sliding out from under the cover. Run both.

**Never judge coverage on a flat region.** A pixelated blank wall and a sharp
blank wall are the same pixels. If the samples you looked at were all flat, you
have proven nothing at all. Aim the check at the detail that had to go - text,
a face, a number - and sample where that detail is.

`cutlist verify` exits 2 when verification fails. A 2 is not a warning and is
not a finished job. Machine-readable results land in `_cut/report.json`.

## 6. When it cannot be covered safely, say so

Some motion is beyond piecewise tracking: a subject crossing frame in a handful
of frames, a direction reversal inside the shortest entry you can measure, a
subject repeatedly occluded and re-emerging somewhere unpredictable. In those
cases tracking either leaves frames exposed or needs a box so large that it is a
different edit anyway.

Say that plainly, and offer the alternative rather than shipping the tracked
version and hoping:

| Alternative | When it is the right call |
|---|---|
| One static box over the whole region the subject travels through, for the whole window | The subject stays within one area of frame. Costs picture; cannot fail. |
| A full-width band across the subject's path | Motion is horizontal and unpredictable, and the band does not eat the point of the shot |
| Cut the shot, or trim the window out | The exposure is short and the shot is not load-bearing. Usually the shortest honest fix. |
| Hold a still, or cut away, over the passage | The audio carries the moment and the picture does not need to |

Never ship a redaction you know exposes the target for some frames because it
looks covered when scrubbed. Report which frames, and let the decision be made
with that on the table.

## mosaic or blur

| Mode | Reads as | Needs | Watch for |
|---|---|---|---|
| `mosaic` | Deliberate. Obviously an edit, which is usually what you want - the viewer knows something was removed rather than wondering if focus slipped. | `pixelize`, ffmpeg >= 6.0 | Too fine a cell leaves a subject recognisable |
| `blur` | Softer, less conspicuous. Can be mistaken for a focus miss. | `boxblur`, available at the 4.3 floor | A light blur leaves large text legible. Verify against the text, not the region. |

Leave `strength` at its default and it is derived from the region - roughly a
twelfth of the shorter side, floored at 4. A fixed cell size is wrong in both
directions: too fine on a large region leaves detail intact, too coarse on a
small one is a solid block that looks like a rendering bug.

`cutlist doctor` gates only on what this config demands, so a blur-only config
runs on the floor build. If mosaic is not available, either switch that entry to
`blur` and verify harder against the detail, or point `CUTLIST_FFMPEG` at a
newer build. Run `doctor` before `build`, not after a failed render.

## Rules that govern

1. **Build, then measure on the render.** Prevents coordinates read against a
   different frame, in a different pixel grid, from the one that ships.
2. **One entry, one constant size.** Prevents an animated `w`/`h`, which renders
   as no change whatsoever and raises no error.
3. **Several short entries for motion, generous boxes, margin for the rest.**
   Prevents the cover drifting off the subject between measured points.
4. **Boundaries before middles, then the band montage.** Prevents an edit that is
   covered everywhere you looked and exposed where you did not.
5. **Aim verification at detail, never at a flat area.** Prevents "it looks
   blurred" from standing in for evidence it worked.
6. **Report exposure instead of shipping it.** Prevents a deliverable that reads
   as finished and is not usable.
