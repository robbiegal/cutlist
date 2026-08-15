"""The three stages: conform, shot, assemble.

    conform   each source's used range -> a constant-rate lossless intermediate
    shot      one segment -> one lossless intermediate, cached by content
    assemble  every shot -> concat, transitions, audio bus, one delivery encode

**Why conform exists.** Source footage lies about time. A phone clip reports a
nominal 30 fps while its actual average is something else, so seeking it by
seconds lands on a different frame than arithmetic predicts -- enough drift to
move a tracked box off its subject. It also carries rotation metadata, a sample
aspect that is not always square, and an arbitrary pixel format. Conform settles
all of it once. After this stage, frame N means frame N.

**Why the shot stage is the unit of work.** A cut is revised dozens of times and
almost every revision touches one segment. Rebuilding a timeline to change one
trim is the difference between a loop that is usable and one that is not.

**Why assemble always re-encodes.** Concatenating separately encoded pieces
without re-encoding requires them to agree bit-for-bit on profile, level, pixel
format and timebase. A still, a graphics-only shot and a footage shot do not
agree, and the resulting file probes perfectly while playing wrong in some
players and not others. Re-encoding once at the end costs a single generation
and removes the entire class.

A transition **overlaps** its two shots, so a 0.5 s dissolve makes the finished
timeline 0.5 s shorter than the sum of its segments. That is how an NLE behaves
when one clip is dragged over another, and the verifier accounts for it rather
than reporting a frame-count mismatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .audio import apply_segment_audio
from .cache import Cache
from .config import Config, Layer, Segment
from .errors import ConfigError, RenderError
from .geometry import fit
from .grade import apply_grade
from .graph import Graph
from .probe import MediaFacts, probe
from .redact import apply_redactions
from .tools import find_tool, run

# Lossless intermediates. FFV1 is free of patent questions, is genuinely
# lossless, and decodes fast enough that the shot stage is not I/O bound.
# `-level 3 -g 1` makes every frame a keyframe, which is what allows an exact
# seek into a shot when extracting evidence.
INTERMEDIATE_V = ["-c:v", "ffv1", "-level", "3", "-g", "1"]
INTERMEDIATE_A = ["-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2"]
INTERMEDIATE_EXT = ".mkv"


def _run_ffmpeg(argv: list[str], what: str) -> None:
    proc = run([find_tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", *argv])
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        detail = "\n    ".join(tail[-6:]) if tail else "(no stderr)"
        raise RenderError(
            f"{what} failed (ffmpeg exit {proc.returncode}).\n    {detail}",
            hint="If the output file is open in a media player, close it and retry.",
        )


@dataclass
class Conformed:
    """One conformed window of one source."""

    layer_key: str
    path: Path
    facts: MediaFacts
    duration_s: float
    has_audio: bool


def _resolve_source(cfg: Config, name: str) -> Path:
    p = Path(name)
    if not p.is_absolute():
        p = cfg.project.media_dir / name
    if not p.exists():
        raise ConfigError(
            f"source not found: {p}",
            hint=f"Paths resolve against {cfg.project.media_dir}",
        )
    return p


# --------------------------------------------------------------------------
# stage 1: conform
# --------------------------------------------------------------------------


def conform_layer(cfg: Config, seg: Segment, layer: Layer, cache: Cache) -> Conformed:
    """Conform one clip or still layer's used range."""
    src = _resolve_source(cfg, layer.source or "")
    facts = probe(src)
    proj = cfg.project

    duration = seg.duration_s
    place = fit(*(facts.display_size if facts.has_video else (proj.width, proj.height)),
                proj.width, proj.height, layer.fit)

    spec = {
        "stage": "conform",
        "in": round(layer.in_s, 4),
        "duration": round(duration, 4),
        "fit": layer.fit,
        "canvas": [proj.width, proj.height],
        "fps": proj.fps,
        "scale": [place.scale_w, place.scale_h],
        "kind": layer.kind,
    }
    key = cache.key(spec, [src])
    out = cache.path_for("conform", f"{seg.id}-{layer.z}", key, INTERMEDIATE_EXT)
    if cache.valid(out):
        return Conformed(f"{seg.id}:{layer.z}", out, facts, duration, facts.has_audio)

    # ffmpeg's command line is positional: every option applies to the file that
    # follows it, so ALL inputs must be assembled before ANY output option. Mix
    # the two and it reports "you are trying to apply an input option to an
    # output file", which names the symptom and not the ordering mistake.
    argv: list[str] = []
    if layer.kind == "still" or facts.is_image:
        # A still has no timeline of its own; loop it for exactly the segment.
        argv += ["-loop", "1", "-framerate", f"{proj.fps}", "-i", str(src)]
    else:
        # Input seeking. Accurate since ffmpeg 2.1 -- it seeks then decodes to
        # the exact position -- and far cheaper than decoding from zero.
        argv += ["-ss", f"{layer.in_s:.6f}", "-i", str(src)]

    source_has_audio = facts.has_audio and layer.kind == "clip"
    if not source_has_audio:
        # Every conformed window carries an audio stream, silent if the source
        # has none. A timeline where some shots have audio and others do not
        # needs special-casing at every later step; giving everything a stream
        # costs almost nothing and removes that entirely.
        argv += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]

    vf = []
    if place.scale_w != proj.width or place.scale_h != proj.height:
        vf.append(f"scale={place.scale_w}:{place.scale_h}:flags=bicubic")
    if layer.fit == "cover":
        vf.append(f"crop={proj.width}:{proj.height}")
    else:
        vf.append(
            f"pad={proj.width}:{proj.height}:{place.picture.x}:{place.picture.y}:color=black"
        )
    # fps and setsar last: the rate conform is the entire point of this stage,
    # and a non-square sample aspect surviving into the intermediate would make
    # every downstream pixel coordinate wrong.
    vf += [f"fps={proj.fps}", "setsar=1"]

    argv += ["-t", f"{duration:.6f}", "-vf", ",".join(vf)]
    argv += ["-map", "0:v:0", "-map", "0:a:0" if source_has_audio else "1:a:0"]
    argv += INTERMEDIATE_V
    argv += INTERMEDIATE_A
    argv += [str(out)]
    _run_ffmpeg(argv, f"conform {seg.id} layer z={layer.z}")
    return Conformed(f"{seg.id}:{layer.z}", out, facts, duration, True)


