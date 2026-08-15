"""Tests that need no ffmpeg and no media.

This is deliberate. Every rule these check is one that, when broken, produces a
*plausible* result rather than an error -- a graph that renders, a config that
loads, a coordinate that looks reasonable. Those are exactly the failures a
render test cannot catch, because the render succeeds.

The ffmpeg-dependent path is covered by one job in CI that builds a synthetic
timeline end to end and asserts on the delivered file.
"""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest

from cutlist import config as C
from cutlist.audio import segment_audio_filters
from cutlist.config import AudioWindow, RedactBox, SegmentAudio
from cutlist.errors import ConfigError
from cutlist.geometry import fit
from cutlist.graph import Graph, clamp_expr, db_to_gain, esc, expr, lerp_expr
from cutlist.probe import MediaFacts
from cutlist.redact import strength_for

# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------


def test_portrait_into_landscape_gives_rails():
    """A 9:16 source in a 16:9 canvas leaves two usable rails.

    The numbers are not arbitrary: 1080/1280 scales 720 to 608, centred at 656.
    This is the case the whole rail-annotation idea rests on, so it is pinned.
    """
    p = fit(720, 1280, 1920, 1080, "contain")
    assert (p.picture.x, p.picture.w) == (656, 608)
    assert p.picture.h == 1080
    assert p.is_pillarboxed and not p.is_letterboxed
    assert set(p.rails) == {"left", "right"}
    assert p.rails["left"].w == 656


def test_landscape_fills_and_offers_no_rails():
    p = fit(1280, 720, 1920, 1080, "contain")
    assert (p.picture.w, p.picture.h) == (1920, 1080)
    assert p.rails == {}


def test_cover_crops_instead_of_padding():
    p = fit(720, 1280, 1920, 1080, "cover")
    assert p.scale_w >= 1920 and p.scale_h >= 1080
    assert (p.picture.w, p.picture.h) == (1920, 1080)
    assert p.rails == {}


@pytest.mark.parametrize(
    "sw,sh",
    [(1001, 667), (999, 999), (1153, 641), (3, 7)],
)
def test_scaled_dimensions_are_always_even(sw, sh):
    """Odd dimensions are not a cosmetic problem.

    Chroma-subsampled delivery formats cannot represent them, and ffmpeg fails
    outright rather than rounding, so this has to hold for every input.
    """
    p = fit(sw, sh, 1920, 1080, "contain")
    assert p.scale_w % 2 == 0
    assert p.scale_h % 2 == 0


def test_zero_source_size_is_rejected():
    with pytest.raises(ValueError):
        fit(0, 100, 1920, 1080)


# --------------------------------------------------------------------------
# probe facts
# --------------------------------------------------------------------------


def test_rotation_swaps_display_size():
    m = MediaFacts(Path("x"), 1.0, width=1280, height=720, rotation=270)
    assert m.display_size == (720, 1280)


def test_sample_aspect_widens_display_size():
    m = MediaFacts(Path("x"), 1.0, width=720, height=576, sar=Fraction(16, 11))
    w, h = m.display_size
    assert (w, h) == (1047, 576)


@pytest.mark.parametrize(
    "r,a,expect_vfr",
    [
        ("30/1", "30/1", False),
        ("25/1", "25/1", False),
        # Two spellings of 29.97 differ by ~1e-6 and are both constant.
        ("30000/1001", "2997/100", False),
        # A real phone clip: 2.4e-4 off, which reads as negligible and is the
        # discrepancy that moves a tracked box off its subject.
        ("30/1", "139950000/4663903", True),
        ("30/1", "33750000/1141667", True),
    ],
)
def test_vfr_detection(r, a, expect_vfr):
    m = MediaFacts(
        Path("x"), 1.0, width=2, height=2,
        r_frame_rate=Fraction(r), avg_frame_rate=Fraction(a),
    )
    assert m.is_vfr is expect_vfr


# --------------------------------------------------------------------------
# graph escaping and expressions
# --------------------------------------------------------------------------


def test_expression_commas_are_escaped_exactly_once():
    """Double escaping is the failure this guards.

    Escaping in the builder *and* at the boundary yields `\\\\,`, which ffmpeg
    reports as "Missing ')' or too many args" -- a message pointing at the
    parenthesis rather than at the real cause.
    """
    out = expr(clamp_expr("40+2*t", "0", "100"))
    assert "\\\\," not in out
    assert out.count("\\,") == 2
    assert out.startswith("'") and out.endswith("'")


def test_literal_escaping_covers_structural_characters():
    for ch in "\\'[],;:=":
        assert esc(f"a{ch}b") == f"a\\{ch}b"


def test_lerp_holds_outside_its_window():
    e = lerp_expr(10.0, 90.0, 1.0, 3.0)
    assert "lt(t,1.0000)" in e and "10.0000" in e
    assert "gt(t,3.0000)" in e and "90.0000" in e


