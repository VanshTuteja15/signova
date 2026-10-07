"""Evaluation sessions with a fluent signer (docs/EVALUATION.md).

The hand performs signs in random order; the signer types what they saw. Answers are not
revealed until the session ends. Results are saved locally as CSV: one row per trial plus
a summary file with recognition % per sign and overall. No names are stored unless the
operator types a participant label on purpose.
"""

from __future__ import annotations

import csv
import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .library import Library, normalize_phrase

TRIAL_FIELDS = [
    "session_id",
    "participant",
    "trial",
    "sign_id",
    "tier",
    "guess",
    "confidence",
    "correct",
    "performed_at",
    "answered_at",
    "response_s",
]
SUMMARY_FIELDS = ["sign_id", "shown", "correct", "recognition_pct"]

_PREFIXES = ("the letter ", "letter ", "the number ", "number ", "the sign for ", "sign for ", "sign ")


class EvalError(Exception):
    """A user-facing problem with the evaluation flow (409/400 in the API)."""


def is_correct(guess: str, sign_id: str, library: Library) -> bool:
    """Case-insensitive match against the sign id or any of its English phrases."""
    g = normalize_phrase(guess or "")
    for p in _PREFIXES:
        if g.startswith(p):
            g = g[len(p) :]
            break
    if not g:
        return False
    candidates = {normalize_phrase(sign_id)}
    if sign_id in library.signs:
        candidates.update(library.get(sign_id).english)
    return g in candidates or re.sub(r"\s+", "", g) == sign_id.lower()


@dataclass
class Trial:
    index: int
    sign_id: str
    tier: int
    performed_at: datetime | None = None
    answered_at: datetime | None = None
    guess: str | None = None
    confidence: int | None = None
    correct: bool | None = None

    @property
    def answered(self) -> bool:
        return self.answered_at is not None


@dataclass
class EvalSession:
    library: Library
    count: int = 5
    tiers: list[int] = field(default_factory=lambda: [1])
    randomize: bool = True
    participant: str = ""
    seed: int | None = None
    session_id: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d-%H%M%S"))
    trials: list[Trial] = field(default_factory=list)
    cursor: int = -1
    saved_paths: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 1 <= self.count <= 200:
            raise EvalError("count must be between 1 and 200")
        pool = [sid for sid in self.library.available_ids() if self.library.get(sid).tier in set(self.tiers)]
        if not pool:
            raise EvalError("No available signs in the chosen tiers.")
        rng = random.Random(self.seed)
        order: list[str] = []
        while len(order) < self.count:
            batch = list(pool)
            if self.randomize:
                rng.shuffle(batch)
            order.extend(batch)
        self.trials = [
            Trial(index=i, sign_id=sid, tier=self.library.get(sid).tier)
            for i, sid in enumerate(order[: self.count])
        ]
        self.participant = (self.participant or "").strip()[:40]

    # ------------------------------------------------------------------ flow
    @property
    def finished(self) -> bool:
        return all(t.answered for t in self.trials)

    @property
    def current(self) -> Trial | None:
        return self.trials[self.cursor] if 0 <= self.cursor < len(self.trials) else None

    def next(self) -> Trial:
        cur = self.current
        if cur is not None and not cur.answered:
            raise EvalError("Record an answer for the current sign first (or use repeat).")
        if self.cursor + 1 >= len(self.trials):
            raise EvalError("The session is finished.")
        self.cursor += 1
        trial = self.trials[self.cursor]
        trial.performed_at = datetime.now()
        return trial

    def repeat(self) -> Trial:
        cur = self.current
        if cur is None or cur.answered:
            raise EvalError("There is no sign waiting for an answer.")
        return cur

    def answer(self, guess: str, confidence: int | None = None) -> dict[str, Any]:
        cur = self.current
        if cur is None or cur.answered:
            raise EvalError("There is no sign waiting for an answer. Press 'Perform next sign' first.")
        if confidence is not None and not 1 <= int(confidence) <= 5:
            raise EvalError("confidence must be 1..5")
        cur.guess = (guess or "").strip()[:80]
        cur.confidence = int(confidence) if confidence is not None else None
        cur.correct = is_correct(cur.guess, cur.sign_id, self.library)
        cur.answered_at = datetime.now()
        return self.progress()

    def progress(self) -> dict[str, Any]:
        answered = sum(t.answered for t in self.trials)
        return {
            "session_id": self.session_id,
            "total": len(self.trials),
            "answered": answered,
            "current": self.cursor if self.current is not None else None,
            "waiting_for_answer": self.current is not None and not self.current.answered,
            "finished": self.finished,
        }

    # ------------------------------------------------------------------ results
    def summary(self, reveal: bool | None = None) -> dict[str, Any]:
        """Per-sign and overall recognition. Answers are only revealed when the session is finished."""
        reveal = self.finished if reveal is None else reveal
        out = self.progress()
        if not reveal:
            out["revealed"] = False
            return out
        per: dict[str, dict[str, Any]] = {}
        for t in self.trials:
            if not t.answered:
                continue
            row = per.setdefault(t.sign_id, {"sign_id": t.sign_id, "shown": 0, "correct": 0})
            row["shown"] += 1
            row["correct"] += int(bool(t.correct))
        for row in per.values():
            row["recognition_pct"] = round(100 * row["correct"] / row["shown"], 1)
        answered = [t for t in self.trials if t.answered]
        correct = sum(bool(t.correct) for t in answered)
        out.update(
            revealed=True,
            overall_pct=round(100 * correct / len(answered), 1) if answered else None,
            correct=correct,
            per_sign=sorted(per.values(), key=lambda r: r["sign_id"]),
            trials=[
                {
                    "trial": t.index + 1,
                    "sign_id": t.sign_id,
                    "guess": t.guess,
                    "confidence": t.confidence,
                    "correct": t.correct,
                }
                for t in answered
            ],
        )
        return out

    def write_csv(self, out_dir: Path) -> dict[str, str]:
        out_dir.mkdir(parents=True, exist_ok=True)
        trials_path = out_dir / f"{self.session_id}.csv"
        with trials_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=TRIAL_FIELDS)
            w.writeheader()
            for t in self.trials:
                if not t.answered:
                    continue
                rt = (
                    round((t.answered_at - t.performed_at).total_seconds(), 2)
                    if t.answered_at and t.performed_at
                    else ""
                )
                w.writerow(
                    {
                        "session_id": self.session_id,
                        "participant": self.participant,
                        "trial": t.index + 1,
                        "sign_id": t.sign_id,
                        "tier": t.tier,
                        "guess": t.guess,
                        "confidence": t.confidence if t.confidence is not None else "",
                        "correct": int(bool(t.correct)),
                        "performed_at": t.performed_at.isoformat(timespec="seconds")
                        if t.performed_at
                        else "",
                        "answered_at": t.answered_at.isoformat(timespec="seconds") if t.answered_at else "",
                        "response_s": rt,
                    }
                )
        summary = self.summary(reveal=True)
        summary_path = out_dir / f"{self.session_id}_summary.csv"
        with summary_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS)
            w.writeheader()
            for row in summary.get("per_sign", []):
                w.writerow({k: row[k] for k in SUMMARY_FIELDS})
            total = sum(r["shown"] for r in summary.get("per_sign", []))
            w.writerow(
                {
                    "sign_id": "OVERALL",
                    "shown": total,
                    "correct": summary.get("correct", 0),
                    "recognition_pct": summary.get("overall_pct")
                    if summary.get("overall_pct") is not None
                    else "",
                }
            )
        self.saved_paths = {"trials": str(trials_path), "summary": str(summary_path)}
        return self.saved_paths
