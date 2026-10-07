"""Validator that always runs on Claude's output: the AI can only emit what the hand can do.

Unknown sign IDs, signs this hand can't make (missing joints) and words that can't be
fingerspelled are converted to `skip` items with reason "rejected by validator: ...".
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..library import Library
from .rules import fingerspell_problem
from .types import GlossItem

REJECTED = "rejected by validator"
MAX_ITEMS = 120
MAX_WORD = 60
MAX_REASON = 160


def _text(value: Any, limit: int) -> str:
    return str(value if value is not None else "").strip()[:limit]


def validate_items(raw_items: Iterable[Any], library: Library) -> tuple[list[GlossItem], int]:
    """Return (clean items, number of items the validator rejected)."""
    letters = library.letters
    out: list[GlossItem] = []
    rejected = 0
    for raw in list(raw_items or [])[:MAX_ITEMS]:
        if not isinstance(raw, dict):
            rejected += 1
            out.append(
                GlossItem(type="skip", word=_text(raw, MAX_WORD), reason=f"{REJECTED}: not a gloss item")
            )
            continue
        kind = _text(raw.get("type"), 10).lower()
        raw_id = _text(raw.get("id"), 32)
        word = _text(raw.get("word"), MAX_WORD) or raw_id

        if kind == "sign":
            sid = library.resolve_id(raw_id)
            if sid is None:
                rejected += 1
                out.append(
                    GlossItem(
                        type="skip",
                        word=word,
                        reason=f"{REJECTED}: {raw_id or 'empty id'!r} is not in the library",
                    )
                )
            elif not library.available(sid):
                rejected += 1
                out.append(
                    GlossItem(
                        type="skip",
                        word=word,
                        reason=f"{REJECTED}: {sid} - {library.unavailable_reason(sid)}",
                    )
                )
            else:
                out.append(GlossItem(type="sign", id=sid, word=word or sid))
        elif kind == "fs":
            problem = fingerspell_problem(word, letters)
            if problem is None:
                out.append(GlossItem(type="fs", word=word.replace("'", "").lower()))
            else:
                rejected += 1
                out.append(GlossItem(type="skip", word=word, reason=f"{REJECTED}: {problem}"))
        elif kind == "drop":
            out.append(GlossItem(type="drop", word=word))
        elif kind == "skip":
            out.append(
                GlossItem(
                    type="skip", word=word, reason=_text(raw.get("reason"), MAX_REASON) or "not supported"
                )
            )
        else:
            rejected += 1
            out.append(GlossItem(type="skip", word=word, reason=f"{REJECTED}: unknown item type {kind!r}"))
    return out, rejected


def validator_note(rejected: int) -> str:
    if rejected == 0:
        return "Validator: every item is in the library."
    plural = "s" if rejected != 1 else ""
    return f"Validator rejected {rejected} item{plural} the hand can't perform."
