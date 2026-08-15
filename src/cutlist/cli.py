"""Command line. This module never calls a language model.

Exit codes are a contract, because a hook and a skill both key on them:

    0  success
    1  an error -- bad config, missing tool, failed render
    2  the build ran but VERIFICATION FAILED

Two is separate from one on purpose. "ffmpeg refused" and "ffmpeg produced a
file that is not what you asked for" are different situations, and the second is
the one a completion gate has to be able to see.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .cache import Cache
from .config import Config, load, orphan_scenes
from .errors import CutlistError, VerificationError
from .tools import MIN_FFMPEG, capabilities, find_tool

DEFAULT_CONFIG_NAMES = ("project.yaml", "project.yml", "project.json", "project.toml")


def _find_config(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    cwd = Path.cwd()
    for d in (cwd, *cwd.parents):
        for name in DEFAULT_CONFIG_NAMES:
            p = d / name
            if p.exists():
                return p
        if (d / ".git").exists():
            break
    raise CutlistError(
        "no project config found.",
        hint="Run `cutlist init` here, or pass --config PATH.",
    )


def _load(args) -> Config:
    return load(_find_config(getattr(args, "config", None)))


def _say(msg: str = "") -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------


def cmd_init(args) -> int:
    from .scaffold import init_project

    written = init_project(Path.cwd(), profile=args.profile, force=args.force)
    _say(f"Scaffolded a project in {Path.cwd()}")
    for p in written:
        _say(f"  {p}")
    _say()
    _say("Next: put footage in media/, then `cutlist ingest` to see what you have.")
    return 0


def cmd_doctor(args) -> int:
    ok = True
    _say(f"cutlist {__version__}")
    _say(f"python  {sys.version.split()[0]}")

    try:
        caps = capabilities()
        _say(f"ffmpeg  {caps.version}  {caps.path}")
        _say(f"ffprobe {find_tool('ffprobe')}")
        floor = ".".join(str(n) for n in MIN_FFMPEG)
        if caps.version_tuple and caps.version_tuple < MIN_FFMPEG:
            _say(f"  FAIL   ffmpeg {caps.version} is below the minimum {floor}")
            ok = False
        else:
            _say(f"  ok     meets the minimum ({floor})")
        _say(f"  {len(caps.filters)} filters, {len(caps.encoders)} encoders available")
    except CutlistError as exc:
        _say(f"  FAIL   {exc}")
        return 1

    try:
        from PIL import Image  # noqa: F401

        _say("text    Pillow present -- cards, lower thirds and captions available")
    except ModuleNotFoundError:
        _say("text    Pillow not installed -- text scenes unavailable")
        _say("        pip install 'cutlist[text]'   (~3 MB)")

    # Gate only on what THIS config demands. A three-clip join must never fail
    # because a filter it will never emit is missing from the build.
    try:
        cfg = _load(args)
    except CutlistError:
        _say()
        _say("No project config here, so nothing config-specific was checked.")
        return 0 if ok else 1

    feats = cfg.features()
    _say()
    _say(f"config  {cfg.source_path}")
    _say(f"        {len(cfg.segments)} segments, {cfg.total_duration_s:.2f}s")
    _say(f"        needs: {', '.join(feats) if feats else '(core only)'}")
    try:
        from .tools import require

        require(feats)
        _say("  ok     this build can do everything this config asks for")
    except CutlistError as exc:
        _say(f"  FAIL   {exc}")
        ok = False

    return 0 if ok else 1


def cmd_ingest(args) -> int:
    from .ingest import ingest

    cfg = _load(args)
    report = ingest(cfg)
    if args.json:
        _say(json.dumps(report, indent=2, default=str))
        return 0

    _say(f"{len(report['sources'])} source(s), canvas {cfg.project.width}x{cfg.project.height}"
         f" @ {cfg.project.fps:g}fps")
    _say()
    for s in report["sources"]:
        flags = []
        if s["rotation"]:
            flags.append(f"rotated {s['rotation']}deg")
        if s["is_vfr"]:
            flags.append("variable rate")
        if not s["has_audio"]:
            flags.append("no audio")
        note = f"  [{', '.join(flags)}]" if flags else ""
        _say(f"  {s['name']}")
        _say(f"    {s['coded'][0]}x{s['coded'][1]} coded -> {s['display'][0]}x{s['display'][1]}"
             f" displayed, {s['duration_s']:.2f}s{note}")
        pl = s["placement"]
        _say(f"    picture at ({pl['picture']['x']},{pl['picture']['y']}) "
             f"{pl['picture']['w']}x{pl['picture']['h']}")
        if pl["rails"]:
            rails = ", ".join(f"{k} {v['w']}x{v['h']}" for k, v in pl["rails"].items())
            _say(f"    rails: {rails}  <- usable annotation space")
    _say()
    _say(f"Written to {report['path']}")
    return 0


def cmd_lint(args) -> int:
    cfg = _load(args)
    problems: list[str] = []
    notes: list[str] = []

    orphans = orphan_scenes(cfg)
    if orphans:
        notes.append(f"scenes defined but never used: {', '.join(orphans)}")

    for seg in cfg.segments:
        if seg.audio.mute and seg.audio.windows:
            problems.append(
                f"{seg.id}: audio.mute is set AND windows are declared -- "
                f"the windows do nothing under a whole-shot mute"
            )
        for i, r in enumerate(seg.redact):
            if r.margin_px == 0 and r.moves:
                notes.append(
                    f"{seg.id}.redact[{i}] tracks motion with margin_px 0 -- "
                    f"a straight line rarely follows a real subject exactly"
                )
        if seg.transition_in.kind != "cut" and seg.transition_in.duration_s <= 0:
            problems.append(
                f"{seg.id}: transition_in.kind is {seg.transition_in.kind!r} "
                f"but duration_s is 0, so it renders as a cut"
            )

    _say(f"{cfg.source_path.name}: {len(cfg.segments)} segments, "
         f"{cfg.total_duration_s:.2f}s, {len(cfg.scenes)} scenes")
    for n in notes:
        _say(f"  note  {n}")
    for p in problems:
        _say(f"  FAIL  {p}")
    if not problems and not notes:
        _say("  ok    nothing to report")
    return 1 if problems else 0


def cmd_conform(args) -> int:
    from .render import conform_all
    from .tools import require

    cfg = _load(args)
    require(cfg.features())
    cache = Cache(cfg.project.work_dir, capabilities().version)

    made = conform_all(cfg, cache, only=args.only or None, force=args.force)
    total = sum(p.stat().st_size for _, p in made if p.exists())
    _say(f"{len(made)} conformed window(s), {total / 1e6:.1f} MB "
         f"in {cfg.project.work_dir / 'conform'}")
    for seg_id, path in made:
        _say(f"  {seg_id:20s} {path.name}")
    _say()
    _say("These are lossless and large. They are cache, not output -- delete _cut/ any time.")
    return 0


def cmd_build(args) -> int:
    from .assemble import assemble, timeline_duration
    from .render import build_shot
    from .tools import require

    cfg = _load(args)
    require(cfg.features())

    variant = next((v for v in cfg.variants if v.name == args.variant), None)
    if variant is None:
        raise CutlistError(
            f"unknown variant {args.variant!r}",
            hint=f"Defined: {', '.join(v.name for v in cfg.variants)}",
        )

    cache = Cache(cfg.project.work_dir, capabilities().version)
    graph_dir = cfg.project.work_dir / "graphs"

    targets = [s for s in cfg.segments if not args.only or s.id in args.only]
    if args.only and not targets:
        raise CutlistError(f"no segment matches --only {args.only}")

    shots: list[Path] = []
    for seg in cfg.segments:
        selected = not args.only or seg.id in args.only
        before = cache.root / "shots"
        n_before = len(list(before.iterdir())) if before.is_dir() else 0
        path = build_shot(
            cfg, seg, cache,
            variant_tags=variant.tags,
            graph_dir=graph_dir,
            force=args.force and selected,
        )
        n_after = len(list(before.iterdir())) if before.is_dir() else 0
        state = "built" if n_after > n_before else "cached"
        _say(f"  {state:6s} {seg.id:20s} {seg.duration_s:6.2f}s")
        shots.append(path)

    if args.graph_only:
        _say()
        _say(f"Graphs written to {graph_dir} -- no delivery encode.")
        return 0

    out = cfg.project.out_dir / f"{cfg.project.name}_{variant.name}.mp4"
    expected, overlap = timeline_duration(cfg)
    _say()
    _say(f"Assembling {len(shots)} shots -> {out.name}")
    if overlap > 0:
        _say(f"  transitions overlap {overlap:.2f}s, so the timeline is "
             f"{expected:.2f}s not {cfg.total_duration_s:.2f}s")
    asm = assemble(cfg, shots, variant=variant.name, graph_dir=graph_dir, out_path=out)
    _say(f"  {asm.duration_s:.2f}s / {asm.frames} frames")

    from .verify import verify_delivery

    result = verify_delivery(cfg, asm, variant=variant.name)
    _say()
    for line in result.lines:
        _say(f"  {line}")
    _say()
    _say(f"Report: {cfg.project.work_dir / 'report.json'}")
    if not result.passed:
        _say()
        _say("VERIFICATION FAILED -- the file exists but is not what was asked for.")
        return 2
    _say(f"Wrote {out}")
    return 0


def cmd_verify(args) -> int:
    from .evidence import build_evidence

    cfg = _load(args)
    paths = build_evidence(
        cfg,
        variant=args.variant,
        boundaries=args.boundaries,
        audio=args.audio,
    )
    _say(f"{len(paths)} evidence artifact(s):")
    for p in paths:
        _say(f"  {p}")
    _say()
    _say("These are images. Read them -- an exit code is not evidence.")
    return 0


def cmd_prompt(args) -> int:
    from .resources import read_prompt

    _say(read_prompt(args.name))
    return 0


def cmd_scan(args) -> int:
    from .policy import scan

    cfg = _load(args)
    hits = scan(cfg)
    if not hits:
        _say("Policy scan clean.")
        return 0
    _say(f"{len(hits)} policy violation(s):")
    for h in hits:
        _say(f"  {h}")
    return 2


def cmd_sheets(args) -> int:
    from .evidence import contact_sheet

    cfg = _load(args)
    p = contact_sheet(cfg, args.clip, fps=args.fps, tile=args.tile,
                      start=getattr(args, "from"), end=args.to)
    _say(f"Wrote {p}")
    _say(f"At {args.fps}fps in a {args.tile} grid, cell (row, col) zero-indexed "
         f"is (row*{args.tile.split('x')[0]}+col)/{args.fps:g} seconds into the window.")
    return 0


def cmd_measure(args) -> int:
    from .evidence import measure_frame

    cfg = _load(args)
    p = measure_frame(cfg, args.segment, at=args.at, grid=args.grid,
                      crop=args.crop, zoom=args.zoom)
    _say(f"Wrote {p}")
    _say("Read the coordinates off this image. It is the RENDER, not the source -- "
         "source coordinates do not survive conform.")
    return 0


def cmd_grain(args) -> int:
    from .evidence import sample_grain

    cfg = _load(args)
    p = sample_grain(cfg, args.clip, start=getattr(args, "from"), end=args.to, out=args.out)
    _say(f"Wrote {p}")
    _say("Reference it from an audio window's `fill`. Sampled from the same clip, "
         "so mic, level and timbre already match.")
    return 0


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cutlist", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=f"cutlist {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        s = sub.add_parser(name, help=help_)
        s.set_defaults(func=fn)
        return s

    s = add("init", cmd_init, "scaffold a project here")
    s.add_argument("--profile", default="1080p30",
                   choices=["1080p30", "1080p25", "720p30", "2160p30"])
    s.add_argument("--force", action="store_true")

    s = add("doctor", cmd_doctor, "check the toolchain against what this config needs")
    s.add_argument("--config")

    s = add("ingest", cmd_ingest, "probe every source and record the facts")
    s.add_argument("--config")
    s.add_argument("--json", action="store_true")

    s = add("lint", cmd_lint, "strict config check")
    s.add_argument("--config")

    s = add("conform", cmd_conform, "conform sources to a constant rate, without building")
    s.add_argument("--config")
    s.add_argument("--only", nargs="*", default=[], metavar="ID")
    s.add_argument("--force", action="store_true")

    s = add("build", cmd_build, "conform, build shots, assemble and verify")
    s.add_argument("--config")
    s.add_argument("--only", nargs="*", default=[], metavar="ID")
    s.add_argument("--variant", default="final")
    s.add_argument("--force", action="store_true")
    s.add_argument("--graph-only", action="store_true")

    s = add("verify", cmd_verify, "produce the evidence pack")
    s.add_argument("--config")
    s.add_argument("--variant", default="final")
    s.add_argument("--boundaries", action="store_true")
    s.add_argument("--audio", action="store_true")

    s = add("sheets", cmd_sheets, "contact sheet for beat mapping")
    s.add_argument("--config")
    s.add_argument("--clip", required=True)
    s.add_argument("--fps", type=float, default=1.0)
    s.add_argument("--tile", default="6x5")
    s.add_argument("--from", type=float, default=0.0)
    s.add_argument("--to", type=float)

    s = add("measure", cmd_measure, "a grid-annotated frame from the RENDER")
    s.add_argument("--config")
    s.add_argument("segment")
    s.add_argument("--at", type=float, required=True)
    s.add_argument("--grid", action="store_true")
    s.add_argument("--crop")
    s.add_argument("--zoom", type=float, default=1.0)

    s = add("grain", cmd_grain, "sample room tone from a clip")
    s.add_argument("--config")
    s.add_argument("--clip", required=True)
    s.add_argument("--from", type=float, required=True)
    s.add_argument("--to", type=float, required=True)
    s.add_argument("--out", required=True)

    s = add("scan", cmd_scan, "policy scan for forbidden on-screen content")
    s.add_argument("--config")

    s = add("prompt", cmd_prompt, "print a contract that ships with the code")
    s.add_argument("name")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except VerificationError as exc:
        print(f"\ncutlist: {exc}", file=sys.stderr)
        return 2
    except CutlistError as exc:
        print(f"\ncutlist: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
