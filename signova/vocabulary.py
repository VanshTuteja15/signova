"""Extra speech vocabulary (people's names etc.) from config/vocabulary.yaml.

Names are not signs; they get fingerspelled. This list only helps speech recognition spell
them right. The file is re-read whenever it changes, so edits apply without a restart.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import ROOT, load_yaml

log = logging.getLogger("signova")

DEFAULT_PATH = ROOT / "config" / "vocabulary.yaml"


class Vocabulary:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_PATH
        self._mtime: int | None = None
        self._names: list[str] = []
        self._words: list[str] = []

    def _refresh(self) -> None:
        try:
            mtime = self.path.stat().st_mtime_ns
        except OSError:
            self._names, self._words, self._mtime = [], [], None
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            raw = load_yaml(self.path) or {}
            if not isinstance(raw, dict):
                raise ValueError("expected a mapping with 'names' and 'words'")
            self._names = _clean(raw.get("names"))
            self._words = _clean(raw.get("words"))
        except Exception as exc:  # a typo in the file must never break speech
            log.warning("ignoring %s: %s", self.path.name, exc)
            self._names, self._words = [], []

    @property
    def names(self) -> list[str]:
        self._refresh()
        return list(self._names)

    @property
    def words(self) -> list[str]:
        self._refresh()
        return list(self._words)

    def hotwords(self) -> str | None:
        """Comma-separated list for Whisper's `hotwords`, or None when empty."""
        terms = self.names + self.words
        return ", ".join(terms) if terms else None


def _clean(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out
