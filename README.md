# cutlist

**Turn raw footage and a written brief into a finished video with ffmpeg — then
prove the delivered file is what you asked for, instead of trusting the exit
code.**

Describe the cut as a config: which ranges of which clips, in what order, graded
how, with what obscured, over what audio. `cutlist` conforms the sources, builds
each shot, assembles the delivery, and then measures the file it just produced
against what the config declared.

## The thing it catches

Render a cut the ordinary way and the shell tells you it worked:

```
$ ffmpeg -i room.mp4 -i handheld.mp4 -filter_complex "..." -c:v libx264 out.mp4
$ echo $?
0
```

That zero is honest. ffmpeg encoded exactly the frames it was handed, and every
one of them was black. **A zero exit code is compatible with a completely black
video**, a silent audio stream, a delivered bitrate lower than the one you
requested, and a mute that never actually went quiet.

Same render, measured:

```
$ cutlist build --variant final
  built  opening                3.00s
  built  at_the_desk            9.50s
  built  closing                4.00s

Assembling 3 shots -> walkthrough_final.mp4
  transitions overlap 0.50s, so the timeline is 16.00s not 16.50s
  16.00s / 480 frames

  ok     geometry: expected 1920x1080, got 1920x1080
  ok     codec: expected h264, got h264
  ok     frame rate: expected 30, got 30
  ok     duration: expected 16.00s, got 16.00s
  FAIL   not black: expected mean luma > 2.0, got 0.4
  ok     audio codec: expected aac, got aac
  FAIL   silence 6.50-8.70s: expected silent, got audible

Report: _cut/report.json

VERIFICATION FAILED -- the file exists but is not what was asked for.
$ echo $?
2
```

Two is not a warning. It is a third outcome, separate from `1`, because "ffmpeg
refused" and "ffmpeg produced a file that is not what you asked for" are
different situations and only the second one survives a green build.

## What it is

A public Claude Code plugin, and a plain CLI underneath it. The foundation is
ffmpeg and ffprobe — no project file, no editor, no GUI. The cut is text you can
diff, review and regenerate.

The build runs in three stages, and each one is the unit of re-render:

| Stage | Unit | Produces | Rebuilds when |
|---|---|---|---|
| `conform` | one used range of one source | a CFR lossless intermediate at the project profile, rotation baked in | its in/out window, the project profile, or the source file changes |
| `shot` | one segment | grade → redaction → layer composite → per-shot audio, as one filter graph | that segment's layers, grade, redaction or audio change |
| `assemble` | the whole timeline | concat, transitions, audio bus, and one delivery encode | any shot changes, or transitions, the audio bus or `render` change |

Conforming first is what retires variable-frame-rate seek drift and rotation
surprises: after it, everything is frame-indexed and a timeline second means one
thing. The cache key is the resolved spec plus input content hashes plus the
engine and ffmpeg versions, so changing one trim rebuilds one shot — not the
project.

The delivery is always re-encoded, never stream-copied. A cut assembled by copy
plays on some players and not others; one generation at the end is the price of
a file that plays everywhere.

## ffmpeg measures. You look.

The split is the whole design, and it is enforced rather than encouraged.

**Everything countable is computed.** Durations, frame counts, geometry,
rotation, variable frame rate, delivered codec and bitrate, mean luma, and
silence where a mute was declared — all measured into `_cut/report.json` by
ffprobe and ffmpeg's own filters, never estimated and never asserted from
memory.

**Everything that needs eyes is rendered as an image.** Contact sheets,
grid-annotated frames from the render, boundary stacks either side of a
redaction window, crop-band montages that follow a moving box. Every instrument
collapses an inspection of a thousand frames into a handful of pictures a person
can actually look at.

Neither half can cover for the other. No assertion tells you the cut is good,
the right take was chosen, or that the pacing works. No amount of looking tells
you the delivered bitrate silently halved. So:

| Exit code | Means |
|---|---|
| `0` | it built and every assertion that ran passed |
| `1` | an error — bad config, missing tool, failed render |
| `2` | **it rendered, and verification failed** |

