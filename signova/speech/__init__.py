"""Speech-to-text engines: faster-whisper (default) and Vosk grammar mode ("demo-safe")."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..library import Library
from .vosk_stt import VoskSTT
from .whisper_stt import WhisperSTT

ENGINES = ("whisper", "vosk")


class SpeechManager:
    """Owns both engines; transcription runs in a worker thread so the server stays responsive."""

    def __init__(
        self,
        library: Library,
        models_dir: Path,
        whisper_model: str | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self.whisper = WhisperSTT(whisper_model, download_root=models_dir / "whisper", on_status=on_status)
        self.vosk = VoskSTT(library, models_dir)

    def status(self) -> dict[str, Any]:
        return {"whisper": self.whisper.status(), "vosk": self.vosk.status()}

    async def transcribe(self, audio: bytes, engine: str = "whisper") -> tuple[str, float]:
        if engine not in ENGINES:
            raise ValueError(f"unknown speech engine {engine!r} (use whisper or vosk)")
        stt = self.whisper if engine == "whisper" else self.vosk
        t0 = time.perf_counter()
        text = await asyncio.to_thread(stt.transcribe, audio)
        return text, round((time.perf_counter() - t0) * 1000, 1)
