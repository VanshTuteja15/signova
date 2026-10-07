from __future__ import annotations

from typing import Any

import pytest
from ruamel.yaml import YAML

from signova.config import ROOT, load_hand_config
from signova.gloss import GlossService, rule_gloss
from signova.gloss.rules import fingerspell_problem
from signova.library import Library

SENTENCES: list[dict[str, Any]] = YAML(typ="safe", pure=True).load(
    (ROOT / "tests" / "data" / "sentences.yaml").read_text(encoding="utf-8")
)


def test_at_least_30_sentences() -> None:
    assert len(SENTENCES) >= 30


@pytest.mark.parametrize("case", SENTENCES, ids=[c["text"] for c in SENTENCES])
def test_reference_sentences(case: dict[str, Any], library: Library) -> None:
    result = rule_gloss(case["text"], library)
    assert result.short() == [str(g) for g in case["gloss"]]
    assert result.engine == "rules"


def test_code_is_so_cool_details(library: Library) -> None:
    result = rule_gloss("code is so cool", library)
    assert [(i.type, i.word) for i in result.items] == [
        ("fs", "code"),
        ("drop", "is"),
        ("fs", "so"),
        ("fs", "cool"),
    ]


def test_skip_reasons(library: Library) -> None:
    items = {i.word: i for i in rule_gloss("hat you disclosable 10 b2b", library).items}
    assert "H" in (items["hat"].reason or "")
    assert items["you"].reason == "pointing sign needs arm movement"
    assert "too long" in (items["disclosable"].reason or "")
    assert "number" in (items["10"].reason or "")
    assert "digits" in (items["b2b"].reason or "")


def test_longest_phrase_wins(library: Library) -> None:
    result = rule_gloss("I love you", library)
    assert len(result.items) == 1
    assert result.items[0].word == "i love you"


def test_unavailable_letters_are_not_spelled(library: Library) -> None:
    # U and V need spread_index_middle, which the default hand lacks.
    item = rule_gloss("vow", library).items[0]
    assert item.type == "skip"
    assert "V" in (item.reason or "")


def test_empty_text(library: Library) -> None:
    result = rule_gloss("  ...  ", library)
    assert result.items == []
    assert result.note == "Nothing to sign."


def test_apostrophes_are_stripped_for_spelling(library: Library) -> None:
    item = rule_gloss("Lisa's", library).items[0]
    assert item.type == "fs"
    assert item.word == "lisas"


def test_fingerspell_problem() -> None:
    letters = {"A", "B"}
    assert fingerspell_problem("ab", letters) is None
    assert fingerspell_problem("abc", letters) == "can't fingerspell: no handshape for C"
    assert fingerspell_problem("aaaaaaaaa", letters) == "too long to fingerspell (9 letters, max 8)"
    assert fingerspell_problem("", letters) == "nothing to spell"


def test_no_thumb_hand_skips_everything(library_path) -> None:
    amazing = load_hand_config(ROOT / "config" / "hand.amazing.yaml")
    lib = Library.load(amazing, library_path)
    result = rule_gloss("I love you, code is cool", lib)
    assert all(i.type in ("skip", "drop") for i in result.items)


def test_new_library_phrase_is_used(library: Library) -> None:
    library.upsert(
        "ROCK",
        {"kind": "word", "tier": 2, "english": ["rock on"], "frames": [{"pose": {"index": 0, "pinky": 0}}]},
    )
    assert rule_gloss("rock on", library).short() == ["ROCK"]


async def test_service_rules_records_latency(library: Library) -> None:
    svc = GlossService(library)
    result = await svc.gloss("I love you", "rules")
    assert result.short() == ["ILY"]
    assert result.latency_ms >= 0
    assert result.fallback is False


async def test_service_empty_text(library: Library) -> None:
    result = await GlossService(library).gloss("", "claude")
    assert result.items == []
