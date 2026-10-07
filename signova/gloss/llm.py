"""Claude gloss engine: English -> ASL-style gloss with structured outputs.

The JSON schema puts the available sign IDs in an `enum`, so the model can only name signs
the hand can actually make. The validator still checks every item afterwards (enum casing
isn't guaranteed and fingerspelling needs a letter check). Any failure - no key, network
error, timeout, refusal, bad output - raises ClaudeUnavailable so the caller can fall back
to the rule-based engine.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from ..library import Library
from .rules import DROP_WORDS, MAX_FINGERSPELL, POINTING_REASON, POINTING_WORDS

DEFAULT_MODEL = "claude-haiku-4-5"
TIMEOUT_S = 8.0
MAX_INPUT_CHARS = 500


class ClaudeUnavailable(Exception):
    """Claude could not produce a gloss. `reason` is short and user-facing."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def model_name() -> str:
    return os.environ.get("SIGNOVA_MODEL", "").strip() or DEFAULT_MODEL


def api_key_present() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def build_schema(sign_ids: list[str]) -> dict[str, Any]:
    """JSON schema for structured outputs. Every property is required; non-sign items use id ""."""
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["sign", "fs", "drop", "skip"]},
                        "id": {"type": "string", "enum": [*sign_ids, ""]},
                        "word": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["type", "id", "word", "reason"],
                    "additionalProperties": False,
                },
            },
            "note": {"type": "string"},
        },
        "required": ["items", "note"],
        "additionalProperties": False,
    }


def build_system_prompt(library: Library) -> str:
    lines = []
    for sid in library.available_ids():
        sign = library.get(sid)
        if sign.kind == "letter":
            continue
        meaning = ", ".join(f'"{p}"' for p in sign.english) or sign.notes
        lines.append(f"- {sid} ({sign.kind}): {meaning}")
    letters = " ".join(sorted(library.letters))
    return f"""You turn spoken English into an ASL-style gloss for SIGNOVA, a one-handed robotic hand \
research prototype. The hand only makes static handshapes: no facial grammar, no arm movement, \
no pointing. It is not a translator; your job is to choose what this hand can show.

Signs the hand can make (use these IDs exactly, only when the word truly means that sign):
{chr(10).join(lines)}

Letters the hand can fingerspell: {letters}

Rules:
1. Use ASL-like order where it matters: time and topic first, then comment. Keep it simple.
2. Drop words ASL does not sign, such as {", ".join(sorted(DROP_WORDS))}: type "drop".
3. Pronouns ({", ".join(sorted(POINTING_WORDS))}) need pointing, which this hand can't do: \
type "skip", reason "{POINTING_REASON}", unless they are part of a library phrase like "I love you".
4. Otherwise fingerspell (type "fs", word = the English word) only if every letter is in the \
fingerspelling list above and the word has at most {MAX_FINGERSPELL} letters.
5. If a word can't be signed or spelled, type "skip" with a short reason (e.g. name the missing letters).
6. Never invent sign IDs. For any item that is not type "sign", set id to "".
7. Cover every word of the input exactly once (a phrase sign may cover several words).
8. "note": one short sentence explaining the order or choices you made."""


class ClaudeGlosser:
    def __init__(self, library: Library, client: Any | None = None, model: str | None = None) -> None:
        self.library = library
        self._client = client
        self.model = model or model_name()

    def available(self) -> tuple[bool, str]:
        if self._client is not None:
            return True, f"Claude ({self.model})"
        if not api_key_present():
            return False, "no ANTHROPIC_API_KEY set"
        return True, f"Claude ({self.model})"

    def _get_client(self) -> Any:
        if self._client is None:
            if not api_key_present():
                raise ClaudeUnavailable("no ANTHROPIC_API_KEY set")
            import anthropic

            # No retries: a retried timeout would blow the 8 s budget.
            self._client = anthropic.AsyncAnthropic(timeout=TIMEOUT_S, max_retries=0)
        return self._client

    async def gloss(self, text: str) -> tuple[list[dict[str, Any]], str]:
        """Return (raw items, note) from Claude. Raises ClaudeUnavailable on any failure."""
        import anthropic

        client = self._get_client()
        ids = self.library.available_ids()
        try:
            response = await asyncio.wait_for(
                client.messages.create(
                    model=self.model,
                    max_tokens=2048,
                    system=build_system_prompt(self.library),
                    messages=[{"role": "user", "content": f"Sentence: {text[:MAX_INPUT_CHARS]}"}],
                    output_config={"format": {"type": "json_schema", "schema": build_schema(ids)}},
                ),
                timeout=TIMEOUT_S + 0.5,
            )
        except (TimeoutError, anthropic.APITimeoutError) as exc:
            raise ClaudeUnavailable(f"Claude timed out after {TIMEOUT_S:.0f} s") from exc
        except anthropic.AuthenticationError as exc:
            raise ClaudeUnavailable("the API key was rejected") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ClaudeUnavailable("the API key can't use this model") from exc
        except anthropic.NotFoundError as exc:
            raise ClaudeUnavailable(f"model {self.model} was not found") from exc
        except anthropic.RateLimitError as exc:
            raise ClaudeUnavailable("Claude is rate limited right now") from exc
        except anthropic.APIStatusError as exc:
            raise ClaudeUnavailable(f"Claude API error {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise ClaudeUnavailable("network error reaching Claude") from exc

        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            raise ClaudeUnavailable("Claude declined this request")
        if stop == "max_tokens":
            raise ClaudeUnavailable("Claude's answer was cut off")
        text_block = next(
            (b for b in getattr(response, "content", []) if getattr(b, "type", "") == "text"), None
        )
        if text_block is None:
            raise ClaudeUnavailable("Claude returned no text")
        try:
            data = json.loads(text_block.text)
        except (TypeError, ValueError) as exc:
            raise ClaudeUnavailable("Claude returned invalid JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ClaudeUnavailable("Claude's answer had the wrong shape")
        return data["items"], str(data.get("note") or "")[:300]