And an assertion that could not run is recorded as `NOT RUN` with a reason,
never as a pass. Silence there reads as "no problems found", which is the worst
thing a verification step can say.

## Install

**As a Claude Code plugin** — recommended, and the way it is designed to be
used:

```
/plugin marketplace add robbiegal/plugin-marketplace
/plugin install cutlist@robbiegal-tools
/cutlist:cutlist-doctor
```

`cutlist-doctor` checks ffmpeg, ffprobe, the version floor and the optional
bands, and gates only on what your config actually demands. Nothing heavy
installs at plugin-install time.

**As a plain CLI:**

```bash
git clone https://github.com/robbiegal/cutlist
cd cutlist
pip install -e ".[text]"
```

Needs Python 3.10+ and **ffmpeg 4.3 or newer** with ffprobe. The package itself
has **zero hard dependencies** — the entire engine, including every verification
instrument, is the standard library plus ffmpeg. Drop the `[text]` extra if you
do not need title cards.

ffmpeg is the one prerequisite no plugin mechanism can install:
`brew install ffmpeg`, `sudo apt install ffmpeg`, `winget install Gyan.FFmpeg`.
Point at a specific binary with `CUTLIST_FFMPEG` / `CUTLIST_FFPROBE`; an
override that is set but unusable is a hard error, never a quiet fallback to
whatever is on PATH.

## The loop

```bash
mkdir walkthrough && cd walkthrough
cutlist init --profile 1080p30     # project.yaml, PLAN.md, .gitignore, media/ _cut/ _out/
# put footage in media/ -- it is read-only from here on
cutlist doctor
```

**Find out what you actually have.** Probe every source and record the facts:

```bash
cutlist ingest
```

```
2 source(s), canvas 1920x1080 @ 30fps

  room.mp4
    1920x1080 coded -> 1920x1080 displayed, 214.32s
    picture at (0,0) 1920x1080

  handheld.mp4
    1920x1080 coded -> 1080x1920 displayed, 96.08s  [rotated 90deg, variable rate]
    picture at (656,0) 608x1080
    rails: left 656x1080, right 656x1080  <- usable annotation space
```

Read the second one closely. A portrait clip in a landscape canvas resolves to a
picture **608 px wide starting at x=656**, leaving two 656 px rails — the only
part of the frame where a label can sit without covering the subject. That is
derived from probe data every time the config is loaded, not measured once by
hand and pasted in as a constant, which is what silently stops being true the
moment a source is replaced with one of a different aspect.

**Pick your beats off a picture, not off a timestamp you guessed.**

```bash
cutlist sheets --clip room.mp4 --fps 1 --tile 6x5 --from 60 --to 120
```

A grid of the whole window, with the arithmetic printed to convert a cell back
to seconds. Write the in/out points you read into `project.yaml`.

**Build, then look:**

```bash
cutlist lint                          # strict schema check, plus notes worth reading
cutlist build --variant final
cutlist measure at_the_desk --at 3.5 --grid    # coordinates, off the RENDER
cutlist verify --boundaries --audio            # the evidence pack
```

```
6 evidence artifact(s):
  _cut/evidence/frames/opening-mid.png
  _cut/evidence/frames/at_the_desk-mid.png
  _cut/evidence/frames/closing-mid.png
  _cut/evidence/boundaries/at_the_desk-redact0.png
  _cut/evidence/boundaries/at_the_desk-mute0.png
  _cut/evidence/audio/silence.txt

These are images. Read them -- an exit code is not evidence.
```

Each boundary file is a single stacked image: the frame just before the window
opens, just after it opens, just before it closes, just after it closes. Window
rounding and interpolated geometry go wrong at the edges, so a mid-window spot
check passes on a redaction that exposes its subject in the first tenth of a
second.

**Then revise by changing a key.** Someone says the shot ends too late: move
that clip layer's `out`. Says they cannot read the card: strengthen the scrim in
the scene and lengthen the segment. Says the blur slips off: raise `margin_px`.
Never patch the delivered file, never hand-edit an intermediate — `_cut/` and
`_out/` are derived and disposable, and anything not expressed as a key
disappears at the next build, usually after it has been approved.

