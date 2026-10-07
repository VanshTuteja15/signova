# MORNING_REPORT: SIGNOVA overnight build

**Status: every Definition of Done item passes in simulation.** Things that need the physical hand,
a real microphone, an API key or a fluent signer are built and tested as far as possible without
them, and listed in §3 with a 10-minute check each.

## 1. What works (and how to see it)

```powershell
cd D:\Signova
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1          # already done; re-run anytime
powershell -ExecutionPolicy Bypass -File scripts\run_sim_demo.ps1   # opens http://127.0.0.1:8000
```

* **Live tab:** type *I love you* → the 3D hand signs **ILY**. Type *code is so cool* → it fingerspells
  C-O-D-E, S-O, C-O-O-L, drops "is", and does a small bounce on the double O. Hold **Hold to talk** (or
  Space) to speak: Whisper `base.en` and the Vosk grammar models are already downloaded into `models\`.
* **Pose Studio:** load or create a sign, drag sliders, save. It is in the gloss vocabulary at once
  (e.g. save `ROCK` with phrase "rock on", then type "rock on").
* **Calibration:** Mode → *ESP32 emulator* → Connect: per-joint raw slider, min/max/rest/invert, test
  sweep, save/load through the real serial protocol.
* **Evaluation:** blind session for a signer: answers hidden until the end, then recognition % per sign
  and overall, CSV export.
* **Library** (availability + draft badges) and **About & limits** (honest framing).
* No hardware: `scripts\run.ps1 -Mode emulator`. Real hand: `scripts\run.ps1 -Mode serial -Port COM5`.
* CLI: `.venv\Scripts\signova gloss "code is so cool"`, `check`, `ports`, `metrics`, `gloss-eval -v`,
  `bench --mode emulator`.

## 2. Test results

| Check | Result |
|---|---|
| `pytest` (main folder, models present) | **265 passed** (2 min 27 s), including the real-model speech tests and the Playwright dashboard run |
| `pytest` in a **fresh clone** via `scripts\setup.ps1` | **262 passed, 3 skipped** (the 3 real-model speech tests skip until `-Models`), exit 0 |
| Coverage (`signova/`, speech modules excluded from the figure) | **94%** (2611 statements) |
| `ruff check` / `ruff format --check` | clean |
| Firmware `pio test -e native` | **19 / 19** passed |
| `pio run -e esp32dev` / `esp32dev_feetech` | both build, no warnings in our sources; RAM 7.3%, flash 24.2% |
| Real `signova serve --sim` smoke test over HTTP | dashboard 200; "I love you" → ILY; "code is so cool" → FS:CODE, -is, FS:SO, FS:COOL (11 steps) |

### Definition of Done checklist

- [x] `scripts/setup.ps1` works from a clean clone and finishes with all tests passing (see above).
- [x] `pytest` green; `ruff` clean.
- [x] `signova serve --sim` starts; dashboard loads with no console errors (Playwright asserts zero);
      "I love you" → ILY; "code is so cool" fingerspells CODE, SO, COOL with "is" dropped.
- [x] Emulator mode works end-to-end from the dashboard, including calibration save/load
      (`test_dashboard_browser.py`, `test_server.py::test_emulator_mode_end_to_end`).
- [x] Claude engine works with mocks (schema enum, validator, every failure → clean rules fallback); no key needed to run.
- [x] Whisper transcription endpoint works on a test audio file; Vosk grammar mode works after
      `signova download-models` (offline TTS → webm/opus → real models).
- [x] Firmware native tests pass; `pio run -e esp32dev` builds.
- [x] Pose Studio creates, edits and saves a new sign that then appears in the gloss vocabulary.
- [x] Evaluation runs a 5-sign session and exports CSVs with the correct recognition % (80% with 4/5 right).
- [x] All section 9 docs exist and match the code; screenshots in `docs/screenshots/`.
- [x] `MORNING_REPORT.md` written.

## 3. Not verifiable without hardware / mic / key, and the 10-minute check

Full steps in `docs/HANDOFF.md`.

1. **Real microphone.** Speech was tested with synthetic TTS audio. Run the sim demo, allow the mic, say
   "I love you": expect the transcript, ILY, and a latency readout under 3 s.
2. **ESP32 on a real board.** Built, never flashed. Flash (`docs/SETUP_WINDOWS.md` §6), check the boot
   line and `hello` in the serial monitor, close it, run `scripts\run.ps1 -Mode serial`, sign "I love you",
   unplug/replug USB (auto-reconnect), stop the server (watchdog relaxes after 5 s, LED double-blinks).
3. **Servos + calibration.** Wire per `HARDWARE_WIRING.md`, calibrate per `CALIBRATION.md`, preview A/B/5/ILY.
   Confirm relax really removes holding torque on your PCA9685 board.
4. **Feetech driver:** packet format unit-tested, never run on a servo. Test one servo first.
5. **Claude with a real key:** add `ANTHROPIC_API_KEY` to `.env`, choose the Claude engine, then run `signova gloss-eval -v`.
6. **Signer validation:** all 22 poses are drafts. Run the Evaluation tab with a fluent signer.

## 4. Decisions I made and why

* **Python 3.13** (what's installed) with code targeting 3.11+. Every tool cache stays inside the folder
  (`.platformio\`, `models\`). Playwright uses the installed Chrome (`channel="chrome"`).
* **Decode audio ourselves with PyAV:** faster-whisper 1.2.1's decoder passes an argument PyAV 19
  removed. `signova/speech/audio.py` fixes this for both engines.
* **Protocol extension:** firmware errors may include `"cmd"` / `"id"` so the laptop fails the right
  request instantly (backwards compatible). A newer pose **supersedes** an older one; only the latest
  sends `done`. Both are documented in `docs/PROTOCOL.md`.
* **Bad field values** (e.g. `ms: "slow"`) reply `bad_json`, to stay within the 4 specified error codes.
* **Evaluation runs mask the answer** everywhere (step events show "?", pose ids are `EVAL.n`).
* **Run log stores numbers only** (never what was said); recordings are saved only if the labelled box is ticked.
* **Claude client:** `AsyncAnthropic(timeout=8, max_retries=0)` so retries can't exceed the 8 s budget.
* **three.js r170** vendored: the last release whose module build is a single file (works offline, no Node).
* **Whisper "C-O-D-E"** output is joined back into one fingerspelled word by the rules engine.
* **Extra tools** for the report: `signova metrics`, `gloss-eval`, and `bench` (repeatability: 10 signs × N runs).
* **Optional extras done:** `tools/capture_pose.py` (MediaPipe → starter pose; the math in
  `signova/handpose.py` is unit-tested on synthetic landmarks) and `tools/asl_lex_candidates.py`.
  MediaPipe is an optional install and the capture script was not run on a real camera.

## 5. Known issues and next steps (priority order)

1. Flash and calibrate the real hand; run `signova bench --mode serial --runs 20`.
2. Signer session; validate or fix poses in Pose Studio; record their feedback (including negative).
3. Measure real speech latency (20 sentences, `signova metrics`); try `small.en` if accuracy is poor.
4. **Gloss references equal the rules output** (`tests/data/sentences.yaml`), so the baseline scores 100%
   on them by construction. Have a signer write real reference glosses before comparing Claude vs rules.
5. Vosk heard spelled "C. O. D. E." as "cod" (one letter lost); Whisper handles spelling better.
6. The 3D hand is an approximation for timing and joint intent, not for judging handshape quality.
7. Timing limits are duplicated: `config/hand.yaml` (laptop/emulator) and `config.h` (firmware). Change both together.
8. Hardware future work: a finger-spread joint (U/V, 4/B) and wrist flex (G, H, P, Q). The Amazing Hand
   needs a two-servo finger mixer in the firmware.
9. Housekeeping: I left two throwaway clones from the setup checks, `D:\signova_check` (a safety hook
   blocked me from deleting it) and `D:\signova_check2`. Delete both when convenient.

## 6. 60-second demo script for class

1. **(10 s)** "SIGNOVA is a research prototype: speech → AI → handshapes on a robotic hand. It shows ASL
   handshapes only. It is not a translator." Point at the banner.
2. **(15 s)** Hold to talk: "Hello, I love you." The hand signs **ILY**. Point out the gloss chips: *hello*
   is skipped (the hand has no H handshape), while "I love you" is matched as one phrase.
3. **(15 s)** Type "Lisa is so wise": fingerspelling letter by letter, "is" dropped, the latency readout.
4. **(10 s)** Switch the gloss engine to Claude (or show the About tab): the AI can only choose signs from
   the library, and a validator rejects anything else. Show a greyed-out U in the Library.
5. **(10 s)** "What it can't do: facial grammar, two hands, movement. Every sign stays a draft until a Deaf
   signer reviews it, and their feedback goes in our report."
