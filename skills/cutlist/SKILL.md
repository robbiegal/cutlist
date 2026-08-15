---
name: cutlist
description: Cut, grade, redact, caption and mix a finished video out of raw footage with ffmpeg, then prove the delivered file frame by frame instead of trusting the exit code. Use whenever someone wants footage edited or put together - a montage, a walkthrough, a demo, a highlight cut - or wants shots trimmed, reordered or retimed, something on screen blurred or pixelated, title cards, section cards, lower thirds or captions added, music laid under speech or distracting audio removed, or a cut they have already watched revised ("it ends too late", "I can't read that", "too many sections").
---

# Cutlist

A video editor in two halves. **ffmpeg measures. You look.**

Everything countable - durations, frame counts, geometry, rotation, variable
frame rate, delivered bitrate, mean luma, silence where a mute was declared -
is computed by the CLI into `_cut/report.json`. Everything that needs a pair of
eyes is rendered into `_cut/evidence/` as images: contact sheets, grid-annotated
frames, boundary stacks, crop-band montages. You read those images and decide
whether the cut is right.

A zero exit code is not verification. ffmpeg exits 0 on a render that is
entirely black - it encoded exactly the frames it was handed, and every one of
them was black. It also exits 0 after silently delivering a different bitrate
than the one requested. "It rendered" is a statement about the encoder, not
about the video.

Exit codes carry the distinction: `0` succeeded, `1` errored, **`2` rendered but
failed verification**. Two is not a warning.

## The working loop

| # | Step | Owner | What lands here |
|---|---|---|---|
| 0 | Check the toolchain against this config | `cutlist-doctor` | nothing - it only reads |
| 1 | Ingest: probe every source, sheet it, pick beats | `plan-edit` | `project` keys including `media_dir`, source names, `in`/`out` candidates |
| 2 | Plan: write the cut list | `plan-edit` | `timeline`, `scenes`, `variants`, `grade`, `audio`, `policy` |
| 3 | Build: conform, shots, assemble, one delivery encode | `render` | nothing - the config is the input |
| 4 | Verify: render the evidence and read it | `verify` | nothing - it only measures |
| 5 | Revise: route what the human said to a key | this skill, then back to 3 | see the routing table below |

Owners are sibling skills in this plugin. `redact`, `audio-pass` and
`build-graphics` are entered from step 2 or step 5 when the change is confined
to one of those passes.

Steps 3 and 4 are separate on purpose. Building is slow and deterministic;
looking is the part that needs your attention. Keeping them apart means a
second look never re-renders, and a rebuild never quietly rewrites what you
concluded.

## Feedback routing

The human describes a symptom. You change a key. Nothing else - never the
delivered file, never a work-directory artifact.

| What they say | What you change |
|---|---|
| "it ends too late", "cut it earlier" | that segment's clip layer `out` (or `duration_s`, when the segment has no clip) |
| "it takes too long to get going" | that segment's clip layer `in` |
| "too many sections" | fold or drop `timeline` entries. Where the difference is which layers appear rather than which shots, tag the layers and add a `variants` entry so both cuts stay buildable |
| "the cuts are too abrupt", "smooth that join" | `timeline[].transition_in.kind` and `.duration_s` |
| "I can't read that" | the scene's own definition under `scenes.<name>` - shorter text, a stronger scrim - and a longer segment `duration_s`. Contrast is measured against the scrim behind the text, not against the picture, so strengthening the scrim is the fix that survives whatever the footage does |
| "the caption is over the wrong thing" | that scene's placement under `scenes.<name>`, and the layer's `z` when something else is drawing over it |
| "the graphic vanishes before the shot ends" | that scene layer's `time_fit` (`trim`, `loop`, `stretch`, `once`). Never a second duration - a graphic has no length of its own |
| "blur the screen", "hide that document" | a `timeline[].redact[]` entry: `box`, `from`, `to_s`, `mode` (`mosaic` or `blur`) |
| "the blur slips off it" | raise `margin_px`, or split into consecutive short entries. Position interpolates `box` -> `to`; size never does |
| "the audio is distracting" | `timeline[].audio.windows[]` with `fade_s`, and a `fill` sampled by `cutlist grain`. Whole shot: `audio.mute` or `audio.gain_db` |
| "the music drowns them out" | `audio.bed_gain_db`, or `audio.duck` with `duck_threshold_db` / `duck_ratio` |
| "the level jumps between shots" | `audio.loudnorm` and `audio.target_lufs` |
| "it looks flat", "too dark", "too orange" | `grade.enabled`, then `grade.eq`, `grade.colorbalance` or `grade.curves` |
| "it looks squashed", "why the black bars" | that layer's `fit` (`contain`, `cover`, `stretch`, `none`) |
| "the file is huge", "it looks soft" | `render.crf`, `render.preset`, `render.abr` |
| "it needs to be square", "it needs to be 25fps" | `project.width` / `height` / `fps` - then rebuild everything, because conform is keyed on the profile |
| "that must not appear on screen" | `policy.forbidden_strings` / `forbidden_patterns`, then `cutlist scan` |

