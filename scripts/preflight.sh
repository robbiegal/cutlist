#!/bin/sh
# SessionStart preflight: silent when healthy, one actionable line when not.
#
# Invoked as `sh "${CLAUDE_PLUGIN_ROOT}/scripts/preflight.sh"` from
# hooks/hooks.json rather than executed directly. WHY: the executable bit does
# not survive every checkout and does not exist at all on Windows, and a hook
# that silently never runs is indistinguishable from a hook that always finds
# everything healthy -- which is the failure mode this file is least able to
# afford.
#
# It installs NOTHING. A session that begins by downloading something nobody
# asked for is a surprise cost at the worst possible moment, and an install
# started inside a five-second hook timeout is an install that gets killed
# halfway and leaves a broken environment behind. This reports;
# /cutlist:cutlist-doctor acts.
#
# Two states earn a line, and the second is the one that otherwise goes
# unnoticed for weeks:
#
#   missing  There is no environment. Nothing works, and the `cutlist` command
#            does not resolve at all.
#
#   stale    There is an environment, built by a PREVIOUS version of this
#            plugin. The data directory deliberately survives plugin updates --
#            that is what stops an update from deleting a working install --
#            so after one, the old environment is still there and still runs.
#            Every command succeeds. `cutlist --version` reports last week's
#            number. Nothing anywhere else in the system notices, and the
#            symptom, when it finally appears, is a feature that the release
#            notes say exists behaving as though it does not.
#
# Silence is the healthy case, on purpose. A hook that prints on every start
# trains people to stop reading it, and at that point it is not a check, it is
# scenery.
set -eu

stale=0

# CLAUDE_PLUGIN_ROOT *is* exported to hook subprocesses, unlike an ordinary
# Bash tool call. Deriving it from $0 as a fallback costs one subshell and
# makes this runnable by hand while debugging it.
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root="${CLAUDE_PLUGIN_ROOT:-$(dirname -- "$here")}"

# The same search order as bin/cutlist, bin/cutlist.cmd and venv_home() in
# scripts/cutlist_env.py. Four copies, and they must agree: change one, change
# all of them. This copy is tolerated because it only ever reads -- a drift
# here produces a wrong message, not a wrong action -- and because a hook that
# had to start a Python interpreter to answer "is anything installed" would
# report "missing" on any machine where python3 is not on PATH.
home=""
if [ -n "${CUTLIST_HOME:-}" ]; then
    home=$CUTLIST_HOME
elif [ -n "${CLAUDE_PLUGIN_DATA:-}" ]; then
    home=$CLAUDE_PLUGIN_DATA
else
    first=""
    for d in "${HOME:-}"/.claude/plugins/data/cutlist*; do
        [ -d "$d" ] || continue
        [ -n "$first" ] || first=$d
        if [ -f "$d/venv/bin/python" ] || [ -f "$d/venv/Scripts/python.exe" ]; then
            home=$d
            break
        fi
    done
    [ -n "$home" ] || home=$first
fi

if [ -z "$home" ] || { [ ! -f "$home/venv/bin/python" ] && [ ! -f "$home/venv/Scripts/python.exe" ]; }; then
    echo "cutlist: no environment installed yet -- run /cutlist:cutlist-doctor before the first build; nothing is installed automatically."
    exit 0
fi

# Staleness. The installer copies the plugin's pyproject.toml into the data
# directory after a successful install, so the two files are identical exactly
# when the environment was built from the plugin version now on disk.
stamp="$home/pyproject.toml"
bundled="$root/pyproject.toml"

if [ ! -f "$stamp" ]; then
    # No stamp at all means the environment was built by an installer that did
    # not write one, which by definition is not the current one. Unknowable is
    # reported in the direction that gets it checked, rather than assumed
    # current -- the same choice the version comparison inside the package
    # makes for a build whose version string cannot be parsed.
    stale=1
elif [ ! -f "$bundled" ]; then
    # Nothing to compare against. Stay silent: this says the plugin files are
    # incomplete, not that the environment is wrong, and a session-start hook
    # is the wrong place to raise it.
    :
elif command -v cmp >/dev/null 2>&1; then
    cmp -s "$bundled" "$stamp" || stale=1
else
    # No cmp -- a stripped container image. Byte counts are a weaker test:
    # equal sizes with different contents pass. They are POSIX, always present,
    # and they catch the ordinary case, which is a version bump changing the
    # file's length. Stated here so the weakness is known rather than assumed
    # away by whoever reads this next.
    [ "$(wc -c < "$bundled")" = "$(wc -c < "$stamp")" ] || stale=1
fi

if [ "$stale" = "1" ]; then
    echo "cutlist: the installed environment predates this plugin version -- it still runs, and quietly reports the older version; run /cutlist:cutlist-doctor to refresh it."
fi

# Always 0. The value of this hook is the line, not the exit code: a
# session-start check that can block a session is a check people disable, and
# then both the line and the gate are gone.
exit 0