def test_lerp_collapses_to_a_constant_when_static():
    assert lerp_expr(50.0, 50.0, 0.0, 5.0) == "50.0000"


def test_graph_labels_are_deterministic():
    """Labels must not come from object identity.

    Address-derived ids differ between runs and interpreters, which makes the
    emitted graph non-reproducible and a golden-file test impossible.
    """
    def build():
        g = Graph()
        a = g.chain("0:v", "scale=2:2")
        b = g.chain(a, "format=yuv420p")
        return g.render(), b

    first, lbl1 = build()
    second, lbl2 = build()
    assert first == second
    assert lbl1 == lbl2


# --------------------------------------------------------------------------
# audio
# --------------------------------------------------------------------------


def test_db_to_gain_reference_points():
    assert db_to_gain(0.0) == pytest.approx(1.0)
    assert db_to_gain(-6.0) == pytest.approx(0.5012, abs=1e-4)
    assert db_to_gain(-20.0) == pytest.approx(0.1, abs=1e-6)
    # Anything at or below the floor is exact silence, not a tiny number.
    assert db_to_gain(-96.0) == 0.0
    assert db_to_gain(-200.0) == 0.0


def test_constant_gain_is_emitted_before_window_mutes():
    """Filters multiply, so ordering decides whether a mute survives.

    With the constant trim emitted after a window mute, the trim overwrites the
    silence and the mute stops being silent.
    """
    sa = SegmentAudio(
        gain_db=-6.0,
        windows=(AudioWindow(from_s=1.0, to_s=2.0),),
    )
    fs = segment_audio_filters(sa, 5.0)
    gain_i = next(i for i, f in enumerate(fs) if f.startswith("volume=0.501"))
    mute_i = next(i for i, f in enumerate(fs) if "volume=0.0:enable" in f)
    assert gain_i < mute_i


def test_whole_shot_mute_suppresses_everything_else():
    sa = SegmentAudio(mute=True, gain_db=-6.0, windows=(AudioWindow(0.0, 1.0),))
    assert segment_audio_filters(sa, 5.0) == ["volume=0.0"]


def test_fade_emits_a_ramp_before_the_window_not_inside_it():
    sa = SegmentAudio(windows=(AudioWindow(from_s=2.0, to_s=3.0, fade_s=0.5),))
    fs = segment_audio_filters(sa, 6.0)
    assert any("afade=t=out:st=1.5000" in f for f in fs)
    assert any("volume=0.0:enable" in f for f in fs)


# --------------------------------------------------------------------------
# redaction
# --------------------------------------------------------------------------


def test_mosaic_strength_scales_with_the_region():
    assert strength_for(300, 160, 0) == 13
    assert strength_for(40, 20, 0) == 4  # floored: below this, detail survives
    assert strength_for(300, 160, 7) == 7  # explicit wins


def test_redaction_box_reports_motion():
    static = RedactBox(x=1, y=2, w=3, h=4, from_s=0, to_s=1)
    assert not static.moves and (static.end_x, static.end_y) == (1, 2)
    moving = RedactBox(x=1, y=2, w=3, h=4, from_s=0, to_s=1, to_x=9, to_y=9)
    assert moving.moves


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


def _write(tmp_path: Path, doc: dict) -> Path:
    p = tmp_path / "project.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


BASE = {
    "project": {"name": "t", "width": 1920, "height": 1080, "fps": 30},
    "timeline": [
        {
            "id": "a",
            "duration_s": 2.0,
            "video_layers": [{"kind": "color", "value": "#101014"}],
        }
    ],
}


def test_minimal_config_loads(tmp_path):
    cfg = C.load(_write(tmp_path, BASE))
    assert cfg.total_duration_s == 2.0
    assert cfg.render.gop == 30  # derived from fps when unset
    assert [v.name for v in cfg.variants] == ["final"]


def test_unknown_key_is_an_error_not_a_no_op(tmp_path):
    """A typo must fail loudly.

    A key that is read and ignored, or never read at all, is an invitation to
    edit something that silently does nothing.
    """
    doc = json.loads(json.dumps(BASE))
    doc["project"]["widht"] = 1920
    with pytest.raises(ConfigError, match="unknown key"):
        C.load(_write(tmp_path, doc))


def test_redaction_size_change_is_refused(tmp_path):
    """`crop` fixes its dimensions at init, so an animated size does nothing.

    Accepting the shape of that mistake would make it a silent no-op.
    """
    doc = json.loads(json.dumps(BASE))
    doc["timeline"][0]["redact"] = [
        {"box": [10, 10, 100, 50], "to": [20, 20, 200, 100], "from": 0.0, "to_s": 1.0}
    ]
    with pytest.raises(ConfigError, match="cannot change size"):
        C.load(_write(tmp_path, doc))


