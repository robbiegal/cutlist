# Failure ledger

Failures that were actually hit, on real footage, in shipped work — not a list
of what could theoretically go wrong.

The distribution is the point. Of 53 recorded failures, 40 were outright
defects and 13 were quality problems. **Of those 40 defects, 31 produced
silently wrong output and only 9 crashed.** More than three in four never
announced themselves: the command exited 0, the file played, and the mistake
was found by looking at a frame.

That ratio is the entire argument for the verification apparatus. A pipeline
whose failures crash needs good error messages. A pipeline whose failures are
silent needs instruments — extracted frames, boundary stacks, contact sheets,
probes asserted against the spec — because nothing else will tell you. It is
also why `cutlist verify` exits **2** rather than 1: a failed check is a
different event from a broken build and must never be reported as one.

Read the severity marker on every entry:

| Marker | Means |
|---|---|
| (crash) | The run stops. You get a message, possibly a bad one. |
| (silent wrong output) | The run succeeds and the result is wrong. |
| (quality) | The result is defensible, and worse than it should be. |

Entries marked **translated** were first observed on another rendering engine
and are restated here in ffmpeg terms. The mechanism carries over unchanged;
the option names and error strings quoted are ffmpeg's.

---

## ffmpeg

Band 1. Every rule here applies to every project, because ffmpeg and ffprobe
are the only things always installed.

### Convert every frame range once, at the spec boundary — (silent wrong output) · translated

**Rule.** Resolve every authored time to a half-open frame interval `[start, end)`
in exactly one place, then let a single emitter write each consumer's own
convention: `trim=start_frame=A:end_frame=B` **drops** frame `B`, while
`select='between(n,A,B)'` and every `enable='between(t,...)'` expression include
both endpoints. Never read an emitted end-point back into the spec.

**Symptom.** Every segment is one frame long or short. Because the error is per
boundary it accumulates across the cut: a redaction opens one frame late, a
label survives one frame into the next shot, and by the end of a long assembly
a layer keyed to a moment sits visibly off the picture it annotates.

**Why.** Half-open and closed conventions coexist inside one graph by design —
`trim` names the first *dropped* frame, `between` tests a closed interval. Both
are documented and both are correct; mixing them costs exactly one frame at
every boundary, and boundaries are the only places anyone looks.

### Track a moving subject with contiguous short windows, never one long one — (silent wrong output) · translated

