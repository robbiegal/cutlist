"""Reading the truth about a media file, once, and writing it down.

Every downstream decision -- frame arithmetic, pillarbox geometry, whether a
source needs conforming, whether audio exists to mix -- derives from these
numbers. Getting them from ffprobe and persisting them makes the facts auditable
across sessions instead of re-derived from memory or, worse, assumed.

Two facts matter more than the rest and are the reason this module exists at
all:

**Rotation.** Phone footage carries a display matrix. ffmpeg applies it by
default, so a 1280x720 file with a -90 rotation *displays* as 720x1280 and
pillarboxes into a landscape canvas. Every graphics coordinate in a mixed-
orientation project depends on that, so it is computed here rather than
eyeballed.

**Variable frame rate.** A phone clip reports `r_frame_rate 30/1` while its
`avg_frame_rate` is something like 139950000/4663903. Seeking such a file by
seconds lands on a different frame than a conformed constant-rate copy does,
which is easily 100px of drift on a moving subject. That discrepancy is detected
here, and it is what makes the conform stage non-optional.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from .errors import ProbeError
from .tools import find_tool, run


def _fraction(text: str | None) -> Fraction | None:
    if not text or text in ("0/0", "N/A"):
        return None
    try:
        return Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None


@dataclass(frozen=True)
class MediaFacts:
    """Everything the engine needs to know about one source file."""

    path: Path
    duration_s: float

    # Video, absent for audio-only sources.
    width: int = 0
    height: int = 0
    rotation: int = 0
    sar: Fraction = field(default=Fraction(1, 1))
    r_frame_rate: Fraction | None = None
    avg_frame_rate: Fraction | None = None
    pix_fmt: str = ""
    vcodec: str = ""
    nb_frames: int = 0

    # Audio.
    has_audio: bool = False
    sample_rate: int = 0
    channels: int = 0
    acodec: str = ""

    is_image: bool = False

    @property
    def has_video(self) -> bool:
        return self.width > 0 and self.height > 0

    @property
    def display_size(self) -> tuple[int, int]:
        """Size as the viewer sees it: rotation and sample aspect applied.

        This, not the coded size, is what graphics get laid out against.
        """
        w, h = self.width, self.height
        if self.sar and self.sar != 1:
            w = round(w * float(self.sar))
        if self.rotation % 180 == 90:
            w, h = h, w
        return w, h

    # A constant-rate file reports the same nominal and average rate. Anything
    # else means frames are not evenly spaced, which means seeking by seconds
    # does not land where arithmetic says it will.
    #
    # The threshold has to be tight, and 1e-4 is chosen against two real files.
    # A phone clip measured 30 nominal against 139950000/4663903 average -- a
    # 2.4e-4 discrepancy, which reads as negligible and is exactly the file
    # whose seek drift moved a tracked redaction box by about 100px. A genuinely
    # constant 29.97 file reports 30000/1001 against 2997/100, which differ by
    # 1e-6 and must not be flagged.
    #
    # Being wrong in the flagging direction is cheap: conforming an already-
    # constant file costs one transcode. Missing a variable one is a silent
    # correctness bug that only shows up as misplaced geometry much later.
    _VFR_TOLERANCE = 1e-4

    @property
    def is_vfr(self) -> bool:
        """True when frame timestamps cannot be trusted to be evenly spaced."""
        r, a = self.r_frame_rate, self.avg_frame_rate
        if not r or not a or a == 0:
            return False
        if r == a:
            return False
        return abs(float(r) - float(a)) / float(r) > self._VFR_TOLERANCE

    def to_dict(self) -> dict:
        d = {
            "path": str(self.path),
            "duration_s": round(self.duration_s, 6),
            "width": self.width,
            "height": self.height,
            "display_size": list(self.display_size),
            "rotation": self.rotation,
            "sar": f"{self.sar.numerator}/{self.sar.denominator}",
            "r_frame_rate": str(self.r_frame_rate) if self.r_frame_rate else None,
            "avg_frame_rate": str(self.avg_frame_rate) if self.avg_frame_rate else None,
            "is_vfr": self.is_vfr,
            "pix_fmt": self.pix_fmt,
            "vcodec": self.vcodec,
            "nb_frames": self.nb_frames,
            "has_audio": self.has_audio,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "acodec": self.acodec,
            "is_image": self.is_image,
        }
        return d


IMAGE_CODECS = {"png", "mjpeg", "bmp", "gif", "webp", "tiff"}


def _rotation_of(stream: dict) -> int:
    """Extract display rotation, checking both places ffprobe reports it."""
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            try:
                return int(round(float(sd["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    tag = (stream.get("tags") or {}).get("rotate")
    if tag:
        try:
            return int(round(float(tag))) % 360
        except (TypeError, ValueError):
            pass
    return 0


def probe(path: str | Path) -> MediaFacts:
    """Probe one media file, or raise.

    A probe failure is never absorbed into a default. Returning 0.0 on an
    exception turns a mistyped filename into a one-frame clip, a valid-looking
    project and a successful build of the wrong video. Here it stops.
    """
    p = Path(path)
    if not p.exists():
        raise ProbeError(
            f"source not found: {p}",
            hint="Check the path in the config; it resolves relative to the project's media dir.",
        )

    proc = run(
        [
            find_tool("ffprobe"),
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-print_format",
            "json",
            str(p),
        ]
    )
    if proc.returncode != 0:
        raise ProbeError(
            f"ffprobe failed on {p.name} (exit {proc.returncode}).",
            hint=(proc.stderr.strip().splitlines() or ["no stderr"])[-1],
        )
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe returned unparseable JSON for {p.name}: {exc}") from exc

    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None and audio is None:
        raise ProbeError(
            f"{p.name} contains no audio or video stream.",
            hint="Is this actually a media file?",
        )

    fmt = data.get("format") or {}
    duration = 0.0
    for candidate in (fmt.get("duration"), (video or {}).get("duration")):
        try:
            duration = float(candidate)
            break
        except (TypeError, ValueError):
            continue

    kw: dict = {"has_audio": audio is not None}

    if video is not None:
        vcodec = video.get("codec_name", "")
        is_image = vcodec in IMAGE_CODECS and not fmt.get("duration")
        sar = _fraction(video.get("sample_aspect_ratio")) or Fraction(1, 1)
        if sar == 0:
            sar = Fraction(1, 1)
        try:
            nb = int(video.get("nb_frames") or 0)
        except (TypeError, ValueError):
            nb = 0
        kw.update(
            width=int(video.get("width") or 0),
            height=int(video.get("height") or 0),
            rotation=_rotation_of(video),
            sar=sar,
            r_frame_rate=_fraction(video.get("r_frame_rate")),
            avg_frame_rate=_fraction(video.get("avg_frame_rate")),
            pix_fmt=video.get("pix_fmt", ""),
            vcodec=vcodec,
            nb_frames=nb,
            is_image=is_image,
        )
        # A still has no meaningful duration; the timeline supplies one.
        if is_image:
            duration = 0.0

    if audio is not None:
        try:
            sr = int(audio.get("sample_rate") or 0)
        except (TypeError, ValueError):
            sr = 0
        kw.update(
            sample_rate=sr,
            channels=int(audio.get("channels") or 0),
            acodec=audio.get("codec_name", ""),
        )

    if duration <= 0 and not kw.get("is_image"):
        raise ProbeError(
            f"{p.name} reports no usable duration.",
            hint="The file may be truncated or still being written.",
        )

    return MediaFacts(path=p, duration_s=duration, **kw)
