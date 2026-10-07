"""Speech tests.

Unit tests (grammar, letter joining, decoding browser-style webm/opus) always run.
Real-model tests are marked `slow`: they generate speech offline with pyttsx3 (Windows SAPI),
convert it to webm/opus like Chrome's MediaRecorder would, and transcribe it with the real
faster-whisper and Vosk models. They skip if a model has not been downloaded
(`signova download-models`) or if no offline TTS voice is available.
"""

from __future__ import annotations

import io
import math
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from signova.config import ROOT, HandConfig
from signova.gloss.rules import join_spelled_letters as join_hyphenated
from signova.library import Library, normalize_phrase
from signova.speech import SpeechManager
from signova.speech.audio import decode_to_float32, decode_to_pcm16k
from signova.speech.vosk_stt import VoskSTT, build_grammar, download_model, join_spelled_letters
from signova.speech.whisper_stt import WhisperSTT

MODELS = ROOT / "models"
WHISPER_PRESENT = any((MODELS / "whisper").glob("models--Systran--faster-whisper-base.en*"))
VOSK_PRESENT = (MODELS / "vosk-model-en-us-0.22-lgraph" / "am").is_dir()


# ---------------------------------------------------------------------------- audio helpers
def encode(samples: np.ndarray, rate: int, fmt: str = "webm") -> bytes:
    """Encode float32 mono samples like a browser would (webm/opus) or as wav."""
    import av

    buf = io.BytesIO()
    codec = {"webm": "libopus", "ogg": "libopus", "wav": "pcm_s16le"}[fmt]
    out_rate = 48000 if codec == "libopus" else rate
    with av.open(buf, "w", format=fmt) as out:
        st = out.add_stream(codec, rate=out_rate, layout="mono")
        frame = av.AudioFrame.from_ndarray(
            samples.astype(np.float32).reshape(1, -1), format="flt", layout="mono"
        )
        frame.sample_rate = rate
        rs = av.AudioResampler(format="s16", layout="mono", rate=out_rate)
        for f in [*rs.resample(frame), *rs.resample(None)]:
            for p in st.encode(f):
                out.mux(p)
        for p in st.encode(None):
            out.mux(p)
    return buf.getvalue()


def wav_to_webm(wav: bytes) -> bytes:
    samples = decode_to_float32(wav)
    return encode(samples, 16000, "webm")


def sine(seconds: float = 1.0, rate: int = 44100, hz: float = 440.0) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (0.3 * np.sin(2 * math.pi * hz * t)).astype(np.float32)


@pytest.fixture(scope="session")
def tts(tmp_path_factory: pytest.TempPathFactory) -> dict[str, bytes]:
    """Offline TTS clips (webm/opus) for a few phrases; skip if no TTS voice."""
    try:
        import pyttsx3

        engine = pyttsx3.init()
        engine.setProperty("rate", 140)
    except Exception as exc:  # no SAPI voice / not Windows
        pytest.skip(f"offline TTS unavailable: {exc}")
    d = tmp_path_factory.mktemp("tts")
    phrases = {"ily": "I love you", "cool": "Code is so cool", "three": "three"}
    for key, text in phrases.items():
        engine.save_to_file(text, str(d / f"{key}.wav"))
    engine.runAndWait()
    clips = {}
    for key in phrases:
        wav = (d / f"{key}.wav").read_bytes()
        if len(wav) < 2000:
            pytest.skip("TTS produced no audio")
        clips[key] = wav_to_webm(wav)
    return clips


def norm(text: str) -> str:
    return normalize_phrase(text)


# ---------------------------------------------------------------------------- unit tests
def test_grammar_is_restricted_to_the_library(library: Library) -> None:
    g = build_grammar(library)
    assert "i love you" in g and "love you" in g and "no" in g
    assert {"one", "five", "ten"} <= set(g)
    assert {"a", "c", "y"} <= set(g) and "u" not in g and "v" not in g  # U/V unavailable on this hand
    assert g[-1] == "[unk]"
    assert not any(p.isdigit() for p in g)


def test_join_spelled_letters_vosk() -> None:
    assert join_spelled_letters("c o d e is cool") == "code is cool"
    assert join_spelled_letters("i love you") == "i love you"
    assert join_spelled_letters("a") == "a"
    assert join_spelled_letters("s o w i s e") == "sowise"
    assert join_spelled_letters("") == ""


def test_join_hyphenated_letters_whisper() -> None:
    assert join_hyphenated("C-O-D-E") == "CODE"
    assert join_hyphenated("My name is B.O.B. ok") == "My name is BOB. ok"
    assert join_hyphenated("e-mail me") == "e-mail me"
    assert join_hyphenated("I love you") == "I love you"


