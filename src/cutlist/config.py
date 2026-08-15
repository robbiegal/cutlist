"""The cut list: loading it, validating it strictly, resolving it.

Design rules, each of which exists because its absence caused a real bug.

**Unknown keys are errors.** That pipeline shipped about a dozen keys that were
read and ignored, or never read at all -- a whole `layout` block no code
touched, a `titles_mode` whose interesting branch was never implemented, a
`pad_color` assigned to a field and never used. Every one is an invitation to
edit something that silently does nothing. There is no middle ground between
implementing a key and rejecting it.

**Levels are decibels.** Never linear amplitude. That pipeline had
`background_gain: 0.2` with a comment glossing it as "-80% (~ -14 dB)", which is
two different units in one line and reads as either. The engine converts; the
config states intent.

**Durations are declared once.** A graphic and the shot under it drifting out of
sync was a whole category of bug there, guarded by a static cross-check. Here a
graphic layer is fitted to its segment by policy (`trim`, `loop`, `stretch`), so
the two cannot disagree in the first place.

Times in this file are always **seconds**. Frames appear only downstream, after
conform, where they are exact.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .errors import ConfigError

# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------


def _load_raw(path: Path) -> dict:
    """Parse a config by extension.

    JSON and TOML need nothing beyond the standard library, which is what keeps
    this package installable with zero dependencies. YAML is the nicest to write
    by hand, so it is supported -- but only when PyYAML is present, and the
    ImportError names the exact command that fixes it.
    """
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()

    if suffix == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path.name} is not valid JSON: {exc}") from exc

    if suffix == ".toml":
        try:
            import tomllib
        except ModuleNotFoundError as exc:  # pragma: no cover - 3.10 only
            raise ConfigError(
                "TOML configs need Python 3.11 or newer.",
                hint="Use a .json or .yaml config, or upgrade Python.",
            ) from exc
        try:
            return tomllib.loads(text)
        except Exception as exc:
            raise ConfigError(f"{path.name} is not valid TOML: {exc}") from exc

    if suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ModuleNotFoundError as exc:
            raise ConfigError(
                "This config is YAML, and PyYAML is not installed.",
                hint="pip install pyyaml   "
                "(or write the config as .json / .toml, which need nothing)",
            ) from exc
        try:
            return yaml.safe_load(text) or {}
        except Exception as exc:
            raise ConfigError(f"{path.name} is not valid YAML: {exc}") from exc

    raise ConfigError(
        f"unrecognised config extension {path.suffix!r}",
        hint="Use .yaml, .json or .toml.",
    )


# --------------------------------------------------------------------------
# strict field checking
# --------------------------------------------------------------------------


class _Checker:
    """A tiny strict validator.

    Hand-rolled rather than jsonschema, to keep the hard dependency count at
    zero. It only has to do three things well: reject unknown keys, coerce
    numbers, and say *where* in the document the problem is -- because "invalid
    config" without a path is a scavenger hunt.
    """

    def __init__(self, where: str, data: Any) -> None:
        self.where = where
        if not isinstance(data, dict):
            raise ConfigError(f"{where}: expected a mapping, got {type(data).__name__}")
        self.data = dict(data)
        self.seen: set[str] = set()

    def _get(self, key: str, default: Any) -> Any:
        self.seen.add(key)
        return self.data.get(key, default)

    def str_(self, key: str, default: str | None = None, *, choices: tuple[str, ...] = ()) -> str:
        v = self._get(key, default)
        if v is None:
            raise ConfigError(f"{self.where}.{key} is required")
        if not isinstance(v, str):
            raise ConfigError(f"{self.where}.{key} must be a string, got {type(v).__name__}")
        if choices and v not in choices:
            raise ConfigError(
                f"{self.where}.{key} must be one of {', '.join(choices)}; got {v!r}"
            )
        return v

    def num(
        self,
        key: str,
        default: float | None = None,
        *,
        min: float | None = None,
        max: float | None = None,
    ) -> float:
        v = self._get(key, default)
        if v is None:
            raise ConfigError(f"{self.where}.{key} is required")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ConfigError(f"{self.where}.{key} must be a number, got {type(v).__name__}")
        v = float(v)
        if not math.isfinite(v):
            raise ConfigError(f"{self.where}.{key} must be finite")
        if min is not None and v < min:
            raise ConfigError(f"{self.where}.{key} must be >= {min}, got {v}")
        if max is not None and v > max:
            raise ConfigError(f"{self.where}.{key} must be <= {max}, got {v}")
        return v

    def int_(self, key: str, default: int | None = None, **kw: Any) -> int:
        v = self.num(key, default, **kw)
        if v != int(v):
            raise ConfigError(f"{self.where}.{key} must be a whole number, got {v}")
        return int(v)

    def bool_(self, key: str, default: bool) -> bool:
        v = self._get(key, default)
        if not isinstance(v, bool):
            raise ConfigError(f"{self.where}.{key} must be true or false")
        return v

    def list_(self, key: str) -> list:
        v = self._get(key, [])
        if v is None:
            return []
        if not isinstance(v, list):
            raise ConfigError(f"{self.where}.{key} must be a list, got {type(v).__name__}")
        return v

    def dict_(self, key: str) -> dict:
        v = self._get(key, {})
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise ConfigError(f"{self.where}.{key} must be a mapping, got {type(v).__name__}")
        return v

    def opt_str(self, key: str) -> str | None:
        v = self._get(key, None)
        if v is None:
            return None
        if not isinstance(v, str):
            raise ConfigError(f"{self.where}.{key} must be a string")
        return v

    def opt_num(self, key: str, **kw: Any) -> float | None:
        if self.data.get(key) is None:
            self.seen.add(key)
            return None
        return self.num(key, **kw)

    def done(self) -> None:
        """Reject anything not consumed above."""
        unknown = sorted(set(self.data) - self.seen)
        if unknown:
            raise ConfigError(
                f"{self.where}: unknown key(s): {', '.join(unknown)}",
                hint="Keys are rejected rather than ignored, so a typo cannot silently do nothing. "
                "Run `cutlist lint` to see the accepted schema.",
            )


# --------------------------------------------------------------------------
# resolved model
# --------------------------------------------------------------------------

FitMode = Literal["contain", "cover", "stretch", "none"]
TimeFit = Literal["trim", "loop", "stretch", "once"]


@dataclass(frozen=True)
class Project:
    name: str
    width: int
    height: int
    fps: float
    media_dir: Path
    out_dir: Path
    work_dir: Path

    @property
    def canvas(self) -> tuple[int, int]:
        return self.width, self.height


@dataclass(frozen=True)
class Layer:
    """One element of a segment's picture, composited in ascending `z`.

    A layer may be footage, a still, a flat colour or a rendered graphic. Making
    these one type rather than "the footage, plus an overlay slot, plus a HUD
    flag" is what makes picture-in-picture, split screen and B-roll-over-A-roll
    expressible at all -- and stops graphics needing a private vocabulary.
    """

    kind: Literal["clip", "still", "color", "scene"]
    z: int = 0
    opacity: float = 1.0
    fit: FitMode = "contain"
    tags: tuple[str, ...] = ()

    # clip / still
    source: str | None = None
    in_s: float = 0.0
    out_s: float | None = None

    # color
    value: str = "#000000"

    # scene
    name: str | None = None
    time_fit: TimeFit = "trim"

    # placement, when the layer is not full-frame (picture-in-picture)
    box: tuple[int, int, int, int] | None = None

    @property
    def is_timed(self) -> bool:
        """True when this layer's own length can define the segment's length."""
        return self.kind == "clip" and self.out_s is not None


@dataclass(frozen=True)
class RedactBox:
    """A region to obscure.

    Width and height are constant for the life of one entry, and that is
    enforced rather than merely documented. `crop` evaluates its dimensions once
    at filter-graph init, so an animated size is not slow or approximate -- it
    simply does not happen, with no error. A schema that accepts `w_end` would
    be a trap that renders as no change at all.

    Position interpolates linearly from `box` to `to`. Track fast motion with
    several short consecutive entries; following a subject across a hard pan can
    easily need a dozen.
    """

    x: int
    y: int
    w: int
    h: int
    from_s: float
    to_s: float
    to_x: int | None = None
    to_y: int | None = None
    mode: Literal["mosaic", "blur"] = "mosaic"
    margin_px: int = 16
    strength: int = 0  # 0 = derive from box size

    @property
    def end_x(self) -> int:
        return self.x if self.to_x is None else self.to_x

    @property
    def end_y(self) -> int:
        return self.y if self.to_y is None else self.to_y

    @property
    def moves(self) -> bool:
        return (self.end_x, self.end_y) != (self.x, self.y)


@dataclass(frozen=True)
class AudioWindow:
    """A stretch of a segment's own audio to suppress.

    `fade_s` ramps down into silence instead of cutting; an abrupt drop reads as
    a mistake to a listener.

    `fill` names a sound to lay over the hole. A gain-of-zero can only *remove*
    sound -- it cannot put anything back -- so a mute in the middle of continuous
    ambience leaves a conspicuous silence that draws more attention than
    whatever was removed. Sample the fill from a clean stretch of the same clip
    with `cutlist grain`, and the mic, level and timbre match for free.
    """

    from_s: float
    to_s: float
    fade_s: float = 0.0
    fill: str | None = None


@dataclass(frozen=True)
class SegmentAudio:
    mute: bool = False
    gain_db: float = 0.0
    windows: tuple[AudioWindow, ...] = ()


@dataclass(frozen=True)
class Transition:
    kind: str = "cut"
    duration_s: float = 0.0

    @property
    def is_cut(self) -> bool:
        return self.kind == "cut" or self.duration_s <= 0


@dataclass(frozen=True)
class Segment:
    id: str
    layers: tuple[Layer, ...]
    duration_s: float
    redact: tuple[RedactBox, ...] = ()
    audio: SegmentAudio = field(default_factory=SegmentAudio)
    transition_in: Transition = field(default_factory=Transition)
    tags: tuple[str, ...] = ()

    @property
    def base(self) -> Layer | None:
        """The lowest visual layer -- the one the segment's length comes from."""
        vis = [ly for ly in self.layers if ly.kind in ("clip", "still", "color")]
        return min(vis, key=lambda ly: ly.z) if vis else None


