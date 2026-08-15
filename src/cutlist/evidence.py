"""The instruments. Each one turns a question into a single image to read.

The constraint that shapes all of them: a reader -- human or model -- can afford
to look at a handful of images, not a thousand frames. So every instrument
collapses an O(frames) inspection into O(1) images.

  contact sheet   where is the beat?          a grid of the whole clip
  grid reader     what pixel is that at?      a lattice over the RENDER
  boundary stack  does the window's edge hold? just inside and just outside
  band montage    does the tracking follow?   one crop, sampled, stacked

Two rules about using them.

**Measure on the render, never the source.** After conform, a source second and
a timeline second are different things, and the picture has been scaled and
placed. Coordinates read off a source clip are wrong by an amount that looks
plausible, which is the worst kind of wrong.

**Aim at detail.** A pixelated blank wall and a sharp blank wall are the same
pixels. If the crop you are inspecting has no text, face or edge in it, it
cannot tell you whether the redaction worked.
"""

from __future__ import annotations

from pathlib import Path

from .assemble import segment_starts
from .config import Config
from .errors import ConfigError, RenderError
from .render import _resolve_source, _run_ffmpeg
from .tools import capabilities


def _evidence_dir(cfg: Config, kind: str) -> Path:
    d = cfg.project.work_dir / "evidence" / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def _delivery(cfg: Config, variant: str) -> Path:
    p = cfg.project.out_dir / f"{cfg.project.name}_{variant}.mp4"
    if not p.exists():
        raise ConfigError(
            f"no delivered file for variant {variant!r}: {p}",
            hint="Run `cutlist build` first.",
        )
    return p


def contact_sheet(
    cfg: Config,
    clip: str,
    *,
    fps: float = 1.0,
    tile: str = "6x5",
    start: float = 0.0,
    end: float | None = None,
) -> Path:
    """A grid of frames for choosing in and out points.

    The cell arithmetic is the point: at `fps` sampling in a CxR grid, cell
    (row, col) zero-indexed is (row*C + col) / fps seconds into the window. That
    makes every timecode derivable rather than guessed, which matters because
    the ffmpeg build many people have cannot burn timestamps into the frames --
    `drawtext` needs libfreetype, and one widely distributed build ships without
    it.
    """
    src = _resolve_source(cfg, clip)
    out = _evidence_dir(cfg, "sheets") / f"{Path(clip).stem}-{fps:g}fps.jpg"

    argv = ["-ss", f"{start:.3f}"]
    if end is not None:
        argv += ["-t", f"{max(0.1, end - start):.3f}"]
    argv += [
        "-i", str(src),
        "-vf", f"fps={fps},scale=320:-1,tile={tile}",
        "-frames:v", "1",
        "-qscale:v", "3",
        str(out),
    ]
    _run_ffmpeg(argv, f"contact sheet for {clip}")
    return out


def measure_frame(
    cfg: Config,
    segment: str,
    *,
    at: float,
    grid: bool = False,
    crop: str | None = None,
    zoom: float = 1.0,
    variant: str = "final",
) -> Path:
    """One frame from the delivered render, optionally with a coordinate grid.

    Taken from the delivery rather than a shot intermediate so the coordinates
    read off it are the coordinates a redaction box needs.
    """
    seg = next((s for s in cfg.segments if s.id == segment), None)
    if seg is None:
        raise ConfigError(
            f"unknown segment {segment!r}",
            hint=f"Defined: {', '.join(s.id for s in cfg.segments)}",
        )

    # Segment starts, never a running sum of durations. A transition overlaps
    # its two shots, so after the first one every naive offset lands late -- and
    # a frame sampled from the wrong shot looks like a rendering bug rather than
    # a measurement bug, which is a long way to chase.
    t = segment_starts(cfg)[cfg.segments.index(seg)] + at

    src = _delivery(cfg, variant)
    out = _evidence_dir(cfg, "frames") / f"{segment}-{at:.2f}s{'-grid' if grid else ''}.png"

    vf: list[str] = []
    if crop:
        parts = [p.strip() for p in crop.split(",")]
        if len(parts) != 4:
            raise ConfigError("--crop must be X,Y,W,H")
        x, y, w, h = parts
        vf.append(f"crop={w}:{h}:{x}:{y}")
    if zoom and abs(zoom - 1.0) > 1e-6:
        vf.append(f"scale=iw*{zoom:g}:ih*{zoom:g}:flags=neighbor")
    if grid:
        if not capabilities().has_filter("drawgrid"):
            raise RenderError(
                "this ffmpeg has no drawgrid filter, so a coordinate grid cannot be drawn.",
                hint="Use --crop and --zoom, and read coordinates from the crop origin.",
            )
        # Two lattices: a fine one for reading a position, a coarse one for
        # counting without losing your place.
        vf.append("drawgrid=w=50:h=50:t=1:c=0xffffff@0.30")
        vf.append("drawgrid=w=250:h=250:t=2:c=0xff9020@0.65")

    argv = ["-ss", f"{t:.4f}", "-i", str(src), "-frames:v", "1"]
    if vf:
        argv += ["-vf", ",".join(vf)]
    argv += [str(out)]
    _run_ffmpeg(argv, f"measure frame {segment}@{at}")
    return out


