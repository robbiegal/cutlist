"""Audio: per-shot treatment, and the timeline-spanning bus.

The split matters. Anything scoped to one shot -- a mute, a ramp, a level trim,
room tone laid over a hole -- belongs to that shot and is cached with it.
Anything that spans cuts -- a music bed, a duck keyed on the assembled dialogue,
a single loudness pass -- can only be resolved once every shot exists, so it
happens at assemble.

Four rules are load-bearing.

**Every level in config is decibels.** Amplitude appears only here. A config
holding `0.2` invites being read as a decibel figure by the next person, and the
result is wrong by about 14 dB in a direction nobody hears until playback.

**Filters multiply, so the constant gain is emitted first.** A per-shot trim
must be applied before the window mutes and ramps, so those land on top of it.
Inverted, the constant gain overwrites a carefully verified mute and the
silence stops being silent.

**A mute can only remove sound.** Under continuous ambience -- an engine, a
room, traffic -- a silent hole draws far more attention than whatever was
removed. `cutlist grain` samples room tone from a clean stretch of the *same*
clip, so the microphone, level and timbre match with no correction needed.

**Verify numerically.** A mute that is not actually silent is a real and common
outcome, and it is inaudible to a person scrubbing quickly. `cutlist verify
--audio` measures it.
"""

from __future__ import annotations

from .config import AudioBus, SegmentAudio
from .graph import Graph, db_to_gain, expr

# Silence in practice, not in theory. Genuine digital silence is unnecessary and
# a hard zero can produce a click at the boundary in some encoders; this is far
# below anything audible and measures as silence to any meter.
MUTE_GAIN = 0.0


def segment_audio_filters(sa: SegmentAudio, duration_s: float) -> list[str]:
    """Filters for one shot's own audio, in emission order."""
    filters: list[str] = []

    if sa.mute:
        # Whole-shot mute short-circuits everything else. A per-window treatment
        # under a whole-shot mute is dead config, so it is not emitted -- and
        # `cutlist lint` reports the combination rather than silently honouring
        # one of them.
        return [f"volume={MUTE_GAIN}"]

    # The constant trim goes FIRST so window treatments multiply on top of it.
    if abs(sa.gain_db) > 1e-6:
        filters.append(f"volume={db_to_gain(sa.gain_db):.6f}")

    for w in sa.windows:
        if w.fade_s > 0:
            # Ramp down into the window, then hold silence through it. Two
            # filters, because a fade alone would recover afterwards and a mute
            # alone would cut abruptly -- and an abrupt drop reads to a listener
            # as a mistake rather than as an edit.
            ramp_start = max(0.0, w.from_s - w.fade_s)
            filters.append(
                f"afade=t=out:st={ramp_start:.4f}:d={w.fade_s:.4f}"
                f":enable={expr(f'between(t,{ramp_start:.4f},{w.from_s:.4f})')}"
            )
        filters.append(
            f"volume={MUTE_GAIN}:enable={expr(f'between(t,{w.from_s:.4f},{w.to_s:.4f})')}"
        )
        if w.fade_s > 0 and w.to_s < duration_s - 1e-6:
            fade_in_end = min(duration_s, w.to_s + w.fade_s)
            filters.append(
                f"afade=t=in:st={w.to_s:.4f}:d={(fade_in_end - w.to_s):.4f}"
                f":enable={expr(f'between(t,{w.to_s:.4f},{fade_in_end:.4f})')}"
            )

    return filters


def apply_segment_audio(g: Graph, src: str, sa: SegmentAudio, duration_s: float) -> str:
    filters = segment_audio_filters(sa, duration_s)
    if not filters:
        return src
    return g.chain(src, ",".join(filters), "a")


def fill_windows(sa: SegmentAudio) -> list:
    """Windows that name a fill sound, in order."""
    return [w for w in sa.windows if w.fill]


def bus_filters(bus: AudioBus, *, have_bed: bool, have_dialogue: bool) -> dict[str, list[str]]:
    """Filters for the assemble-stage bus.

    Returned as named lanes rather than one string, because the duck needs the
    dialogue as a side chain and therefore cannot be expressed as a linear
    chain over the bed alone.
    """
    lanes: dict[str, list[str]] = {"bed": [], "mix": [], "master": []}

    if have_bed:
        lanes["bed"].append(f"volume={db_to_gain(bus.bed_gain_db):.6f}")

    if bus.loudnorm:
        # Single pass. Two-pass measures first and corrects second, which is
        # more accurate but doubles the cost of the last stage of every build;
        # the measurement is cached separately by the assemble stage so a
        # revision does not pay for it twice.
        lanes["master"].append(
            f"loudnorm=I={bus.target_lufs:.1f}:TP=-1.5:LRA=11"
        )

    # `normalize=0` is not optional. amix defaults to scaling every input by
    # 1/n, so adding a quiet music bed to dialogue halves the dialogue -- the
    # mix gets quieter the more you put in it, which is never what anyone means.
    lanes["mix"].append("amix=inputs=2:duration=longest:normalize=0")

    return lanes


def duck_filter(bus: AudioBus) -> str:
    """Sidechain compression of the bed, keyed by the dialogue.

    Preferred over a static gain because a bed only needs to drop while someone
    is speaking; a static duck holds the music down through the silences too,
    which sounds like a mistake in the mix rather than an intent.
    """
    return (
        f"sidechaincompress=threshold={db_to_gain(bus.duck_threshold_db):.6f}"
        f":ratio={bus.duck_ratio:.2f}:attack=20:release=300"
    )


def silence_expectations(sa: SegmentAudio, duration_s: float) -> list[tuple[float, float]]:
    """Windows that must measure as silent afterwards.

    Handed to the verifier so the check is derived from the config rather than
    restated by hand in a test -- a mute nobody remembered to assert is a mute
    nobody notices failing.
    """
    if sa.mute:
        return [(0.0, duration_s)]
    return [(w.from_s, w.to_s) for w in sa.windows]
