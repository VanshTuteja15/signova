"""faster-whisper speech-to-text (MIT licence), CPU int8, loaded lazily on first use.

Accepts audio bytes in any container PyAV can decode (the browser sends webm/opus).
Audio is processed in memory and never written to disk here.
"""

from __future__ import annotations

import io
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "base.en"


def whisper_installed() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


class WhisperSTT:
    def __init__(
        self,
        model_name: str | None = None,
        download_root: Path | None = None,
        on_status: Callable[[str], None] | None = None,
        beam_size: int | None = None,
    ) -> None:
        self.model_name = model_name or os.environ.get("SIGNOVA_WHISPER_MODEL", "").strip() or DEFAULT_MODEL
        self.download_root = download_root
        self.on_status = on_status
        self.beam_size = beam_size or int(os.environ.get("SIGNOVA_WHISPER_BEAM", "1") or 1)
        self._model: Any = None
        self._lock = threading.Lock()
        self.load_ms: float | None = None
        self.error: str | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def status(self) -> dict[str, Any]:
        return {
            "engine": "whisper",
            "installed": whisper_installed(),
            "model": self.model_name,
            "loaded": self.loaded,
            "load_ms": self.load_ms,
            "error": self.error,
        }

    def _say(self, msg: str) -> None:
        if self.on_status is not None:
            try:
                self.on_status(msg)
            except Exception:
                pass

    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            if not whisper_installed():
                self.error = "faster-whisper is not installed (pip install faster-whisper)"
                raise RuntimeError(self.error)
            from faster_whisper import WhisperModel

            self._say(f"Loading speech model {self.model_name} (first time downloads ~150 MB)...")
            t0 = time.perf_counter()
            kwargs: dict[str, Any] = {"device": "cpu", "compute_type": "int8"}
            if self.download_root is not None:
                self.download_root.mkdir(parents=True, exist_ok=True)
                kwargs["download_root"] = str(self.download_root)
            try:
                self._model = WhisperModel(self.model_name, **kwargs)
            except Exception as exc:
                self.error = f"Could not load Whisper model {self.model_name}: {exc}"
                raise RuntimeError(self.error) from exc
            self.load_ms = round((time.perf_counter() - t0) * 1000, 1)
            self.error = None
            self._say(f"Speech model {self.model_name} ready")

    def transcribe(self, audio: bytes) -> str:
        if not audio:
            return ""
        self.load()
        segments, _info = self._model.transcribe(
            io.BytesIO(audio),
            language="en",
            beam_size=self.beam_size,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segments).strip()
