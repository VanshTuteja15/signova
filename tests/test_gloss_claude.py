"""Claude engine tests. The Anthropic client is always mocked; no network calls."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from signova.gloss import GlossService
from signova.gloss.llm import ClaudeGlosser, build_schema, build_system_prompt, model_name
from signova.library import Library

REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(cls: type[anthropic.APIStatusError], code: int) -> anthropic.APIStatusError:
    return cls("boom", response=httpx2.Response(code, request=REQ), body=None)


class FakeMessages:
    def __init__(self, result: Any = None, exc: BaseException | None = None, delay: float = 0) -> None:
        self.result = result
        self.exc = exc
        self.delay = delay
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return self.result


class FakeClient:
    def __init__(self, **kw: Any) -> None:
        self.messages = FakeMessages(**kw)


def reply(payload: Any, stop_reason: str = "end_turn") -> SimpleNamespace:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])


GOOD = {
    "items": [
        {"type": "sign", "id": "ily", "word": "I love you", "reason": ""},
        {"type": "fs", "id": "", "word": "code", "reason": ""},
        {"type": "drop", "id": "", "word": "is", "reason": ""},
        {"type": "sign", "id": "THANKS", "word": "thanks", "reason": ""},
    ],
    "note": "Topic first.",
}


async def test_claude_success_and_validation(library: Library) -> None:
    client = FakeClient(result=reply(GOOD))
    svc = GlossService(library, claude_client=client)
    result = await svc.gloss("I love you, code is thanks", "claude")
    assert result.engine == "claude"
    assert result.fallback is False
    assert result.short() == ["ILY", "FS:CODE", "-is", "?thanks"]
    assert result.rejected == 1
    assert "Topic first." in result.note and "rejected 1 item" in result.note
    assert result.model == "claude-haiku-4-5"

    call = client.messages.calls[0]
    assert call["model"] == "claude-haiku-4-5"
    schema = call["output_config"]["format"]["schema"]
    assert call["output_config"]["format"]["type"] == "json_schema"
    id_enum = schema["properties"]["items"]["items"]["properties"]["id"]["enum"]
    assert "ILY" in id_enum and "" in id_enum
    assert "U" not in id_enum and "V" not in id_enum  # unavailable on this hand
    assert "ILY" in call["system"]
    assert call["messages"][0]["content"] == "Sentence: I love you, code is thanks"


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (anthropic.APITimeoutError(request=REQ), "timed out"),
        (anthropic.APIConnectionError(request=REQ), "network"),
        (status_error(anthropic.AuthenticationError, 401), "rejected"),
        (status_error(anthropic.RateLimitError, 429), "rate limited"),
        (status_error(anthropic.NotFoundError, 404), "not found"),
        (status_error(anthropic.PermissionDeniedError, 403), "can't use"),
        (status_error(anthropic.InternalServerError, 500), "500"),
    ],
)
async def test_api_errors_fall_back_to_rules(library: Library, exc: BaseException, reason: str) -> None:
    svc = GlossService(library, claude_client=FakeClient(exc=exc))
    result = await svc.gloss("code is so cool", "claude")
    assert result.engine == "rules"
    assert result.requested_engine == "claude"
    assert result.fallback is True
    assert reason in (result.fallback_reason or "")
    assert result.short() == ["FS:CODE", "-is", "FS:SO", "FS:COOL"]
    assert "rule-based gloss was used" in result.note


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (reply(GOOD, stop_reason="refusal"), "declined"),
        (reply(GOOD, stop_reason="max_tokens"), "cut off"),
        (reply("not json"), "invalid JSON"),
        (reply({"nope": 1}), "wrong shape"),
        (SimpleNamespace(stop_reason="end_turn", content=[]), "no text"),
    ],
)
async def test_bad_responses_fall_back(library: Library, response: Any, reason: str) -> None:
    svc = GlossService(library, claude_client=FakeClient(result=response))
    result = await svc.gloss("I love you", "claude")
    assert result.fallback is True
    assert reason in (result.fallback_reason or "")
    assert result.short() == ["ILY"]


async def test_no_key_falls_back(library: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    svc = GlossService(library)
    assert svc.claude_status()["available"] is False
    result = await svc.gloss("I love you", "claude")
    assert result.fallback is True
    assert result.fallback_reason == "no ANTHROPIC_API_KEY set"
    assert result.short() == ["ILY"]


async def test_hard_timeout(library: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    import signova.gloss.llm as llm

    monkeypatch.setattr(llm, "TIMEOUT_S", 0.05)
    svc = GlossService(library, claude_client=FakeClient(result=reply(GOOD), delay=2))
    result = await svc.gloss("I love you", "claude")
    assert result.fallback is True
    assert "timed out" in (result.fallback_reason or "")


def test_key_present_means_available(library: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    ok, detail = ClaudeGlosser(library).available()
    assert ok and "claude-haiku-4-5" in detail


def test_model_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNOVA_MODEL", "claude-sonnet-5-5")
    assert model_name() == "claude-sonnet-5-5"
    monkeypatch.setenv("SIGNOVA_MODEL", "  ")
    assert model_name() == "claude-haiku-4-5"


def test_schema_shape() -> None:
    schema = build_schema(["A", "ILY"])
    item = schema["properties"]["items"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"type", "id", "word", "reason"}
    assert item["properties"]["id"]["enum"] == ["A", "ILY", ""]


def test_system_prompt_lists_vocabulary(library: Library) -> None:
    prompt = build_system_prompt(library)
    assert "ILY" in prompt and '"i love you"' in prompt
    assert "A B C D E F I J L O S W Y" in prompt
    assert "pointing sign needs arm movement" in prompt