```bash
cutlist build --only at_the_desk      # one shot rebuilds; the rest come from cache
```

Two rules survive from that loop and are worth stating flat. **Measure
coordinates off the render, never the source** — after conform the picture has
been rotated, scaled and placed, so source coordinates are wrong by an amount
that looks plausible. And **aim redaction inspection at detail**: a pixelated
blank wall and a sharp blank wall are the same pixels, so a crop with no text or
edge in it proves nothing.

## The config

`project.yaml`, `project.json` or `project.toml`. Times are **seconds**. Levels
are **always decibels**, always a `_db` key, never a linear multiplier. Unknown
keys are **errors**, never ignored — a key that is read and quietly does nothing
is an invitation to spend an afternoon editing it.

```yaml
project:
  name: walkthrough
  width: 1920
  height: 1080
  fps: 30
  media_dir: media
  out_dir: _out
  work_dir: _cut

grade:
  enabled: true
  eq: { saturation: 1.05, contrast: 1.02 }

audio:
  bed: media/bed.wav
  bed_gain_db: -18
  duck: true
  duck_threshold_db: -24
  loudnorm: true
  target_lufs: -16

policy:
  forbidden_strings: []      # text that must never appear in a generated caption

scenes:
  opening_title:
    kind: card
    title: "A walkthrough"
    subtitle: "in three parts"
  closing_card:
    kind: card
    title: "Next steps"

timeline:
  - id: opening
    duration_s: 3.0
    video_layers:
      - { kind: color, value: "#141414", z: 0, tags: [footage] }
      - { kind: scene, name: opening_title, z: 10, time_fit: trim, tags: [graphics] }

  - id: at_the_desk
    transition_in: { kind: fade, duration_s: 0.5 }
    video_layers:
      # in/out declare this segment's length. There is no second place to say it.
      - { kind: clip, source: room.mp4, in: 84.0, out: 93.5, fit: contain, z: 0, tags: [footage] }
    redact:
      # A document on the desk, drifting as the subject moves it.
      - { box: [980, 620, 260, 180], to: [1120, 660], from: 0.0, to_s: 9.5,
          mode: mosaic, margin_px: 24 }
    audio:
      windows:
        - { from: 4.0, to: 6.2, fade_s: 0.2, fill: media/tone.wav }

  - id: closing
    duration_s: 4.0
    video_layers:
      - { kind: still, source: last_frame.png, fit: cover, z: 0, tags: [footage] }
      # The card has no length of its own -- time_fit says how it meets the 4.0s.
      - { kind: scene, name: closing_card, z: 10, time_fit: trim, tags: [graphics] }

variants:
  - name: final
  - name: clean                # same cut, footage layers only
    tags: [footage]

render:
  vcodec: libx264
  crf: 18
  preset: medium
  acodec: aac
  abr: 192k
```

Two schema rules carry more weight than the rest:

**A redaction box has constant width and height.** Only its position
interpolates, `box` → `to`. Passing a four-element `to` is a hard error with an
explanation, and that is deliberate: `crop` resolves its dimensions once at
filter-graph initialisation, so an animated size does not render slowly or
approximately — it does nothing at all, and nothing errors. Track a subject that
changes size with consecutive entries, or widen `margin_px` to cover the range.

**A segment's duration is declared once** — either `duration_s`, or derived from
a clip layer's `in`/`out`. A graphic layer is fitted to its segment by `time_fit`
policy (`trim`, `loop`, `stretch`, `once`), so a graphic and the shot under it
cannot drift apart. The whole class of bug is removed rather than validated
against.

Passing a `.yaml` config needs PyYAML; `.json` and `.toml` need nothing, and the
loader's error names the exact command that fixes it.

## What it verifies

Every build ends in these assertions, written to `_cut/report.json` alongside the
raw measurements they came from.

