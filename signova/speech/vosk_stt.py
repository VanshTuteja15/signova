"""Vosk "demo-safe" speech-to-text with a grammar restricted to what the hand can sign.

The grammar contains the library's English phrases, number words, the spelled letters the
hand can fingerspell and "[unk]". Grammars need a dynamic-graph model such as
vosk-model-en-us-0.22-lgraph (~128 MB); `signova download-models` fetches it into models/.
Browser audio (webm/opus) is decoded to 16 kHz mono PCM with PyAV first.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..library import Library
from .audio import SAMPLE_RATE, decode_to_pcm16k

DEFAULT_MODEL = "vosk-model-en-us-0.22-lgraph"
MODEL_URL = f"https://alphacephei.com/vosk/models/{DEFAULT_MODEL}.zip"
NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def vosk_installed() -> bool:
    try:
        import vosk  # noqa: F401
    except ImportError:
        return False
    return True


def build_grammar(library: Library) -> list[str]:
    phrases: list[str] = []
    for sid in library.available_ids():
        for p in library.get(sid).english:
            if not p.isdigit() and p not in phrases:
                phrases.append(p)
    for w in NUMBER_WORDS:
        if w not in phrases:
            phrases.append(w)
    for letter in sorted(library.letters):
        if letter.lower() not in phrases:
            phrases.append(letter.lower())
    phrases.append("[unk]")
    return phrases


def join_spelled_letters(text: str) -> str:
    """'c o d e is cool' -> 'code is cool'. Only runs of 2+ single letters are joined."""
    tokens = text.split()
    out: list[str] = []
    run: list[str] = []
    for tok in [*tokens, ""]:
        if len(tok) == 1 and tok.isalpha():
            run.append(tok)
            continue
        if len(run) >= 2:
            out.append("".join(run))
        else:
            out.extend(run)
        run = []
        if tok:
            out.append(tok)
    return " ".join(out)


class VoskSTT:
    def __init__(self, library: Library, models_dir: Path, model_name: str = DEFAULT_MODEL) -> None:
        self.library = library
        self.models_dir = models_dir
        self.model_name = model_name
        self._model: Any = None
        self._lock = threading.Lock()
        self.error: str | None = None

    @property
    def model_path(self) -> Path:
        return self.models_dir / self.model_name

    def model_present(self) -> bool:
        return (self.model_path / "am").is_dir() or (self.model_path / "conf").is_dir()

    def status(self) -> dict[str, Any]:
        return {
            "engine": "vosk",
            "installed": vosk_installed(),
            "model": self.model_name,
            "model_present": self.model_present(),
            "loaded": self._model is not None,
            "error": self.error,
        }

    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            if not vosk_installed():
                self.error = "vosk is not installed (pip install vosk)"
                raise RuntimeError(self.error)
            if not self.model_present():
                self.error = f"Vosk model missing: run `signova download-models` to fetch {self.model_name}"
                raise RuntimeError(self.error)
            import vosk

            vosk.SetLogLevel(-1)
            self._model = vosk.Model(str(self.model_path))
            self.error = None

    def transcribe(self, audio: bytes) -> str:
        if not audio:
            return ""
        self.load()
        import vosk

        pcm = decode_to_pcm16k(audio)
        rec = vosk.KaldiRecognizer(self._model, SAMPLE_RATE, json.dumps(build_grammar(self.library)))
        chunk = SAMPLE_RATE * 2  # 1 s of 16-bit audio
        for i in range(0, len(pcm), chunk):
            rec.AcceptWaveform(pcm[i : i + chunk])
        text = json.loads(rec.FinalResult()).get("text", "")
        text = re.sub(r"\[unk\]", " ", text)
        return join_spelled_letters(" ".join(text.split()))


def download_model(
    models_dir: Path,
    url: str = MODEL_URL,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Download and unzip the Vosk model into models_dir. Returns the model folder."""
    models_dir.mkdir(parents=True, exist_ok=True)
    name = url.rsplit("/", 1)[-1].removesuffix(".zip")
    target = models_dir / name
    if (target / "am").is_dir() or (target / "conf").is_dir():
        return target
    zip_path = models_dir / (name + ".zip.part")
    with urllib.request.urlopen(url, timeout=60) as resp, zip_path.open("wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            block = resp.read(1 << 20)
            if not block:
                break
            fh.write(block)
            done += len(block)
            if progress is not None:
                progress(done, total)
    with zipfile.ZipFile(zip_path) as zf:
        root = models_dir.resolve()
        for member in zf.namelist():
            dest = (models_dir / member).resolve()
            if root not in dest.parents and dest != root:
                raise RuntimeError(f"unsafe path in model zip: {member}")
        zf.extractall(models_dir)
    zip_path.unlink(missing_ok=True)
    if not target.is_dir():
        raise RuntimeError(f"model zip did not contain {name}/")
    return target


def remove_model(models_dir: Path, name: str = DEFAULT_MODEL) -> None:
    shutil.rmtree(models_dir / name, ignore_errors=True)
