"""Gloss data types shared by the rule engine, the Claude engine and the validator."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ItemType = Literal["sign", "fs", "drop", "skip"]
EngineName = Literal["rules", "claude"]


class GlossItem(BaseModel):
    """One word (or phrase) of the input and what the hand does with it.

    sign  - perform library sign `id`
    fs    - fingerspell `word` letter by letter
    drop  - ASL doesn't sign this word (articles, forms of "be", ...)
    skip  - the hand can't express it; `reason` says why
    """

    type: ItemType
    word: str
    id: str | None = None
    reason: str | None = None

    def short(self) -> str:
        """Compact notation used in tests and logs: ILY, FS:CODE, -is, ?hello."""
        if self.type == "sign":
            return str(self.id)
        if self.type == "fs":
            return "FS:" + self.word.upper()
        if self.type == "drop":
            return "-" + self.word
        return "?" + self.word


class GlossResult(BaseModel):
    text: str
    items: list[GlossItem] = Field(default_factory=list)
    note: str = ""
    engine: EngineName = "rules"
    requested_engine: EngineName = "rules"
    fallback: bool = False
    fallback_reason: str | None = None
    rejected: int = 0
    latency_ms: float = 0.0
    model: str | None = None

    def short(self) -> list[str]:
        return [i.short() for i in self.items]

    @property
    def performable(self) -> list[GlossItem]:
        return [i for i in self.items if i.type in ("sign", "fs")]
