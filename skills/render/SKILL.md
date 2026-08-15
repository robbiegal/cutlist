---
name: render
description: Conform, build and deliver a cut with ffmpeg - the three build stages, the content-addressed cache, rebuilding one shot after one edit, reading the emitted filter script before paying for an encode, variants, and the delivery assertions. Use when asked to render, build, re-render, conform, export or produce a variant of a cutlist project, when a trim or a redaction changed and only that shot should rebuild, when a build should be inspected before spending an encode, or when a render fails partway through.
allowed-tools: Bash, Read, Glob
---

# Build a cut

One command runs all three stages and stops with a verdict:

```bash
cutlist build --variant final
```

Exit `0` built and passed. Exit `1` something refused. Exit `2` the file exists
and is **not what was asked for** - that is a different event and must never be
reported as an error or as a success.

## 1. Get the contract, do not recall it

```bash
cutlist prompt build
```

Follow what that prints. It ships with the engine and names the fields this
version actually reads; a remembered copy sends you to keys that were renamed,
and unknown keys are a hard error rather than an ignored line.

Before a long build, spend the seconds that save the minutes:

| Command | Answers |
|---|---|
| `cutlist doctor` | Does *this* build of ffmpeg do what *this* config asks for? It gates only on the features the config demands, so a three-clip join never fails over a filter it will never emit. |
| `cutlist lint` | Do all the cross-references resolve, and is anything configured that does nothing? |
| `cutlist ingest` | What is each source really - rotation, variable rate, no audio, where the picture lands, what empty rails are left. Writes `_cut/geometry.json`. |

## 2. The three stages, and what each one retires

| Stage | Does | Retires |
|---|---|---|
| `conform` | Transcodes each source's used ranges, plus transition handles, to a constant-rate lossless intermediate at the project profile, rotation baked in and sample aspect squared. | Variable-frame-rate seek drift, rotation surprises, non-square pixels. After this, frame N means frame N and a timestamp lands where arithmetic says. |
| `shot` | One `-filter_complex_script` per segment: grade, then redaction, then layer composite, then per-shot audio. Output is a lossless, content-addressed shot intermediate. | Whole-timeline rebuilds. This is the unit of re-render: change one trim, one shot rebuilds. |
| `assemble` | Concat and transitions, the audio bus (bed, duck, loudness), then **one** delivery encode. | Generation loss from repeated encoding, and the class of file that probes clean and plays wrong. Never a stream copy: that needs every piece to agree bit-for-bit on profile, level, pixel format and timebase, and a still, a graphics shot and a footage shot do not. |

Stage 1 can be paid for on its own, which is worth doing before any measuring
session, since coordinates only mean anything after conform:

```bash
cutlist conform            # everything the timeline uses
cutlist conform --only intro --force
```

A transition **overlaps** its two shots, so a 0.5 s dissolve makes the finished
timeline 0.5 s shorter than the sum of the segments. The build prints the
overlap and the assertions account for it, so a correct render never trips a
frame-count mismatch.

## 3. The cache, and what invalidates it

The cache key is four things, and all four are load-bearing:

```
resolved spec  +  input content hashes  +  engine version  +  ffmpeg version
```

Dropping the engine version is not a stale-file annoyance, it is a live
wrong-output bug: upgrade the tool and every project silently reuses artifacts
built by the previous version of the engine, with no symptom. Dropping the ffmpeg version is
the same failure with a distribution's hand on the lever.

| You change | Rebuilds |
|---|---|
| A trim, a redaction, a grade parameter, an audio window in one segment | That shot |
| A layer's source file, changed in any byte | Every shot that uses it |
| A scene's text, anchor or theme | That scene's PNG, and every shot that composites it |
| `project.width` / `height` / `fps` | Everything |
| The engine version, or ffmpeg | Everything |

Inputs are identified by the hash of their contents, not by name or timestamp.
A source that is re-exported, re-encoded or replaced in place therefore
rebuilds every shot that touches it even when its filename, size and modified
time are unchanged - which is the case a timestamp check gets wrong, and gets
wrong silently.

## 4. Rebuild one shot, because that is the whole point

```bash
cutlist build --only crop_wide            # scope the work to one segment
cutlist build --only crop_wide --force    # and rebuild it from scratch
```

`--only` scopes what `--force` applies to; every other shot comes from cache
and the timeline is still assembled and delivered in full, so a one-shot
rebuild still produces a complete, playable file you can hand to someone. A cut
is revised dozens of times and almost every revision touches one segment -
whether that costs a shot or a timeline is the difference between a loop that
gets run and one that gets skipped.

The per-segment line says `built` or `cached`. If you changed a segment and its
line says `cached`, the spec change did not reach the key - look for an edit
made to a file the config does not read, or an edit made inside the work
directory.

