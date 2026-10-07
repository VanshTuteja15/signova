"""Audio decoding shared by both speech engines (PyAV, in memory).

We decode ourselves instead of using faster-whisper's built-in decoder: faster-whisper 1.2.1
passes `metadata_errors=` to `av.open`, which PyAV 15+ removed. Decoding here works with any
PyAV version and any container the browser sends (webm/opus, ogg, wav, mp4).
"""

from __future__ import annotations

import io

import numpy as np

SAMPLE_RATE = 16000


def decode_to_pcm16k(audio: bytes) -> bytes:
    """Decode to 16 kHz mono signed 16-bit little-endian PCM."""
    import av

    out = bytearray()
    try:
        container = av.open(io.BytesIO(audio))
    except Exception as exc:  # PyAV raises several error types for junk input
        raise ValueError(f"could not open audio: {exc}") from exc
    with container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise ValueError("no audio stream found")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        for frame in container.decode(stream):
            for rf in resampler.resample(frame):
                out.extend(bytes(rf.planes[0])[: rf.samples * 2])
        for rf in resampler.resample(None):
            out.extend(bytes(rf.planes[0])[: rf.samples * 2])
    return bytes(out)


def decode_to_float32(audio: bytes) -> np.ndarray:
    """Decode to a 16 kHz mono float32 array in -1..1 (what faster-whisper expects)."""
    pcm = np.frombuffer(decode_to_pcm16k(audio), dtype="<i2")
    return pcm.astype(np.float32) / 32768.0