def test_duration_disagreeing_with_its_clip_is_refused(tmp_path):
    doc = json.loads(json.dumps(BASE))
    doc["timeline"][0]["duration_s"] = 9.0
    doc["timeline"][0]["video_layers"] = [
        {"kind": "clip", "source": "x.mp4", "in": 1.0, "out": 3.0}
    ]
    with pytest.raises(ConfigError, match="duration_s"):
        C.load(_write(tmp_path, doc))


def test_scene_reference_must_exist(tmp_path):
    doc = json.loads(json.dumps(BASE))
    doc["timeline"][0]["video_layers"].append({"kind": "scene", "name": "nope"})
    with pytest.raises(ConfigError, match="not defined"):
        C.load(_write(tmp_path, doc))


def test_duplicate_segment_ids_are_refused(tmp_path):
    doc = json.loads(json.dumps(BASE))
    doc["timeline"].append(json.loads(json.dumps(doc["timeline"][0])))
    with pytest.raises(ConfigError, match="duplicate segment id"):
        C.load(_write(tmp_path, doc))


def test_odd_canvas_is_refused(tmp_path):
    doc = json.loads(json.dumps(BASE))
    doc["project"]["width"] = 1921
    with pytest.raises(ConfigError, match="must be even"):
        C.load(_write(tmp_path, doc))


def test_window_past_the_end_of_its_segment_is_refused(tmp_path):
    doc = json.loads(json.dumps(BASE))
    doc["timeline"][0]["audio"] = {"windows": [{"from": 0.5, "to": 99.0}]}
    with pytest.raises(ConfigError, match="but the segment is"):
        C.load(_write(tmp_path, doc))


def test_features_are_derived_from_what_the_config_uses(tmp_path):
    """The doctor gates on this.

    Gating on everything the engine could emit makes a simple job fail on a
    filter it would never reach.
    """
    doc = json.loads(json.dumps(BASE))
    assert C.load(_write(tmp_path, doc)).features() == []

    doc["timeline"][0]["redact"] = [
        {"box": [1, 1, 10, 10], "from": 0.0, "to_s": 1.0, "mode": "mosaic"}
    ]
    doc["audio"] = {"duck": True}
    feats = C.load(_write(tmp_path, doc)).features()
    assert "redact_mosaic" in feats and "duck" in feats
    assert "transition" not in feats


def test_orphan_scenes_are_reported(tmp_path):
    doc = json.loads(json.dumps(BASE))
    doc["scenes"] = {"used": {"kind": "card"}, "unused": {"kind": "card"}}
    doc["timeline"][0]["video_layers"].append({"kind": "scene", "name": "used"})
    cfg = C.load(_write(tmp_path, doc))
    assert C.orphan_scenes(cfg) == ["unused"]


def test_yaml_without_pyyaml_names_the_fix(tmp_path, monkeypatch):
    """The zero-dependency path has to stay real.

    JSON and TOML need nothing; YAML is the only format with a dependency, and
    its absence must produce an actionable message rather than an ImportError.
    """
    import builtins

    real_import = builtins.__import__

    def no_yaml(name, *a, **kw):
        if name == "yaml":
            raise ModuleNotFoundError("no yaml")
        return real_import(name, *a, **kw)

    p = tmp_path / "project.yaml"
    p.write_text("project: {}\n", encoding="utf-8")
    monkeypatch.setattr(builtins, "__import__", no_yaml)
    with pytest.raises(ConfigError, match="pip install pyyaml"):
        C.load(p)


# --------------------------------------------------------------------------
# timeline arithmetic
# --------------------------------------------------------------------------


def test_transitions_overlap_and_shorten_the_timeline(tmp_path):
    """A transition consumes from both shots, as dragging one clip over another does.

    Segment starts must account for it, or every evidence frame and silence
    assertion after the first transition samples the wrong moment -- and reports
    a correct render as broken.
    """
    from cutlist.assemble import segment_starts, timeline_duration

    doc = json.loads(json.dumps(BASE))
    doc["timeline"] = [
        {"id": "a", "duration_s": 2.0, "video_layers": [{"kind": "color"}]},
        {"id": "b", "duration_s": 3.0, "video_layers": [{"kind": "color"}],
         "transition_in": {"kind": "dissolve", "duration_s": 0.5}},
        {"id": "c", "duration_s": 2.0, "video_layers": [{"kind": "color"}]},
    ]
    cfg = C.load(_write(tmp_path, doc))

    assert cfg.total_duration_s == 7.0
    finished, overlap = timeline_duration(cfg)
    assert overlap == 0.5
    assert finished == 6.5
    assert segment_starts(cfg) == [0.0, 1.5, 4.5]
