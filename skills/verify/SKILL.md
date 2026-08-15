---
name: verify
description: Prove a rendered cut is what was asked for, using contact sheets, grid-annotated frames, boundary stacks, crop-band montages, probe assertions and luma and silence checks. Use after any build or re-render, before reporting a video as finished or handing it to a reviewer, when asked whether a redaction actually covers its subject, where a beat falls in a source, what pixel coordinate something sits at, whether the edges of a windowed effect hold, or whether a track follows across a whole window.
allowed-tools: Bash, Read, Glob
---

# The gate

This is where a build stops being a claim. Nothing here judges taste; every
instrument answers one question and hands you an image or a number.

## 1. Get the contract, do not recall it

```bash
cutlist prompt verify
```

Follow what that prints. It is versioned with the engine and names the fields
in `_cut/report.json` that this version writes.

## 2. Why the gate exists at all

Of 53 recorded failures on real footage, 40 were defects and 13 were quality
problems. **Of the 40 defects, 31 produced silently wrong output and only 9
crashed.** More than three in four never announced themselves: the command
exited 0, the file played, and the mistake was found by looking at a frame.

A pipeline whose failures crash needs good error messages. A pipeline whose
failures are silent needs instruments. That is the whole argument, and it is
why a failed check exits `2` rather than `1` - "ffmpeg refused" and "ffmpeg
produced a file that is not what you asked for" are different events, and only
the second one looks like success from the outside.

## 3. The instruments

| Instrument | Command | The question it answers |
|---|---|---|
| Contact sheet | `cutlist sheets --clip NAME --fps 1 --tile 10x6 --from 0 --to 120` | **Where is the beat?** One image per source window, cell to timecode stated in the filename and printed by the command, because burnt-in timecode may not be available. This is how you find a trim point in a long source without scrubbing. |
| Grid reader | `cutlist measure SEG --at 4.5 --grid --crop 600,300,480,320 --zoom 3` | **What pixel is that at?** A zoomed crop of a *rendered* frame with a grid burnt over it. Read coordinates straight off the image. |
| Boundary stack | `cutlist verify --boundaries` | **Does the window's edge hold?** The frames just inside and just outside every in and out point of every windowed or interpolated effect, stacked into one image. |
| Band montage | `cutlist verify` → `_cut/evidence/montage/` | **Does the track follow?** The same crop sampled across a window and stacked, so a whole trajectory is one read. |
| Probe assertions | `cutlist build`, results in `_cut/report.json` | **Did I ship what I asked for?** Codec, dimensions, `avg_frame_rate` and `r_frame_rate`, frame count and duration against the spec's timeline minus transition overlap, per-stream bit rate. |
| Luma and silence checks | `cutlist verify --audio` | **Is anything actually there?** Luma statistics catch a frame that is black because a layer never composited; silence detection catches a shot with no audio path to the output, or a mute that swallowed more than its window. |

Everything above is band 1: ffmpeg and ffprobe, nothing installed, nothing
downloaded. Evidence lands under `_cut/evidence/` in `sheets/`, `frames/`,
`boundaries/` and `montage/`, and the machine-readable results in
`_cut/report.json`.

## 4. Measure in output space, never in the source

`measure` reads the **render**. Source coordinates do not survive conform: a
variable-rate source reports a nominal frame rate that differs from its real
average, so the same timestamp lands on a different picture; a rotated or
portrait source is pillarboxed into the profile, so its own pixel grid is not
the grid the output uses. Authoring a box against a source frame is how a
redaction ends up around a hundred pixels from its subject while every number
in the spec looks deliberate.

Conform first, then measure. That ordering is the point of the conform stage.

## 5. The montage idea, stated properly

Take one crop rectangle - the region the effect is supposed to cover. Sample it
from the render roughly every 0.15 s across the window. Stack the strips
vertically into one tall image. Time now runs down the page, and a whole
trajectory is a single image read.

That is the entire trick, and it matters for one reason: **a reviewer, human or
model, can afford a handful of image reads per pass.** Inspecting a five-second
window honestly is on the order of a hundred and fifty frames. Nobody has that
budget, so verification quietly degrades into two spot-checks in the middle of
the window - which is exactly where linear interpolation is most likely to be
right. Turning an O(N)-frames inspection into one image is what makes
fine-grained checking affordable, and affordability is what decides whether it
happens at all.