@dataclass(frozen=True)
class Variant:
    name: str
    tags: tuple[str, ...] = ()

    def includes(self, layer_tags: tuple[str, ...]) -> bool:
        """A variant with no tag list takes everything."""
        if not self.tags:
            return True
        return any(t in self.tags for t in layer_tags) if layer_tags else True


@dataclass(frozen=True)
class Grade:
    enabled: bool = False
    eq: dict[str, float] = field(default_factory=dict)
    colorbalance: dict[str, float] = field(default_factory=dict)
    curves: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AudioBus:
    bed: str | None = None
    bed_gain_db: float = -18.0
    duck: bool = False
    duck_threshold_db: float = -24.0
    duck_ratio: float = 6.0
    loudnorm: bool = False
    target_lufs: float = -16.0


@dataclass(frozen=True)
class Policy:
    forbidden_strings: tuple[str, ...] = ()
    forbidden_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class Render:
    vcodec: str = "libx264"
    crf: int = 18
    preset: str = "medium"
    pix_fmt: str = "yuv420p"
    acodec: str = "aac"
    abr: str = "192k"
    gop: int = 0  # 0 -> one second, filled in at resolve time


@dataclass(frozen=True)
class Config:
    project: Project
    segments: tuple[Segment, ...]
    variants: tuple[Variant, ...]
    grade: Grade
    audio: AudioBus
    policy: Policy
    render: Render
    scenes: dict[str, dict]
    theme: dict
    source_path: Path

    @property
    def total_duration_s(self) -> float:
        return sum(s.duration_s for s in self.segments)

    def features(self) -> list[str]:
        """Which ffmpeg capabilities this particular config actually needs.

        The doctor gates on this, not on everything the engine could ever emit.
        Failing a three-clip join because a build lacks a filter it will never
        reach is how a tool becomes uninstallable.
        """
        feats: set[str] = set()
        if self.grade.enabled:
            if self.grade.eq:
                feats.add("grade_eq")
            if self.grade.colorbalance:
                feats.add("grade_balance")
            if self.grade.curves:
                feats.add("grade_curves")
        for seg in self.segments:
            for r in seg.redact:
                feats.add("redact_mosaic" if r.mode == "mosaic" else "redact_blur")
            if not seg.transition_in.is_cut:
                feats.add("transition")
        if self.audio.duck:
            feats.add("duck")
        if self.audio.loudnorm:
            feats.add("loudnorm")
        return sorted(feats)


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def _parse_layer(raw: Any, where: str) -> Layer:
    c = _Checker(where, raw)
    kind = c.str_("kind", choices=("clip", "still", "color", "scene"))
    z = c.int_("z", 0)
    opacity = c.num("opacity", 1.0, min=0.0, max=1.0)
    fit = c.str_("fit", "contain", choices=("contain", "cover", "stretch", "none"))
    tags = tuple(str(t) for t in c.list_("tags"))

    box = None
    raw_box = c.list_("box")
    if raw_box:
        if len(raw_box) != 4:
            raise ConfigError(f"{where}.box must be [x, y, w, h]")
        box = tuple(int(v) for v in raw_box)

    source = None
    in_s = 0.0
    out_s = None
    value = "#000000"
    name = None
    time_fit = "trim"

    if kind in ("clip", "still"):
        source = c.str_("source")
        in_s = c.num("in", 0.0, min=0.0)
        out_s = c.opt_num("out", min=0.0)
        if kind == "clip" and out_s is not None and out_s <= in_s:
            raise ConfigError(f"{where}: out ({out_s}) must be greater than in ({in_s})")
    elif kind == "color":
        value = c.str_("value", "#000000")
        if not _HEX_RE.match(value):
            raise ConfigError(f"{where}.value must be a hex colour like #101014, got {value!r}")
    else:
        name = c.str_("name")
        time_fit = c.str_("time_fit", "trim", choices=("trim", "loop", "stretch", "once"))

    c.done()
    return Layer(
        kind=kind,
        z=z,
        opacity=opacity,
        fit=fit,
        tags=tags,
        source=source,
        in_s=in_s,
        out_s=out_s,
        value=value,
        name=name,
        time_fit=time_fit,
        box=box,
    )


