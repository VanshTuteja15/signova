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
- Next: M5 emulator + serial transport.