Times are always seconds. Levels are always decibels, always a `_db` key -
never a linear multiplier.

## What a rebuild actually costs

| Stage | Unit | Rebuilds when |
|---|---|---|
| conform | one used range of one source | its `in`/`out` window, the project profile, or the source file changes |
| shot | one segment | that segment's layers, grade, redaction or audio change |
| assemble | the whole timeline | any shot changes, or transitions, the audio bus or `render` change |

The cache key is the resolved spec plus input content hashes plus the engine
and ffmpeg versions. Change one trim and one shot rebuilds. That is why the
answer to feedback is a key, not a re-plan.

## Rules that govern every build

1. **Never claim done from an exit code.** Done means `cutlist verify` ran, the
   assertions in `_cut/report.json` passed, and you read the evidence images.
   Exit 0 with an all-black delivery is a real outcome, not a hypothetical.

2. **Measure coordinates off the render, never off the source.** Use
   `cutlist measure SEGMENT_ID --at S --grid`. After conform the picture has
   been rotated, scaled and placed, and a source second is not a timeline
   second. Source coordinates are wrong by an amount that looks plausible.

3. **Mark every timing you generate with its confidence**, using the same three
   markers throughout: `confirmed` - read off a contact sheet, a measured frame,
   or stated by the human. `estimate` - derived from something confirmed but not
   frame-checked. `guess` - nothing in the footage has confirmed it yet. Put the
   marker in a config comment where the format allows one, and always in what
   you report back. Never promote a marker silently, and never deliver a cut
   containing a `guess` without saying which numbers they are. An estimate that
   reads as a confirmed number is how a cut lands two seconds off with everybody
   confident.

4. **The config is the source of truth, and rebuilding overwrites.** `_cut/`
   and `_out/` are derived and disposable. Never hand-edit an intermediate,
   never patch the delivered file, never fix something outside the config -
   anything not expressed as a key disappears at the next build, usually after
   it has already been approved.

5. **Never rename, move, re-encode or trim the source media.** The media
   directory is read-only. Conform writes its own intermediates; the original
   files stay exactly as delivered, because a re-encode you cannot undo is one
   bad grade away from being the only copy.

6. **An assertion that could not run is not a pass.** Report every `NOT RUN`
   line with its reason. Silence there reads as "no problems found", which is
   the worst thing a verification step can say.

7. **Never paste a contract from memory.** The contracts ship with the code and
   move with it. Run the command and follow what it prints.

## Two schema rules that catch people

**A redaction box has constant width and height.** Only its position
interpolates, `box` -> `to`. Passing a four-element `to` is a hard error with an
explanation, and that is deliberate: `crop` resolves its dimensions once at
graph initialisation, so an animated size does not render slowly or
approximately - it does nothing at all, and nothing errors. Track a subject that
changes size with consecutive entries, or widen `margin_px` to cover the range.

**A segment's duration is declared once** - either `duration_s`, or derived from
a clip layer's `in`/`out`. A graphic layer is fitted to its segment by
`time_fit` policy. There is no second place to state a length, so a graphic and
the shot under it cannot drift apart.

Unknown keys are errors, never ignored. A key that is read and quietly does
nothing is an invitation to spend an afternoon editing it.

## Never paste a contract from memory

```bash
cutlist prompt <name>
```

These name specific fields in `_cut/report.json`, and those fields change when
the measurement changes. A remembered copy tells you to quote a field that no
longer exists, and nothing errors when you do. If a name is unknown, the error
lists what this version actually ships.

## Scope

Works on ordinary camera, phone and screen-recording footage plus stills. The
core band needs nothing but ffmpeg 4.3 or newer and ffprobe: trims, conform,
grade, redaction, audio, transitions, delivery, and every verification
instrument. Static text - title cards, section cards, lower thirds, captions,
label chips - needs the text band, about 3 MB. Mosaic redaction needs ffmpeg
6.0; blur mode works from 4.3.

## Limits

- **Animated motion graphics are not in v0.1.** Text is static. Do not offer
  animation, and do not fake it with a stack of stills.
- **Nothing here understands content.** It cannot find the interesting moment,
  read a face, follow a subject automatically, or transcribe speech. Beats come
  from a contact sheet you looked at or from the human. Redaction tracking is
  linear between two positions you supplied.
- **Redaction is a picture operation, not a guarantee.** It obscures a region
  you specified, for the span you specified, in the variant you built. Verify
  the boundary frames with `cutlist verify --boundaries` and aim the inspection
  at detail - a pixelated blank wall and a sharp blank wall are the same pixels,
  so a crop with no text or edge in it proves nothing.
- **Verification checks the delivered file against the declared intent.** It
  cannot tell you the cut is good, that the right take was chosen, or that a
  clip is upside down when the config asked for it upside down.
- **The delivery is always re-encoded**, never stream-copied. A cut assembled by
  copy plays on some players and not others; one generation at the end is the
  price of a file that plays everywhere.
