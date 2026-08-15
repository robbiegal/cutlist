"""Probing every source once, and writing the facts down.

This runs before anything is designed, and its output is meant to be read rather
than just consulted by code. Two questions it answers change what a good edit
looks like, and both are invisible until you ask:

  * Does the picture fill the frame? A portrait source in a landscape canvas
    leaves two rails, and those rails are the only place an annotation can sit
    without covering the subject. Designing a layout before knowing this is how
    graphics end up on top of the thing they are pointing at.

  * Is the source constant-rate? If not, no timestamp taken from it can be
    trusted, and every coordinate must come from the conformed render instead.

The facts land in `_cut/geometry.json` so they survive the session and can be
diffed when a source is replaced.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import Config
from .geometry import anchors, fit
from .probe import probe


def used_sources(cfg: Config) -> list[tuple[str, str]]:
    """(source name, fit mode) for every distinct source the timeline uses."""
    seen: dict[str, str] = {}
    for seg in cfg.segments:
        for ly in seg.layers:
            if ly.kind in ("clip", "still") and ly.source:
                seen.setdefault(ly.source, ly.fit)
        for w in seg.audio.windows:
            if w.fill:
                seen.setdefault(w.fill, "none")
    if cfg.audio.bed:
        seen.setdefault(cfg.audio.bed, "none")
    return sorted(seen.items())


def ingest(cfg: Config) -> dict:
    """Probe everything the config references and record it."""
    proj = cfg.project
    out: list[dict] = []

    for name, fit_mode in used_sources(cfg):
        path = Path(name)
        if not path.is_absolute():
            path = proj.media_dir / name
        facts = probe(path)

        entry: dict = {
            "name": name,
            "path": str(path),
            "duration_s": round(facts.duration_s, 4),
            "coded": [facts.width, facts.height],
            "display": list(facts.display_size),
            "rotation": facts.rotation,
            "is_vfr": facts.is_vfr,
            "has_audio": facts.has_audio,
            "is_image": facts.is_image,
            "vcodec": facts.vcodec,
            "acodec": facts.acodec,
        }

        if facts.has_video:
            place = fit(*facts.display_size, proj.width, proj.height, fit_mode)
            entry["placement"] = place.to_dict()
            entry["anchors"] = {
                k: list(v) for k, v in anchors(place, margin=proj.width // 30).items()
            }
        else:
            entry["placement"] = {"picture": {}, "rails": {}}
            entry["anchors"] = {}

        out.append(entry)

    report = {
        "schema": 1,
        "canvas": [proj.width, proj.height],
        "fps": proj.fps,
        "sources": out,
    }

    dest = proj.work_dir / "geometry.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["path"] = str(dest)
    return report
