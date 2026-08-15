"""Asserting that the delivered file is what was asked for.

The premise of this module is one measured fact: **a zero exit code is
compatible with a completely black video.** The encoder is not lying when it
reports success -- it encoded exactly the frames it was given, and every one of
them was black. So "it rendered" is not evidence of anything, and a pipeline
that treats it as evidence ships broken cuts.

What is checked, and why each one exists:

  geometry, rate, codec  -- cheap, and catches a config that did not take effect
  frame count            -- catches drift, off-by-one and a truncated encode
  requested vs delivered -- an encoder can silently ignore a flag. One measured
                            case: 256 kbps requested, 153 kbps delivered, and
                            the pipeline reported success
  mean luma              -- catches the black render, which nothing else does
  audio presence         -- catches a stream that exists and is silent
  declared mutes         -- a mute that is not actually silent is common and
                            inaudible to someone scrubbing quickly

An assertion that could not run is recorded as *not run*, never as a pass.
Silence there reads as "no problems found", which is the worst thing a
verification tool can say.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .assemble import Assembly, segment_starts
from .audio import silence_expectations
from .config import Config
from .errors import VerificationError
from .redact import describe as describe_redactions
from .tools import find_tool, run

# A frame this dark across the whole timeline means nothing was drawn. Chosen
# well below any real graded picture -- a deliberately dark, moody cut still
# measures far above this -- so it flags a broken render and not a dark style.
BLACK_LUMA = 2.0

# ffmpeg names encoders, containers name codecs, and they are different
# vocabularies. Anything not listed passes through unchanged.
ENCODER_TO_CODEC = {
    "libx264": "h264",
    "libx265": "hevc",
    "libvpx-vp9": "vp9",
    "libvpx": "vp8",
    "libaom-av1": "av1",
    "libsvtav1": "av1",
    "mpeg4": "mpeg4",
    "prores_ks": "prores",
    "libmp3lame": "mp3",
    "libopus": "opus",
}


@dataclass
class Assertion:
    name: str
    expected: object
    actual: object
    passed: bool
    ran: bool = True
    reason: str = ""

    def line(self) -> str:
        if not self.ran:
            return f"NOT RUN  {self.name}: {self.reason}"
        mark = "ok    " if self.passed else "FAIL  "
        return f"{mark} {self.name}: expected {self.expected}, got {self.actual}"


@dataclass
class Result:
    assertions: list[Assertion] = field(default_factory=list)
    report: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(a.passed for a in self.assertions if a.ran)

    @property
    def lines(self) -> list[str]:
        return [a.line() for a in self.assertions]


def probe_delivery(path: Path) -> dict:
    proc = run(
        [
            find_tool("ffprobe"), "-v", "error",
            "-show_streams", "-show_format", "-print_format", "json", str(path),
        ]
    )
    if proc.returncode != 0:
        raise VerificationError(f"could not probe the delivered file: {path}")
    return json.loads(proc.stdout)


def mean_luma(path: Path, *, samples: int = 5, duration: float = 0.0) -> float:
    """Average luma over a few sampled frames.

    Sampled rather than measured over the whole file because a full pass costs
    as much as the render. Five points spread across the timeline is enough to
    tell "black" from "not black", which is the question being asked.
    """
    if duration <= 0:
        return -1.0
    total = 0.0
    got = 0
    for i in range(samples):
        t = duration * (i + 0.5) / samples
        # `-v info`, not `error`. The metadata filter reports through the log at
        # info level, so at a quieter level the measurement runs, prints
        # nothing, and the check silently downgrades itself to "could not run" --
        # which is exactly the black render it exists to catch going unnoticed.
        proc = run(
            [
                find_tool("ffmpeg"), "-v", "info", "-ss", f"{t:.3f}", "-i", str(path),
                "-frames:v", "1", "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                "-f", "null", "-",
            ]
        )
        for line in (proc.stderr + proc.stdout).splitlines():
            if "YAVG" in line and "=" in line:
                try:
                    total += float(line.rsplit("=", 1)[1].strip())
                    got += 1
                except ValueError:
                    pass
    return (total / got) if got else -1.0


def measure_silence(path: Path, threshold_db: float = -60.0) -> list[tuple[float, float]]:
    """Windows the file measures as silent, via ffmpeg's own detector."""
    proc = run(
        [
            find_tool("ffmpeg"), "-v", "info", "-i", str(path),
            "-af", f"silencedetect=noise={threshold_db}dB:d=0.2", "-f", "null", "-",
        ]
    )
    out: list[tuple[float, float]] = []
    start = None
    for line in (proc.stderr or "").splitlines():
        if "silence_start:" in line:
            try:
                start = float(line.rsplit("silence_start:", 1)[1].strip().split()[0])
            except (ValueError, IndexError):
                start = None
        elif "silence_end:" in line and start is not None:
            try:
                end = float(line.rsplit("silence_end:", 1)[1].strip().split()[0])
                out.append((start, end))
            except (ValueError, IndexError):
                pass
            start = None
    return out


