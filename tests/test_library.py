from __future__ import annotations

from pathlib import Path

import pytest

from signova.config import ROOT, ConfigError, HandConfig, load_hand_config
from signova.library import Library, Sign, normalize_phrase, parse_library, tokenize

TIER1 = ["1", "2", "3", "4", "5", "ILY", "A", "B", "C", "D", "E", "F", "I", "L", "O", "S", "W", "Y"]


def test_seed_library_contents(library: Library) -> None:
    for sid in [*TIER1, "NO", "J", "U", "V"]:
        assert sid in library.signs, sid
    for sid in TIER1:
        assert library.get(sid).tier == 1
    assert library.get("NO").tier == 2
    assert all(not s.validated_by_signer for s in library.signs.values())


def test_seed_poses_match_spec(library: Library) -> None:
    assert library.frame_vectors("1") == [[0.7, 0.9, 0.0, 1.0, 1.0, 1.0, 0.5]]
    assert library.frame_vectors("D") == [[0.6, 0.85, 0.0, 0.72, 0.75, 0.78, 0.5]]
    assert library.frame_vectors("ILY") == [[0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.5]]
    no = library.frame_vectors("NO")
    assert len(no) == 4
    assert no[0] == no[2] == [0.2, 0.55, 0.15, 0.15, 1.0, 1.0, 0.5]
    assert no[1] == no[3] == [0.5, 0.8, 0.55, 0.55, 1.0, 1.0, 0.5]
    assert all(f.hold_ms == 220 for f in library.get("NO").frames)
    j = library.frame_vectors("J")
    assert j[0][-1] == 0.5 and j[1][-1] == 1.0


def test_availability_follows_hand(library: Library) -> None:
    assert library.available("A")
    assert library.available("J")  # default hand has a wrist
    assert not library.available("U")
    assert not library.available("V")
    assert "spread_index_middle" in (library.unavailable_reason("U") or "")
    assert "U" not in library.letters and "V" not in library.letters
    assert {"A", "B", "C", "D", "E", "F", "I", "L", "O", "S", "W", "Y"} <= library.letters


def test_amazing_hand_makes_thumb_signs_unavailable(library_path: Path) -> None:
    amazing = load_hand_config(ROOT / "config" / "hand.amazing.yaml")
    lib = Library.load(amazing, library_path)
    assert not lib.available("ILY")
    assert not lib.letters
    assert lib.phrase_map() == {}


def test_missing_joints_default_to_rest(library: Library) -> None:
    vec = library.vector({"index": 1.0})
    assert vec == [0.15, 0.2, 1.0, 0.15, 0.18, 0.2, 0.5]
    assert library.rest_vector() == [0.15, 0.2, 0.15, 0.15, 0.18, 0.2, 0.5]


def test_vector_clamps(library: Library) -> None:
    # Values outside 0..1 can't get in through the schema, but vector() clamps defensively too.
    assert library.vector({"index": 3.0})[2] == 1.0


def test_phrase_map_and_resolve(library: Library) -> None:
    pm = library.phrase_map()
    assert pm[("i", "love", "you")] == "ILY"
    assert pm[("three",)] == "3"
    assert pm[("3",)] == "3"
    assert library.resolve_id("ily") == "ILY"
    assert library.resolve_id(" no ") == "NO"
    assert library.resolve_id("XYZ") is None
    assert library.resolve_id(None) is None


def test_api_view(library: Library) -> None:
    views = {v["id"]: v for v in library.to_api()}
    assert views["U"]["available"] is False
    assert views["A"]["draft"] is True
    assert set(views["A"]["frames"][0]["pose"]) == set(library.hand.joint_names)


