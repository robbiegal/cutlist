"""Locating ffmpeg and ffprobe, and finding out what they can actually do.

Three hard-won lessons are baked in here.

**Discovery must be lazy.** Resolving binaries at module import fires a
recursive glob over a Windows package directory on every single import --
including on Linux, where it can never match.

**The candidate list must include per-user installs.** Probing only the
machine-wide `C:\\Program Files` variants and a package-manager glob misses the
toolchain entirely when it sits at `%LOCALAPPDATA%\\Programs\\...`, which is
where current installers put it. It then resolves only by accident, through the
PATH fallback, on the machines where it works at all.

**An env override must be an override.** Honouring one only `if Path(p).exists()`
with no else branch means setting it to something wrong silently uses a
*different* binary than the one you pinned. Here a set-but-unusable override is a
hard error.

And one that is not portability at all but licensing: these binaries are always
reached as **separate processes**. Never replace this module with an in-process
binding to libav*; that is linking, and it changes this package's licence
obligations. See LICENSING.md.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .errors import CapabilityMissing, ToolNotFound, ToolTooOld

# Environment overrides. Neutral names: an env var contract is public API, and
# the tool's own name is the only sensible thing to put in it.
ENV_VAR = {"ffmpeg": "CUTLIST_FFMPEG", "ffprobe": "CUTLIST_FFPROBE"}


def _windows_dirs() -> list[Path]:
    """Windows candidate directories, most-likely first.

    The per-user location leads deliberately: it is where current installers put
    things, and it is the one most easily forgotten.
    """
    out: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    program_data = os.environ.get("PROGRAMDATA")
    user_profile = os.environ.get("USERPROFILE")

    if local:
        lp = Path(local)
        # Per-user installs of the applications that bundle a toolchain.
        for app in ("Kdenlive", "Shotcut", "ffmpeg"):
            out.append(lp / "Programs" / app / "bin")
        # Package managers that install per-user.
        wingetroot = lp / "Microsoft" / "WinGet" / "Packages"
        if wingetroot.is_dir():
            # Sorted, newest last -- take the newest rather than whatever the
            # filesystem happened to enumerate first.
            for pkg in sorted(wingetroot.glob("*")):
                out.append(pkg / "bin")
                out.append(pkg)
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if base:
            for app in ("kdenlive", "Kdenlive", "Shotcut", "ffmpeg"):
                out.append(Path(base) / app / "bin")
    if program_data:
        out.append(Path(program_data) / "chocolatey" / "bin")
    if user_profile:
        out.append(Path(user_profile) / "scoop" / "shims")
    return out


def _macos_dirs() -> list[Path]:
    return [
        Path("/opt/homebrew/bin"),  # Apple Silicon
        Path("/usr/local/bin"),  # Intel Homebrew
        Path("/opt/local/bin"),  # MacPorts
        Path("/Applications/kdenlive.app/Contents/MacOS"),
        Path("/Applications/Shotcut.app/Contents/MacOS"),
    ]


def _linux_dirs() -> list[Path]:
    out = [Path("/usr/bin"), Path("/usr/local/bin"), Path("/snap/bin")]
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        out.append(Path(appimage).parent)
    return out


def _candidate_dirs() -> list[Path]:
    if sys.platform == "win32":
        return _windows_dirs()
    if sys.platform == "darwin":
        return _macos_dirs()
    return _linux_dirs()


def _exe_names(name: str) -> list[str]:
    return [f"{name}.exe", name] if sys.platform == "win32" else [name]


@functools.cache
def find_tool(name: str) -> str:
    """Return an absolute path to `name` ("ffmpeg" or "ffprobe").

    Resolution order: explicit env override, then known install directories for
    this OS, then PATH. Raises rather than returning a bare command name, so a
    later failure names the missing tool instead of surfacing as a confusing
    `FileNotFoundError` from deep inside a render.
    """
    env = ENV_VAR.get(name)
    if env:
        override = os.environ.get(env)
        if override:
            p = Path(override)
            # An override that does not resolve is an error, never a fallback.
            # Silently ranking past it means the operator pinned one build and
            # got another, which is undiagnosable from the output.
            if p.is_file():
                return str(p)
            found = shutil.which(override)
            if found:
                return found
            raise ToolNotFound(
                f"{env} is set to {override!r}, which is not an executable.",
                hint=f"Point {env} at the {name} binary, or unset it to search normally.",
            )

    for d in _candidate_dirs():
        for exe in _exe_names(name):
            p = d / exe
            if p.is_file():
                return str(p)

    found = shutil.which(name)
    if found:
        return found

    raise ToolNotFound(
        f"{name} was not found.",
        hint=_install_hint(name),
    )


def _install_hint(name: str) -> str:
    if sys.platform == "win32":
        return f"winget install Gyan.FFmpeg   (or set {ENV_VAR[name]} to an existing {name})"
    if sys.platform == "darwin":
        return f"brew install ffmpeg   (or set {ENV_VAR[name]} to an existing {name})"
    return f"sudo apt install ffmpeg   (or set {ENV_VAR[name]} to an existing {name})"


def run(argv: list[str], *, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing output as text.

    Never `shell=True`: every path this package handles may contain spaces, and
    one of the sources it was designed against had a trailing space before its
    extension. An argv list has no quoting rules to get wrong.
    """
    return subprocess.run(  # noqa: S603 - argv list, never a shell string
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


@dataclass(frozen=True)
class Capabilities:
    """What the located ffmpeg can actually do."""

    path: str
    version: str
    version_tuple: tuple[int, ...]
    filters: frozenset[str]
    encoders: frozenset[str]
    muxers: frozenset[str]

    def has_filter(self, name: str) -> bool:
        return name in self.filters

    def has_encoder(self, name: str) -> bool:
        return name in self.encoders


_VERSION_RE = re.compile(r"ffmpeg version (\S+)")


def _parse_version_tuple(v: str) -> tuple[int, ...]:
    """Best-effort numeric version.

    Distribution builds report things like `n6.1.1-static`, `6.0-essentials` or
    a bare git hash. A hash yields an empty tuple, which compares as older than
    everything -- the safe direction to be wrong in, since it turns an
    unknowable build into an explicit "cannot confirm" rather than a silent pass.
    """
    m = re.match(r"n?(\d+(?:\.\d+)*)", v)
    if not m:
        return ()
    return tuple(int(x) for x in m.group(1).split("."))


def _listing(path: str, flag: str, pattern: re.Pattern[str]) -> frozenset[str]:
    proc = run([path, "-hide_banner", flag])
    names: set[str] = set()
    for line in proc.stdout.splitlines():
        m = pattern.match(line)
        if m:
            names.add(m.group(1))
    return frozenset(names)


@functools.cache
def capabilities() -> Capabilities:
    """Inventory the installed ffmpeg once, and cache it.

    Generating references to services that a *particular* build does not have is
    a real failure mode, not a theoretical one: the ffmpeg bundled with one
    popular editor has no libfreetype and therefore no `drawtext`, which broke
    the documented contact-sheet recipe of the project this came from. Inventory
    first; emit only what is there.
    """
    path = find_tool("ffmpeg")

    proc = run([path, "-hide_banner", "-version"])
    first = proc.stdout.splitlines()[0] if proc.stdout else ""
    m = _VERSION_RE.search(first)
    version = m.group(1) if m else "unknown"

    return Capabilities(
        path=path,
        version=version,
        version_tuple=_parse_version_tuple(version),
        # A filter line is ` TS colorbalance      V->V   Adjust the color balance.`
        # The flag field is two characters on ffmpeg 5+ and three on older
        # builds, so accept either. The `->` signature is the load-bearing part:
        # it appears on every real filter row (`V->V`, `AA->A`, `N->N`, `|->V`
        # for sources) and on none of the legend rows above them, which is what
        # keeps ` T.. = Timeline support` out of the set.
        filters=_listing(path, "-filters", re.compile(r"^\s*[TSC.]{2,3}\s+(\S+)\s+\S*->\S*")),
        # ` V....D libx264   H.264 / AVC ...` -- six flag characters.
        encoders=_listing(path, "-encoders", re.compile(r"^\s*[VASFXBD.]{6}\s+(\S+)")),
        # ` DE matroska   Matroska` -- two, either of which may be a space.
        muxers=_listing(path, "-muxers", re.compile(r"^\s*[DE ]{2}\s+(\S+)")),
    )


# Minimum ffmpeg for the core engine. `xfade` landed in 4.3 and the engine uses
# it for any non-cut transition; below that, nothing here is trustworthy.
# Printing this number is the single line that answers most "why doesn't it
# work" questions, so `doctor` states it explicitly.
MIN_FFMPEG = (4, 3)

# Features that need a newer build than the floor. Checked only when the config
# actually asks for them -- a three-clip join must never fail because a filter
# it will never emit is absent.
FEATURE_REQUIREMENTS: dict[str, tuple[str, tuple[int, ...], str]] = {
    # key            (filter,          min version, what asked for it)
    "redact_mosaic": ("pixelize", (6, 0), "redaction in mosaic mode"),
    "redact_blur": ("boxblur", (4, 3), "redaction in blur mode"),
    "duck": ("sidechaincompress", (4, 3), "audio ducking"),
    "loudnorm": ("loudnorm", (4, 3), "loudness normalisation"),
    "transition": ("xfade", (4, 3), "a non-cut transition"),
    "grade_curves": ("curves", (4, 3), "a curves grade"),
    "grade_eq": ("eq", (4, 3), "a contrast/saturation grade"),
    "grade_balance": ("colorbalance", (4, 3), "a colour-balance grade"),
}


def require(features: list[str]) -> None:
    """Assert that the installed ffmpeg supports exactly what this build needs.

    `features` comes from the resolved config, not from a fixed list. Gating a
    simple job on the union of everything the engine *could* emit is how a tool
    becomes uninstallable: one missing filter and the user gets nothing, forever.
    """
    caps = capabilities()

    if caps.version_tuple and caps.version_tuple < MIN_FFMPEG:
        raise ToolTooOld(
            f"ffmpeg {caps.version} is older than the minimum "
            f"{'.'.join(str(n) for n in MIN_FFMPEG)} ({caps.path}).",
            hint="Upgrade ffmpeg, or point CUTLIST_FFMPEG at a newer build.",
        )

    missing: list[str] = []
    for feat in features:
        req = FEATURE_REQUIREMENTS.get(feat)
        if req is None:
            continue
        filter_name, min_ver, because = req
        if not caps.has_filter(filter_name):
            missing.append(f"{filter_name!r} (needed for {because})")
        elif caps.version_tuple and caps.version_tuple < min_ver:
            missing.append(
                f"{filter_name!r} needs ffmpeg >= {'.'.join(str(n) for n in min_ver)}, "
                f"this is {caps.version} (needed for {because})"
            )

    if missing:
        raise CapabilityMissing(
            "This build of ffmpeg cannot do what the config asks for:\n"
            + "\n".join(f"    - {m}" for m in missing)
            + f"\n  ffmpeg: {caps.path} ({caps.version})",
            hint="Install a fuller ffmpeg build, or remove the features above from the config.",
        )
