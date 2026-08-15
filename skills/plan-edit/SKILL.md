---
name: plan-edit
description: Turn a folder of raw footage plus a rough brief into an agreed beat sheet and then a cutlist config. Use when someone points at footage and describes the video they want, when an edit has to be planned before anything is rendered, when trims and timecodes need to be found in long takes, when PLAN.md needs writing or revising, or when picking up an edit that was planned in an earlier session.
argument-hint: "<footage dir> [brief]"
allowed-tools: Bash, Read, Write, Glob
---

# Plan the edit

This is the step that decides whether the video is any good. Everything after
it is mechanical: the engine will faithfully render a bad plan, quickly, and
verify that it rendered exactly the bad plan you gave it.

The output of this skill is an **agreed beat sheet** and a config. Not a
render. Do not build until step 5.

## 1. Get the schema, do not recall it

```bash
cutlist prompt config
```

**Follow what that prints, exactly.** Unknown keys are errors, never ignored,
so a key spelled from memory does not degrade gracefully - it fails the whole
config. The contract is versioned with the parser; a remembered copy sends you
to fields that no longer exist.

Three things it will tell you that shape the plan rather than the syntax:

- Times are **seconds**. Levels are always `_db`.
- Segment duration is declared **once**: either `duration_s`, or derived from a
  clip layer's `in`/`out`. Decide per beat which one governs, and record that
  in the plan. A graphic layer is fitted to its segment by `time_fit`, so a
  graphic cannot drift out of sync with its shot - that class of bug is
  removed, not validated against.
- A redaction box has **constant** w/h. Only position interpolates (`box` to a
  two-element `to`). Passing a four-element `to` is a hard error on purpose:
  crop resolves its dimensions once at graph init, so an animated size does not
  render approximately - it does nothing at all and reports nothing. Plan one
  box large enough for the whole travel.

## 2. Ingest first, and read the geometry

Never design a layout before you know whether the picture fills the frame.

```bash
cutlist init --profile 1080p30      # only if there is no config here yet
cutlist ingest --json
```

`ingest` probes every source the config references, so before the edit exists
you still need something for it to probe: a stub timeline with one segment per
source, whole clip, no effects. That stub is throwaway - its only job is to
make the footage visible to the prober.

Then **read `_cut/geometry.json`**. Report it back as a table. These fields
change the plan:

| Field | What it decides |
|---|---|
| `display` vs `coded` | The size a viewer sees, rotation and sample aspect already applied. Plan against `display`. A portrait phone take is frequently coded landscape. |
| `placement.pillarboxed` / `letterboxed` | Whether there are rails at all. See step 6. |
| `placement.rails` | The named empty regions wide enough to hold a legible label. An absent rail is not a narrow rail - it is not offered. |
| `anchors` | The names you are allowed to position graphics against. See step 6. |
| `is_vfr` | If true, no timestamp taken from that source is a promise. |
| `has_audio` / `is_image` | Whether a beat can carry its own sound, and whether `duration_s` is mandatory. |
| `duration_s` | The outer bound on every in/out you are about to choose. |

On a variable-rate source, use sheets for coarse ranges and then confirm the
decisive frame from the **render** after the first build (`cutlist measure`).
Source coordinates and source timestamps do not survive conform, and a plan
that treats them as exact produces trims that land a few frames off in a way
nobody can reproduce.

## 3. Beat mapping with contact sheets

```bash
cutlist sheets --clip NAME --fps 1 --tile 6x5
```

**Derive timecodes, do not guess them.** In a `CxR` tile, cells are filled
left to right then top to bottom, so cell `(row, col)` zero-indexed is index
`row*C + col`, and:

```
time = --from  +  (row*C + col) / fps      seconds
```

At 1 fps in the default 6x5 tile that is `row*6 + col` seconds from the
start of the window. Cell (3, 4) is 22 s. With `--fps 3 --from 120`, cell
(2, 1) is `(2*6 + 1)/3 = 4.33` s into the window, so 124.33 s.

Two passes, always in this order:

| Pass | Command shape | What it gives you |
|---|---|---|
| Coarse | `--fps 1` over the whole clip | Which minute the beat lives in, and a rough in/out. |
| Fine | `--fps 3 --from X --to Y` bracketing the moment | The frame you actually cut on. |

Reaching for the fine pass first burns a large sheet on a clip whose useful
range you have not established. Skipping it leaves every in/out a full second
uncertain, which is visible on any cut into motion.