def _parse_redact(raw: Any, where: str, seg_duration: float) -> RedactBox:
    c = _Checker(where, raw)
    box = c.list_("box")
    if len(box) != 4:
        raise ConfigError(f"{where}.box must be [x, y, w, h]")
    x, y, w, h = (int(v) for v in box)
    if w <= 0 or h <= 0:
        raise ConfigError(f"{where}.box width and height must be positive")

    to = c.list_("to")
    to_x = None
    to_y = None
    if to:
        if len(to) == 4:
            # Caught here rather than rendering as a silent no-op: crop resolves
            # its dimensions once at graph init, so an end box of a different
            # size does not animate slowly or approximately -- it does nothing
            # at all, with no error. A schema accepting a size keyframe would be
            # a trap, so the shape of the mistake is rejected explicitly.
            raise ConfigError(
                f"{where}.to must be [x, y] -- a redaction box cannot change size.",
                hint="Position interpolates; size is fixed for the entry. For a subject that grows "
                "or shrinks, use consecutive entries, or widen margin_px to cover the range.",
            )
        if len(to) != 2:
            raise ConfigError(f"{where}.to must be [x, y]")
        to_x, to_y = int(to[0]), int(to[1])

    from_s = c.num("from", 0.0, min=0.0)
    to_s = c.num("to_s", seg_duration, min=0.0)
    if to_s <= from_s:
        raise ConfigError(f"{where}: to_s ({to_s}) must be greater than from ({from_s})")

    mode = c.str_("mode", "mosaic", choices=("mosaic", "blur"))
    margin = c.int_("margin_px", 16, min=0)
    strength = c.int_("strength", 0, min=0)
    c.done()
    return RedactBox(
        x=x,
        y=y,
        w=w,
        h=h,
        from_s=from_s,
        to_s=to_s,
        to_x=to_x,
        to_y=to_y,
        mode=mode,
        margin_px=margin,
        strength=strength,
    )


