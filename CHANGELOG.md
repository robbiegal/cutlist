# Changelog

## 0.1.0 — unreleased

First release. Cut, grade, redact, caption and mix a video from a declarative
cut list, using ffmpeg and nothing else.

### What works

- **Three-stage pipeline.** `conform` normalises each source's used range to a
  constant rate at the project profile; `shot` builds one segment into a cached
  lossless intermediate; `assemble` joins them, mixes the bus and encodes once.
- **Layered timeline.** A segment carries `video_layers[]` — clip, still,
  colour or graphics scene — each with `z`, `opacity`, `fit` and an optional
  placement box.
- **Redaction** with constant-size boxes whose position interpolates, in mosaic
  or blur, gated to a time window.
- **Audio** per shot (mute, ramp, level, room-tone fill) and across the timeline
  (bed, sidechain duck, loudness). Every level is declared in dB.
- **Static text** via Pillow: cards, lower thirds, captions and chips.
- **Verification** that asserts geometry, rate, codec, duration, mean luma,
  audio presence and every declared mute — and reports what it could not check
  rather than passing it.
- **Evidence instruments**: contact sheets, grid-annotated frames from the
  render, boundary stacks and crop-band montages.
- Content-addressed cache keyed on the resolved spec, input hashes, the engine
  version and the ffmpeg version.

### Not in this release

- **Animated motion graphics.** Text is static. The browser-rendered animated
  layer is designed but not implemented.
- Multi-source audio mixing within a single shot. Mix at the bus instead.
- Any NLE project-file export.

### Known limitations

- System font stacks are not deterministic across machines: different metrics
  mean different line breaks, so two machines can render the same text scene
  slightly differently. Pin a font file when reproducibility matters.
- `pixelize` needs ffmpeg 6.0; blur-mode redaction works further back. The
  floor for everything else is 4.3.
- Validated against a small number of projects. Expect at least one assumption
  here to need work on footage unlike what it was built against.