| Assertion | What it catches |
|---|---|
| geometry | a `project.width`/`height` change that never took effect |
| codec | the wrong encoder. Encoder names are mapped to codec names first — `libx264` delivers `h264`, and comparing those directly reports a correct render as wrong |
| frame rate | a profile change that reached the config and not the encode |
| duration | drift, off-by-one and a truncated encode, with one frame of tolerance so muxer rounding is not a false alarm |
| **not black** | the render nothing else catches. Mean luma sampled across the timeline against a floor far below any real graded picture, so a deliberately dark cut still passes |
| audio stream present | a delivery with no audio at all |
| audio codec | the container carrying something other than what `render.acodec` asked for |
| **declared mutes** | a mute that is not actually silent — measured with ffmpeg's own detector, at the segment's real start position rather than a running sum of durations, because a transition overlaps its two shots and everything after one sits earlier than its nominal time |

Anything that could not be measured is reported as `NOT RUN` with the reason, and
`report.json` carries a `capabilities` list of exactly those. A check that
silently scored zero would read as a clean result.

The verification instruments are all band 1. There is no configuration in which
you can build but not verify.

## Dependency bands

Installed on demand, never all at once, and the cost is stated before anything
downloads.

| Band | Needs | Cost | Buys you |
|---|---|---|---|
| 1 `core` | ffmpeg + ffprobe | **nothing** beyond ffmpeg itself | Trims, conform, grade, redaction, audio, transitions, delivery, and every verification instrument |
| 2 `text` | Pillow | ~3 MB | Static text: title cards, section cards, lower thirds, captions, label chips |
| 3 `graphics` | Playwright + pinned Chromium | hundreds of MB, plus a separate browser download | Animated motion graphics. **Not implemented in v0.1** |

Band 2 exists for a measured reason. Text is rendered to an image rather than
drawn by ffmpeg, because **one widely distributed ffmpeg build ships without
libfreetype and therefore has no `drawtext` filter at all** — measured on a
machine where that same build reported 490 available filters, `drawtext` not
among them. A pipeline that assumes `drawtext` produces nothing there, and
cannot say why: the filter is missing, not broken, so there is no error to read.
Pillow bundles FreeType on all three platforms, so a title card needs neither a
particular ffmpeg build nor a browser engine.

Version floors, beyond the 4.3 minimum:

| Feature | Needs |
|---|---|
| Redaction, `mosaic` mode | ffmpeg **6.0** (`pixelize`) |
| Redaction, `blur` mode | 4.3 (`boxblur`) — use this when you are stuck below 6.0 |
| Transitions, grade, ducking, loudness | 4.3 |

`cutlist doctor` gates on the features **your config resolves to**, not on the
union of everything the engine could ever emit. A three-clip join must never
fail because a filter it will never reach is absent from the build.

## Scope and limitations

Stated plainly, because a tool that oversells its reach costs more time than it
saves.

- **No animated motion graphics in v0.1.** Band 3 is not implemented. Text is
  static. Do not plan a cut around animation and do not fake it with a stack of
  stills.
- **Text metrics differ across platforms.** The same card, the same font stack,
  the same point size will not line-break identically on macOS, Linux and
  Windows. Look at the render on the machine that produced it; do not carry a
  layout judgement across machines.
- **Nothing here understands content.** It cannot find the interesting moment,
  read a face, follow a subject automatically, or transcribe speech. Beats come
  from a contact sheet you looked at, or from a person. Redaction tracking is
  linear interpolation between two positions you supplied.
- **Redaction is a picture operation, not a guarantee.** It obscures the region
  you specified, for the span you specified, in the variant you built. Check the
  boundary frames.
- **Verification checks the delivered file against declared intent.** It cannot
  tell you the cut is good, that the right take was chosen, or that a clip is
  upside down when the config asked for it upside down.
- **Source media is never modified.** The media directory is read-only; conform
  writes its own intermediates. A re-encode you cannot undo is one bad grade away
  from being the only copy.
- **Validated on a small number of projects.** The engine is deterministic and
  the assertions are real, but the range of footage it has met is narrow. Expect
  to find edges, and read `cutlist lint` and the evidence images rather than
  assuming.

Works on ordinary camera, phone and screen-recording footage, plus stills.

## Licence

MIT.