def _parse_segment_audio(raw: Any, where: str) -> SegmentAudio:
    c = _Checker(where, raw)
    mute = c.bool_("mute", False)
    gain_db = c.num("gain_db", 0.0, min=-96.0, max=24.0)
    windows = []
    for i, w in enumerate(c.list_("windows")):
        wc = _Checker(f"{where}.windows[{i}]", w)
        frm = wc.num("from", min=0.0)
        to = wc.num("to", min=0.0)
        if to <= frm:
            raise ConfigError(f"{where}.windows[{i}]: to must be greater than from")
        fade = wc.num("fade_s", 0.0, min=0.0)
        fill = wc.opt_str("fill")
        wc.done()
        windows.append(AudioWindow(from_s=frm, to_s=to, fade_s=fade, fill=fill))
    c.done()
    return SegmentAudio(mute=mute, gain_db=gain_db, windows=tuple(windows))


def _parse_segment(raw: Any, index: int) -> Segment:
    where = f"timeline[{index}]"
    c = _Checker(where, raw)
    seg_id = c.str_("id")
    if not _ID_RE.match(seg_id):
        raise ConfigError(
            f"{where}.id must be alphanumeric with - or _, got {seg_id!r}",
            hint="Ids become filenames for cached shots and evidence frames.",
        )
    where = f"timeline[{index}]({seg_id})"

    raw_layers = c.list_("video_layers")
    if not raw_layers:
        raise ConfigError(f"{where}.video_layers must contain at least one layer")
    layers = tuple(
        _parse_layer(ly, f"{where}.video_layers[{i}]") for i, ly in enumerate(raw_layers)
    )

    explicit = c.opt_num("duration_s", min=0.001)
    timed = [ly for ly in layers if ly.is_timed]
    if explicit is not None:
        duration = explicit
        for ly in timed:
            span = (ly.out_s or 0.0) - ly.in_s
            if abs(span - duration) > 0.001:
                raise ConfigError(
                    f"{where}: duration_s is {duration} but a clip layer spans {span:.3f}s.",
                    hint="Drop duration_s and let the clip define it, or make them agree.",
                )
    elif timed:
        duration = (timed[0].out_s or 0.0) - timed[0].in_s
    else:
        raise ConfigError(
            f"{where}: no duration.",
            hint="Give the segment a duration_s, or give a clip layer both in and out.",
        )

    redact = tuple(
        _parse_redact(r, f"{where}.redact[{i}]", duration) for i, r in enumerate(c.list_("redact"))
    )
    for i, r in enumerate(redact):
        if r.to_s > duration + 0.001:
            raise ConfigError(
                f"{where}.redact[{i}] ends at {r.to_s}s "
                f"but the segment is {duration:.3f}s long."
            )

    audio = _parse_segment_audio(c.dict_("audio"), f"{where}.audio")
    for i, w in enumerate(audio.windows):
        if w.to_s > duration + 0.001:
            raise ConfigError(
                f"{where}.audio.windows[{i}] ends at {w.to_s}s "
                f"but the segment is {duration:.3f}s long."
            )

    traw = c.dict_("transition_in")
    tc = _Checker(f"{where}.transition_in", traw)
    trans = Transition(
        kind=tc.str_("kind", "cut"),
        duration_s=tc.num("duration_s", 0.0, min=0.0),
    )
    tc.done()

    tags = tuple(str(t) for t in c.list_("tags"))
    c.done()

    return Segment(
        id=seg_id,
        layers=layers,
        duration_s=duration,
        redact=redact,
        audio=audio,
        transition_in=trans,
        tags=tags,
    )


