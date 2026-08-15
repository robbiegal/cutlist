---
name: cutlist-doctor
description: Check that cutlist can run - ffmpeg version and filters, ffprobe, the Python environment, the CLI, and the optional dependency bands - then install whatever is missing. Use before the first build in a project, when a command reports a missing filter, an ffmpeg that is too old or an extra that is not installed, when a render dies on a tool it could not find, or after an update.
allowed-tools: Bash, Read
---

# Environment check

Dependencies are installed here, on request, rather than up front. Band 1 needs
nothing but ffmpeg, the text band is a few megabytes and the graphics band is
hundreds - blocking a session on a download nobody asked for, for a feature this
config may never use, is the failure this ordering prevents.

## 1. Look first

```bash
cutlist doctor              # inside a project, picks the config up automatically
cutlist doctor --config path/to/project.yaml
```

Report the result as a short table. The command prints, in order:

| Line | What it tells you |
|---|---|
| `cutlist <version>` / `python <version>` | The environment resolved at all. If this does not print, go to step 4. |
| `ffmpeg <version> <path>` | The build actually being used, including a side-install that never reached PATH. |
| `ffprobe <path>` | Probed separately. The two are found independently and can disagree. |
| `ok` / `FAIL` against the minimum | The floor check. See step 2. |
| `N filters, M encoders available` | The inventory this build was measured to have, not what the version number implies. |
| `text` line | Whether the text band is installed. |
| `config` block | Path, segment count, total duration, and `needs:` - the feature list **this** config demands. |
| final `ok` / `FAIL` | Whether this build can do everything this config asks for. |

Exit code 0 means usable, 1 means not. A non-zero doctor is a stop, not a
warning: starting a build anyway spends conform time to arrive at the same
error with a half-populated work directory behind it.

## 2. It gates on this config only, and that is deliberate

`doctor` checks the version floor always, and beyond that checks only the
features the loaded config actually resolves to - a mosaic redaction, a
non-cut transition, a curves grade, ducking, loudness normalisation.

A three-clip join must never fail because a filter it will never emit is
absent from the build. Gating on the union of everything the engine *could*
emit is how a tool becomes uninstallable: one missing filter and the user gets
nothing, forever, for a job that would have rendered fine.

Two consequences you have to carry into your reporting:

- Run outside a project, `doctor` says so explicitly and checks nothing
  config-specific. "Doctor passed" then means only "the floor is met".
- A pass is a statement about the config as it was when you ran it. Add a
  mosaic redaction or a wipe afterwards and the answer can change. Re-run
  `cutlist doctor` after editing the config, before `cutlist build`.

## 3. Version floor, and what needs newer

| Requirement | Version | Why |
|---|---|---|
| Everything (the floor) | ffmpeg **4.3** | `xfade` landed there and every non-cut transition uses it. Below the floor nothing in the engine is trustworthy. |
| Redaction, mosaic mode | ffmpeg **6.0** | `pixelize`. |
| Redaction, blur mode | 4.3 | `boxblur`. Use this mode when you are stuck below 6.0. |
| Ducking, loudness, grade, transitions | 4.3 | No newer requirement. |

A build whose version string is a bare git hash yields no comparable number and
is treated as older than everything. That is the safe direction: an unknowable
build becomes an explicit "cannot confirm" instead of a silent pass.

Point at a different binary with `CUTLIST_FFMPEG` / `CUTLIST_FFPROBE`. An
override that is set but unusable is a hard error, never a quiet fallback to
whatever is on PATH - a pin that silently runs a different binary than the one
you pinned is worse than no pin.

## 4. Install what is missing

The package has no hard dependencies: band 1 is the standard library plus
ffmpeg. An install that resolves at all can already conform, grade, redact, mix,
deliver and verify. Everything below is optional and is installed only when the
config in front of you needs it.

| Situation | Command |
|---|---|
| `cutlist` does not resolve | `pip install cutlist` |
| Config is `.yaml` and the loader reports PyYAML missing | `pip install pyyaml`, or write the config as `.json` / `.toml`, which need nothing |
| Text scenes needed | `pip install 'cutlist[text]'` |

Then confirm the command resolves:

```bash
cutlist --version
```

If that fails after an install reported success, the package landed in an
interpreter other than the one on PATH. Report which `python` and which `pip`
were used rather than reinstalling and hoping.

## 5. Bands, and saying the cost out loud

Dependencies are installed on demand, never all at once.

| Band | Contents | Size | Buys you |
|---|---|---|---|
| 1 `core` | ffmpeg + ffprobe only | 0 | Trims, conform, grade, redaction, audio, transitions, delivery, and every verification instrument. |
| 2 `text` | Pillow | ~3 MB | Static text: title cards, section cards, lower thirds, captions, label chips. |
| 3 `graphics` | Playwright + pinned Chromium | hundreds of MB | Animated motion graphics. **Not in v0.1** - do not offer it. |

**State the cost before installing, and wait.** Three megabytes still deserves
a sentence; a browser engine deserves a refusal in v0.1. Do not install a band
because it seemed likely to be needed later.

Band 2 exists because text is rendered to an image rather than drawn by
ffmpeg. That is not a preference: one widely distributed bundled build ships
without libfreetype and therefore has no `drawtext` at all - measured on a
machine where 490 other filters were present. A pipeline that assumes
`drawtext` works produces nothing on that machine and cannot say why.

## 6. ffmpeg itself

The one prerequisite no plugin mechanism can install.

| Platform | Install |
|---|---|
| macOS | `brew install ffmpeg` |
| Debian/Ubuntu | `sudo apt install ffmpeg` |
| Fedora | `sudo dnf install ffmpeg` |
| Arch | `sudo pacman -S ffmpeg` |
| Windows | `winget install Gyan.FFmpeg` |

Distribution packages on older long-term-support releases are often 4.x, which
clears the floor but not mosaic redaction. When that is the gap, either switch
those redactions to blur mode or fetch a current static build and point
`CUTLIST_FFMPEG` at it - do not upgrade a system package set to move one
filter.

## 7. Do not

- Do not install a band to "get ahead". An unused browser engine is a download
  the user pays for and never sees a frame from.
- Do not carry a pass forward across a directory change. `doctor` picks the
  config up from where it is run, so a pass earned in one project says nothing
  about the next one.
- Do not report a doctor pass without naming the config it was run against.
  Without that, the reader cannot tell a checked project from an empty
  directory.
