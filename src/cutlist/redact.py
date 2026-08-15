"""Obscuring a region, including one that moves.

The technique is: split the picture, crop the region out of one copy, destroy
the detail in it, and composite it back over the original, gated to a time
window. Everything interesting is in the constraints.

**The box cannot change size.** `crop` resolves its width and height once, when
the graph is initialised. An animated size therefore does not render slowly or
approximately -- it does nothing at all, and reports nothing. The config schema
rejects the shape of that mistake rather than letting it through, and the
position is the only thing that interpolates.

**Track motion with several short entries.** One entry is a straight line. A
real subject does not move in a straight line, and a fast one certainly does
not, so a long entry either drifts off the subject in the middle or needs a box
so large it obscures half the frame. Consecutive short entries approximate the
real path, and `margin_px` absorbs the error between the chord and the curve.

**Verify at the boundaries.** Interpolated geometry and window rounding fail at
the edges of a window, not in the middle, so a mid-window spot check is close to
worthless. `cutlist verify --boundaries` samples just inside and just outside
each window and stacks the frames.

**Never judge coverage on a flat region.** A pixelated blank wall and a sharp
blank wall are the same pixels. Verification must be aimed at the detail --
text, a face, a number -- or it proves nothing at all.
"""

from __future__ import annotations

import functools

from .config import RedactBox
from .graph import Graph, clamp_expr, expr, lerp_expr
from .tools import find_tool, run


@functools.lru_cache(maxsize=1)
def _crop_needs_eval() -> bool:
    """Whether this build's `crop` has an `eval` option that must be set.

    Older builds default `crop` to evaluating x/y once at init, so an animated
    position silently does not move; they expose `eval` to opt into per-frame.
    Newer builds removed the option and always evaluate per frame -- and reject
    `eval` outright with "Option not found", failing the whole render.

    So the option is neither always right nor always wrong, and it is decided by
    asking the binary rather than by assuming a version.
    """
    proc = run([find_tool("ffmpeg"), "-hide_banner", "-h", "filter=crop"])
    return "eval" in proc.stdout


def _even(n: int) -> int:
    return n if n % 2 == 0 else n + 1


def strength_for(w: int, h: int, requested: int) -> int:
    """Mosaic cell size.

    Scaled to the region by default. A fixed cell is wrong in both directions:
    too fine on a large region leaves a subject recognisable, too coarse on a
    small one is a solid block that looks like a bug. One-twelfth of the shorter
    side keeps roughly a dozen cells across the subject, which destroys detail
    while still reading as deliberate.

    Floored at 4: below that, detail survives -- which is the failure that
    matters, since it means the redaction did not redact.
    """
    if requested > 0:
        return max(2, requested)
    return max(4, min(w, h) // 12)


def apply_redactions(
    g: Graph,
    src: str,
    boxes: tuple[RedactBox, ...],
    canvas_w: int,
    canvas_h: int,
) -> str:
    """Chain every redaction onto `src`, returning the final label."""
    cur = src
    use_eval = _crop_needs_eval()

    for box in boxes:
        # Grow by the margin, then clamp to the canvas. The margin is what
        # absorbs tracking error, so it is applied to the geometry rather than
        # left to the author to add by hand and forget.
        m = box.margin_px
        cw = _even(min(box.w + 2 * m, canvas_w))
        ch = _even(min(box.h + 2 * m, canvas_h))

        x0, x1 = box.x - m, box.end_x - m
        y0, y1 = box.y - m, box.end_y - m

        max_x = f"{canvas_w - cw}"
        max_y = f"{canvas_h - ch}"

        if box.moves:
            x_expr = clamp_expr(lerp_expr(x0, x1, box.from_s, box.to_s), "0", max_x)
            y_expr = clamp_expr(lerp_expr(y0, y1, box.from_s, box.to_s), "0", max_y)
        else:
            x_expr = f"{max(0, min(canvas_w - cw, x0))}"
            y_expr = f"{max(0, min(canvas_h - ch, y0))}"

        base = g.label("rb")
        region = g.label("rr")
        g.add([cur], "split=2", [base, region])

        crop_opts = [f"w={cw}", f"h={ch}", f"x={expr(x_expr)}", f"y={expr(y_expr)}"]
        if use_eval:
            crop_opts.append("eval=frame")
        cropped = g.chain(region, "crop=" + ":".join(crop_opts), "rc")

        s = strength_for(box.w, box.h, box.strength)
        if box.mode == "mosaic":
            obscured = g.chain(cropped, f"pixelize=w={s}:h={s}", "rp")
        else:
            # boxblur's radius must stay under half the region, or it errors.
            r = max(2, min(s * 2, min(cw, ch) // 2 - 1))
            obscured = g.chain(cropped, f"boxblur=luma_radius={r}:luma_power=2", "rp")

        out = g.label("rv")
        # `enable` gates the composite, not the crop. The cropped copy is
        # produced for every frame and simply not drawn outside the window --
        # which costs a little and keeps the timing exact at the boundary.
        g.add(
            [base, obscured],
            f"overlay=x={expr(x_expr)}:y={expr(y_expr)}"
            f":enable={expr(f'between(t,{box.from_s:.4f},{box.to_s:.4f})')}",
            [out],
        )
        cur = out

    return cur


def describe(boxes: tuple[RedactBox, ...]) -> list[str]:
    """One human-readable line per redaction, for the build report."""
    out = []
    for i, b in enumerate(boxes):
        motion = f" -> ({b.end_x},{b.end_y})" if b.moves else " (static)"
        out.append(
            f"[{i}] {b.mode} {b.w}x{b.h} at ({b.x},{b.y}){motion} "
            f"from {b.from_s:.2f}s to {b.to_s:.2f}s, margin {b.margin_px}px"
        )
    return out