def load(path, project_root=None) -> Config:
    """Load, validate and resolve a cut list.

    `project_root` defaults to the config file's own directory. Every relative
    path in the config resolves against it -- never against this module's
    location, which becomes site-packages once the package is installed and is
    how a tool ends up looking for a user's media inside their Python
    environment.
    """
    cfg_path = Path(path).resolve()
    if not cfg_path.exists():
        raise ConfigError(
            f"config not found: {cfg_path}",
            hint="Run `cutlist init` to scaffold one.",
        )
    root = (project_root or cfg_path.parent).resolve()
    raw = _load_raw(cfg_path)
    if not isinstance(raw, dict):
        raise ConfigError(f"{cfg_path.name}: top level must be a mapping")

    top = _Checker("(top level)", raw)

    pc = _Checker("project", top.dict_("project"))
    width = pc.int_("width", 1920, min=16)
    height = pc.int_("height", 1080, min=16)
    if width % 2 or height % 2:
        raise ConfigError(
            f"project size {width}x{height} must be even.",
            hint="Chroma-subsampled delivery formats cannot represent odd dimensions.",
        )
    project = Project(
        name=pc.str_("name", cfg_path.parent.name),
        width=width,
        height=height,
        fps=pc.num("fps", 30.0, min=1.0, max=240.0),
        media_dir=(root / pc.str_("media_dir", "media")).resolve(),
        out_dir=(root / pc.str_("out_dir", "_out")).resolve(),
        work_dir=(root / pc.str_("work_dir", "_cut")).resolve(),
    )
    pc.done()

    raw_timeline = top.list_("timeline")
    if not raw_timeline:
        raise ConfigError("timeline is empty -- there is nothing to render.")
    segments = tuple(_parse_segment(s, i) for i, s in enumerate(raw_timeline))

    seen_ids = set()
    for s in segments:
        if s.id in seen_ids:
            raise ConfigError(f"duplicate segment id {s.id!r} -- ids name cache entries.")
        seen_ids.add(s.id)

    gc = _Checker("grade", top.dict_("grade"))
    grade = Grade(
        enabled=gc.bool_("enabled", False),
        eq={k: float(v) for k, v in gc.dict_("eq").items()},
        colorbalance={k: float(v) for k, v in gc.dict_("colorbalance").items()},
        curves={k: str(v) for k, v in gc.dict_("curves").items()},
    )
    gc.done()

    ac = _Checker("audio", top.dict_("audio"))
    audio = AudioBus(
        bed=ac.opt_str("bed"),
        bed_gain_db=ac.num("bed_gain_db", -18.0, min=-96.0, max=12.0),
        duck=ac.bool_("duck", False),
        duck_threshold_db=ac.num("duck_threshold_db", -24.0, min=-96.0, max=0.0),
        duck_ratio=ac.num("duck_ratio", 6.0, min=1.0, max=20.0),
        loudnorm=ac.bool_("loudnorm", False),
        target_lufs=ac.num("target_lufs", -16.0, min=-40.0, max=-5.0),
    )
    ac.done()

    poc = _Checker("policy", top.dict_("policy"))
    policy = Policy(
        forbidden_strings=tuple(str(s) for s in poc.list_("forbidden_strings")),
        forbidden_patterns=tuple(str(s) for s in poc.list_("forbidden_patterns")),
    )
    poc.done()

    rc = _Checker("render", top.dict_("render"))
    gop = rc.int_("gop", 0, min=0)
    render = Render(
        vcodec=rc.str_("vcodec", "libx264"),
        crf=rc.int_("crf", 18, min=0, max=51),
        preset=rc.str_("preset", "medium"),
        pix_fmt=rc.str_("pix_fmt", "yuv420p"),
        acodec=rc.str_("acodec", "aac"),
        abr=rc.str_("abr", "192k"),
        gop=gop if gop else max(1, round(project.fps)),
    )
    rc.done()

    raw_variants = top.list_("variants")
    if raw_variants:
        variants = []
        for i, v in enumerate(raw_variants):
            vc = _Checker(f"variants[{i}]", v)
            variants.append(
                Variant(name=vc.str_("name"), tags=tuple(str(t) for t in vc.list_("tags")))
            )
            vc.done()
        variants_t = tuple(variants)
    else:
        variants_t = (Variant(name="final"),)

    scenes_raw = top.dict_("scenes")
    scenes = {str(k): dict(v) for k, v in scenes_raw.items()} if scenes_raw else {}
    theme = top.dict_("theme")
    top.done()

    # Every scene a layer names must exist. A graphic that resolved to nothing
    # is the classic silent failure here: the render succeeds and the annotation
    # simply is not there.
    for seg in segments:
        for ly in seg.layers:
            if ly.kind == "scene" and ly.name not in scenes:
                raise ConfigError(
                    f"segment {seg.id!r} references scene {ly.name!r}, which is not defined.",
                    hint=f"Defined scenes: {', '.join(sorted(scenes)) or '(none)'}",
                )

    return Config(
        project=project,
        segments=segments,
        variants=variants_t,
        grade=grade,
        audio=audio,
        policy=policy,
        render=render,
        scenes=scenes,
        theme=theme,
        source_path=cfg_path,
    )


def orphan_scenes(cfg: Config) -> list[str]:
    """Scenes defined but referenced by nothing.

    Not an error -- work in progress is legitimate -- but worth reporting. The
    reference project shipped three, each costing a slow render nobody used.
    """
    used = {ly.name for seg in cfg.segments for ly in seg.layers if ly.kind == "scene"}
    return sorted(set(cfg.scenes) - used)
