"""Shared pytest fixtures. Tests never touch the real library file or the network."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from signova.config import ROOT, HandConfig, load_hand_config
from signova.library import Library

# Never let a developer's real key leak into tests: the Claude engine is always mocked.
os.environ.pop("ANTHROPIC_API_KEY", None)


@pytest.fixture
def hand() -> HandConfig:
    return load_hand_config(ROOT / "config" / "hand.yaml")


@pytest.fixture
def library_path(tmp_path: Path) -> Path:
    dst = tmp_path / "library.yaml"
    shutil.copy2(ROOT / "signs" / "library.yaml", dst)
    return dst


@pytest.fixture
def library(hand: HandConfig, library_path: Path) -> Library:
    return Library.load(hand, library_path)


@pytest.fixture
def fast_hand(hand: HandConfig) -> HandConfig:
    """Same joints, but tiny timings so performer / server tests run quickly."""
    h = hand.model_copy(deep=True)
    h.timing.move_ms = 20
    h.timing.word_hold_ms = 10
    h.timing.letter_hold_ms = 5
    h.timing.bounce_ms = 5
    h.timing.done_timeout_extra_ms = 1000
    return h
