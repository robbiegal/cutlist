#!/usr/bin/env python3
"""Create and inspect the Python environment cutlist runs in.

Standard library only, and that is a constraint rather than a preference: this
script is what *creates* the environment, so anything it imported would have to
be installed before it could install anything.

Three decisions here are worth stating, because each prevents a failure that
arrives late and looks like something else.

**The environment goes in the plugin DATA directory, never in the plugin
root.** The root is version-scoped and is garbage-collected after an update. An
environment built there -- or worse, an editable install pointing at it --
keeps working for days and then stops, at a moment with no visible connection
to the change that caused it. The data directory survives updates. That is
exactly why the session hook checks for staleness: the same property that makes
the data directory safe to install into is what lets it hold last week's
version indefinitely.

**The build runs from a staging copy.** The plugin directory is managed by
another installer and is treated as read-only, and a build writes `.egg-info`
and `build/` into whatever directory it is pointed at. Copying first costs a
few megabytes of temporary space and leaves the shipped tree exactly as
delivered.

**venv_home() is duplicated in the launcher shims, deliberately.**
`bin/cutlist` and `bin/cutlist.cmd` cannot import this file -- they run before
there is an interpreter to import it with. The order below and the order there
must stay identical: this script decides where an environment is created, the
shims decide which one runs, and if they disagree then `install` reports
success while `cutlist` keeps running something else.

Subcommands:

    status                                  report the environment as JSON
    install [--extras a,b] [--upgrade]      build or refresh it
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

# Must match [project.optional-dependencies] in pyproject.toml.
KNOWN_EXTRAS = ("yaml", "text", "graphics", "dev", "all")

# What gets copied into the staging build: everything the wheel needs and
# nothing else. Skills, hooks, media and work directories have no business in a
# build tree, and copying them makes an install slower for no result.
STAGED_FILES = ("pyproject.toml", "README.md", "LICENSE", "LICENSING.md", "NOTICE")
STAGED_DIRS = ("src",)

# After a successful install the bundled pyproject.toml is copied here. The
# session hook compares the two with `cmp -s` to tell a current environment
# from one built by a previous version of the plugin. Written LAST, so a failed
# install cannot leave behind a stamp claiming success.
STAMP = "pyproject.toml"


def plugin_root() -> Path:
    """The plugin directory this script ships inside.

    Answered from `__file__` rather than CLAUDE_PLUGIN_ROOT: that variable is
    exported to hook and MCP subprocesses only and is empty in an ordinary
    call, and this file already knows where it is.
    """
    return Path(__file__).resolve().parent.parent


def _python_in(home: Path) -> Path | None:
    """The interpreter of the environment at `home`, if there is one.

    POSIX layout first, then the Windows one, and both are checked on both
    platforms. Checking the layout that cannot exist costs one stat, and the
    case it covers is real: a POSIX shell session on Windows sees only the
    `Scripts` layout.
    """
    for rel in ("venv/bin/python", "venv/Scripts/python.exe"):
        candidate = home / rel
        if candidate.is_file():
            return candidate
    return None


def venv_home() -> Path:
    """Where the environment lives.

    This order is the contract. `bin/cutlist`, `bin/cutlist.cmd` and
    `scripts/preflight.sh` implement the same one; changing it here means
    changing it in all four places.
    """
    # 1. An explicit override wins outright, whether or not it holds anything.
    #    `status` will then report `installed: false` for it and `install` will
    #    build there. What must never happen -- and what the shims enforce on
    #    their side -- is silently using a different environment than the one
    #    that was pinned.
    override = os.environ.get("CUTLIST_HOME", "").strip()
    if override:
        return Path(override).expanduser()

    # 2. The harness's answer, when there is one. Empty in an ordinary Bash
    #    tool call, which is why step 3 exists at all.
    data = os.environ.get("CLAUDE_PLUGIN_DATA", "").strip()
    if data:
        return Path(data).expanduser()

    # 3. The default location. Globbed because the directory carries a
    #    marketplace-dependent suffix this file cannot know. Sorted, so the
    #    answer never depends on directory enumeration order, and a directory
    #    that already holds an environment is preferred, so that this function
    #    and the shims converge on the same one whenever there is one.
    base = Path.home() / ".claude" / "plugins" / "data"
    try:
        matches = sorted(p for p in base.glob("cutlist*") if p.is_dir())
    except OSError:
        matches = []
    for candidate in matches:
        if _python_in(candidate):
            return candidate
    return matches[0] if matches else base / "cutlist"


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------

_PROBE = """\
import json
from importlib.util import find_spec
import cutlist
print(json.dumps({
    "version": cutlist.__version__,
    "extras": {
        "yaml": find_spec("yaml") is not None,
        "text": find_spec("PIL") is not None,
        "graphics": find_spec("playwright") is not None,
    },
}))
"""


def _probe(py: Path) -> dict:
    """Ask the installed package about itself, in its own interpreter.

    Not by reading files in the venv: what matters is what an import actually
    resolves to there, and a directory listing is a guess about that.
    """
    proc = subprocess.run([str(py), "-c", _PROBE], capture_output=True, text=True)
    if proc.returncode != 0:
        return {}
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {}


def _ffmpeg(py: Path | None) -> str | None:
    """The ffmpeg the engine would actually use, or None.

    Asked of the installed package rather than reimplemented here. The
    package's resolver honours CUTLIST_FFMPEG and searches per-user install
    locations that are not on PATH, so a second, simpler answer in this file
    would disagree with the one that decides what renders -- and a status
    command that disagrees with the tool is worse than one that says nothing.
    """
    if py is not None:
        code = "from cutlist.tools import find_tool\nprint(find_tool('ffmpeg'))\n"
        proc = subprocess.run([str(py), "-c", code], capture_output=True, text=True)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip().splitlines()[-1]
        return None
    return shutil.which("ffmpeg")


def status() -> dict:
    home = venv_home()
    py = _python_in(home)
    info = _probe(py) if py else {}
    return {
        "venv_home": str(home),
        "venv_python": str(py) if py else None,
        # An interpreter that exists but cannot import cutlist is not an
        # install. Reporting the directory as installed because a venv is
        # sitting in it is how a half-finished install gets treated as ready.
        "installed": bool(info),
        "version": info.get("version"),
        "extras": info.get("extras", {"yaml": False, "text": False, "graphics": False}),
        "ffmpeg": _ffmpeg(py),
    }


# --------------------------------------------------------------------------
# install
# --------------------------------------------------------------------------


def _make_venv(venv_dir: Path) -> None:
    """Create the virtual environment, with pip inside it.

    `ensurepip` is packaged separately on Debian and Ubuntu, so this is the one
    step that fails on an otherwise healthy machine. The message names the fix
    instead of surfacing a traceback about a module nobody chose to import.
    """
    try:
        venv.EnvBuilder(
            with_pip=True,
            clear=False,
            symlinks=(os.name != "nt"),
        ).create(str(venv_dir))
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        raise SystemExit(
            f"cutlist: could not create a virtual environment at {venv_dir} ({exc}).\n"
            f"cutlist: on Debian/Ubuntu this is usually a missing package: "
            f"sudo apt install python3-venv"
        ) from exc


def _stage(root: Path, into: Path) -> None:
    for name in STAGED_FILES:
        src = root / name
        if src.is_file():
            shutil.copy2(src, into / name)
    for name in STAGED_DIRS:
        src = root / name
        if src.is_dir():
            shutil.copytree(
                src,
                into / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.py[cod]", "*.egg-info"),
            )
    if not (into / "pyproject.toml").is_file():
        raise SystemExit(
            f"cutlist: no pyproject.toml under {root} -- this is not a cutlist plugin "
            f"directory, so there is nothing to install."
        )


def install(extras: list[str], upgrade: bool) -> dict:
    root = plugin_root()
    home = venv_home()

    # Enforced, not merely documented. The failure it prevents is silent and
    # delayed: the plugin root is replaced on update and the environment
    # vanishes with it, days after whatever decision put it there.
    if home == root or root in home.parents:
        raise SystemExit(
            f"cutlist: refusing to build an environment at {home}, which is inside the "
            f"plugin directory ({root}).\n"
            f"cutlist: that directory is version-scoped and is removed on update, so an "
            f"environment there stops existing without warning.\n"
            f"cutlist: unset CUTLIST_HOME, or point it somewhere outside the plugin."
        )

    home.mkdir(parents=True, exist_ok=True)

    # Staged inside the destination rather than the system temp directory, so
    # the build cannot cross a filesystem boundary or land somewhere the
    # sandbox forbids writing.
    staging = Path(tempfile.mkdtemp(prefix=".build-", dir=str(home)))
    try:
        _stage(root, staging)

        py = _python_in(home)
        if py is None:
            _make_venv(home / "venv")
            py = _python_in(home)
        if py is None:
            raise SystemExit(
                f"cutlist: created {home / 'venv'} but found no interpreter inside it."
            )

        spec = str(staging)
        if extras:
            spec = f"{spec}[{','.join(extras)}]"

        cmd = [
            str(py),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
        ]
        if upgrade:
            # --force-reinstall alongside --upgrade, because the version number
            # does not have to change between plugin builds. Without it, pip
            # can decide the already-installed distribution satisfies the
            # requirement and do nothing -- and a refresh that silently does
            # nothing is the exact failure the staleness check exists to
            # surface, reported as success.
            cmd += ["--upgrade", "--force-reinstall"]
        cmd.append(spec)

        proc = subprocess.run(cmd, text=True)
        if proc.returncode != 0:
            raise SystemExit(f"cutlist: pip install failed (exit {proc.returncode}).")
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    # The staleness stamp, written only now that the install has succeeded.
    shutil.copy2(root / "pyproject.toml", home / STAMP)
    return status()


# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cutlist_env",
        description="Create and inspect the Python environment cutlist runs in.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="report the environment as JSON")

    ins = sub.add_parser("install", help="build or refresh the environment")
    ins.add_argument(
        "--extras",
        default="",
        metavar="A,B",
        help="comma-separated optional bands: " + ", ".join(KNOWN_EXTRAS),
    )
    ins.add_argument(
        "--upgrade",
        action="store_true",
        help="rebuild the installed package even when the version is unchanged",
    )

    args = parser.parse_args(argv)

    if args.cmd == "status":
        # Always exit 0. "Not installed" is an answer, not a failure, and a
        # status command that exits non-zero for it forces every caller to
        # special-case the ordinary first-run case.
        print(json.dumps(status(), indent=2))
        return 0

    extras = [e.strip() for e in args.extras.split(",") if e.strip()]
    unknown = [e for e in extras if e not in KNOWN_EXTRAS]
    if unknown:
        # Rejected rather than passed through. pip accepts an unknown extra
        # with a warning and installs nothing for it, so a typo would look
        # exactly like a successful install of the band that was asked for.
        print(f"cutlist: unknown extra(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"cutlist: known extras: {', '.join(KNOWN_EXTRAS)}", file=sys.stderr)
        return 1

    print(json.dumps(install(extras, args.upgrade), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