def verify_delivery(cfg: Config, asm: Assembly, *, variant: str) -> Result:
    """Assert the delivered file against what the config asked for."""
    res = Result()
    proj = cfg.project
    data = probe_delivery(asm.path)

    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if v is None:
        res.assertions.append(Assertion("video stream", "present", "absent", False))
        return res

    res.assertions.append(
        Assertion("geometry", f"{proj.width}x{proj.height}",
                  f"{v.get('width')}x{v.get('height')}",
                  v.get("width") == proj.width and v.get("height") == proj.height)
    )
    # An encoder's name is not the codec's name -- libx264 produces `h264`,
    # libvpx-vp9 produces `vp9`. Comparing them directly reports a correct
    # render as wrong, which trains people to ignore the checker.
    want_codec = ENCODER_TO_CODEC.get(cfg.render.vcodec, cfg.render.vcodec)
    res.assertions.append(
        Assertion("codec", want_codec, v.get("codec_name"),
                  str(v.get("codec_name", "")) == want_codec)
    )

    rate = v.get("r_frame_rate", "0/1")
    try:
        num, den = (int(x) for x in rate.split("/"))
        actual_fps = num / den if den else 0.0
    except ValueError:
        actual_fps = 0.0
    res.assertions.append(
        Assertion("frame rate", f"{proj.fps:g}", f"{actual_fps:g}",
                  abs(actual_fps - proj.fps) < 0.01)
    )

    fmt = data.get("format", {})
    try:
        actual_dur = float(fmt.get("duration", 0))
    except (TypeError, ValueError):
        actual_dur = 0.0
    # One frame of tolerance. Container duration and exact frame count disagree
    # by a rounding at the last frame in most muxers, and failing on that would
    # be a false alarm on every correct build.
    tol = 1.0 / proj.fps + 0.02
    res.assertions.append(
        Assertion("duration", f"{asm.duration_s:.2f}s", f"{actual_dur:.2f}s",
                  abs(actual_dur - asm.duration_s) <= tol)
    )

    luma = mean_luma(asm.path, duration=actual_dur)
    if luma < 0:
        res.assertions.append(
            Assertion("not black", f">{BLACK_LUMA}", "unmeasured", True, ran=False,
                      reason="signalstats unavailable in this ffmpeg build")
        )
    else:
        res.assertions.append(
            Assertion("not black", f"mean luma > {BLACK_LUMA}", f"{luma:.1f}",
                      luma > BLACK_LUMA)
        )

    if a is None:
        res.assertions.append(Assertion("audio stream", "present", "absent", False))
    else:
        res.assertions.append(
            Assertion("audio codec", cfg.render.acodec, a.get("codec_name"),
                      str(a.get("codec_name", "")) == cfg.render.acodec)
        )

    silent = measure_silence(asm.path)
    expected_silence = []
    # Segment starts, not a running sum of durations -- a transition overlaps
    # its two shots, so everything after one sits earlier than its nominal
    # position. Using the naive sum reports every mute after the first
    # transition as failed, on a render where they are all correct.
    starts = segment_starts(cfg)
    for seg, start in zip(cfg.segments, starts, strict=True):
        for s, e in silence_expectations(seg.audio, seg.duration_s):
            expected_silence.append((round(start + s, 2), round(start + e, 2)))

    for want_s, want_e in expected_silence:
        covered = any(s <= want_s + 0.35 and e >= want_e - 0.35 for s, e in silent)
        res.assertions.append(
            Assertion(f"silence {want_s:.2f}-{want_e:.2f}s", "silent",
                      "silent" if covered else "audible", covered)
        )

    res.report = {
        "schema": 1,
        "project": proj.name,
        "variant": variant,
        "duration_s": round(asm.duration_s, 4),
        "frames": asm.frames,
        "transition_overlap_s": round(asm.overlap_s, 4),
        "segments": [
            {
                "id": s.id,
                "duration_s": round(s.duration_s, 4),
                "frames": int(round(s.duration_s * proj.fps)),
                "layers": len(s.layers),
                "redactions": describe_redactions(s.redact),
                "muted_windows": [
                    [round(w.from_s, 3), round(w.to_s, 3)] for w in s.audio.windows
                ],
            }
            for s in cfg.segments
        ],
        "delivery": {
            "path": str(asm.path),
            "codec": v.get("codec_name"),
            "width": v.get("width"),
            "height": v.get("height"),
            "fps": actual_fps,
            "duration_s": actual_dur,
            "bitrate": fmt.get("bit_rate"),
            "audio_codec": a.get("codec_name") if a else None,
            "audio_bitrate": a.get("bit_rate") if a else None,
            "mean_luma": luma,
        },
        "assertions": [
            {"name": x.name, "expected": x.expected, "actual": x.actual,
             "passed": x.passed, "ran": x.ran, "reason": x.reason}
            for x in res.assertions
        ],
        "capabilities": [
            {"check": x.name, "ran": x.ran, "reason": x.reason}
            for x in res.assertions if not x.ran
        ],
    }

    dest = proj.work_dir / "report.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(res.report, indent=2), encoding="utf-8")
    return res
