"""`cutlist init`: everything a project needs before the first render.

Including a `.gitignore`, which is not housekeeping. A video project directory
fills with media and rendered evidence, and the first `git add -A` in one that
lacks this file commits gigabytes -- and, in a project with anything sensitive
in shot, commits the unredacted originals alongside the redacted delivery. It is
written first, before anything else exists to be committed.

`PLAN.md` is scaffolded too, with a revision log that reads newest-first. A
resuming session reads the top entries to learn the current state, which is
worth more than it sounds: the alternative is a plan document whose tables
quietly contradict the config after a few revisions, and an agent that acts on
the stale half.
"""

from __future__ import annotations

from pathlib import Path

PROFILES = {
    "1080p30": (1920, 1080, 30),
    "1080p25": (1920, 1080, 25),
    "720p30": (1280, 720, 30),
    "2160p30": (3840, 2160, 30),
}

GITIGNORE = """\
# Written before the first `git add`, deliberately.
#
# A video project directory fills with source media and rendered evidence. Both
# are large, and the source is often the thing you are redacting -- committing
# it defeats the redaction in the delivered file.

media/
source/
_cut/
_out/
*.mp4
*.mov
*.mkv
*.webm
*.wav
*.mp3
"""

PLAN = """\
# {name}

## Revision log

Newest first. Each entry records what was asked for and what changed, so a
session resuming here can read only the top entries to learn the current state.

### v1 -- initial
Scaffolded. No beats chosen yet.

## Beat sheet

Confidence markers, on every row, always:

| | meaning |
|---|---|
| `[C]` | confirmed -- watched the frame, this is right |
| `[I]` | inferred -- read off a contact sheet, probably right |
| `[U]` | unverified -- a guess, must be checked before delivery |

An unmarked timing gets treated as fact by the next person to read this, which
is how a guess becomes a bug.

| # | segment | source | in | out | conf | note |
|---|---|---|---|---|---|---|
| 1 | | | | | `[U]` | |
"""

CONFIG = """\
# {name} -- a cut list.
#
# Times are SECONDS. Levels are ALWAYS dB. Unknown keys are rejected rather than
# ignored, so a typo fails loudly instead of quietly doing nothing.

project:
  name: {name}
  width: {w}
  height: {h}
  fps: {fps}
  media_dir: media
  out_dir: _out
  work_dir: _cut

# Off by default. This tool does not tint your footage unless asked.
grade:
  enabled: false
  eq: {{ saturation: 1.0, contrast: 1.0, gamma: 1.0 }}

audio:
  bed_gain_db: -18
  duck: false
  loudnorm: false

# Strings and regexes that must never appear in any generated text. Empty is the
# right default -- the mechanism ships, the content is yours.
#
# Note what this can and cannot do: it reads text, so it catches a name you
# typed into a caption. It cannot see a name written on a whiteboard in the
# footage -- that needs `redact` and a look at the frames.
policy:
  forbidden_strings: []
  forbidden_patterns: []

scenes:
  opening_title:
    kind: card
    title: "{name}"
    subtitle: ""

timeline:
  - id: opening
    duration_s: 3.0
    video_layers:
      - {{ kind: color, value: "#141414", z: 0, tags: [footage] }}
      - {{ kind: scene, name: opening_title, z: 10, tags: [graphics] }}

  # - id: shot_a
  #   video_layers:
  #     - {{ kind: clip, source: your-clip.mp4, in: 0.0, out: 5.0,
  #         fit: contain, z: 0, tags: [footage] }}

variants:
  - name: final
  - name: clean
    tags: [footage]

render:
  vcodec: libx264
  crf: 18
  preset: medium
  acodec: aac
  abr: 192k
"""


def init_project(root: Path, *, profile: str = "1080p30", force: bool = False) -> list[str]:
    w, h, fps = PROFILES[profile]
    name = root.name.replace(" ", "-").lower() or "project"

    files = {
        ".gitignore": GITIGNORE,
        "PLAN.md": PLAN.format(name=name),
        "project.yaml": CONFIG.format(name=name, w=w, h=h, fps=fps),
    }

    written: list[str] = []
    for rel, body in files.items():
        p = root / rel
        if p.exists() and not force:
            written.append(f"{rel} (kept -- already exists)")
            continue
        p.write_text(body, encoding="utf-8")
        written.append(rel)

    for d in ("media", "_cut", "_out"):
        (root / d).mkdir(exist_ok=True)
    written.append("media/  _cut/  _out/")

    return written
