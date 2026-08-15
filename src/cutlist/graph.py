"""Building ffmpeg filter graphs safely, and writing them somewhere auditable.

Three decisions, all of which exist to stop a class of bug rather than to be
tidy.

**Graphs go in a script file, never on the command line.** `-filter_complex_script`
removes the shell entirely from the escaping problem, and it sidesteps the
Windows command-line length limit, which a real multi-layer graph reaches
surprisingly quickly.

**Labels come from a counter, never from `id()`.** Object addresses differ
between runs and interpreters, which makes output non-deterministic, every
regeneration a noisy diff, and a golden-file test impossible. A monotonic
counter costs nothing and buys the entire cheap-regression-test story.

**The graph is kept.** It is written to the work directory next to its output.
When a render is wrong, the first question is always "what did it actually
ask for", and a graph you can read answers it in seconds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Characters ffmpeg treats as structural inside a filter description. A value
# containing one must be escaped or the graph reparses in surprising ways --
# usually as a syntax error, occasionally as a different and valid graph.
_SPECIAL = "\\'[],;:="


def esc(value: str) -> str:
    r"""Escape a literal value for use as a filter option.

    Note there is no shell layer here: the graph is written to a file, so this
    is ffmpeg's own escaping and nothing else.
    """
    out = []
    for ch in value:
        if ch in _SPECIAL:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def expr(value: str) -> str:
    r"""Quote an *expression* option, such as an animated `crop` x.

    Expressions legitimately contain commas -- `min(a\,b)` -- so they are
    single-quoted and their commas escaped, rather than escaped wholesale like a
    literal. Getting this wrong produces "Option not found" or a silently
    truncated expression.
    """
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'").replace(",", "\\,") + "'"


# The builders below emit expressions with ORDINARY commas, and `expr()` does
# the escaping in exactly one place, at the boundary where the value is written
# into the graph. Escaping in both produces `\\,`, which ffmpeg reports as
# "Missing ')' or too many args" -- a message that points at the parenthesis and
# not at the real cause.


def clamp_expr(inner: str, lo: str, hi: str) -> str:
    """`max(lo, min(hi, inner))` -- keep a coordinate inside the frame.

    Unclamped, a tracked box whose path leaves the picture makes ffmpeg fail
    with an out-of-range crop rather than simply stopping at the edge.
    """
    return f"max({lo},min({hi},{inner}))"


def lerp_expr(a: float, b: float, t0: float, t1: float) -> str:
    """A value moving linearly from `a` at `t0` to `b` at `t1`, held outside.

    Held rather than extrapolated on purpose: outside its window the box parks
    at its endpoint instead of flying off, so a slightly mistimed entry degrades
    into a stationary box rather than a wild one.
    """
    if abs(b - a) < 1e-9 or abs(t1 - t0) < 1e-9:
        return f"{a:.4f}"
    slope = (b - a) / (t1 - t0)
    moving = f"{a:.4f}+{slope:.6f}*(t-{t0:.4f})"
    return f"if(lt(t,{t0:.4f}),{a:.4f},if(gt(t,{t1:.4f}),{b:.4f},{moving}))"


@dataclass
class Graph:
    """An accumulating filter graph with deterministic labels."""

    chains: list[str] = field(default_factory=list)
    _n: int = 0

    def label(self, stem: str = "v") -> str:
        """Mint a unique, stable label.

        Stable across runs given the same inputs, which is what makes the
        emitted graph diffable and golden-testable.
        """
        self._n += 1
        return f"{stem}{self._n}"

    def add(self, inputs: list[str], filters: str, outputs: list[str]) -> None:
        """Append one chain: `[in][in] filter,filter [out]`."""
        head = "".join(f"[{i}]" for i in inputs)
        tail = "".join(f"[{o}]" for o in outputs)
        self.chains.append(f"{head}{filters}{tail}")

    def chain(self, src: str, filters: str, stem: str = "v") -> str:
        """Append a one-in one-out chain and return the new label."""
        out = self.label(stem)
        self.add([src], filters, [out])
        return out

    def render(self) -> str:
        # Newline-separated chains: functionally identical to `;` for ffmpeg,
        # and the difference between a graph a human can read and one they
        # cannot.
        return ";\n".join(self.chains) + "\n"

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render(), encoding="utf-8")
        return path

    def __bool__(self) -> bool:
        return bool(self.chains)


def db_to_gain(db: float) -> float:
    """Decibels to linear amplitude.

    The only place this conversion happens. Config states dB; the engine
    converts once, here. A pipeline that lets a linear amplitude appear in a
    config file eventually has someone read `0.2` as a decibel figure, and the
    resulting level is wrong by roughly 14 dB in a direction nobody notices
    until playback.
    """
    if db <= -96.0:
        return 0.0
    return float(10.0 ** (db / 20.0))
