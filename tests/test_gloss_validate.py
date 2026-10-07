from __future__ import annotations

from signova.gloss.validate import REJECTED, validate_items, validator_note
from signova.library import Library


def test_valid_items_pass(library: Library) -> None:
    items, rejected = validate_items(
        [
            {"type": "sign", "id": "ILY", "word": "I love you", "reason": ""},
            {"type": "fs", "id": "", "word": "code", "reason": ""},
            {"type": "drop", "id": "", "word": "is", "reason": ""},
            {"type": "skip", "id": "", "word": "hello", "reason": "H not supported"},
        ],
        library,
    )
    assert rejected == 0
    assert [i.short() for i in items] == ["ILY", "FS:CODE", "-is", "?hello"]
    assert items[3].reason == "H not supported"


def test_case_insensitive_ids(library: Library) -> None:
    items, rejected = validate_items([{"type": "sign", "id": "ily", "word": "love you"}], library)
    assert rejected == 0
    assert items[0].id == "ILY"


def test_unknown_id_rejected(library: Library) -> None:
    items, rejected = validate_items([{"type": "sign", "id": "HELLO", "word": "hello"}], library)
    assert rejected == 1
    assert items[0].type == "skip"
    assert items[0].reason and items[0].reason.startswith(REJECTED)


def test_unavailable_sign_rejected(library: Library) -> None:
    items, rejected = validate_items([{"type": "sign", "id": "V", "word": "v"}], library)
    assert rejected == 1
    assert "spread_index_middle" in (items[0].reason or "")


def test_unspellable_fingerspelling_rejected(library: Library) -> None:
    items, rejected = validate_items(
        [{"type": "fs", "word": "hello"}, {"type": "fs", "word": "disclosable"}, {"type": "fs", "word": ""}],
        library,
    )
    assert rejected == 3
    assert all(i.type == "skip" and (i.reason or "").startswith(REJECTED) for i in items)


def test_garbage_is_rejected_not_crashing(library: Library) -> None:
    items, rejected = validate_items(["nonsense", {"type": "dance", "word": "x"}, {}], library)
    assert rejected == 3
    assert all(i.type == "skip" for i in items)


def test_none_and_long_input(library: Library) -> None:
    assert validate_items(None, library) == ([], 0)  # type: ignore[arg-type]
    items, _ = validate_items([{"type": "drop", "word": "a"}] * 500, library)
    assert len(items) == 120


def test_skip_without_reason_gets_default(library: Library) -> None:
    items, _ = validate_items([{"type": "skip", "word": "x", "reason": ""}], library)
    assert items[0].reason == "not supported"


def test_validator_note() -> None:
    assert "every item" in validator_note(0)
    assert "1 item " in validator_note(1)
    assert "2 items" in validator_note(2)