## 5. Read the graph before you pay for the render

```bash
cutlist build --only crop_wide --graph-only --force
```

Stops before assemble and the delivery encode. Every emitted script is kept at
`_cut/graphs/<id>.filter` so a suspect graph can be read, run by hand, trimmed
one construct at a time and re-run without regenerating anything. A one-frame render costs seconds where a timeline costs
minutes, and that is what makes bisection a loop anyone actually runs.

Pass `--force` with `--graph-only`. A graph is written when a shot is *built*,
so a cache hit leaves the previous run's file sitting there - existence tests
the filesystem, freshness tests the tool.

## 6. Variants

```bash
cutlist build --variant short
```

A variant is a name plus tags. An untagged layer is always included, so adding
a variant never silently removes something nobody labelled; a tagged layer is
included when its tags intersect the variant's. A variant that empties a
segment of every layer is a hard error, not an empty shot. Output lands at
`_out/<project>_<variant>.mp4`, one file per variant, and shots are shared
across variants through the cache.

## 7. What the delivery assertions check

After the encode the delivered file is probed and asserted against both the
request and the timeline computed from the spec:

| Asserted | Because |
|---|---|
| Codec, width, height, pixel format | Encoder options travel through several layers and some are silently clamped, ignored, or handled differently by whichever encoder was actually selected. |
| `avg_frame_rate` **and** `r_frame_rate` | A container can carry a nominal rate that the stream does not honour. |
| Frame count and duration, against the spec's timeline minus transition overlap | A one-frame boundary error accumulates across a cut; a swallowed probe failure turns a segment into a single frame and every downstream duration with it. |
| Per-stream bit rate | A file whose real audio bit rate is a fraction of what was requested probes cleanly, plays, and is shipped without anyone noticing. |

Results land in `_cut/report.json`. A failure exits `2` and prints what
differed. **The assertions read the container and the streams, never the
picture** - they cannot see a missing layer, a substituted font or an
annotation a hundred pixels off. That is what the `verify` skill is for, and no
build is finished until it has run and its images have been read.

## Failure modes you will actually hit

| Symptom | Cause | Fix |
|---|---|---|
| ffmpeg exits non-zero on the *output* path, permission denied, on a file that plainly exists | A media player or preview pane is holding the delivery file open. Windows will not let it be replaced while open. | Close it and re-run. Shots are cached, so the retry costs only the assemble. |
| A run dies partway, or the disk fills | Lossless intermediates are far larger than the delivery: budget for the conformed windows *and* the shots, not for the output. A zero-byte artifact is rejected by the cache, but a truncated non-empty one is not. | Free space, then `cutlist build --force` the segments that were mid-flight. After any interrupted run, force rather than trust. |
| `No such file or directory` on a path you can see in the file manager | Windows path length. Work-directory paths are `<work_dir>/shots/<id>-<hash>.mkv`, and a deep project directory plus a long segment id crosses the limit. | Keep segment ids short, put `project.work_dir` near the drive root, or enable long paths. The engine never shortens a name for you, because a truncated name collides. |
| A source cannot be found after it worked yesterday | A source was renamed or moved. Paths resolve against `project.media_dir`. | Restore the name. Re-encoding a source in place also changes its hash and rebuilds every shot that touches it. |
| `cutlist doctor` reports a filter missing | The ffmpeg on the path lacks it. The floor is 4.3 for `xfade`; mosaic redaction needs 6.0 for `pixelize`. | Install a fuller build and re-run `cutlist doctor`. It gates only on what this config demands, so a config that emits no mosaic never needs 6.0. |

## Rules that govern

1. **Lint and doctor before a long build.** Prevents discovering a missing
   filter or an unresolvable reference after the conform stage has been paid
   for.
2. **Never hand-edit anything under the work directory.** Prevents an artifact
   whose contents no longer match its key being reused forever, with no
   symptom. Change the spec; the spec is the source of truth and regeneration
   overwrites everything derived from it.
3. **Force the rebuild of anything that was mid-flight when a run died.**
   Prevents a truncated intermediate being accepted as valid - only zero-byte
   files are rejected automatically.
4. **Read the graph before re-rendering a timeline to test a one-line change.**
   Prevents hours spent measuring what could have been found by reading.
5. **Never join shots yourself with a stream copy.** Prevents a file that
   probes perfectly and plays wrong in some players and not others.
6. **Do not report a build as finished on an exit code.** Prevents shipping a
   structurally valid, cleanly encoded, wrong file. Run the `verify` skill and
   read its images.
7. **Say which variant you built and where it landed.** Prevents a reviewer
   watching the wrong file and reporting the missing layer that was never in
   that variant.