Read in this order, because each step narrows the next:

1. **Montage** - does the coverage hold across the window, and where does it
   start to drift?
2. **Boundary stack** - do the first and last frames hold? Interpolation is
   exact at its endpoints and rounding is exact in the middle; the two failure
   modes meet at the boundary, an off-by-one shows only there, and it is where
   nobody looks.
3. **Grid reader** at the worst moment the montage showed - what is the actual
   coordinate, and by how much is the spec wrong?

## 6. The completion contract

Non-negotiable, in order:

1. **You must READ the evidence images.** Use the Read tool on the actual PNG
   paths. Listing filenames is not reading them; `cutlist verify` prints paths
   precisely so they can be opened.
2. **You may not claim a build is done on an exit code.** Exit 0 means ffmpeg
   was satisfied. The encoder exits 0 on graphs that produce black, silent or
   mis-ordered output. Correctness of the graph and correctness of the picture
   are different properties and only one of them is machine-checkable; video
   work has no unit tests, and sampled visual inspection is the primitive that
   replaces them.
3. **Report what could NOT be checked, by name and with the reason.** Silence
   about an unrun check reads as "no problems found", which is the worst thing
   this tool can say. A reader who is not told cannot distinguish "the
   redaction holds" from "nobody looked at the redaction", and neither can you
   on the next pass.
4. **Mark the epistemic status of every value you report** - confirmed by
   measurement, inferred from evidence, or unverified. Declare up front the
   classes that can never be confirmed without a human: all trim points and all
   beat timings. That label cannot be reconstructed later.

## 7. What each instrument cannot see

State these when they apply. They are the difference between a gate and a
rubber stamp.

| Instrument | Blind to |
|---|---|
| Probe assertions | The picture. A missing layer, a substituted font, an off-palette grade and a caption pointing at nothing all probe perfectly. |
| Contact sheet at 1 fps | Anything shorter than a second - a one-frame flash, a single dropped frame, a redaction that opens late. |
| Boundary stack | The middle of the window. It is deliberately the complement of the montage; run both. |
| Luma statistics | Intent. A correctly dark shot and a shot whose only layer failed to composite have the same mean. |
| Silence detection | Content. It hears that audio is present, not that it is the right audio, in the right place, at the right level. |
| `cutlist scan` | Anything not in the serialized spec. Text baked into an asset, a font fallback or a second config is invisible to it, and a naive substring match false-positives on numeric coordinates. |

## Rules that govern

1. **Run the instruments on the render, never on a source.** Prevents a spec
   full of coordinates measured in a pixel grid the output does not use.
2. **Check every windowed or interpolated effect at its boundaries, not in its
   middle.** Prevents coverage that looks perfect mid-window while the first
   and last frames expose exactly what the effect was meant to hide.
3. **Prefer one montage to ten stills.** Prevents honest verification being
   abandoned for two spot-checks because the honest version costs too many
   reads.
4. **Compare against the timeline computed from the spec, including transition
   overlap.** Prevents a correct render being reported as a frame-count failure
   because a 0.5 s dissolve shortened the timeline by 0.5 s.
5. **Delete or date the artifact you are about to re-check.** Prevents a
   leftover image from a previous round making a broken toolchain look green
   forever. Freshness, not existence.
6. **Treat exit 2 as a verdict, not a crash.** Prevents a failed check being
   reported to a human as a tool problem, or retried until it passes by
   accident.
7. **Never summarise evidence you did not open.** Prevents the failure this
   entire skill exists to catch: a confident report about a file nobody looked
   at.

## Close with this block, every time

Its value is that it is identical between runs, so a reader can glance rather
than read.

```
VERIFIED    <what was checked, one line each, with the evidence path>
NOT CHECKED <what was not, and why - never omit this section>
STATUS      pass | FAILED (exit 2) | unverified
BUILT       <variant> -> <output path>
```

If the NOT CHECKED section is empty, say so explicitly rather than deleting the
heading.
