"""Content-addressed intermediates.

The whole point of the shot stage is that changing one trim rebuilds one shot,
not the timeline. That is only safe if the cache key is complete, so the key
covers four things and all four matter:

  1. the resolved spec for this artifact -- every parameter that reaches ffmpeg
  2. the content of every input, by hash
  3. the engine version
  4. the ffmpeg version

Leaving out (3) is the subtle one, and it is a live wrong-output bug rather than
a stale-file annoyance: upgrade the tool, and every existing project silently
reuses shots built by the previous compiler, with no symptom at all. Leaving out
(4) is the same failure with someone else's hand on the lever -- a distribution
upgrading ffmpeg underneath you changes what the same graph produces.

Inputs are hashed by size and modification time rather than by reading them.
Media files are large and a full digest of a folder of them costs more than the
render it is meant to save. The tradeoff is real: touching a file without
changing it forces a rebuild, and changing a file while preserving both size and
mtime would not. The first is cheap; the second requires deliberate effort.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import __version__


def hash_file(path: Path) -> str:
    """Cheap identity for an input file: size and mtime, not contents."""
    try:
        st = path.stat()
    except OSError:
        return "missing"
    return f"{st.st_size}:{int(st.st_mtime)}"


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Cache:
    """A directory of derived artifacts keyed by what produced them."""

    root: Path
    ffmpeg_version: str

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def key(self, spec: dict, inputs: list[Path]) -> str:
        """A stable digest of everything that determines the output."""
        payload = {
            "engine": __version__,
            "ffmpeg": self.ffmpeg_version,
            "spec": spec,
            "inputs": {str(p): hash_file(p) for p in sorted(inputs, key=str)},
        }
        # sort_keys so a dict reordering never invalidates a cache entry.
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def path_for(self, kind: str, name: str, key: str, suffix: str) -> Path:
        d = self.root / kind
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{name}-{key}{suffix}"

    def valid(self, path: Path) -> bool:
        """A cached artifact counts only if it exists and is not empty.

        An interrupted render leaves a zero-byte file behind, and a cache that
        accepts one turns a single Ctrl-C into a permanently broken build that
        reports success.
        """
        try:
            return path.exists() and path.stat().st_size > 0
        except OSError:
            return False

    def sweep(self, kind: str, keep: set[Path]) -> int:
        """Delete artifacts of `kind` that the current build does not reference.

        Called after a successful build. Without it, every edit to a trim leaves
        its predecessor behind and a working directory grows without bound --
        lossless intermediates are large enough for that to matter within an
        afternoon.
        """
        d = self.root / kind
        if not d.is_dir():
            return 0
        removed = 0
        for p in d.iterdir():
            if p.is_file() and p not in keep:
                p.unlink(missing_ok=True)
                removed += 1
        return removed

    def clear(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)
