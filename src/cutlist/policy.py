"""Checking that nothing forbidden reaches the screen.

Some projects have content that must never be visible: a client's name before an
announcement, an internal codename, a figure that turned out to be wrong. Left
to a human re-watching the output, that check fails eventually.

Two things this does that the obvious implementation does not:

**It scans every text surface, not just the config.** A banned string that
survives in a code fallback -- a default label in a template, a placeholder in a
scene definition -- renders on screen while a config-only scan reports clean.
That is a real way this check gets defeated, so the scan covers configs,
templates and source alike.

**It matches on word boundaries.** A naive substring search for a three-digit
figure hits every coordinate containing those digits, and a check that cries
wolf gets switched off.

The honest limitation, stated here because it must be stated somewhere: this
reads text, and text is not pixels. It cannot see a name on a whiteboard, a
plate on a vehicle or a figure on a device screen in the footage itself. Those
need redaction and a human looking at frames. A clean policy scan means the
*generated* layer is clean, nothing more.
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import Config

# Extensions worth reading. Deliberately includes code, not just configuration.
_TEXT_SUFFIXES = {".yaml", ".yml", ".json", ".toml", ".md", ".py", ".js", ".css", ".html", ".txt"}


def _iter_text_files(root: Path) -> list[Path]:
    out: list[Path] = []
    skip = {"_cut", "_out", "media", "source", ".git", "__pycache__", ".venv", "venv"}
    for p in root.rglob("*"):
        if any(part in skip for part in p.parts):
            continue
        if p.is_file() and p.suffix.lower() in _TEXT_SUFFIXES:
            out.append(p)
    return sorted(out)


def scan(cfg: Config) -> list[str]:
    """Return one line per violation. Empty means clean."""
    strings = cfg.policy.forbidden_strings
    patterns = cfg.policy.forbidden_patterns
    if not strings and not patterns:
        return []

    compiled: list[tuple[str, re.Pattern[str]]] = []
    for s in strings:
        # Word boundaries, so a figure does not match every coordinate that
        # happens to contain the same digits.
        compiled.append((s, re.compile(rf"(?<!\w){re.escape(s)}(?!\w)", re.IGNORECASE)))
    for p in patterns:
        try:
            compiled.append((p, re.compile(p, re.IGNORECASE)))
        except re.error as exc:
            return [f"policy.forbidden_patterns: {p!r} is not a valid regex ({exc})"]

    root = cfg.source_path.parent
    hits: list[str] = []
    for path in _iter_text_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, rx in compiled:
                if rx.search(line):
                    rel = path.relative_to(root) if path.is_relative_to(root) else path
                    hits.append(f"{rel}:{lineno}: matches {label!r}  |  {line.strip()[:90]}")
    return hits