def conform_color(cfg: Config, seg: Segment, layer: Layer, cache: Cache) -> Conformed:
    """Render a flat colour layer as a conformed window."""
    proj = cfg.project
    colour = layer.value.lstrip("#")
    spec = {
        "stage": "conform-color",
        "value": colour,
        "duration": round(seg.duration_s, 4),
        "canvas": [proj.width, proj.height],
        "fps": proj.fps,
    }
    key = cache.key(spec, [])
    out = cache.path_for("conform", f"{seg.id}-{layer.z}-color", key, INTERMEDIATE_EXT)
    if cache.valid(out):
        return Conformed(f"{seg.id}:{layer.z}", out, MediaFacts(Path(out), seg.duration_s),
                         seg.duration_s, True)

    argv = [
        "-f", "lavfi",
        "-i",
        f"color=c=0x{colour}:s={proj.width}x{proj.height}"
        f":r={proj.fps}:d={seg.duration_s:.6f}",
        "-f", "lavfi",
        "-i", "anullsrc=r=48000:cl=stereo",
        "-t", f"{seg.duration_s:.6f}",
        "-vf", "setsar=1",
        *INTERMEDIATE_V,
        *INTERMEDIATE_A,
        str(out),
    ]
    _run_ffmpeg(argv, f"conform colour layer in {seg.id}")
    return Conformed(f"{seg.id}:{layer.z}", out, MediaFacts(Path(out), seg.duration_s),
                     seg.duration_s, True)


# --------------------------------------------------------------------------
# stage 2: shot
# --------------------------------------------------------------------------


