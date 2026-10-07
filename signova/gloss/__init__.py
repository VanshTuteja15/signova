"""Gloss service: English text -> list of sign / fingerspell / drop / skip items.

`GlossService.gloss(text, engine)` never raises for engine problems: if Claude is not
available it falls back to the rule-based engine and explains why in `note`.
"""

from __future__ import annotations

import time
from typing import Any

from ..library import Library
from .llm import ClaudeGlosser, ClaudeUnavailable, api_key_present, model_name
from .rules import rule_gloss
from .types import EngineName, GlossItem, GlossResult
from .validate import validate_items, validator_note

__all__ = [
    "ClaudeGlosser",
    "ClaudeUnavailable",
    "GlossItem",
    "GlossResult",
    "GlossService",
    "api_key_present",
    "model_name",
    "rule_gloss",
    "validate_items",
]

MAX_TEXT = 500


class GlossService:
    def __init__(self, library: Library, claude_client: Any | None = None, model: str | None = None) -> None:
        self.library = library
        self.claude = ClaudeGlosser(library, client=claude_client, model=model)

    def claude_status(self) -> dict[str, Any]:
        ok, detail = self.claude.available()
        return {"available": ok, "detail": detail, "model": self.claude.model}

    async def gloss(self, text: str, engine: EngineName | str = "rules") -> GlossResult:
        t0 = time.perf_counter()
        text = (text or "").strip()[:MAX_TEXT]
        engine = "claude" if str(engine).lower() == "claude" else "rules"
        if not text:
            result = GlossResult(text="", note="Nothing to sign.", engine="rules", requested_engine=engine)
        elif engine == "claude":
            result = await self._claude(text)
        else:
            result = rule_gloss(text, self.library)
        result.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        return result

    async def _claude(self, text: str) -> GlossResult:
        try:
            raw_items, note = await self.claude.gloss(text)
        except ClaudeUnavailable as exc:
            result = rule_gloss(text, self.library)
            result.requested_engine = "claude"
            result.fallback = True
            result.fallback_reason = exc.reason
            result.note = (
                f"Claude unavailable ({exc.reason}), so the rule-based gloss was used. " + result.note
            )
            return result
        items, rejected = validate_items(raw_items, self.library)
        full_note = (note.strip() + " " if note.strip() else "") + validator_note(rejected)
        return GlossResult(
            text=text,
            items=items,
            note=full_note,
            engine="claude",
            requested_engine="claude",
            rejected=rejected,
            model=self.claude.model,
        )
