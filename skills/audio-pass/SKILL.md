---
name: audio-pass
description: Silence a passage without leaving a hole, ramp instead of cutting, fill the gap with room tone sampled from the same clip, lay a music bed under the cut and duck it, then measure the result instead of assuming it. Use when asked to mute part of a video, remove someone talking or a passing conversation, drop distracting or uneven audio, add background music, duck the music under speech, or make loudness consistent across a cut.
argument-hint: "[segment-id]"
allowed-tools: Bash, Read, Edit, Glob
---

# Treat the audio, then measure it

Two things go wrong in an audio pass and neither is audible while you are doing
it. A mute leaves a hole that draws more attention than what it removed. And a
mute that is not actually silent looks identical in config to one that is.
Everything below exists to close those two.

## 1. Get the contract, do not recall it

```bash
cutlist prompt audio
```

Follow what that prints. It is versioned with the engine and names the exact
keys and ranges. A remembered copy is how a level ends up in the wrong unit.

## 2. Decide where the treatment belongs

| Treatment | Scope | Where it goes |
|---|---|---|
| Mute a passage | One shot | `timeline[].audio.windows[]` |
| Trim one shot's level | One shot | `timeline[].audio.gain_db` |
| Kill a whole shot's sound | One shot | `timeline[].audio.mute` |
| Lay room tone over a hole | One shot | `windows[].fill` |
| Music bed | Whole cut | `audio.bed`, `audio.bed_gain_db` |
| Duck the bed under speech | Whole cut | `audio.duck`, `duck_threshold_db`, `duck_ratio` |
| One loudness target for the deliverable | Whole cut | `audio.loudnorm`, `audio.target_lufs` |

The split is not cosmetic. Shot-scoped work is cached with the shot, so changing
one mute rebuilds one shot. Bus work spans cuts and cannot resolve until every
shot exists, so it happens once, at assemble. Putting a per-shot problem on the
bus makes it everyone's problem; putting a bus problem in a shot makes it
inconsistent across the cut.

## 3. Ramp, never cut

```yaml
timeline:
  - id: room-wide
    audio:
      windows:
        - from: 12.40
          to: 15.10
          fade_s: 0.25
```

A hard drop to silence does not read as an edit. It reads as a fault - a
dropout, a bad file, a player glitch - and the viewer's attention goes to the
mechanism instead of the picture. A ramp reads as intent.

The ramp is placed **before** `from`, so the window is fully silent from `from`
onward. Ramping inside the window would leave its first fraction audible, and
the first fraction of a window is usually exactly the word you were removing.

| Material | `fade_s` |
|---|---|
| Speech, tight removal | 0.15 - 0.25 |
| Speech with ambience under it | 0.25 - 0.40 |
| Music or a continuous tone | 0.5 - 1.0 |
| Anything at all | Never 0 |

## 4. A mute can only remove sound. Fill the hole

Under continuous ambience - a room, a fan, traffic, an air handler - the removal
is not the conspicuous part. The silence is. The listener hears the seam even
when they could not hear what you cut, and a hole is a louder edit than a word.

Sample the fill from a clean stretch of the **same clip**:

```bash
cutlist grain --clip room-wide --from 48.0 --to 52.0 --out media/tone-room-wide.wav
```

```yaml
        - from: 12.40
          to: 15.10
          fade_s: 0.25
          fill: tone-room-wide.wav
```

| Rule for sampling | Why |
|---|---|
| Same clip, always. Never a stock room-tone file. | Same microphone, same preamp gain, same room, same encoder. Mic, level and timbre match with no gain tweaking at all. A stock file is a different room, and every one of those differences is audible at the seam even after you match the level. |
| A genuinely clean stretch: no speech, no footsteps, no handling noise, no chair. | Anything in the sample repeats across the fill and becomes a rhythm the ear locks onto. |
| 3 - 5 s, longer than the hole where you can. | Gives the fill room to run without an obvious loop period. |
| Sampled near the window in time. | Level and background drift as a scene runs on; a sample from the far end of a long take will not sit. |

If the source has no clean stretch to sample, say so rather than substituting a
different room. The honest options are a shorter window, a longer ramp, or
covering the passage with the bed.

## 5. Order of operations is load-bearing

The constant per-shot gain is emitted **first**, so the window mutes and fades
multiply on top of it and stay intact. Invert that order and the constant gain
cancels them.

Two consequences for how you write config:

- Express a shot's level trim once, as `gain_db` on that shot. Do not stack a
  second corrective level after the windows, and do not reach for the bus to fix
  one shot.