Record the sheet path and the cell you read in the plan. A timecode that was
guessed cannot be re-derived by anyone, including you, and the first trim
revision restarts the search from nothing.

## 4. Write PLAN.md before you write config

Two parts, in this order in the file: the revision log, then the beat sheet.
The log is first because that is what a resuming reader hits first.

````markdown
# PLAN - <project>

## Revisions (newest first)

### 2026-08-15 - tighten the opening
Asked: the open drags, lose about ten seconds before the first cut.
Changed: beat 1 out 41.0 -> 33.0 (confirmed, sheet 3x from 24s, cell (1,3));
beat 2 unchanged; total 04:12 -> 04:04.

### 2026-08-15 - first plan
Asked: a four-minute walkthrough from the two takes in media/.
Changed: created beats 1-9 from a 1 fps sheet of each take.

## Beat sheet

| # | Beat | Source | in | out | Must show | Layout | Conf |
|---|---|---|---|---|---|---|---|
| 1 | Open on the workspace | take_a | 12.0 | 33.0 | The subject arriving at the desk | full frame | confirmed |
| 2 | The document | take_b | 96.0 | 108.5 | The page readable, face not | picture fills, redact box travels with the page | estimate |
| 3 | Close | take_a | 402.0 | 418.0 | Room settles, no speech over the last 2 s | title card on `rail_right` | guess |
````

Confidence marker per row, one of three, and they mean exactly this:

| Marker | Means |
|---|---|
| `confirmed` | Read off a named sheet cell or a measured frame. The evidence is cited in the row or the log. |
| `estimate` | Derived from a sheet but not frame-checked, or inherited from the brief and consistent with what the sheet shows. |
| `guess` | Nothing in the footage has confirmed it yet. |

Never silently promote a marker. A row that was never checked still reads
`guess` at delivery, and that is the point: it is the list of things to look
at first when the cut feels wrong.

Rules for the log, each one preventing a specific failure:

1. **Append at the top, newest first.** A resuming agent reads only the top
   entries to learn current state. Appending at the bottom makes current state
   the most expensive thing in the file to find.
2. **Each entry records what was asked and what changed.** In the requester's
   words for the ask, in field-level terms for the change. "Tightened the
   open" does not tell the next reader which beat moved or by how much.
3. **Never rewrite an older entry.** The log is the only record of why a trim
   is where it is; editing it to match the current cut destroys the reason.
4. **The top entry must stand alone.** Someone who reads only it should be able
   to describe the current cut. If it cannot, the entry is incomplete.

## 5. Propose the beat sheet, get agreement, then generate

Present the beat sheet and the layout intent, and say plainly which rows are
`guess`. **Wait for agreement before writing config.**

A disagreement about beat 3 costs one line in a table now. Discovered after
config, conform and assemble, it costs a rebuild - and worse, a reviewer
handed a rendered video argues about the render. They comment on the grade and
the music while the structural problem, which is the only thing that was
actually up for discussion, goes unmentioned.

Once agreed:

```bash
cutlist lint                # strict; unknown keys are errors
cutlist doctor              # gates on what THIS config now demands
cutlist build --graph-only  # inspect _cut/graphs/<id>.filter before spending render time
```

Then build for real, and verify.

## 6. Portrait in a landscape canvas: the rails are the surface

When `placement.pillarboxed` is true, most of the frame is not picture. The
instinct is to treat that as waste. It is the opposite: the side rails are the
only part of the frame where an annotation can sit without covering the
subject, and a video that uses them deliberately reads as composed rather than
cropped.

So plan the rails as content. A section label, a running caption, a chip that
names what is on screen - these belong in `rail_left` or `rail_right`, not
floating over the subject's hands.

**Reference anchors by name. Never write a literal pixel pair.**

| Do | Do not |
|---|---|
| `rail_right`, `rail_left_top`, `picture_top`, `lower_third`, `canvas_center` | any `[x, y]` you read off a frame |

A graphic anchored to `rail_right` follows the layout when the canvas profile
changes or the footage is replaced with a source of a different aspect. One
anchored to a pixel pair is locked to one shoot - and nothing errors when it
stops being right. The label lands on the subject's face, in a render that
exits zero.

If `rails` is empty, the picture fills the frame. Then the annotation surface
is `lower_third`, or a held segment with a still or `color` layer under the
text - not a rail you wished were there.