def build_shot(
    cfg: Config,
    seg: Segment,
    cache: Cache,
    *,
    variant_tags: tuple[str, ...],
    graph_dir: Path,
    force: bool = False,
) -> Path:
    """Build one segment into a cached lossless intermediate."""
    proj = cfg.project

    layers = sorted(
        (ly for ly in seg.layers if _layer_included(ly, variant_tags)),
        key=lambda ly: ly.z,
    )
    if not layers:
        raise ConfigError(
            f"segment {seg.id!r} has no layers left after variant filtering.",
            hint="A variant that removes every layer from a segment produces nothing to show.",
        )

    conformed: list[tuple[Layer, Conformed]] = []
    for ly in layers:
        if ly.kind in ("clip", "still"):
            conformed.append((ly, conform_layer(cfg, seg, ly, cache)))
        elif ly.kind == "color":
            conformed.append((ly, conform_color(cfg, seg, ly, cache)))
        elif ly.kind == "scene":
            conformed.append((ly, _conform_scene(cfg, seg, ly, cache)))

    spec = {
        "stage": "shot",
        "id": seg.id,
        "duration": round(seg.duration_s, 4),
        "canvas": [proj.width, proj.height],
        "fps": proj.fps,
        "grade": {
            "enabled": cfg.grade.enabled,
            "eq": cfg.grade.eq,
            "cb": cfg.grade.colorbalance,
            "curves": cfg.grade.curves,
        },
        "redact": [
            {
                "b": [r.x, r.y, r.w, r.h],
                "to": [r.end_x, r.end_y],
                "t": [round(r.from_s, 4), round(r.to_s, 4)],
                "m": r.mode,
                "mg": r.margin_px,
                "s": r.strength,
            }
            for r in seg.redact
        ],
        "audio": {
            "mute": seg.audio.mute,
            "gain_db": seg.audio.gain_db,
            "windows": [
                [round(w.from_s, 4), round(w.to_s, 4), round(w.fade_s, 4), w.fill or ""]
                for w in seg.audio.windows
            ],
        },
        "layers": [
            {"z": ly.z, "kind": ly.kind, "opacity": ly.opacity, "fit": ly.fit, "box": ly.box}
            for ly in layers
        ],
    }
    inputs = [c.path for _, c in conformed]
    key = cache.key(spec, inputs)
    out = cache.path_for("shots", seg.id, key, INTERMEDIATE_EXT)
    if cache.valid(out) and not force:
        return out

    g = Graph()

    # Video: composite in ascending z. The lowest layer is the ground; each
    # further layer is overlaid on the accumulating result.
    base_label = "0:v"
    for idx, (ly, _c) in enumerate(conformed):
        if idx == 0:
            base_label = f"{idx}:v"
            continue
        over = f"{idx}:v"
        if ly.opacity < 1.0:
            over = g.chain(
                over,
                f"format=rgba,colorchannelmixer=aa={ly.opacity:.4f}",
                "op",
            )
        x, y = (ly.box[0], ly.box[1]) if ly.box else (0, 0)
        merged = g.label("cm")
        g.add([base_label, over], f"overlay=x={x}:y={y}:format=auto", [merged])
        base_label = merged

    # Grade before redaction. A mosaic is a destructive local operation; grading
    # after it would push the obscured block's colour around and make its edge
    # visible against the graded picture.
    cur = apply_grade(g, base_label, cfg.grade)
    cur = apply_redactions(g, cur, seg.redact, proj.width, proj.height)
    cur = g.chain(cur, f"format={cfg.render.pix_fmt}", "fmt")
    vout = cur

    # Audio: the base layer's audio is the shot's audio. Additional layers are
    # picture; a mix of many sources per shot is deliberately not offered here,
    # because the place to mix is the bus, where the whole timeline is visible.
    #
    # `aformat` is applied unconditionally, and not only to normalise the
    # stream. A shot needing no audio treatment would otherwise leave the label
    # as the input reference `0:a`, which is not an output of any chain -- and
    # ffmpeg rejects the whole graph with "does not exist in any defined filter
    # graph". A terminal chain guarantees there is always something to map.
    aout = apply_segment_audio(g, "0:a", seg.audio, seg.duration_s)
    aout = g.chain(aout, "aformat=sample_rates=48000:channel_layouts=stereo", "af")

    for w in seg.audio.windows:
        if not w.fill:
            continue
        fill_path = _resolve_source(cfg, w.fill)
        prior = seg.audio.windows[: seg.audio.windows.index(w)]
        fill_idx = len(conformed) + sum(1 for x in prior if x.fill)
        inputs.append(fill_path)
        delayed = g.chain(
            f"{fill_idx}:a",
            f"adelay={int(w.from_s * 1000)}|{int(w.from_s * 1000)},"
            f"atrim=0:{w.to_s:.4f},asetpts=PTS-STARTPTS",
            "fl",
        )
        mixed = g.label("am")
        g.add([aout, delayed], "amix=inputs=2:duration=first:normalize=0", [mixed])
        aout = mixed

    graph_path = graph_dir / f"{seg.id}.filter"
    text = g.render()
    text = text.replace(f"[{vout}]", "[vout]").replace(f"[{aout}]", "[aout]")
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(text, encoding="utf-8")

    argv: list[str] = []
    for _ly, c in conformed:
        argv += ["-i", str(c.path)]
    for w in seg.audio.windows:
        if w.fill:
            argv += ["-i", str(_resolve_source(cfg, w.fill))]

    argv += [
        "-filter_complex_script", str(graph_path),
        "-map", "[vout]",
        "-map", "[aout]",
        "-t", f"{seg.duration_s:.6f}",
        *INTERMEDIATE_V,
        *INTERMEDIATE_A,
        str(out),
    ]
    _run_ffmpeg(argv, f"shot {seg.id}")
    return out


