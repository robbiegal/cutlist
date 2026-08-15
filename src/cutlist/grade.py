"""Colour grading.

Three ffmpeg filters cover almost everything a documentary or demo cut needs:
`eq` for contrast, saturation and gamma; `colorbalance` for pushing shadows,
midtones and highlights toward a hue; `curves` for precise control of the
transfer, most usefully to lift or crush a single channel's black point.

The default is **no grade at all**. A tool that tints someone's footage out of
the box is imposing a look, and the look it imposes will be the one its author
happened to like. `grade.enabled` is false unless asked for, and the shipped
example preset is labelled as one example rather than as a default.

Emission is pass-through: any key in `eq` or `colorbalance` is written straight
through as a filter option. That means a newer ffmpeg's added parameters work
without a code change, and it means a typo reaches ffmpeg rather than being
silently dropped -- which is the right direction, because ffmpeg names the
option it did not recognise.
"""

from __future__ import annotations

from .config import Grade
from .graph import Graph, esc

# Applied in this order, and the order is not arbitrary. `eq` sets the overall
# level and contrast; `curves` reshapes the transfer within that; and
# `colorbalance` tints last, so its shadow and highlight targets act on the
# tones actually present rather than on the ungraded ones.
_ORDER = ("eq", "curves", "colorbalance")


def _fmt(v: float | str) -> str:
    if isinstance(v, str):
        return esc(v)
    # Trim trailing zeros so the emitted graph is readable and stable.
    return f"{float(v):.6g}"


def grade_filters(grade: Grade) -> list[str]:
    """The filter strings for this grade, in order. Empty when disabled."""
    if not grade.enabled:
        return []

    out: list[str] = []
    for name in _ORDER:
        params: dict = {
            "eq": grade.eq,
            "curves": grade.curves,
            "colorbalance": grade.colorbalance,
        }[name]
        if not params:
            continue
        args = ":".join(f"{k}={_fmt(v)}" for k, v in sorted(params.items()))
        out.append(f"{name}={args}")
    return out


def apply_grade(g: Graph, src: str, grade: Grade) -> str:
    """Append the grade to `src`, returning the resulting label."""
    filters = grade_filters(grade)
    if not filters:
        return src
    return g.chain(src, ",".join(filters), "gr")


# A neutral starting point, written out so `cutlist init` can offer it without
# the values being buried in code. Every number here is the identity: this
# changes nothing, and exists to be edited.
IDENTITY_PRESET = {
    "enabled": False,
    "eq": {"saturation": 1.0, "contrast": 1.0, "gamma": 1.0, "brightness": 0.0},
    "colorbalance": {},
    "curves": {},
}
