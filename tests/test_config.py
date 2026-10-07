from __future__ import annotations

from pathlib import Path

import pytest

from signova.config import ROOT, ConfigError, HandConfig, load_hand_config


@pytest.mark.parametrize("name", ["hand.yaml", "hand.inmoov.yaml", "hand.amazing.yaml"])
def test_example_configs_load(name: str) -> None:
    cfg = load_hand_config(ROOT / "config" / name)
    assert cfg.joint_names
    assert cfg.transport.baud == 115200


def test_default_joint_order(hand: HandConfig) -> None:
    assert hand.joint_names == ["thumb", "thumb_rot", "index", "middle", "ring", "pinky", "wrist"]
    assert hand.transport.mode == "sim"
    assert hand.timing.letter_hold_ms == 450
    assert hand.timing.word_hold_ms == 800


def test_amazing_hand_has_no_thumb() -> None:
    cfg = load_hand_config(ROOT / "config" / "hand.amazing.yaml")
    assert not cfg.has_joint("thumb")
    assert cfg.driver == "feetech"


def test_clamp_respects_soft_limits() -> None:
    cfg = HandConfig.model_validate({"joints": [{"name": "index", "min": 0.1, "max": 0.9}]})
    assert cfg.clamp("index", -3) == 0.1
    assert cfg.clamp("index", 0.5) == 0.5
    assert cfg.clamp("index", 7) == 0.9


def test_duplicate_joint_rejected(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("joints:\n  - {name: index}\n  - {name: index}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="duplicate joint"):
        load_hand_config(p)


def test_bad_joint_range_rejected(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("joints:\n  - {name: index, min: 0.8, max: 0.2}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="min"):
        load_hand_config(p)


def test_missing_file_is_friendly(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_hand_config(tmp_path / "nope.yaml")


def test_speed_out_of_range_rejected(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("joints:\n  - {name: index}\ntiming: {speed: 5}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="speed"):
        load_hand_config(p)


def test_labels_default_from_name() -> None:
    cfg = HandConfig.model_validate({"joints": [{"name": "thumb_rot"}]})
    assert cfg.joints[0].label == "Thumb rot"


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNOVA_HAND_CONFIG", "config/hand.amazing.yaml")
    cfg = load_hand_config()
    assert "thumb" not in cfg.joint_names
