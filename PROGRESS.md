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