**Rule.** A position expression interpolates linearly across its own window, so
one long window draws a straight chord through a curved path. Split the follow
into contiguous windows of roughly 0.3 s each, where every window starts where
the previous one ended (`box` = the previous entry's `to`, `from` = the previous
entry's `to_s`), and size the box generously to absorb tracking error.

**Symptom.** The mosaic slides off the subject in the middle of a window, or
jumps at the seam between two windows, exposing for a handful of frames exactly
the region that was supposed to be hidden.

**Why.** Interpolation is exact only at the two endpoints you authored. Between
them the box travels in a straight line at constant speed regardless of what
the subject does, and any seam whose endpoints disagree is a visible jump. Size
cannot interpolate at all — `crop` evaluates its width and height once when the
graph is configured and only `x`/`y` per frame — so a generous box is the only
tolerance available.

### Composite by explicit chain order; there is no z-index — (silent wrong output) · translated

**Rule.** `overlay` takes exactly two inputs, so N layers are N−1 chained
overlays and the topology of that chain alone decides what covers what. Sort
layers by `z` once, then emit one overlay per layer in ascending `z`, each
consuming the accumulated result as its first input and the new layer as its
second. The last overlay emitted is the top layer.

**Symptom.** A full-frame graphic covers the caption it was meant to sit behind.
Or a layer added later covers a redaction and the hidden region becomes legible
again — a redaction that is present in the graph, correct in its geometry, and
doing nothing.

**Why.** `overlay` has no concept of depth; the composite is defined entirely by
chain order, so a `z` number that means something in the spec must be turned
into topology before emission. Swapping the two inputs of a single overlay
inverts that pair with no diagnostic, and an overlay left at its default
`eof_action=repeat` freezes its last frame over everything downstream of it.

### Probe the delivered file; requested flags are not necessarily applied — (silent wrong output)

**Rule.** After the delivery encode, `ffprobe` the output and assert on what is
actually in it: codec, width and height, `avg_frame_rate` and `r_frame_rate`,
frame count, duration, per-stream bit rate. Compare each against what was
requested *and* against the timeline length computed from the spec.

**Symptom.** You ship and document a file whose real parameters differ from the
command that made it — an audio bit rate a third of what was asked for while
video honoured its setting exactly — and nobody notices until someone checks
the file against the spec.

**Why.** Encoder options travel through several layers, and some are silently
clamped, ignored, or handled differently by whichever encoder was actually
selected. Nothing reports the substitution. The exit code is 0 either way.

### Never swallow a probe failure into a default — (silent wrong output)

**Rule.** A probe helper must raise on a missing file, a renamed file, an
unreadable stream, or a missing binary. Do not write `except: return 0.0`, and
do not clamp a frame count with `max(1, n)`.

**Symptom.** The build prints a normal success summary and produces a valid
graph whose segment is a single frame of black or garbage. Every duration
derived from that segment is wrong, and the run exits 0.

**Why.** Zero is a legal duration. It becomes a one-frame input that every later
stage accepts without complaint, so the failure travels the whole length of the
pipeline before it is visible — and by then it looks like an authoring mistake
rather than a probe that returned nothing.

### Carry alpha only in a pixel format that has an alpha plane — (quality)

**Rule.** When an intermediate must keep transparency, name a format that
carries alpha and then verify it survived: `-c:v qtrle`, or `-c:v prores_ks
-profile:v 4444 -pix_fmt yuva444p10le -vendor apl0`, or VP9 with
`-pix_fmt yuva420p`. Keep the source RGBA frame sequence on disk regardless, and
`ffprobe` the intermediate for its `pix_fmt` before compositing with it.

**Symptom.** Overlays composite as opaque rectangles that hide the footage behind
them. Nothing fails at encode time; the loss surfaces one stage later, at
composite, where it presents as an overlay bug.

**Why.** Alpha exists only if the pixel format has a plane for it. An encode into
a format without one succeeds, discards the plane silently, and produces a file
that looks correct when played on its own. The vendor atom makes downstream
tools treat the file as genuine. Keeping the frame sequence makes the rollback
free.

---

## Audio

### Build audio-only inputs from a probe, not from their position in the spec — (crash)

**Rule.** Decide an input's stream layout from `ffprobe`, never from the role it
plays in the spec. Reference audio-only inputs by stream type (`[N:a]`) and never
take a video label from one. Key any input cache so a path first ingested as
footage cannot be handed back for an audio-only role.

**Symptom.** `Stream specifier ':v' in filtergraph description ... matches no
streams` or `Invalid file index N in filtergraph description`, and the build
stops. The quieter variant is worse: a positional index picks the wrong stream
in a file that has more than one, and the track renders silent with a clean
exit.

**Why.** Stream indices are per-file and positional; stream-type specifiers are
not. A layout assumed from the spec is correct right up to the first file that
does not match it, which is usually a music bed or a room-tone grain.

### Routing a source's video does not route its audio — (silent wrong output) · translated

**Rule.** A layer that must be heard needs its audio stream taken into the audio
graph explicitly and the graph's output label handed to `-map`. The moment any
`-map` appears for an output, automatic stream selection is off for that output
entirely — every stream you want must then be mapped by hand.

**Symptom.** Total silence from the footage while the music bed plays perfectly.
Or one segment mute in a cut where every other segment has sound. No error, no
warning, and no structural check catches it, because the graph is valid — the
audio simply has no path to the output.

**Why.** Video and audio are separate stream types in a filter graph with no
implicit coupling. A source consumed only by video filters never reaches the
audio bus, and the default "pick the best stream of each type" behaviour that
would have rescued it is disabled by the first explicit `-map`.

### `volume` is dB only when you write the suffix — (silent wrong output)

**Rule.** `volume` reads a bare number as a linear amplitude multiplier and a
`dB`-suffixed number as decibels, so `volume=6` is roughly +15.6 dB, not +6 dB.
Emit the suffix on every level. Keep every level in the spec under a key ending
in `_db`, so an unsuffixed number can never reach a filter.

**Symptom.** A level authored as if it were dB arrives as a multiplier: a
modest-looking `6` clips the mix, `0.5` reads as a half-decibel trim and is
actually −6 dB, and a negative value inverts polarity instead of attenuating.
All of them render cleanly.

**Why.** The option is overloaded on the presence of a suffix, and the value
carries no unit, so the filter cannot tell a mistake from an intention. Naming
is the only place the ambiguity can be removed — which is why every level key
in the spec ends in `_db`.

### Emit the constant gain first, ahead of every level-dependent filter — (silent wrong output) · translated

**Rule.** Put the constant `volume` at the head of the audio chain, before
`sidechaincompress`, `acompressor`, `alimiter` or `loudnorm`. Constant gains
commute with each other; nothing commutes with a threshold, and `loudnorm`
normalises only what reaches it.

**Symptom.** The bed sits at the right level in isolation and ducks by the wrong
amount under speech, because `duck_threshold_db` was compared against a level
the delivered mix never had. Or the delivery misses `target_lufs` by exactly the
gain applied after normalisation, and neither filter says a word about it. Or a
window authored as silent audibly leaks, because a downstream dynamics filter
released its gain reduction across the window edge and pulled level back in —
which survives structural review, since both filters are present and each is
individually correct.

**Why.** `volume` scales samples, so two constant gains multiply and their order
genuinely does not matter. Every other filter in the bus decides what to do by
*measuring* its input, so its position in the chain changes its output.
Order-independence is a property of constant gain alone and does not extend to
the chain around it.

### Whole-segment mute and per-window treatment are mutually exclusive — (quality)

**Rule.** If a segment's audio is muted, do not emit its windows at all — a
window inside a muted segment is dead configuration that conceals an authoring
mistake. Document what a window's `fade_s` does: it ramps level to silence over
the first `fade_s` seconds of the window and then holds silence to the out
point. It is a mute ease-in, never a symmetric fade back up.

**Symptom.** A fill asset placed inside a fully muted segment plays over
otherwise dead audio and sounds like a bug. Or an author expecting a symmetric
fade gets a hard level restore at the out point, which is more conspicuous than
the mute it was meant to soften.

**Why.** The two controls answer different questions — one removes a segment
from the mix, the other shapes a region inside it — and applying both leaves no
defined result. The one-sided ramp is deliberate: nothing restores level before
the out point because the restore *is* the return to unmuted audio.

### A mute can only remove signal; mask the hole with matched ambience — (quality)

**Rule.** To hide a mid-segment mute, place a level-matched ambience grain over
exactly the muted window on its own layer. Sample the grain from a clean,
speech-free stretch of the **same** source so microphone, level and noise floor
match (`cutlist grain --clip NAME --from S --to S --out PATH`), and store it as
an explicitly marked derived asset.

**Symptom.** The mute draws more attention than whatever it removed: continuous
room ambience drops to dead digital silence for two seconds and returns. A grain
lifted from a different source announces itself instantly through timbre and
noise-floor mismatch — the hole is filled and still audible.

**Why.** `volume` can only scale what is already present, so a gain of zero
always leaves a hole exactly the shape of the window. The only way to fill it is
to add signal, and the only signal that matches is signal from the same
recording.

---

## Overlays

Band 3 — browser-rendered animated graphics. These rules apply when that band is
installed; nothing here is required for band 1 or band 2 work.

### Serve the render page over http, never `file://` — (crash)

**Rule.** Start an ephemeral static server bound to `127.0.0.1` on port 0, in a
daemon thread with request logging suppressed. Point the browser at that origin
and shut the server down in a `finally`.

**Symptom.** The page reports a fetch or CORS error and every scene is skipped —
or it renders with fallback fonts because the local typefaces never loaded. An
entire graphics pass produces either nothing or something subtly wrong.

**Why.** A `file://` origin blocks `fetch()` of a local JSON config and blocks
local `@font-face` files. Port 0 asks the operating system for a free port, so
the harness cannot collide with anything and needs no configuration.

### Pause every animation and drive it from one seek clock — (silent wrong output)

**Rule.** Create every animation through the Web Animations API, `.pause()` it in
the same statement, and push the handle into a flat array. Expose one
`window.seek(ms)` that assigns `currentTime = ms` to every handle. Ban wall-clock
time, `requestAnimationFrame` loops, CSS `@keyframes` playback and CSS
transitions anywhere on the page.

**Symptom.** Captured frames stutter and differ between runs. Re-rendering one
scene changes it. A sequence that looked right once cannot be reproduced,
resumed, or bisected.

**Why.** Any self-driving animation makes a captured frame a function of how long
the harness took to reach the screenshot, not of the frame index. A seek clock
makes every frame a pure function of `ms` — reproducible, resumable, and safe to
render out of order.

### Never let the screenshot API disable animations — (silent wrong output)

**Rule.** Do not pass any "disable animations" option to the screenshot call.
Drive state only through the seek function, and leave a comment at the call site
explaining why, because that option looks exactly like what a deterministic
renderer should want.

**Symptom.** Every frame renders the end state of every animation — elements
fully faded out, or frozen at their final position — and `seek()` appears to have
no effect whatsoever. Nothing is raised.

**Why.** The option does not freeze animations; it fast-forwards and **cancels**
them, destroying the paused handles the seek clock addresses. The name describes
the intent, not the mechanism.

### Give every animation `fill: 'both'` — (silent wrong output)

**Rule.** Create every handle with `{fill: 'both'}` and pause it immediately.
This applies to entrance and exit envelopes and to continuous sub-motion loops
alike.

**Symptom.** Every element is visible from frame 0 regardless of its scheduled
entrance, and lingers after its exit — a page where the timing config appears to
be ignored entirely.

**Why.** Without `fill: 'both'`, seeking to a time inside an animation's `delay`
renders the element's static CSS state instead of keyframe 0, and seeking past
the end renders the static state again. Fill mode is the only thing that makes
an off-interval seek render the correct hidden state.

### Make the page transparent and opacity opt-in — (silent wrong output)

**Rule.** Set `html, body { background: transparent }`, screenshot with the
omit-background flag and an explicit clip rectangle equal to the viewport, give
full-frame scenes an opt-in class that paints their own background, and launch
the browser with `--disable-lcd-text` and `--force-color-profile=srgb`.

**Symptom.** An overlay that composites as an opaque rectangle hiding the footage.
Or coloured fringes around every glyph once it is composited over video. Or
frames whose colours differ between two machines rendering the same scene.

**Why.** Omit-background is a no-op over an opaque page, so the default page
background silently defeats it. Subpixel (LCD) antialiasing over transparency
writes coloured RGB into glyph edges, which becomes visible haloing once the
alpha is used. A pinned colour profile removes per-machine display drift, and
the explicit clip rectangle guarantees exact pixel dimensions no matter how the
layout overflows.

### Delete the frame directory before every re-render — (silent wrong output)

**Rule.** Remove and recreate the per-scene frame directory at the start of each
render, before frame 0 is written.

**Symptom.** Shortening a scene produces a clip that is still the old length,
with stale trailing frames from the previous run appended to it — and everything
after that point on the timeline is silently off by the difference.

**Why.** The image-sequence demuxer reads whatever numbered files it finds.
Leftover higher-numbered frames from a longer previous run are indistinguishable
from frames this run produced.

### An animated transform outranks the static one for the element's whole life — (silent wrong output)

**Rule.** If an envelope animates `transform` with `fill: 'both'`, it overrides
any static centering transform in the stylesheet *and* in inline styles, for the
element's entire life — including during its delay and after its end. Either
compose the centering into every keyframe, animate a different property, or
document that authored x/y are the element's top-left corner.

**Symptom.** Elements render offset down and to the right by half their own size
relative to where the config puts them, and documentation claiming x/y are
centres becomes quietly false for exactly the element kinds that use the
envelope.

**Why.** Animation-origin values outrank both stylesheet and inline declarations
in the cascade, and `fill: 'both'` extends that authority across the whole
timeline rather than only the active interval.

### Put easing on keyframes and keep the effect timeline linear — (quality)

**Rule.** Specify per-keyframe easing and leave effect-level easing at `linear`,
so keyframe offsets continue to correspond to real time. Build the
reveal/hold/exit envelope as four keyframes, with reveal and exit expressed as a
fraction of the element's life and capped in milliseconds.

**Symptom.** Elements read as fading for their entire life — "too fast",
"vanishing", permanently semi-transparent — while every per-element duration
looks correct when you inspect it.

**Why.** Effect-level easing remaps the whole timeline, so a reveal authored as
the first tenth of an element's life is stretched across all of it and the
offsets stop meaning what they say. Per-keyframe easing shapes the segment
between two keyframes and leaves the offset-to-time mapping alone.

### Non-animatable state needs its own seek-resolved channel — (quality)

**Rule.** For anything the animation API cannot animate — text content, counters,
odometers, tickers — register a record of `{element, finalValue, startTime,
rate}`, set the element to its **final** value at build time, and recompute the
displayed value inside `seek()` from `ms`. Call `seek(0)` once before the first
capture.

**Symptom.** A type-on element captures fully typed on every frame, or the card
containing it visibly resizes as characters appear.

**Why.** Text content is not an animatable property, so it needs a parallel
deterministic channel driven by the same clock. Assigning the final string at
build time lets layout and font metrics settle at their final size, so the
container is never re-measured mid-reveal.

### Gate the first capture on font readiness — (quality)

**Rule.** Declare every face with `font-display: block`, await the document's
fonts-ready promise, call `seek(0)`, wait one animation frame, then flip the
ready flag the harness polls. Flip that flag on the error path too, and expose
the error string for the harness to report.

**Symptom.** The first frames capture with fallback-font metrics and different
line breaks — a one-frame text pop in the finished video that nobody can
explain. Without the error path, the harness instead blocks until timeout with
nothing to diagnose.

**Why.** `font-display: block` renders text invisible rather than swapping in a
fallback, the ready promise guarantees the real faces have loaded, and the extra
frame gives the compositor one paint with them. Flipping ready on error turns a
hang into a reported skip.

### Make every loop period divide the clip duration — (quality)

**Rule.** Choose continuous-motion periods so an integer number of cycles fits
the clip: a 2000 ms pulse becomes `2000 × round(duration / 2000)`; a drift gets
`duration == period` and one iteration that returns to its origin. Animate a
stroke-dash offset by an exact multiple of the dash-plus-gap sum.

**Symptom.** A visible pop at every tile boundary and at every dash iteration —
and it appears only in the composited timeline, never in a single-scene preview,
so it survives every review that inspects scenes in isolation.

**Why.** A looping graphic is tiled back to back to fill its segment, so any cycle
that does not close at the clip boundary is a discontinuity repeated once per
tile. A dash offset that is not a whole dash period is the same failure at a
smaller scale.

---

## Assets

### Never rename, move or re-encode a source file — (crash)

**Rule.** Treat source media as immutable and reference it verbatim, including
hostile filenames — spaces, `+`, `&`, a trailing space before the extension, and
outright typos. Quote every path in every shell invocation, list the known
filename hazards where the spec can see them, and give derived assets stored
beside originals a distinguishing prefix.

**Symptom.** A dropped trailing space makes the probe return nothing, the input
collapses to a single frame, and the segment renders as garbage with no error.
Or a media re-import silently drops the generated stills and audio grains the
spec depends on, and the next build fails on files that were there yesterday.

**Why.** Filenames are the join key between the spec, the conform cache and every
piece of evidence. Cleaning one up breaks every reference to it simultaneously.
Grade, redaction and overlays exist precisely so that nothing has to be baked
into an original.

### Prove the pillarbox geometry before designing against it — (quality)

**Rule.** For a mixed-orientation source set, conform one frame of a portrait
source and one of a landscape source through the delivery profile, look at both,
and measure the resulting action window. Compute the bounds from probe facts —
rotation side data, sample aspect ratio, coded dimensions — rather than
asserting them; re-run the proof whenever the source set changes; and flag
portrait sources to the human before any layout is committed.

**Symptom.** A whole layer layout authored against the wrong safe area, with
labels overlapping the picture or floating in the dead bars beside it —
discovered only after every graphic has been rendered.

**Why.** Rotation metadata is applied during conform and the result is
pillarboxed into the profile, producing a narrow centred action window with dead
bars on both sides. That measured window is the only place a layer may live: a
design-defining fact, not a detail. `cutlist ingest` writes the computed
placement to `_cut/geometry.json` — read it from there rather than assuming it.

---

## Verification

### A zero exit code is not verification — (silent wrong output)

**Rule.** After every render, extract stills at chosen timestamps
(`ffmpeg -ss <sec> -i <render> -frames:v 1 <out>.png`) into an evidence
directory, and actually look at them. Prefix filenames by revision round so the
evidence is dated and comparable, and keep a before/after pair for every spatial
change.

**Symptom.** A structurally valid, cleanly encoded file that is wrong — a missing
layer, a substituted font, an off-palette grade, an annotation a hundred pixels
from the thing it points at. Every automated check in the pipeline passes on it.

**Why.** The encoder exits 0 on graphs that produce black, silent or mis-ordered
output; correctness of the graph and correctness of the picture are different
properties, and only one of them is machine-checkable. Video work has no unit
tests. Sampled visual inspection at a chosen cadence is the primitive that
replaces them.

### Measure every coordinate and timestamp in output space — (silent wrong output)

**Rule.** Read pixel geometry and beat timings off rendered frames, never off the
source file. Burn a grid over a zoomed crop of a rendered frame and read the
coordinates directly: `cutlist measure SEGMENT_ID --at S --grid --crop X,Y,W,H
--zoom N`.

**Symptom.** Roughly a hundred pixels of drift between where a redaction is
authored and where the subject actually is — enough to leave the region legible
for the entire window, while every number in the spec looks deliberate.

**Why.** A variable-frame-rate source reports a nominal frame rate that differs
from its real average, so a seek into the source lands on a different picture
than the same timestamp in the conformed timeline. A rotated or portrait source
is pillarboxed into the profile, so its own pixel grid is not the grid the
output uses. Conform exists to retire both; measuring before conform reintroduces
them.

### Verify windowed and interpolated effects at their boundaries — (silent wrong output)

**Rule.** For any effect with an in/out window or interpolated geometry, sample
the frames just inside and just outside each boundary and stack them into one
image: `cutlist verify --boundaries`. Do not spot-check the middle.

**Symptom.** Coverage looks perfect mid-window while the first and last frames
expose exactly what the effect was supposed to hide.

**Why.** Linear interpolation is exact at its endpoints and rounding is exact in
the middle; the two failure modes meet at the boundary. That is where an
off-by-one shows, where two adjacent windows disagree, and where nobody looks.

### Smoke tests must assert freshness, not existence — (silent wrong output)

**Rule.** Delete the expected artifact before running a smoke test, or compare
its modification time against the run start. Never assert only that the output
file exists.

**Symptom.** The preflight gate reports green on a machine where the encoder or
the headless browser cannot run at all, and the failure surfaces deep inside the
first real build wearing a completely different error.

**Why.** A leftover artifact from any previous successful run makes a broken
toolchain report OK forever afterwards. Existence tests the filesystem;
freshness tests the tool.

### Cross-check the spec against the disk, and fail rather than warn — (silent wrong output)

**Rule.** Before any render, statically assert every cross-reference: every named
scene resolves, every referenced file exists on disk, every graphic fits its
segment. Derive the required-asset manifest from the spec itself — every layer
source of every kind, plus audio fill references and bed paths — never from a
hand-maintained list. A missing generated asset **fails** the gate; it does not
warn.

**Symptom.** A stale manifest reports "all sources present" about media the
current cut no longer uses, while the media it does use is missing. The run
exits 0 with dead layers in it.

**Why.** Two things that must agree will drift, and a hand-written manifest
freezes at the moment it was written while the spec keeps moving. Note which
half of this class the schema already removed: segment duration is declared
once and a graphic is fitted to its segment by `time_fit`, so a graphic and its
shot cannot drift out of sync and there is nothing left to validate. The other
half — names that resolve to nothing, files that are not there — still needs the
gate. Run it as `cutlist lint`, and treat a warning in a build that continues as
a warning nobody reads.

### Validate transitively, and bisect in minimal graphs — (silent wrong output)

**Rule.** When the real consumer cannot be driven automatically, prove the
artifact through a second consumer of the same artifact. Before emitting a large
graph, prove each construct in a graph of a few filters that renders exactly one
frame — one variable per file, log kept beside each image. When the minimal case
works and the real one does not, copy the real graph and delete exactly one
construct per variant.

**Symptom.** Hours spent re-rendering a thousand-line filter graph whole to test
a one-line change, and a defect finally found by reading rather than by
measuring.

**Why.** A one-frame render costs seconds instead of minutes, so the loop is short
enough to actually run, and subtractive ablation isolates the offending construct
without a rewrite. Every emitted script is kept at `_cut/graphs/<id>.filter`
precisely so a failing graph can be run by hand, trimmed and re-run without
regenerating anything; `cutlist build --graph-only` produces it without paying
for the render.

### Collapse many-frame inspection into single images — (quality)

**Rule.** Build instruments rather than scrubbing. Crop the same band every
~0.15 s across a window and stack the bands vertically into one tall image, so a
whole trajectory is one image read (`_cut/evidence/montage/`). Make whole-clip
contact sheets for beat mapping (`cutlist sheets --clip NAME --fps 1 --tile
10x6`) and record the cell-to-timecode mapping in the filename, since burnt-in
timecode may not be available.

**Symptom.** Verification quietly degrades into two spot-checks, because
inspecting the window honestly would cost dozens of separate image loads and
nobody has that budget.

**Why.** A reviewer — human or model — can afford a handful of image reads per
pass. Converting an O(N)-frames problem into one image is what makes fine-grained
verification affordable at all, and affordability is what decides whether it
happens.

---

## Process

### A named reference that resolves to nothing must fail loudly — (silent wrong output)

**Rule.** When the spec references something by name, treat "name not found" and
"file missing on disk" as errors, never as a fall-through to an empty slot.
Guard every lookup the same way — do not mix guarded lookups and bare subscripts
across sibling code paths — and warn on assets that exist but are referenced by
nothing.

**Symptom.** A misspelt or never-rendered graphic yields a video missing that
graphic, with a clean exit code and a normal-looking summary line. Meanwhile
render time is being spent on orphans nobody references.

**Why.** Name-keyed indirection is the architecture; without a loud failure a
typo is indistinguishable from an intentionally empty slot. This is the same
reasoning that makes unknown keys an error rather than an ignored key: a silent
default is a decision made by nobody.

### Put on-screen content constraints in the spec from day one — (silent wrong output)

**Rule.** Give the project an explicit constraints surface from the first commit:
strings and marks that may not appear, a closed list of factual claims with
"invent none beyond these", and the redactions that are required. Keep every
on-screen string in the spec so a late reversal is a spec edit plus one asset
re-render, and gate the build on the scan — `cutlist scan`, driven by
`policy.forbidden_strings` and `policy.forbidden_patterns`.

**Symptom.** A forbidden string ships because it lives in a stylesheet, a markup
fallback or a code default the scan never reads. Or a late reversal forces
hand-editing dozens of already-rendered assets under deadline.

**Why.** On-screen content restrictions are the single most likely requirement to
reverse late: something mandatory in the brief becomes forbidden weeks later.
State the gate's limits out loud rather than trusting it — scanning one
serialized spec misses text baked into stylesheets, markup, code fallbacks or a
second config, and a naive substring match false-positives on numeric
coordinates.

### The spec is the source of truth; regeneration overwrites hand edits — (quality)

**Rule.** State in the spec header and in the handoff notes that regenerating
overwrites generated output, so anyone who hand-edited must save a copy first.
Keep the change loop to one command per stage, keep regenerated assets at the
same filenames so a reload picks them up instead of re-importing, and snapshot
before any sweeping structural change.

**Symptom.** A human's manual timing work is destroyed on the next regenerate, in
a media project that is not under version control and has no way back.

**Why.** Deriving the deliverable from a spec is what makes a dozen rounds of
revisions affordable — but only if the boundary between generated and
hand-edited is explicit. Content-addressed caching helps on one side of that
boundary and not the other: a shot whose inputs did not change is never rebuilt,
which protects nothing that was edited outside the cache.

### Flag the epistemic status of every generated timing — (quality)

**Rule.** Mark every reported value with a three-symbol vocabulary — confirmed by
measurement, inferred from evidence, unverified — and declare up front which
classes can never be confirmed without a human: all trim points, all beat
timings. Carry the markers into the spec's own comments, and keep an
append-at-top revision log naming the request, the change and the resulting
total, which declares older planning tables stale.

**Symptom.** An inferred cut point is treated as ground truth and a mistimed edit
ships. Or a later session copies trims back out of a superseded planning table
and silently reverts a dozen revisions at once.

**Why.** It is how a reviewer triages generated output at a glance, and it is the
only thing that stops a resuming agent restoring values from a document that was
correct when it was written. Anything a machine cannot verify must be labelled as
such at the moment it is written — that label cannot be reconstructed later.
