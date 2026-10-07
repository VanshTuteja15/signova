"""Sequencer: gloss items -> timed list of pose steps in the hand's joint order.

* sign items perform every frame of the library sign (word hold 800 ms by default)
* fingerspelled words become one step per letter (letter hold 450 ms by default)
* the same sign twice in a row gets a "bounce": move 15% toward rest for 150 ms, then back
  (ASL shows doubled letters with a small movement)
* a global speed factor 0.5 .. 2.0 divides every time (2.0 = twice as fast)
* frame-level hold_ms / move_ms in the library override the defaults
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .config import TimingConfig
from .gloss.rules import letters_of
from .gloss.types import GlossItem
from .library import Library

StepKind = Literal["sign", "letter", "bounce"]
MIN_SPEED, MAX_SPEED = 0.5, 2.0


def clamp_speed(speed: float | None, default: float = 1.0) -> float:
    try:
        s = float(speed if speed is not None else default)
    except (TypeError, ValueError):
        s = default
    return max(MIN_SPEED, min(MAX_SPEED, s))


@dataclass
class Step:
    index: int
    item_index: int
    sign_id: str
    label: str
    context: str
    kind: StepKind
    frame: int
    frames: int
    pose_vector: list[float] = field(default_factory=list)
    move_ms: int = 0
    hold_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Unit:
    item_index: int
    sign_id: str
    context: str
    fingerspelled: bool


class Sequencer:
    def __init__(self, library: Library, timing: TimingConfig) -> None:
        self.library = library
        self.timing = timing

    def _units(self, items: list[GlossItem]) -> list[_Unit]:
        units: list[_Unit] = []
        letters = self.library.letters
        for idx, item in enumerate(items):
            if item.type == "sign" and item.id and self.library.available(item.id):
                units.append(_Unit(idx, item.id, item.word.upper(), False))
            elif item.type == "fs":
                ctx = "FS:" + letters_of(item.word)
                units.extend(_Unit(idx, ch, ctx, True) for ch in letters_of(item.word) if ch in letters)
        return units

    def build(self, items: list[GlossItem], speed: float | None = None) -> list[Step]:
        return self._build(self._units(items), speed)

    def for_sign(self, sign_id: str, speed: float | None = None) -> list[Step]:
        """Steps to preview one sign (used by the library preview and evaluation)."""
        if not self.library.available(sign_id):
            return []
        return self._build([_Unit(0, sign_id, sign_id, False)], speed)

    def _build(self, units: list[_Unit], speed: float | None) -> list[Step]:
        t = self.timing
        factor = clamp_speed(speed, t.speed)
        rest = self.library.rest_vector()
        steps: list[Step] = []
        prev_id: str | None = None
        prev_vec: list[float] | None = None

        def scaled(ms: int) -> int:
            return round(ms / factor)

        for unit in units:
            sign = self.library.get(unit.sign_id)
            vectors = self.library.frame_vectors(unit.sign_id)
            bounce = unit.sign_id == prev_id and prev_vec is not None
            if bounce and prev_vec is not None:
                bvec = [
                    round(p + t.bounce_fraction * (r - p), 4) for p, r in zip(prev_vec, rest, strict=True)
                ]
                steps.append(
                    Step(
                        index=len(steps),
                        item_index=unit.item_index,
                        sign_id=unit.sign_id,
                        label=unit.sign_id,
                        context=unit.context,
                        kind="bounce",
                        frame=0,
                        frames=0,
                        pose_vector=bvec,
                        move_ms=scaled(t.bounce_ms),
                        hold_ms=0,
                    )
                )
            for fi, (frame, vec) in enumerate(zip(sign.frames, vectors, strict=True)):
                if fi == 0 and bounce:
                    move = t.bounce_ms
                else:
                    move = frame.move_ms if frame.move_ms is not None else t.move_ms
                if frame.hold_ms is not None:
                    hold = frame.hold_ms
                else:
                    hold = t.letter_hold_ms if unit.fingerspelled else t.word_hold_ms
                steps.append(
                    Step(
                        index=len(steps),
                        item_index=unit.item_index,
                        sign_id=unit.sign_id,
                        label=unit.sign_id,
                        context=unit.context,
                        kind="letter" if unit.fingerspelled else "sign",
                        frame=fi,
                        frames=len(sign.frames),
                        pose_vector=list(vec),
                        move_ms=scaled(move),
                        hold_ms=scaled(hold),
                    )
                )
            prev_id = unit.sign_id
            prev_vec = vectors[-1]
        return steps


def total_duration_ms(steps: list[Step]) -> int:
    return sum(s.move_ms + s.hold_ms for s in steps)
