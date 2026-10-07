# SIGNOVA build log

Running log for the overnight build described in `MASTER_PROMPT.md`. Newest entries at the bottom.
Re-read this file when resuming.

## 2026-10-07

- **Start.** Read `MASTER_PROMPT.md`, `signova-lab.html` (draft poses, rule gloss, validator ideas) and the
  project plan PDF (14 pages; extracted with pypdf). Machine: Windows 11, Python 3.13.0, git 2.54,
  Node 24 (not required at runtime), MinGW g++ from Dev-C++ on PATH, no PlatformIO, no ffmpeg.
- Created `.venv`, `git init` (local identity already configured). Installed core deps.
  Heavy deps (faster-whisper, vosk, av, playwright, platformio) installed in background.
- Decisions so far:
  - Python 3.13 is what's installed; code targets 3.11+.
  - Tool caches that would normally land in the user profile are kept inside the project folder:
    PlatformIO core dir `.platformio/`, Whisper models `models/`, Playwright uses installed Chrome
    (`channel="chrome"`), falling back to `.playwright-browsers/`.
- **M1-2 done** (commit 6bc0184): hand configs (default, InMoov, Amazing Hand), `signs/library.yaml` seeded with
  the draft poses, pydantic schema, availability by `requires`, ruamel round-trip saving with `.bak`.
  YAML is read as 1.2 (so `NO`/`Y` stay strings) and risky keys are always quoted on save.
- **M3 done**: rule gloss (phrases -> words -> drop -> pronoun skip -> fingerspell <= 8 letters), validator,
  Claude engine (AsyncAnthropic, `output_config.format` json_schema with sign-ID enum, 8 s timeout, no
  retries so the budget holds) with fallback to rules. 40 reference sentences in `tests/data/sentences.yaml`.
- **M4 done**: event bus (thread-safe publish), transport interface, sim transport, sequencer (bounce,
  speed 0.5-2.0, frame overrides), performer (cancellable task, exactly one `done` per run, latency,
  `data/logs/runs.csv` with numbers only - never the transcript text).
- Note: a local "GateGuard" hook blocks the first Write of every new file; I state the facts and retry.
- **M5 done**: pure-Python ESP32 emulator (min-jerk 100 Hz, speed limit, supersede, stop/relax, watchdog,
  JSON calibration, 4 error codes; errors also carry `cmd`/`id` so the laptop can route them) and the serial
  transport (reader thread -> asyncio, heartbeat, 3 missed pongs or serial error -> auto-reconnect, joint
  mismatch blocks poses but not calibration). Tested end-to-end through `EmulatorSerial` (pyserial-like).
- **M6 done**: FastAPI server (all endpoints in MASTER_PROMPT 7.5 + /api/settings, /api/eval/repeat,
  /api/eval/export), WebSocket, evaluation sessions (answers hidden until the end; eval runs mask sign ids
  in events and pose ids), speech managers (whisper lazy, vosk grammar), CLI. TestClient covers every
  endpoint; headless e2e test runs real uvicorn + websockets.
- **M7 done**: dashboard (vanilla ES modules, vendored three.js r170 = last single-file module build, system
  fonts so it works offline). Playwright (installed Chrome via `channel="chrome"`) drives every tab, signs
  sentences, creates a sign in Pose Studio and uses it, calibrates through the emulator, runs a 5-sign
  evaluation (80% shown), checks for console errors, and writes `docs/screenshots/*.png`.
- **M8 done**: models downloaded with `signova download-models` (Vosk lgraph 130.6 MB, Whisper base.en).
  Found a real incompatibility: faster-whisper 1.2.1 calls `av.open(..., metadata_errors=...)`, removed in
  PyAV 15+. Fixed by decoding with PyAV ourselves (`signova/speech/audio.py`) and passing float32 samples.
  Offline TTS (pyttsx3 / SAPI "David") -> webm/opus -> real models: Whisper "I love you." / "Code is so
  cool!" / "3"; Vosk grammar "i love you" / "three" and free-form speech rejected (by design). Whisper
  writes spelled words as "C-O-D-E": the rule gloss now joins those back into one fingerspelled word.
  Vosk heard spelled "C. O. D. E." as "cod" (imperfect; noted for HANDOFF).
- Next: M9 firmware.