def _layer_included(layer: Layer, variant_tags: tuple[str, ...]) -> bool:
    """A variant with no tags takes everything; otherwise tags must intersect.

    An untagged layer is always included, so adding a variant never silently
    removes something the author never labelled.
    """
    if not variant_tags:
        return True
    if not layer.tags:
        return True
    return any(t in variant_tags for t in layer.tags)


def _conform_scene(cfg: Config, seg: Segment, layer: Layer, cache: Cache) -> Conformed:
    """Render a graphics scene to a conformed window with alpha."""
    from .text import render_scene  # local import: Pillow is an optional extra

    proj = cfg.project
    scene = cfg.scenes[layer.name or ""]
    png = render_scene(cfg, layer.name or "", scene, cache)

    spec = {
        "stage": "conform-scene",
        "scene": layer.name,
        "duration": round(seg.duration_s, 4),
        "fps": proj.fps,
        "time_fit": layer.time_fit,
    }
    key = cache.key(spec, [png])
    out = cache.path_for("conform", f"{seg.id}-{layer.z}-scene", key, INTERMEDIATE_EXT)
    if cache.valid(out):
        return Conformed(f"{seg.id}:{layer.z}", out, MediaFacts(out, seg.duration_s),
                         seg.duration_s, True)

    argv = [
        "-loop", "1", "-framerate", f"{proj.fps}", "-i", str(png),
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", f"{seg.duration_s:.6f}",
        "-vf", f"fps={proj.fps},setsar=1,format=rgba",
        "-c:v", "ffv1", "-level", "3", "-g", "1",
        *INTERMEDIATE_A,
        str(out),
    ]
    _run_ffmpeg(argv, f"scene {layer.name} for {seg.id}")
    return Conformed(f"{seg.id}:{layer.z}", out, MediaFacts(out, seg.duration_s),
                     seg.duration_s, True)


def conform_all(
    cfg: Config,
    cache: Cache,
    *,
    only: list[str] | None = None,
    force: bool = False,
) -> list[tuple[str, Path, bool]]:
    """Conform every window the timeline uses, without building shots.

    Useful on its own: it is the slow, purely mechanical part of a build, so
    running it once up front means the first real edit iteration is fast. It
    also surfaces a missing or unreadable source immediately, rather than
    several minutes into a render.

    Returns (segment id, path) per conformed window.
    """
    out: list[tuple[str, Path]] = []
    for seg in cfg.segments:
        if only and seg.id not in only:
            continue
        for layer in sorted(seg.layers, key=lambda ly: ly.z):
            # Graphics scenes are not conformed here. They are rendered from
            # their definition rather than from a source file, so they belong to
            # the shot stage, where the segment's length is already known.
            if layer.kind == "scene":
                continue

            builder = conform_layer if layer.kind in ("clip", "still") else conform_color
            if force:
                # `--force` means "distrust the cache". Deleting the entry this
                # call would otherwise hit is simpler than threading a bypass
                # flag through every builder, and it leaves nothing stale behind
                # if the rebuild then fails.
                builder(cfg, seg, layer, cache).path.unlink(missing_ok=True)

            out.append((seg.id, builder(cfg, seg, layer, cache).path))
    return out