@pytest.mark.parametrize("fmt", ["webm", "ogg", "wav"])
def test_decode_browser_formats(fmt: str) -> None:
    data = encode(sine(1.0), 44100, fmt)
    pcm = decode_to_pcm16k(data)
    assert abs(len(pcm) / 2 / 16000 - 1.0) < 0.1  # ~1 s of 16 kHz samples
    floats = decode_to_float32(data)
    assert floats.dtype == np.float32 and 0.1 < float(np.abs(floats).max()) <= 1.0


def test_decode_garbage_raises() -> None:
    with pytest.raises(ValueError):
        decode_to_pcm16k(b"definitely not audio")


def test_whisper_status_and_missing_package(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import signova.speech.whisper_stt as ws

    stt = WhisperSTT("base.en", download_root=tmp_path)
    assert stt.status()["loaded"] is False and stt.status()["model"] == "base.en"
    assert stt.transcribe(b"") == ""
    monkeypatch.setattr(ws, "whisper_installed", lambda: False)
    with pytest.raises(RuntimeError, match="not installed"):
        stt.load()


def test_vosk_missing_model_message(library: Library, tmp_path: Path) -> None:
    stt = VoskSTT(library, tmp_path)
    assert stt.status()["model_present"] is False
    with pytest.raises(RuntimeError, match="download-models"):
        stt.transcribe(b"x")


async def test_speech_manager_rejects_unknown_engine(library: Library, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown speech engine"):
        await SpeechManager(library, tmp_path).transcribe(b"x", "siri")


def test_download_model_from_local_zip(tmp_path: Path) -> None:
    zpath = tmp_path / "vosk-model-test.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("vosk-model-test/am/final.mdl", b"fake")
        zf.writestr("vosk-model-test/conf/model.conf", b"fake")
    seen: list[tuple[int, int]] = []
    out = download_model(tmp_path / "models", url=zpath.as_uri(), progress=lambda d, t: seen.append((d, t)))
    assert (out / "am" / "final.mdl").exists()
    assert seen
    # second call is a no-op
    assert download_model(tmp_path / "models", url=zpath.as_uri()) == out


def test_download_model_rejects_zip_slip(tmp_path: Path) -> None:
    zpath = tmp_path / "evil.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("../escape.txt", b"x")
    with pytest.raises(RuntimeError, match="unsafe path"):
        download_model(tmp_path / "models", url=zpath.as_uri())
    assert not (tmp_path / "escape.txt").exists()


# ---------------------------------------------------------------------------- real models (slow)
@pytest.mark.slow
@pytest.mark.skipif(not WHISPER_PRESENT, reason="Whisper base.en not downloaded (signova download-models)")
def test_whisper_transcribes_tts(tts: dict[str, bytes]) -> None:
    stt = WhisperSTT("base.en", download_root=MODELS / "whisper")
    assert norm(stt.transcribe(tts["ily"])) == "i love you"
    assert norm(stt.transcribe(tts["cool"])) == "code is so cool"
    assert norm(stt.transcribe(tts["three"])) in ("three", "3")
    silence = encode(np.zeros(16000, dtype=np.float32), 16000)
    assert stt.transcribe(silence) == ""


@pytest.mark.slow
@pytest.mark.skipif(not VOSK_PRESENT, reason="Vosk lgraph model not downloaded (signova download-models)")
def test_vosk_grammar_transcribes_tts(tts: dict[str, bytes], library: Library) -> None:
    stt = VoskSTT(library, MODELS)
    assert stt.transcribe(tts["ily"]) == "i love you"
    assert stt.transcribe(tts["three"]) == "three"
    # Free-form speech outside the grammar is rejected rather than guessed: that's the point.
    assert "cool" not in stt.transcribe(tts["cool"])


@pytest.mark.slow
@pytest.mark.skipif(not WHISPER_PRESENT, reason="Whisper base.en not downloaded (signova download-models)")
def test_transcribe_endpoint_with_real_whisper(
    tts: dict[str, bytes], fast_hand: HandConfig, library: Library, tmp_path: Path
) -> None:
    from signova.server import create_app

    app = create_app(
        fast_hand, library, data_dir=tmp_path / "data", models_dir=MODELS, mode="sim", serve_dashboard=False
    )
    with TestClient(app) as client:
        r = client.post(
            "/api/transcribe",
            files={"audio": ("speech.webm", tts["ily"], "audio/webm")},
            data={"engine": "whisper", "auto_sign": "true"},
        )
        assert r.status_code == 200, r.text
        body: dict[str, Any] = r.json()
        assert norm(body["text"]) == "i love you"
        assert [i["id"] for i in body["gloss"]["items"] if i["type"] == "sign"] == ["ILY"]
        r = client.post(
            "/api/transcribe",
            files={"audio": ("speech.webm", tts["three"], "audio/webm")},
            data={"engine": "vosk", "auto_sign": "true"},
        )
        if VOSK_PRESENT:
            assert r.status_code == 200 and r.json()["text"] == "three"
            assert [i["id"] for i in r.json()["gloss"]["items"]] == ["3"]