def boundary_stack(cfg: Config, variant: str = "final", pad: float = 0.07) -> list[Path]:
    """For every timed window, the frames just outside and just inside its edges.

    This is the instrument that catches the failures the middle of a window
    hides. Interpolated geometry and window rounding go wrong at the edges, so a
    mid-window spot check passes on a redaction that exposes its subject in the
    first and last tenth of a second.
    """
    src = _delivery(cfg, variant)
    out_dir = _evidence_dir(cfg, "boundaries")
    made: list[Path] = []

    starts = segment_starts(cfg)
    for seg, offset in zip(cfg.segments, starts, strict=True):
        windows = [(f"redact{i}", r.from_s, r.to_s) for i, r in enumerate(seg.redact)]
        windows += [(f"mute{i}", w.from_s, w.to_s) for i, w in enumerate(seg.audio.windows)]

        for label, w_from, w_to in windows:
            times = [
                ("before", offset + max(0.0, w_from - pad)),
                ("in-start", offset + min(seg.duration_s, w_from + pad)),
                ("in-end", offset + max(0.0, w_to - pad)),
                ("after", offset + min(seg.duration_s, w_to + pad)),
            ]
            frames: list[Path] = []
            for tag, t in times:
                f = out_dir / f"_{seg.id}-{label}-{tag}.png"
                _run_ffmpeg(
                    ["-ss", f"{t:.4f}", "-i", str(src), "-frames:v", "1",
                     "-vf", "scale=640:-1", str(f)],
                    f"boundary frame {seg.id} {label} {tag}",
                )
                frames.append(f)

            stacked = out_dir / f"{seg.id}-{label}.png"
            argv: list[str] = []
            for f in frames:
                argv += ["-i", str(f)]
            argv += ["-filter_complex", f"{''.join(f'[{i}]' for i in range(len(frames)))}"
                                       f"vstack=inputs={len(frames)}", str(stacked)]
            _run_ffmpeg(argv, f"boundary stack {seg.id} {label}")
            for f in frames:
                f.unlink(missing_ok=True)
            made.append(stacked)

    return made


def band_montage(
    cfg: Config,
    segment: str,
    *,
    crop: str,
    step: float = 0.15,
    variant: str = "final",
) -> Path:
    """One crop band per sample, stacked -- a whole trajectory in one image."""
    seg = next((s for s in cfg.segments if s.id == segment), None)
    if seg is None:
        raise ConfigError(f"unknown segment {segment!r}")
    parts = [p.strip() for p in crop.split(",")]
    if len(parts) != 4:
        raise ConfigError("--crop must be X,Y,W,H")
    x, y, w, h = parts

    offset = segment_starts(cfg)[cfg.segments.index(seg)]
    src = _delivery(cfg, variant)
    out_dir = _evidence_dir(cfg, "montage")

    n = max(2, min(12, int(seg.duration_s / step)))
    frames: list[Path] = []
    for i in range(n):
        t = offset + (i + 0.5) * seg.duration_s / n
        f = out_dir / f"_{segment}-{i:02d}.png"
        _run_ffmpeg(
            ["-ss", f"{t:.4f}", "-i", str(src), "-frames:v", "1",
             "-vf", f"crop={w}:{h}:{x}:{y},scale=760:-1", str(f)],
            f"band {segment} {i}",
        )
        frames.append(f)

    out = out_dir / f"{segment}-band.png"
    argv: list[str] = []
    for f in frames:
        argv += ["-i", str(f)]
    argv += ["-filter_complex",
             f"{''.join(f'[{i}]' for i in range(len(frames)))}vstack=inputs={len(frames)}",
             str(out)]
    _run_ffmpeg(argv, f"band montage {segment}")
    for f in frames:
        f.unlink(missing_ok=True)
    return out


def sample_grain(cfg: Config, clip: str, *, start: float, end: float, out: str) -> Path:
    """Sample room tone from a clean stretch of a clip.

    From the *same* clip the hole is in, so microphone, level and timbre already
    match and no correction is needed. A stock ambience file has none of those
    properties and sounds like what it is.

    The output is written with a leading underscore by convention, marking it as
    derived rather than original -- the source directory should stay exactly as
    it was delivered.
    """
    src = _resolve_source(cfg, clip)
    dest = Path(out)
    if not dest.is_absolute():
        dest = cfg.project.media_dir / out
    if not dest.name.startswith("_"):
        dest = dest.with_name("_" + dest.name)

    dur = end - start
    if dur <= 0:
        raise ConfigError("--to must be greater than --from")

    # Short fades at both ends so the sample loops and butts cleanly against
    # real audio without a click at the join.
    fade = min(0.15, dur / 4)
    _run_ffmpeg(
        ["-ss", f"{start:.4f}", "-t", f"{dur:.4f}", "-i", str(src),
         "-vn", "-af", f"afade=t=in:d={fade:.3f},afade=t=out:st={dur - fade:.3f}:d={fade:.3f}",
         "-ar", "48000", "-ac", "2", str(dest)],
        f"sample grain from {clip}",
    )
    return dest


def build_evidence(
    cfg: Config,
    *,
    variant: str = "final",
    boundaries: bool = False,
    audio: bool = False,
) -> list[Path]:
    """The default pack: frames at every segment start, plus what was asked for."""
    src = _delivery(cfg, variant)
    made: list[Path] = []

    out_dir = _evidence_dir(cfg, "frames")
    for seg, offset in zip(cfg.segments, segment_starts(cfg), strict=True):
        t = offset + seg.duration_s / 2
        f = out_dir / f"{seg.id}-mid.png"
        _run_ffmpeg(
            ["-ss", f"{t:.4f}", "-i", str(src), "-frames:v", "1",
             "-vf", "scale=960:-1", str(f)],
            f"evidence frame {seg.id}",
        )
        made.append(f)

    if boundaries:
        made += boundary_stack(cfg, variant)

    if audio:
        from .verify import measure_silence

        report = _evidence_dir(cfg, "audio") / "silence.txt"
        windows = measure_silence(src)
        report.write_text(
            "\n".join(f"{s:.3f} - {e:.3f}" for s, e in windows) or "(no silence detected)",
            encoding="utf-8",
        )
        made.append(report)

    return made