def test_tokenize() -> None:
    assert tokenize("Hello, World! It's 5 o'clock") == ["hello", "world", "it's", "5", "o'clock"]
    assert normalize_phrase("  I   LOVE you!! ") == "i love you"
    assert tokenize("don’t") == ["don't"]


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ({"signs": {"bad id": {"kind": "word", "tier": 1, "frames": [{"pose": {}}]}}}, "sign id"),
        ({"signs": {"AB": {"kind": "letter", "tier": 1, "frames": [{"pose": {}}]}}}, "single letter"),
        ({"signs": {"X": {"kind": "number", "tier": 1, "frames": [{"pose": {}}]}}}, "digits"),
        ({"signs": {"X": {"kind": "word", "tier": 1, "frames": []}}}, "frames"),
        ({"signs": {"X": {"kind": "word", "tier": 9, "frames": [{"pose": {}}]}}}, "tier"),
        ({"signs": {"X": {"kind": "word", "tier": 1, "frames": [{"pose": {"index": 1.5}}]}}}, "index"),
        ({"signs": {"X": {"kind": "word", "tier": 1, "frames": [{"pose": {"Index!": 0.5}}]}}}, "joint name"),
        ({"signs": {"X": {"kind": "word", "tier": 1, "bogus": 1, "frames": [{"pose": {}}]}}}, "bogus"),
        (
            {
                "signs": {
                    "X": {"kind": "word", "tier": 1, "english": ["hi"], "frames": [{"pose": {}}]},
                    "Z": {"kind": "word", "tier": 1, "english": ["HI"], "frames": [{"pose": {}}]},
                }
            },
            "used by both",
        ),
        (
            {
                "signs": {
                    "X": {"kind": "word", "tier": 1, "validated_on": "7/10/26", "frames": [{"pose": {}}]}
                }
            },
            "YYYY",
        ),
    ],
)
def test_schema_rejects_bad_libraries(raw: dict, match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        parse_library(raw)


def test_yaml11_boolean_keys_are_recovered() -> None:
    data = parse_library({"signs": {False: {"kind": "word", "tier": 2, "frames": [{"pose": {}}]}}})
    assert "NO" in data.signs


def test_upsert_saves_keeps_comments_and_backup(
    library: Library, library_path: Path, hand: HandConfig
) -> None:
    sign = Sign(
        kind="word",
        tier=2,
        english=["Rock on"],
        requires=["index", "pinky"],
        notes="test sign",
        frames=[{"pose": {"index": 0.0, "pinky": 0.0, "middle": 1.0, "ring": 1.0}, "hold_ms": 500}],
    )
    library.upsert("rock", sign)
    text = library_path.read_text(encoding="utf-8")
    assert "ROCK:" in text
    assert "ALL POSES ARE DRAFTS" in text  # header comment preserved
    assert '"NO":' in text  # quoting preserved
    assert library_path.with_suffix(".yaml.bak").exists()
    reloaded = Library.load(hand, library_path)
    assert reloaded.get("ROCK").english == ["rock on"]
    assert reloaded.phrase_map()[("rock", "on")] == "ROCK"


def test_upsert_existing_and_delete(library: Library, library_path: Path, hand: HandConfig) -> None:
    a = library.get("A").model_copy(deep=True)
    a.validated_by_signer = True
    a.reviewer = "Reviewer 1"
    a.validated_on = "2026-10-07"
    library.upsert("A", a)
    reloaded = Library.load(hand, library_path)
    assert reloaded.get("A").validated_by_signer
    assert reloaded.get("A").reviewer == "Reviewer 1"

    library.delete("A")
    assert "A" not in Library.load(hand, library_path).signs
    with pytest.raises(KeyError):
        library.delete("A")


def test_new_risky_key_is_quoted(library: Library, library_path: Path, hand: HandConfig) -> None:
    library.upsert("YES", {"kind": "word", "tier": 3, "frames": [{"pose": {"wrist": 0.7}}]})
    text = library_path.read_text(encoding="utf-8")
    assert '"YES":' in text
    library.upsert("6", {"kind": "number", "tier": 2, "frames": [{"pose": {"thumb": 0.5}}]})
    assert '"6":' in library_path.read_text(encoding="utf-8")
    assert {"YES", "6"} <= set(Library.load(hand, library_path).ids())


def test_invalid_upsert_does_not_touch_file(library: Library, library_path: Path) -> None:
    before = library_path.read_text(encoding="utf-8")
    with pytest.raises(ConfigError):
        library.upsert("BAD!", {"kind": "word", "tier": 1, "frames": [{"pose": {}}]})
    with pytest.raises(ConfigError, match="used by both"):
        library.upsert(
            "LOVE2", {"kind": "word", "tier": 1, "english": ["love you"], "frames": [{"pose": {}}]}
        )
    assert library_path.read_text(encoding="utf-8") == before


def test_suggest_requires(library: Library) -> None:
    sign = Sign(kind="word", tier=2, frames=[{"pose": {"index": 1.0, "wrist": 0.5, "thumb": 0.16}}])
    assert library.suggest_requires(sign) == ["index"]


def test_in_memory_library_does_not_save(hand: HandConfig) -> None:
    lib = Library.from_dict({"signs": {"X": {"kind": "word", "tier": 1, "frames": [{"pose": {}}]}}}, hand)
    lib.upsert("Y2", {"kind": "word", "tier": 1, "frames": [{"pose": {}}]})
    assert "Y2" in lib.signs
