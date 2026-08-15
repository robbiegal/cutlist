"""Fitting a source into the canvas, and naming the space that is left over.

When a portrait phone clip lands in a landscape canvas, most of the frame is
empty. The instinct is to treat that as waste. It is the opposite: those two
side rails are the only part of the frame where an annotation can sit without
covering the subject, and a video that uses them deliberately looks composed
rather than cropped.

So this module does two jobs. It computes where the picture goes, and it hands
back the rectangles that are *not* picture, as named anchors graphics can be
placed against.

The important part is that both are derived from probe data. Measuring the
window once by hand and writing the numbers into a config -- 656..1264, say --
locks an entire graphics system to one shoot, and silently stops being true the
moment a source is replaced with one of a different aspect. Nothing here is a
constant.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FitMode = Literal["contain", "cover", "stretch", "none"]


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        return self.y + self.h // 2

    @property
    def area(self) -> int:
        return self.w * self.h

    def inset(self, px: int) -> Rect:
        return Rect(self.x + px, self.y + px, max(0, self.w - 2 * px), max(0, self.h - 2 * px))

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}

    def __bool__(self) -> bool:
        return self.w > 0 and self.h > 0


@dataclass(frozen=True)
class Placement:
    """Where a source sits inside the canvas, and what surrounds it."""

    canvas: Rect
    picture: Rect
    scale_w: int
    scale_h: int
    mode: FitMode

    # The regions the picture does not cover. Empty rects when it fills the
    # frame, which is the common landscape-into-landscape case.
    left: Rect
    right: Rect
    top: Rect
    bottom: Rect

    @property
    def is_pillarboxed(self) -> bool:
        return bool(self.left) or bool(self.right)

    @property
    def is_letterboxed(self) -> bool:
        return bool(self.top) or bool(self.bottom)

    @property
    def rails(self) -> dict[str, Rect]:
        """The usable empty regions, largest first.

        A rail narrower than this is technically empty space but cannot hold a
        legible label, so it is not offered as somewhere to put one. 240px at
        1080p is roughly the narrowest column that fits a short heading plus a
        caption without hyphenating.
        """
        min_useful = max(160, self.canvas.w // 8)
        found = {
            name: r
            for name, r in (
                ("left", self.left),
                ("right", self.right),
                ("top", self.top),
                ("bottom", self.bottom),
            )
            if r and min(r.w, r.h) >= min_useful
        }
        return dict(sorted(found.items(), key=lambda kv: -kv[1].area))

    def to_dict(self) -> dict:
        return {
            "canvas": self.canvas.to_dict(),
            "picture": self.picture.to_dict(),
            "scale": [self.scale_w, self.scale_h],
            "mode": self.mode,
            "pillarboxed": self.is_pillarboxed,
            "letterboxed": self.is_letterboxed,
            "rails": {k: v.to_dict() for k, v in self.rails.items()},
        }


def _even(n: int) -> int:
    """Round down to an even number.

    Not cosmetic. Chroma-subsampled pixel formats -- yuv420p, which is every
    delivery codec's default -- cannot represent odd dimensions, and ffmpeg
    fails outright with "width not divisible by 2" rather than rounding for you.
    """
    return n - (n % 2)


def fit(
    src_w: int,
    src_h: int,
    canvas_w: int,
    canvas_h: int,
    mode: FitMode = "contain",
) -> Placement:
    """Place a source of `src_w`x`src_h` (as displayed) into the canvas.

    `src_w`/`src_h` must already have rotation and sample aspect applied -- pass
    `MediaFacts.display_size`, never the coded size, or a rotated phone clip
    will be fitted sideways.
    """
    if src_w <= 0 or src_h <= 0:
        raise ValueError(f"source size must be positive, got {src_w}x{src_h}")
    if canvas_w <= 0 or canvas_h <= 0:
        raise ValueError(f"canvas size must be positive, got {canvas_w}x{canvas_h}")

    canvas = Rect(0, 0, canvas_w, canvas_h)

    if mode == "stretch":
        sw, sh = canvas_w, canvas_h
    elif mode == "none":
        sw, sh = src_w, src_h
    else:
        ratio_w = canvas_w / src_w
        ratio_h = canvas_h / src_h
        # contain takes the smaller ratio, so the whole picture fits and the
        # remainder becomes rails. cover takes the larger, filling the frame and
        # cropping the overflow.
        ratio = min(ratio_w, ratio_h) if mode == "contain" else max(ratio_w, ratio_h)
        sw = _even(max(2, round(src_w * ratio)))
        sh = _even(max(2, round(src_h * ratio)))

    # Centre, then clamp the visible picture to the canvas. For cover the scaled
    # size exceeds the canvas, so the picture rect is the canvas itself and the
    # overflow is cropped symmetrically.
    off_x = (canvas_w - sw) // 2
    off_y = (canvas_h - sh) // 2

    vis_x = max(0, off_x)
    vis_y = max(0, off_y)
    vis_w = min(sw, canvas_w - vis_x) if off_x >= 0 else canvas_w
    vis_h = min(sh, canvas_h - vis_y) if off_y >= 0 else canvas_h
    picture = Rect(vis_x, vis_y, vis_w, vis_h)

    left = Rect(0, picture.y, picture.x, picture.h)
    right = Rect(picture.x2, picture.y, canvas_w - picture.x2, picture.h)
    top = Rect(0, 0, canvas_w, picture.y)
    bottom = Rect(0, picture.y2, canvas_w, canvas_h - picture.y2)

    return Placement(
        canvas=canvas,
        picture=picture,
        scale_w=sw,
        scale_h=sh,
        mode=mode,
        left=left,
        right=right,
        top=top,
        bottom=bottom,
    )


def anchors(place: Placement, *, margin: int = 0) -> dict[str, tuple[int, int]]:
    """Named points a graphic can be positioned against.

    These are what a scene should reference instead of literal pixel pairs. A
    callout anchored to `rail_left` follows the layout when the canvas changes
    or the footage is replaced; one anchored to (330, 250) does not, and that is
    how a graphics system ends up locked to one shoot.
    """
    c = place.canvas.inset(margin)
    out: dict[str, tuple[int, int]] = {
        "canvas_center": (place.canvas.cx, place.canvas.cy),
        "canvas_tl": (c.x, c.y),
        "canvas_tr": (c.x2, c.y),
        "canvas_bl": (c.x, c.y2),
        "canvas_br": (c.x2, c.y2),
        "picture_center": (place.picture.cx, place.picture.cy),
        "picture_top": (place.picture.cx, place.picture.y),
        "picture_bottom": (place.picture.cx, place.picture.y2),
        "lower_third": (place.canvas.cx, place.canvas.y + int(place.canvas.h * 0.72)),
    }
    for name, rect in place.rails.items():
        out[f"rail_{name}"] = (rect.cx, rect.cy)
        out[f"rail_{name}_top"] = (rect.cx, rect.y + max(margin, rect.h // 8))
        out[f"rail_{name}_bottom"] = (rect.cx, rect.y2 - max(margin, rect.h // 8))
    return out
