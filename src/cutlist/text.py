"""Static text: cards, lower thirds, captions and label chips.

Rendered with Pillow to a PNG with alpha, then composited like any other layer.

**Why not ffmpeg's `drawtext`.** It requires the binary to have been built with
libfreetype, and that is not something a user can be told to check. One widely
distributed build -- the one bundled with a popular editor, which is how many
people on Windows end up with ffmpeg at all -- ships 490 filters and `drawtext`
is not among them. A text feature that is absent on a large fraction of installs
is not a text feature.

**Why not a browser.** Animated graphics genuinely need one. A title card does
not, and asking someone to download 150 MB of Chromium to put a word on screen
is the kind of dependency that makes a tool not worth installing. Pillow is
about 3 MB, bundles FreeType on all three platforms, and needs no system
libraries.

**Legibility over decoration.** Text sits over footage nobody controls, and a
coloured glow is not a substitute for contrast. Every text element gets a
semi-opaque neutral scrim behind it, and contrast is measured against that
scrim rather than against the picture -- because the picture may be anything.
"""

from __future__ import annotations

import json
from pathlib import Path

from .cache import Cache
from .config import Config
from .errors import CapabilityMissing, ConfigError

# Ordered candidates per role. A system stack renders correctly on a fresh
# machine with no download and no licence question, which is the right default.
# It is NOT deterministic across machines -- different metrics mean different
# line breaks -- so a project that needs reproducible output pins a font file,
# and `cutlist doctor` says so rather than leaving it to be discovered.
_FONT_CANDIDATES = {
    "sans": [
        "DejaVuSans.ttf",
        "Arial.ttf",
        "arial.ttf",
        "Helvetica.ttc",
        "LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ],
    "sans-bold": [
        "DejaVuSans-Bold.ttf",
        "arialbd.ttf",
        "LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ],
    "mono": [
        "DejaVuSansMono.ttf",
        "consola.ttf",
        "Menlo.ttc",
        "LiberationMono-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "C:/Windows/Fonts/consola.ttf",
    ],
}


def _require_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401
    except ModuleNotFoundError as exc:
        raise CapabilityMissing(
            "This config uses a text scene, and Pillow is not installed.",
            hint="pip install 'cutlist[text]'   (about 3 MB; no system libraries)",
        ) from exc
    from PIL import Image, ImageDraw, ImageFont

    return Image, ImageDraw, ImageFont


def _load_font(role: str, size: int, override: str | None = None):
    _, _, ImageFont = _require_pillow()
    candidates = ([override] if override else []) + _FONT_CANDIDATES.get(role, [])
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, ValueError):
            continue
    # A default bitmap font is not a good look, but a crash here would mean a
    # machine with no usable font renders nothing at all. Degrade and let the
    # doctor report it.
    return ImageFont.load_default()


# Neutral defaults. Achromatic, so the plugin imposes no colour identity; a
# project overrides them wholesale in `theme`.
DEFAULT_THEME = {
    "ink": "#f4f4f4",
    "ink_muted": "#b8b8b8",
    "scrim": [20, 20, 20, 220],
    "accent": "#d0d0d0",
    "pad": 28,
    "radius": 0,
    "base_px": 44,
}


def _hex_rgba(value, default=(255, 255, 255, 255)):
    if isinstance(value, (list, tuple)):
        v = list(value) + [255] * (4 - len(value))
        return tuple(int(x) for x in v[:4])
    if not isinstance(value, str):
        return default
    s = value.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) == 6:
        s += "ff"
    if len(s) != 8:
        return default
    return tuple(int(s[i : i + 2], 16) for i in (0, 2, 4, 6))


def _theme(cfg: Config) -> dict:
    t = dict(DEFAULT_THEME)
    t.update(cfg.theme or {})
    return t


def render_scene(cfg: Config, name: str, scene: dict, cache: Cache) -> Path:
    """Render one scene to a PNG with alpha, cached."""
    Image, ImageDraw, _ = _require_pillow()
    proj = cfg.project
    th = _theme(cfg)

    kind = scene.get("kind", "card")
    if kind not in ("card", "lower_third", "caption", "chip"):
        raise ConfigError(
            f"scene {name!r}: unknown kind {kind!r}",
            hint="Supported: card, lower_third, caption, chip",
        )

    spec = {"scene": scene, "theme": th, "canvas": [proj.width, proj.height], "v": 1}
    key = cache.key(spec, [])
    out = cache.path_for("scenes", name, key, ".png")
    if cache.valid(out):
        return out

    W, H = proj.width, proj.height
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    ink = _hex_rgba(th["ink"])
    muted = _hex_rgba(th["ink_muted"])
    scrim = _hex_rgba(th["scrim"], (20, 20, 20, 220))
    base = int(th.get("base_px", 44))
    pad = int(th.get("pad", 28))

    title = str(scene.get("title", "") or "")
    subtitle = str(scene.get("subtitle", "") or "")
    kicker = str(scene.get("kicker", "") or "")
    font_override = scene.get("font")

    if kind == "card":
        # Full-frame slate: an opaque ground, since there is nothing behind it.
        d.rectangle([0, 0, W, H], fill=_hex_rgba(scene.get("background", "#141414")))
        f_kick = _load_font("mono", int(base * 0.42), font_override)
        f_title = _load_font("sans-bold", int(base * 1.9), font_override)
        f_sub = _load_font("sans", int(base * 0.72), font_override)

        block = []
        if kicker:
            block.append((kicker, f_kick, muted, int(base * 0.9)))
        if title:
            block.append((title, f_title, ink, int(base * 2.5)))
        if subtitle:
            block.append((subtitle, f_sub, muted, int(base * 1.1)))

        total = sum(h for _, _, _, h in block)
        y = (H - total) // 2
        for text, font, colour, line_h in block:
            w = d.textlength(text, font=font)
            d.text(((W - w) / 2, y), text, font=font, fill=colour)
            y += line_h

    elif kind in ("lower_third", "caption", "chip"):
        f_title = _load_font("sans-bold", int(base * 0.95), font_override)
        f_sub = _load_font("sans", int(base * 0.55), font_override)

        tw = d.textlength(title, font=f_title) if title else 0
        sw = d.textlength(subtitle, font=f_sub) if subtitle else 0
        box_w = int(max(tw, sw) + pad * 2)
        line_h = int(base * 1.25)
        box_h = int(pad * 2 + (line_h if title else 0) + (int(base * 0.85) if subtitle else 0))

        anchor = scene.get("anchor", "lower_left" if kind != "caption" else "bottom_center")
        margin = int(scene.get("margin", pad * 2))
        if anchor == "bottom_center":
            x = (W - box_w) // 2
            y = H - box_h - margin
        elif anchor == "lower_left":
            x = margin
            y = int(H * 0.72)
        elif anchor == "top_left":
            x, y = margin, margin
        elif anchor == "top_right":
            x, y = W - box_w - margin, margin
        else:
            x, y = margin, H - box_h - margin

        # The scrim is what makes text legible over footage nobody controls.
        d.rectangle([x, y, x + box_w, y + box_h], fill=scrim)
        if scene.get("rule", True):
            d.rectangle([x, y, x + 3, y + box_h], fill=_hex_rgba(th["accent"]))

        ty = y + pad
        if title:
            d.text((x + pad, ty), title, font=f_title, fill=ink)
            ty += line_h
        if subtitle:
            d.text((x + pad, ty), subtitle, font=f_sub, fill=muted)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def scene_summary(cfg: Config) -> str:
    return json.dumps({k: v.get("kind", "card") for k, v in cfg.scenes.items()}, indent=2)
