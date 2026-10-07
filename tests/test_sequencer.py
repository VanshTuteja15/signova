from __future__ import annotations

import pytest

from signova.config import HandConfig, TimingConfig
from signova.gloss import rule_gloss
from signova.gloss.types import GlossItem
from signova.library import Library
from signova.sequencer import Sequencer, clamp_speed, total_duration_ms


@pytest.fixture
def seq(library: Library, hand: HandConfig) -> Sequencer:
    return Sequencer(library, hand.timing)


def test_word_sign(seq: Sequencer, library: Library) -> None:
    steps = seq.build(rule_gloss("I love you", library).items)
    assert len(steps) == 1
    s = steps[0]
    assert (s.sign_id, s.kind, s.move_ms, s.hold_ms) == ("ILY", "sign", 300, 800)
    assert s.pose_vector == [0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.5]
    assert s.item_index == 0 and s.context == "I LOVE YOU"


def test_fingerspelling_one_step_per_letter(seq: Sequencer, library: Library) -> None:
    steps = seq.build(rule_gloss("code is so cool", library).items)
    letters = [s.sign_id for s in steps if s.kind != "bounce"]
    assert letters == ["C", "O", "D", "E", "S", "O", "C", "O", "O", "L"]
    assert all(s.hold_ms == 450 and s.kind == "letter" for s in steps if s.kind != "bounce")
    assert steps[0].context == "FS:CODE"
    # item indexes point back at the gloss items (is = 1 is dropped)
    assert {s.item_index for s in steps} == {0, 2, 3}
    assert [s.index for s in steps] == list(range(len(steps)))


def test_doubled_letter_bounce(seq: Sequencer, library: Library) -> None:
    steps = seq.build(rule_gloss("cool", library).items)
    kinds = [(s.sign_id, s.kind) for s in steps]
    assert kinds == [("C", "letter"), ("O", "letter"), ("O", "bounce"), ("O", "letter"), ("L", "letter")]
    o = library.frame_vectors("O")[0]
    rest = library.rest_vector()
    bounce = steps[2]
    expected = [round(p + 0.15 * (r - p), 4) for p, r in zip(o, rest, strict=True)]
    assert bounce.pose_vector == expected
    assert (bounce.move_ms, bounce.hold_ms) == (150, 0)
    assert steps[3].move_ms == 150  # back to O in 150 ms
    assert steps[3].pose_vector == o


def test_doubled_sign_across_words_also_bounces(seq: Sequencer, library: Library) -> None:
    steps = seq.build(rule_gloss("so old", library).items)
    assert [s.kind for s in steps] == ["letter", "letter", "bounce", "letter", "letter", "letter"]


@pytest.mark.parametrize(("speed", "factor"), [(2.0, 2.0), (0.5, 0.5), (5, 2.0), (0.1, 0.5), (None, 1.0)])
def test_speed_scales_times(seq: Sequencer, library: Library, speed: float | None, factor: float) -> None:
    steps = seq.build(rule_gloss("I love you", library).items, speed=speed)
    assert steps[0].move_ms == round(300 / factor)
    assert steps[0].hold_ms == round(800 / factor)


def test_config_speed_is_default(library: Library) -> None:
    timing = TimingConfig(speed=2.0)
    steps = Sequencer(library, timing).build(rule_gloss("I love you", library).items)
    assert steps[0].hold_ms == 400


def test_multi_frame_signs(seq: Sequencer, library: Library) -> None:
    steps = seq.build(rule_gloss("no", library).items)
    assert len(steps) == 4
    assert all(s.hold_ms == 220 and s.frames == 4 for s in steps)
    assert [s.frame for s in steps] == [0, 1, 2, 3]
    j = seq.for_sign("J")
    assert [s.pose_vector[-1] for s in j] == [0.5, 1.0]


def test_unavailable_and_non_performable_items(seq: Sequencer) -> None:
    items = [
        GlossItem(type="sign", id="V", word="v"),
        GlossItem(type="sign", id="NOPE_NOT_REAL", word="x"),
        GlossItem(type="drop", word="is"),
        GlossItem(type="skip", word="hello", reason="H"),
        GlossItem(type="fs", word="h"),
    ]
    assert seq.build(items) == []
    assert seq.for_sign("U") == []


def test_preview_sign(seq: Sequencer) -> None:
    steps = seq.for_sign("A")
    assert len(steps) == 1 and steps[0].hold_ms == 800 and steps[0].kind == "sign"


def test_total_duration(seq: Sequencer, library: Library) -> None:
    steps = seq.build(rule_gloss("I love you", library).items)
    assert total_duration_ms(steps) == 1100


def test_clamp_speed_bad_input() -> None:
    assert clamp_speed("fast") == 1.0  # type: ignore[arg-type]
    assert clamp_speed(1.5) == 1.5


def test_frame_move_override(library: Library, hand: HandConfig) -> None:
    library.upsert(
        "WAVE",
        {
            "kind": "word",
            "tier": 3,
            "frames": [{"pose": {"wrist": 0.2}, "move_ms": 500}, {"pose": {"wrist": 0.8}}],
        },
    )
    steps = Sequencer(library, hand.timing).for_sign("WAVE")
    assert [s.move_ms for s in steps] == [500, 300]
