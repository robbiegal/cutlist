"""Stage 3: join the shots, mix the bus, encode once.

Everything that spans a cut lives here, because nothing that spans a cut can be
decided while looking at a single shot: a crossfade needs both sides, a music
bed runs underneath all of them, a duck is keyed on the assembled dialogue, and
a loudness pass is meaningless on a fragment.

The join is a pairwise reduction rather than one N-way concat, because a
timeline mixes hard cuts and transitions and the two need different operators
at each junction. Shots are already identical in resolution, rate, pixel format
and sample rate -- conform guaranteed that -- so concatenation is safe without
further conditioning.

The output is encoded exactly once, here. Never a stream copy of separately
encoded pieces: that requires them to agree bit-for-bit on profile, level and
timebase, and when they do not the result probes clean and plays wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .audio import duck_filter
from .config import Config
from .errors import ConfigError
from .graph import Graph, db_to_gain
from .render import _resolve_source, _run_ffmpeg
from .tools import capabilities

# xfade's named transitions. `dissolve` is offered as a friendly alias for the
# plain crossfade because that is what an editor calls it.
_XFADE_ALIASES = {"dissolve": "fade", "crossfade": "fade", "cut": "fade"}


@dataclass
class Assembly:
    path: Path
    duration_s: float
    frames: int
    overlap_s: float


def _transition_name(kind: str) -> str:
    return _XFADE_ALIASES.get(kind, kind)


def r_pix(cfg: Config) -> str:
    return cfg.render.pix_fmt


def segment_starts(cfg: Config) -> list[float]:
    """Where each segment actually begins on the finished timeline.

    Not the running sum of durations. A transition overlaps its two shots, so
    every segment after one starts earlier than its nominal position by the
    total transition time before it. Anything that maps a segment-relative time
    onto the delivered file -- a silence assertion, an evidence frame, a
    boundary stack -- has to use this, or it samples the wrong moment and
    reports a correct render as broken.
    """
    starts: list[float] = []
    t = 0.0
    for i, seg in enumerate(cfg.segments):
        if i > 0:
            prev = cfg.segments[i - 1]
            t += prev.duration_s
            if not seg.transition_in.is_cut:
                t -= min(seg.transition_in.duration_s, prev.duration_s, seg.duration_s)
        starts.append(t)
    return starts


def timeline_duration(cfg: Config) -> tuple[float, float]:
    """(finished duration, total overlap).

    A transition overlaps its two shots, so the finished timeline is shorter
    than the sum of the segments by the total transition time. Reporting this
    explicitly is what stops the frame-count assertion from failing on a
    correct render.
    """
    overlap = sum(
        s.transition_in.duration_s
        for i, s in enumerate(cfg.segments)
        if i > 0 and not s.transition_in.is_cut
    )
    return cfg.total_duration_s - overlap, overlap


def assemble(
    cfg: Config,
    shots: list[Path],
    *,
    variant: str,
    graph_dir: Path,
    out_path: Path,
) -> Assembly:
    if len(shots) != len(cfg.segments):
        raise ConfigError(f"expected {len(cfg.segments)} shots, got {len(shots)}")

    proj = cfg.project
    g = Graph()

    # Normalise every input's timebase and start time before joining anything.
    #
    # This is not defensive tidying. `xfade` refuses outright when its two
    # inputs disagree -- "First input link main timebase (1/1000000) do not
    # match the corresponding second input link xfade timebase (1/1000)" -- and
    # they *will* disagree as soon as a timeline mixes cuts and transitions,
    # because `concat` emits at a different timebase than a container demuxes
    # at. The failure appears only at the second junction, so a timeline with
    # one transition works and the same timeline with two does not.
    #
    # `setpts=PTS-STARTPTS` rebases each shot to zero as well, so a shot whose
    # container carries a non-zero start does not push everything after it.
    norm_v: list[str] = []
    norm_a: list[str] = []
    for i in range(len(shots)):
        norm_v.append(g.chain(f"{i}:v", "settb=AVTB,setpts=PTS-STARTPTS", "nv"))
        norm_a.append(g.chain(f"{i}:a", "asettb=AVTB,asetpts=PTS-STARTPTS", "na"))

    acc_v = norm_v[0]
    acc_a = norm_a[0]
    acc_dur = cfg.segments[0].duration_s

    for i in range(1, len(shots)):
        seg = cfg.segments[i]
        nxt_v, nxt_a = norm_v[i], norm_a[i]
        trans = seg.transition_in

        if trans.is_cut:
            out_v, out_a = g.label("cv"), g.label("ca")
            g.add([acc_v, nxt_v], "concat=n=2:v=1:a=0", [out_v])
            g.add([acc_a, nxt_a], "concat=n=2:v=0:a=1", [out_a])
            acc_dur += seg.duration_s
        else:
            d = min(trans.duration_s, acc_dur, seg.duration_s)
            # offset is where the transition begins in the accumulated stream.
            offset = max(0.0, acc_dur - d)
            out_v, out_a = g.label("xv"), g.label("xa")
            g.add(
                [acc_v, nxt_v],
                f"xfade=transition={_transition_name(trans.kind)}"
                f":duration={d:.4f}:offset={offset:.4f}",
                [out_v],
            )
            # The audio side crossfades over the same span, or the picture
            # dissolves while the sound hard-cuts, which is audible.
            g.add([acc_a, nxt_a], f"acrossfade=d={d:.4f}:c1=tri:c2=tri", [out_a])
            acc_dur = acc_dur + seg.duration_s - d

        acc_v, acc_a = out_v, out_a

    inputs = list(shots)

    # --- the bus -------------------------------------------------------
    bus = cfg.audio
    if bus.bed:
        bed_path = _resolve_source(cfg, bus.bed)
        bed_idx = len(inputs)
        inputs.append(bed_path)

        bed = g.chain(
            f"{bed_idx}:a",
            f"aloop=loop=-1:size=2e9,atrim=0:{acc_dur:.4f},asetpts=PTS-STARTPTS,"
            f"volume={db_to_gain(bus.bed_gain_db):.6f}",
            "bed",
        )

        if bus.duck and capabilities().has_filter("sidechaincompress"):
            # The dialogue is both the thing we hear and the key that pushes the
            # bed down, so it is split: one copy into the mix, one into the
            # sidechain.
            key_a, mix_a = g.label("dk"), g.label("dm")
            g.add([acc_a], "asplit=2", [mix_a, key_a])
            ducked = g.label("bd")
            g.add([bed, key_a], duck_filter(bus), [ducked])
            bed, acc_a = ducked, mix_a

        mixed = g.label("mx")
        g.add([acc_a, bed], "amix=inputs=2:duration=first:normalize=0", [mixed])
        acc_a = mixed

    if bus.loudnorm and capabilities().has_filter("loudnorm"):
        acc_a = g.chain(
            acc_a, f"loudnorm=I={bus.target_lufs:.1f}:TP=-1.5:LRA=11", "ln"
        )

    # Terminal chains, always. A single-segment timeline with no bed reaches
    # here with the labels still pointing at input streams (`0:v`, `0:a`), which
    # are not outputs of any chain -- ffmpeg then rejects the graph with "does
    # not exist in any defined filter graph". These also pin the delivered
    # format rather than inheriting whatever the last filter happened to output.
    acc_v = g.chain(acc_v, f"format={r_pix(cfg)}", "vf")
    acc_a = g.chain(acc_a, "aformat=sample_rates=48000:channel_layouts=stereo", "af")

    graph_path = graph_dir / f"assemble-{variant}.filter"
    text = g.render().replace(f"[{acc_v}]", "[vout]").replace(f"[{acc_a}]", "[aout]")
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(text, encoding="utf-8")

    argv: list[str] = []
    for p in inputs:
        argv += ["-i", str(p)]

    r = cfg.render
    out_path.parent.mkdir(parents=True, exist_ok=True)
    argv += [
        "-filter_complex_script", str(graph_path),
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", r.vcodec,
        "-crf", str(r.crf),
        "-preset", r.preset,
        "-pix_fmt", r.pix_fmt,
        "-g", str(r.gop),
        "-c:a", r.acodec,
        "-b:a", r.abr,
        "-movflags", "+faststart",
        str(out_path),
    ]
    _run_ffmpeg(argv, f"assemble {variant}")

    frames = int(round(acc_dur * proj.fps))
    _, overlap = timeline_duration(cfg)
    return Assembly(path=out_path, duration_s=acc_dur, frames=frames, overlap_s=overlap)