- `mute: true` on a shot short-circuits everything else. Window treatments under
  a whole-shot mute are dead config and are not emitted. That combination is
  reported by `cutlist lint` rather than silently honouring one of them - run
  lint before build:

```bash
cutlist lint
```

## 6. Every level is dB. Never write an amplitude

Amplitude never appears in config. A config holding `0.2` invites the next
reader - which is often you, later - to take it as a decibel figure, and the
result is wrong by roughly 14 dB in a direction nobody hears until playback.

`gain_db` accepts -96 to +24. `bed_gain_db` accepts -96 to +12.

| Intent | dB |
|---|---|
| Barely-there trim | -1 to -2 |
| Clearly quieter, still present | -6 |
| About half as loud to a listener | -10 |
| A bed sitting under speech | -18 to -24 |
| Gone | Use `mute` or a window, not -60 |

## 7. Bed, and ducking

```yaml
audio:
  bed: music/bed-loop.wav
  bed_gain_db: -20
  duck: true
  duck_threshold_db: -24
  duck_ratio: 6
  loudnorm: true
  target_lufs: -16
```

| Approach | Right when |
|---|---|
| `duck: true` - sidechain compression of the bed, keyed on the assembled dialogue | There is speech and it starts and stops. The bed drops only while someone is speaking and returns in the gaps, which is what a mix is supposed to do. |
| Static `bed_gain_db` alone, no duck | There is no speech at all; or speech is wall-to-wall, so a duck would hold the bed down permanently and add pumping at every edge for nothing; or the bed is the point - a title sequence, a montage - and dipping it under an occasional line would read as a fault. |

A static level chosen low enough to be safe under speech also holds the music
down through every silence, which sounds like an oversight in the mix rather
than a decision. That is the failure the sidechain avoids.

`duck_threshold_db` is the dialogue level at which ducking begins. Set it too
low and room tone triggers the duck, so the bed never comes back up. Set it too
high and quiet speech never triggers it, so the bed sits over the line you cared
about. `duck_ratio` between 4 and 8 covers most material; higher is a more
obvious effect, not a cleaner one.

Adding a bed does not reduce the dialogue level - do not pre-compensate by
raising every shot's `gain_db`. And the bed is not looped: one shorter than the
cut stops where it ends, which `verify --audio` will show as the tail segments
measuring well below the rest.

`loudnorm` runs once, at assemble, over the finished mix. It is the right place
for a single loudness target and the wrong tool for one quiet shot - fix that
shot with its own `gain_db`.

## 8. Verify numerically, not by assumption

```bash
cutlist verify --audio
```

It reports measured loudness per segment, and asserts that every window the
config declared silent measures as silent. Results land in `_cut/report.json`;
a failure exits 2.

**A mute that is not actually silent is a real and common outcome.** It is also
inaudible to anyone scrubbing quickly - speech at low level under a bed survives
a fast listen and does not survive one viewer with headphones. Common causes:

| Cause | Tell |
|---|---|
| Window times written against the source's timestamps instead of segment-relative seconds | Segment time starts at 0 for every segment. A window that looks right in the source lands somewhere else in the cut. |
| The window attached to the wrong segment id | `lint` catches a window past the end of its segment; it cannot catch a valid window on the wrong shot. |
| `mute: true` sitting alongside `windows` | The windows were never emitted at all. `lint` reports the combination. |
| A level trim written as an amplitude | See section 6. |

Per-segment loudness is the other half of this check. A shot several LU below
its neighbours is what a viewer experiences as "the audio is uneven", and it is
much easier to read off the report than to hear across a long cut. Fix it on
that shot, not on the master.

Do not report the pass as done on an exit code of 2, and do not report it as
done without having run `--audio` at all.

## Rules that govern

1. **Ramp into and out of every window.** Prevents a hard drop that reads to a
   listener as a fault rather than an edit.
2. **Fill a mute made under continuous ambience, from the same clip.** Prevents
   a silent hole that draws more attention than what was removed, and prevents a
   fill from another room that never sits no matter how it is levelled.
3. **One constant `gain_db` per shot, emitted before the windows.** Prevents a
   later level from cancelling a mute you already verified.
4. **Every level in dB, never an amplitude.** Prevents a number being read in
   the wrong unit and being wrong by more than 10 dB.
5. **Duck when speech starts and stops; a static level only when it does not.**
   Prevents music held down through silences that should have breathed.
6. **Run `verify --audio` and read the numbers.** Prevents shipping a mute that
   is not silent - the failure that is invisible at every stage before playback.
