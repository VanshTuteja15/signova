# SIGNOVA — Master Build Prompt for Claude Code

You are the lead engineer building the complete software for SIGNOVA, a SAIT "Emerging Trends" course project. Work autonomously until every item in the Definition of Done (section 12) passes. The owner is asleep. Nobody will answer questions. Make sensible decisions, write them down, and keep going.

---

## 1. The project in one paragraph

SIGNOVA is an experimental, AI-powered, 3D-printed robotic hand. A person speaks. The software turns the speech into text, uses AI to turn the English into an ASL-style gloss (sign order) made only of signs the hand can physically make, and sends finger positions over USB serial to an ESP32. The ESP32 drives servos (through a PCA9685 board or Feetech serial-bus servos) that move the fingers. The pipeline is **Speech → AI → Sign representation → Robotic motion**. The project explicitly does **not** claim to be a sign-language translator. It is a research prototype that only does handshapes (no facial grammar, no arm movement, one hand). The hand hardware is being built by a teammate and is **not available tonight**, so everything must work fully in **simulation**, and be ready to plug into real hardware with only configuration and calibration changes.

## 2. Environment and ground rules

- Owner's machine: **Windows 10/11**, project folder is the current working directory (`D:\Signova`). Assume Python 3.11+ and Google Chrome. Node.js may or may not be installed: the final product must **not require Node** to run.
- Existing files in this folder: `signova-lab.html` (a browser prototype; reuse its draft poses and rule-based gloss ideas) and `SIGNOVA — Project Plan.pdf` (the project plan). Read both first. Do not delete or overwrite them.
- Use a Python virtual environment at `.venv` inside the project. Never install Python packages globally. Never change system settings, PATH, registry, drivers or anything outside this folder, except installing tools like PlatformIO into the venv.
- Use git. Run `git init` if needed, commit after every milestone with clear messages. Never push anywhere.
- Never put secrets in the repo. The Claude API key is read from the `ANTHROPIC_API_KEY` env var or a git-ignored `.env`. Provide `.env.example`. It is fine if no key exists tonight: the software must fall back to the rule-based gloss and say so in the UI.
- Do not fake results. If a test fails, fix the code, not the test. If something truly cannot be done tonight (for example it needs the physical hand or a microphone), build it fully, test everything that can be tested with simulators/fakes, and record exactly what remains in `docs/HANDOFF.md`.
- Keep a running log in `PROGRESS.md` (timestamp, what you did, what's next). Re-read it whenever you resume or lose context.
- If you get stuck on one approach for more than ~3 attempts, step back, write down why in `PROGRESS.md`, and choose a simpler approach that still meets the requirement.
- Your context may reset during a long run. `PROGRESS.md` must always say exactly what's done, what's in progress and the next step, so a fresh session can continue from it without guessing.
- Before finishing any step, run the tests that cover it. Never leave the repo in a broken state between commits.

**Priorities if time runs short** (finish each level completely before starting the next):
- **P0 — must work:** library, rule-based gloss + validator, sequencer, sim transport, server, Live tab with 3D hand, firmware that compiles with motion + protocol tests, setup/run scripts, README, MORNING_REPORT.
- **P1 — should work:** emulator + serial transport, Claude gloss (mocked tests), Whisper speech, Pose Studio, Calibration, Evaluation, presentation mode, all docs, `signova doctor`.
- **P2 — nice to have:** Vosk grammar mode, report generator charts, Wokwi project, MediaPipe pose capture, ASL-LEX helper.

## 3. Repository layout (create exactly this, adding files as needed)

```
README.md                 # what it is, 5-minute quick start, screenshots/GIF if possible
Start SIGNOVA.bat         # double-click launcher: activates venv, starts server, opens Chrome
logs/  data/  models/  reports/   # all git-ignored, created at runtime
PROGRESS.md               # your running log
MORNING_REPORT.md         # final summary for the owner (section 13)
pyproject.toml            # package "signova", console script "signova"
requirements.txt          # pinned versions that you verified install on Windows
.env.example
.gitignore
config/
  hand.yaml               # joints, order, transport, speeds, limits
  hand.inmoov.yaml        # example: 5 bends + thumb rotation + wrist (PCA9685)
  hand.amazing.yaml       # example: Feetech serial-bus layout (note: Amazing Hand has no thumb)
signs/
  library.yaml            # the sign library (section 6)
signova/                  # Python package
  config.py  library.py  sequencer.py  performer.py  events.py  evaluation.py  cli.py
  doctor.py  report.py  metrics.py  logging_setup.py
  gloss/        __init__.py rules.py llm.py validate.py
  transport/    base.py sim.py serial_esp32.py emulator.py
  speech/       whisper_stt.py vosk_stt.py
  server/       app.py (FastAPI)
dashboard/                # static web app served by FastAPI, no build step
  index.html  app.js  hand3d.js  styles.css  vendor/three.module.min.js (vendored, offline-safe)
firmware/signova_hand/    # PlatformIO project for ESP32
  platformio.ini
  include/config.h
  src/main.cpp  src/motion.h  src/motion.cpp  src/protocol.h  src/protocol.cpp
  src/driver_pca9685.cpp  src/driver_feetech.cpp  src/drivers.h
  test/test_native/       # native unit tests for motion + protocol (PlatformIO "native" env)
  wokwi/                  # diagram.json + wokwi.toml so the firmware can be tried in the Wokwi simulator
tools/
  capture_pose.py         # MediaPipe webcam → starter pose (optional feature, see 7.9)
tests/                    # pytest
docs/
  SETUP_WINDOWS.md  PROTOCOL.md  HARDWARE_WIRING.md  CALIBRATION.md
  SIGN_LIBRARY.md  EVALUATION.md  ETHICS.md  HANDOFF.md  ARCHITECTURE.md
  HARDWARE_GUIDE.md  TROUBLESHOOTING.md  DEMO_DAY.md
  AI_ASSISTED_DEVELOPMENT.md  REPORT_MATERIAL.md
  screenshots/
scripts/
  setup.ps1               # creates venv, installs deps, runs tests
  run.ps1                 # starts the server and opens the dashboard
  run_sim_demo.ps1        # starts in simulation mode with no hardware
```

## 4. Architecture

```
Browser dashboard (mic, 3D hand, studio, calibration, evaluation)
        │  HTTP + WebSocket (localhost:8000)
FastAPI server ── speech (faster-whisper / Vosk) ── gloss (rules | Claude + validator)
        │
   Sequencer → Performer → Transport  ──►  sim  |  emulator  |  ESP32 over USB serial
                                                         │
                                       ESP32 firmware → PCA9685 or Feetech bus → servos → hand
```

- All language and AI runs on the laptop. The ESP32 only receives poses, smooths motion, applies calibration and safety limits.
- Joint values everywhere in Python and in the protocol are **normalised 0.0–1.0** (0 = open/neutral, 1 = fully closed/rotated). Servo microseconds or positions exist **only** in firmware calibration.
- The joint list comes from `config/hand.yaml`, never hard-coded. Default joints, in order: `thumb, thumb_rot, index, middle, ring, pinky, wrist`. Optional extra joints the code must support if present: `spread_index_middle`, `wrist_flex`.
- A sign whose `requires` lists a joint the configured hand lacks is automatically marked unavailable, excluded from the gloss vocabulary, and shown greyed out in the dashboard.

## 5. Serial protocol (laptop ⇄ ESP32), document in `docs/PROTOCOL.md`

Newline-terminated JSON, 115200 baud, one object per line. Parse with ArduinoJson v7 (`JsonDocument`, `deserializeJson`).

Laptop → ESP32:
- `{"cmd":"hello"}` → `{"ok":"hello","fw":"x.y.z","driver":"pca9685|feetech","joints":["thumb",...]}`
- `{"cmd":"pose","id":"A","j":[...N floats 0..1],"ms":300}` → when motion completes: `{"done":"A","t":<millis>}`
- `{"cmd":"stop"}` → hold current position: `{"ok":"stop"}`
- `{"cmd":"relax"}` → move to rest then disable servo output (no holding torque/heat): `{"ok":"relax"}`
- `{"cmd":"ping"}` → `{"pong":<millis>}` (the laptop sends this about once per second)
- `{"cmd":"cal_get"}` → `{"cal":[{"joint":"thumb","ch":0,"min":600,"max":2300,"inv":false,"rest":0.15},...]}`
- `{"cmd":"cal_set","joint":"index","min":550,"max":2350,"inv":false,"rest":0.1}` → saved to NVS (ESP32 `Preferences`) → `{"ok":"cal_set"}`
- `{"cmd":"raw","joint":"index","us":1500}` → drives one servo directly for calibration (PCA9685: microseconds; Feetech: position counts) → `{"ok":"raw"}`
- `{"cmd":"status"}` → `{"status":{"pos":[...],"target":[...],"relaxed":bool,"moving":bool,"uptime_ms":n,"last_err":"..."}}`
- `{"cmd":"test","joint":"index"}` → slow sweep rest → 1 → 0 → rest on one joint (or all joints in turn if `joint` is omitted): `{"ok":"test"}`
- `{"cmd":"demo"}` → plays a short built-in sequence stored in firmware (open hand, fist, numbers 1–5, ILY) so the hardware teammate can test the hand **with no laptop software**, just USB power or the Arduino Serial Monitor.
- Errors: `{"err":"bad_json"|"unknown_cmd"|"bad_joint"|"bad_length","detail":"..."}`. Never crash on bad input.

**Human-friendly Serial Monitor mode:** lines that don't start with `{` are treated as short text commands for the hardware teammate typing in the Arduino/PlatformIO Serial Monitor: `help`, `status`, `open`, `fist`, `rest`, `relax`, `demo`, `test <joint>`, `raw <joint> <us>`, `cal`, `set <joint> min|max|rest <value>`, `save`. `help` prints a readable command list. Document all of this in `docs/PROTOCOL.md` and `docs/HARDWARE_GUIDE.md`.

Firmware safety: clamp every target to 0..1 and to calibration limits; if no message arrives for 5 s, move to rest and relax (watchdog; the watchdog is disabled while a text-mode command is running); limit each joint's speed (configurable, default full range in 250 ms) even if `ms` is smaller; auto-relax after 20 s holding the same pose to protect servos from overheating; boot into rest then relaxed state, never a sudden jump; a brownout reset (servo power dipping) must be detected at boot and reported in `hello`/`status` as `"reset_reason"` so it's easy to diagnose a weak power supply.

## 6. Sign library (`signs/library.yaml`), document in `docs/SIGN_LIBRARY.md`

Format (validate with a schema; pydantic is fine):

```yaml
version: 1
rest: {thumb: 0.15, thumb_rot: 0.2, index: 0.15, middle: 0.15, ring: 0.18, pinky: 0.2, wrist: 0.5}
signs:
  ILY:
    kind: word            # word | letter | number
    tier: 1
    english: ["i love you", "love you", "ily"]
    requires: [thumb, thumb_rot, index, middle, ring, pinky]
    validated_by_signer: false
    notes: "Thumb, index, pinky extended"
    frames:
      - pose: {thumb: 0, thumb_rot: 0, index: 0, middle: 1, ring: 1, pinky: 0}
        hold_ms: 800
```

Missing joints in a frame default to `rest`. Seed the library with these **draft** poses (values: thumb, thumb_rot, index, middle, ring, pinky, wrist; wrist 0.5 unless shown). Every sign starts `validated_by_signer: false`.

| ID | kind | tier | pose (thumb, thumb_rot, index, middle, ring, pinky, wrist) |
|---|---|---|---|
| 1 | number | 1 | .7 .9 0 1 1 1 |
| 2 | number | 1 | .7 .9 0 0 1 1 |
| 3 | number | 1 | 0 0 0 0 1 1 |
| 4 | number | 1 | .8 .95 0 0 0 0 |
| 5 | number | 1 | 0 0 0 0 0 0 |
| ILY | word | 1 | 0 0 0 1 1 0 |
| A | letter | 1 | .05 .1 1 1 1 1 |
| B | letter | 1 | .8 .95 0 0 0 0 |
| C | letter | 1 | .35 .45 .45 .45 .45 .45 |
| D | letter | 1 | .6 .85 0 .72 .75 .78 |
| E | letter | 1 | .85 1 .8 .8 .8 .8 |
| F | letter | 1 | .55 .75 .72 0 0 0 |
| I | letter | 1 | .7 .95 1 1 1 0 |
| L | letter | 1 | 0 0 0 1 1 1 |
| O | letter | 1 | .5 .7 .62 .62 .62 .62 |
| S | letter | 1 | .75 1 1 1 1 1 |
| W | letter | 1 | .7 .95 0 0 0 1 |
| Y | letter | 1 | 0 0 1 1 1 0 |
| NO | word | 2 | 4 frames alternating (.2 .55 .15 .15 1 1) and (.5 .8 .55 .55 1 1), hold 220 ms |
| J | letter | 3 | (.7 .95 1 1 1 0 wrist .5) → (.7 .95 1 1 1 0 wrist 1), requires wrist |
| U, V | letter | 3 | requires `spread_index_middle` (unavailable on the default hand) |

Note in the docs that 4 and B share a handshape here because there's no finger spread, and that poses must be checked by a fluent ASL signer (reference: lifeprint.com fingerspelling pages by Dr. Bill Vicars).

## 7. Components to build

### 7.1 Gloss (`signova/gloss/`)
- Output type: list of items `{type: sign|fs|drop|skip, id?, word, reason?}` plus a `note` and `engine` ("rules" or "claude").
- **Rules engine:** lowercase and tokenize; match multi-word `english` phrases first (longest match), then single words, then digits/number words; drop ASL-unsigned words (a, an, the, is, am, are, was, were, be, been, to, of, do, does, did); mark pronouns (i, me, my, you, your, he, she, we, they, him, her, them, our) as skip with reason "pointing sign needs arm movement" unless part of a phrase like "I love you"; fingerspell a word only if every letter is an available letter sign and it is ≤ 8 letters; otherwise skip with a reason naming the missing letters.
- **Claude engine:** use the official `anthropic` Python SDK with **structured outputs** (`output_config={"format": {"type": "json_schema", "schema": ...}}` on `client.messages.create`; no beta header needed). Put the available sign IDs in the schema as an `enum`. Model from env `SIGNOVA_MODEL`, default `claude-haiku-4-5`. Timeout 8 s. Ask for ASL-like ordering (time/topic first) and a one-sentence `note`. Enum casing isn't guaranteed, so compare case-insensitively.
- **Validator (always runs on Claude output):** rejects unknown IDs, unavailable signs and unspellable fingerspelling, converting them to `skip` with reason "rejected by validator". Report the rejected count.
- If Claude is unavailable (no key, network error, timeout, refusal), fall back to rules and set `note` to explain. Never crash.
- Record gloss latency in ms.

### 7.2 Sequencer + Performer
- Sequencer turns gloss items into steps `{item_index, sign_id, label, pose_vector, move_ms, hold_ms}` using the hand's joint order. Fingerspelled words become one step per letter (default hold 450 ms, words 800 ms, both configurable). The same letter twice in a row gets a short "bounce": move 15% toward rest for 150 ms, then back (ASL shows doubled letters with a small movement). A global speed factor (0.5–2.0) scales times.
- Performer runs steps on a transport as a cancellable asyncio task, waits for `done` (with timeout = move_ms + 1500 ms), publishes events to the WebSocket, logs timings, and supports stop and relax. Measures end-to-end latency: speech end → first `pose` sent.

### 7.3 Transports (`signova/transport/`)
- Common async interface: `connect, close, hello, send_pose(id, vector, ms) -> awaits done, stop, relax, ping, cal_get, cal_set, raw, status`.
- `sim`: no hardware; waits `ms` and reports done. Default mode.
- `serial_esp32`: pyserial with a background reader thread feeding an asyncio queue; auto-reconnect; heartbeat ping every 1 s; list ports for the UI and **auto-detect the ESP32** by USB VID:PID (CP210x `10C4:EA60`, CH340 `1A86:7523`, ESP32-S2/S3 native USB `303A:*`), falling back to a manual choice; give a clear message when the port is busy (e.g. Serial Monitor still open); verifies the firmware's joint list against `hand.yaml` on connect and reports mismatches clearly.
- Only one performance runs at a time: a new request cancels the current one (with a short move to rest), the UI shows that it was interrupted.
- On server shutdown (Ctrl+C or closing the launcher window), send `relax` to the hand.
- `emulator`: a pure-Python ESP32 emulator that implements the **exact** protocol from section 5 (including errors, watchdog, calibration storage in a JSON file) over an in-memory pipe. Use it to test `serial_esp32` end-to-end without hardware, and expose it as a selectable mode in the dashboard.

### 7.4 Speech (`signova/speech/`)
- `whisper_stt`: faster-whisper (MIT). Default model `base.en`, configurable (`small.en` for accuracy), `device="cpu"`, `compute_type="int8"`, `vad_filter=True`. Loads lazily on first use with a visible "loading model" status. Accepts audio bytes in any format the browser sends (webm/opus); faster-whisper decodes with PyAV.
- `vosk_stt` ("demo-safe mode"): Vosk with a **grammar** restricted to the library's English phrases, number words and spelled letters, plus `"[unk]"`. Grammar needs a dynamic-graph model such as `vosk-model-en-us-0.22-lgraph` (~128 MB). Provide a `signova download-models` CLI command that downloads it into `models/` (git-ignored). Decode browser audio to 16 kHz mono PCM with PyAV first.
- The browser records with `getUserMedia` + `MediaRecorder` (works on `http://localhost`). Hold-to-talk button and spacebar.

### 7.5 Server (`signova/server/app.py`, FastAPI + uvicorn)
REST (JSON) plus one WebSocket `/ws` that streams events (`status, transcript, gloss, step, done, error, telemetry, eval`):
- `GET /api/state` — hand config, joints, library (with availability), transport mode/status, speech engines available, Claude available.
- `POST /api/say {text, engine}` — gloss + perform. `POST /api/transcribe` (multipart audio, `engine=whisper|vosk`, `auto_sign=true|false`).
- `POST /api/sign/{id}` preview one sign. `POST /api/pose {pose, ms}` live pose (Pose Studio). `POST /api/stop`, `POST /api/relax`.
- `PUT /api/library/{id}` create/update a sign from Pose Studio (writes `signs/library.yaml` safely: validate, keep a `.bak`, preserve comments if practical). `DELETE` too.
- `GET/POST /api/transport` (mode sim|emulator|serial, port), `GET /api/ports`.
- `GET /api/calibration`, `POST /api/calibration`, `POST /api/raw`.
- Evaluation: `POST /api/eval/start {count, tiers, randomize}`, `POST /api/eval/next`, `POST /api/eval/answer {guess, confidence}`, `GET /api/eval/summary`, results saved to `data/eval/<timestamp>.csv`.
- Serves `dashboard/` at `/`. Binds to `127.0.0.1` by default.

### 7.6 Dashboard (`dashboard/`, vanilla JS ES modules + vendored three.js, no build step)
Clean, professional and accessible UI, readable on a projector, light and dark theme. Tabs:
1. **Live** — hold-to-talk mic, text box fallback, engine pickers (whisper/vosk, rules/claude), transcript, gloss chips (sign / fingerspelled / dropped / skipped with reason), the current sign in large type, a **3D hand** that animates the same pose vectors with the same timing, joint telemetry bars, serial/event log, latency readout, stop/relax buttons, connection status. A clear banner when running in simulation.
2. **Pose Studio** — one slider per joint driving the 3D hand and (if connected) the real hand live (throttled ~20 Hz); load an existing sign, edit frames, add/remove frames, set holds, mark "validated by signer" with reviewer name/date, save to the library.
3. **Calibration** — per joint: raw slider, set min / max / rest / invert, test sweep, save to ESP32. Big warning text about moving slowly the first time.
4. **Evaluation** — the session flow for a fluent signer: hand performs a random sign, signer types what they saw, the page records the result without revealing the answer until the end, then shows recognition % per sign and overall, and exports the CSV.
5. **Library** — grid of all signs, tier, availability, validation status, preview button.
6. **Presentation mode** (for the class demo, toggled with `P` or a button) — full-screen, very large type: what was said, the gloss, the current sign, the 3D hand, a "simulation"/"live hand" badge; a row of preset demo sentences; nothing else on screen.
Also an "About / Limits" panel with the honest framing from section 10.

Dashboard-wide: **Esc = emergency stop** (stop + relax) on every tab; a mic level meter while recording so you can see the mic works; a "mirror" toggle (viewer's view vs signer's own view of the 3D hand); keyboard accessible with visible focus; clear connection/state banners; auto-reconnecting WebSocket; works with internet off (rule-based gloss, local Whisper model, all JS/CSS vendored locally).

The 3D hand: right hand, palm toward viewer; palm box, 4 fingers × 3 phalanges, a thumb with rotation + bend, wrist rotation. Use the same joint mapping as the firmware (curl 0..1 → joint angles roughly 85°/100°/70° for finger phalanges). Smooth easing identical in spirit to the firmware's minimum-jerk curve.

### 7.7 ESP32 firmware (`firmware/signova_hand/`, PlatformIO, Arduino framework)
- `platformio.ini` with env `esp32dev` (libs: `bblanchon/ArduinoJson@^7`, `adafruit/Adafruit PWM Servo Driver Library`) and env `native` for unit tests (Unity).
- `config.h`: driver selection (`DRIVER_PCA9685` or `DRIVER_FEETECH`), joint names/count, channels/IDs, default calibration, speeds, watchdog time, I2C pins (SDA 21, SCL 22), Feetech UART pins.
- `motion.h/.cpp` (pure C++, no Arduino includes, so it runs in native tests): per-joint start/target/duration with **minimum-jerk** easing `s(u)=10u³−15u⁴+6u⁵`, speed limiting, and a "done" flag when all joints arrive. Update at 100 Hz.
- `protocol.h/.cpp`: command parsing/validation, separate from I/O so it can be unit-tested natively.
- PCA9685 driver: `setOscillatorFrequency(27000000)`, `setPWMFreq(50)`, `writeMicroseconds`, output enable/disable for relax. Feetech SCS driver: implement the SCS packet format (header 0xFF 0xFF, ID, length, instruction WRITE 0x03, goal position address, checksum = ~(sum) & 0xFF; SCS series is big-endian). Mark the Feetech driver "untested on hardware" in docs.
- Calibration persisted with `Preferences` (NVS). Watchdog relax. Status LED blink codes documented.
- Must compile cleanly: `pio run -e esp32dev` and pass `pio test -e native`. If PlatformIO cannot install the ESP32 toolchain on this machine, still make `pio test -e native` (or plain g++ tests) pass, and record the exact blocker in `HANDOFF.md`.

- `wokwi/`: a Wokwi project (ESP32 + PCA9685 via a custom chip or the closest available part + servos) so the teammate can try the firmware in a browser. Document what is and isn't simulated.

### 7.8 CLI, diagnostics, logging and metrics
- `signova serve [--sim | --emulator | --port COM5] [--open]`, `signova say "text" [--engine rules|claude]`, `signova doctor`, `signova download-models [--whisper base.en] [--vosk]`, `signova check-library`, `signova bench`, `signova report`.
- `signova doctor` checks and prints a friendly pass/fail table: Python version, venv, every dependency importable, ports found (and which looks like an ESP32), firmware handshake if connected, Whisper/Vosk models present, `ANTHROPIC_API_KEY` set (never print it), Claude reachable (optional, with a flag), dashboard assets present, free disk space. Each failure gives the exact fix.
- `signova bench`: measures gloss latency (rules and Claude if available), Whisper transcription time on bundled test clips, and simulated end-to-end time; writes `data/metrics/bench_<timestamp>.json`.
- Logging: rotating file logs in `logs/` plus console; every performance appends a JSON line to `data/metrics/sessions.jsonl` (timestamp, input text length, engine, gloss, timings, transport, errors; **no audio, no transcript text unless the testing option is on**).
- `signova report`: turns metrics and evaluation CSVs into `reports/metrics.md` with PNG charts (matplotlib): latency distribution, recognition % per sign, rules vs Claude gloss comparison. This feeds the course report directly.

### 7.9 Optional (only after everything else is done and green)
- `tools/capture_pose.py`: MediaPipe Hand Landmarker (21 landmarks, `hand_landmarker.task` model downloaded on demand) from a webcam or video file → compute per-finger curl and thumb rotation → print/save a starter pose for Pose Studio. The landmark→curl math must be a pure function with unit tests on synthetic landmarks. Only use video of people who consented.
- ASL-LEX helper: a script that filters an ASL-LEX CSV (if the user downloads it) for one-handed signs without path movement, to suggest new library candidates. Do not ship ASL-LEX data in the repo.

## 8. Quality bar
- Type hints throughout. `ruff` clean. `pytest` with good coverage of gloss rules, validator, library validation, sequencer timing (including bounce and speed), performer cancel/stop, every server endpoint (FastAPI `TestClient`), and the serial transport against the emulator (happy path, bad JSON, timeouts, watchdog, reconnect). Mock the Anthropic client in tests; never call the real API in tests.
- At least 30 representative English test sentences in `tests/data/sentences.yaml` with the expected rule-based gloss.
- A headless end-to-end test: start the server in sim mode, POST `/api/say` with "I love you", assert WebSocket events arrive in order and finish with `done`.
- Use Playwright (Python) to load the dashboard, click through every tab, run a sentence in sim mode, and save screenshots to `docs/screenshots/`. Fix any console errors.
- Clear, friendly error messages everywhere (no stack traces in the UI).
- Windows-friendly: paths via `pathlib`, PowerShell scripts, no bash-only steps in docs.

## 9. Docs
Write for a teammate who has never seen the code: `README.md` quick start (setup in 3 commands, run in sim mode, connect the real hand), `docs/SETUP_WINDOWS.md` (Python, venv, Chrome mic permission, flashing the ESP32 with PlatformIO in VS Code, USB driver note for CP210x/CH340 boards), `docs/HARDWARE_WIRING.md` (ESP32 ↔ PCA9685 I2C, separate 5–6 V servo supply sized to stall current, common ground, ~100 µF per servo on the V+ rail per Adafruit's guide, never power servos from the ESP32), `docs/CALIBRATION.md`, `docs/PROTOCOL.md`, `docs/SIGN_LIBRARY.md`, `docs/EVALUATION.md` (how to run a session with a signer and what metrics go in the report: recognition %, median latency, repeatability over 20 runs, gloss accuracy vs rules baseline), `docs/ETHICS.md`, `docs/ARCHITECTURE.md` (with a diagram), `docs/HANDOFF.md`, plus:
- `docs/HARDWARE_GUIDE.md` — for the teammate building the 3D-printed hand: what the software expects (joint list, 0–1 direction convention, which joints unlock which signs), recommended starting design (InMoov-style tendon hand + a thumb-rotation servo + wrist rotation; mention PARLOMA's added finger spread and the Amazing Hand's lack of a thumb), servo torque note (MG90S is 1.8–2.2 kg·cm, possibly weak for tendons; InMoov uses larger servos), how to size the power supply by measuring stall current, **step-by-step how to add or remove a joint** across `hand.yaml`, firmware `config.h`, library `requires` and the 3D hand, and the first-power-on checklist (one servo at a time, `test` command, then calibration).
- `docs/TROUBLESHOOTING.md` — COM port busy or missing (drivers), mic blocked in Chrome, model download fails, ESP32 keeps resetting when servos move (brownout → power supply), servo jitters (ground, capacitor), wrong finger moves (channel map), finger moves backwards (invert flag), Claude errors/offline.
- `docs/DEMO_DAY.md` — checklist for class: charge/power, pre-download models, test mic in the room, offline fallback, typed input fallback, sim-mode backup, recorded backup video, presets to use, what to say about limits.
- `docs/AI_ASSISTED_DEVELOPMENT.md` — "AI-assisted development" is a course theme: honestly record how Claude Code built this (the master prompt, the process, what was verified automatically, what humans must still check, mistakes found and fixed along the way).
- `docs/REPORT_MATERIAL.md` — ready-to-adapt text for the course report: problem, objectives, architecture, how each course theme is covered (Physical AI/robotics, Generative AI/NLP, AI-assisted development, accessibility and inclusive design, responsible AI and ethics, interdisciplinary skills), evaluation method, results placeholders filled from `signova report`, limitations, future work (more joints, two hands, other sign languages and regional variations, Deaf-led design), and references (section 14).

## 10. Responsible AI requirements (build them into the product)
- UI and docs say: "Research prototype. Shows ASL handshapes only. Not a translator or a replacement for interpreters."
- Audio is processed in memory and not saved unless the user turns on a clearly labelled "save recordings for testing" option. Show which speech engine and whether Claude (cloud) is in use.
- The AI can only output signs in the library (enum + validator). Unvalidated signs show a "draft" badge.
- Evaluation data stores no names unless entered on purpose; CSV export is local only.

## 11. Order of work (commit after each; follow the P0/P1/P2 priorities in section 2)
1. Read existing files, set up repo, venv, tooling, `PROGRESS.md`.
2. Library + config + validation + tests.
3. Gloss rules + validator + Claude engine (mocked) + tests.
4. Sequencer + performer + sim transport + tests.
5. Emulator + serial transport + tests.
6. Server + WebSocket + tests.
7. Dashboard (all tabs) + 3D hand + Playwright check.
8. Speech (whisper + vosk) with tests on short generated audio clips (e.g. generate speech with an offline TTS such as `pyttsx3` if available; otherwise test decoding/plumbing with synthetic audio and document it).
9. Firmware (motion, protocol, text commands, drivers, safety) + native tests + ESP32 compile + Wokwi project.
10. CLI: doctor, bench, report, download-models; logging and metrics; `Start SIGNOVA.bat`.
11. Presentation mode, Esc stop, offline check.
12. Docs, scripts, screenshots, final full test run from a clean clone (`git clone . ../signova_check` then run `scripts/setup.ps1` steps) to prove setup works.
13. Optional extras (7.9). 14. `MORNING_REPORT.md`.

## 12. Definition of Done (all must be true; check each one explicitly at the end)
- [ ] `scripts/setup.ps1` works from a clean clone and finishes with all tests passing.
- [ ] `pytest` green; `ruff` clean.
- [ ] `signova serve --sim` starts; dashboard loads at http://127.0.0.1:8000 with no console errors; typing "I love you" makes the 3D hand sign ILY; "code is so cool" fingerspells CODE, SO, COOL with "is" dropped.
- [ ] Emulator mode works end-to-end from the dashboard, including calibration save/load.
- [ ] Claude engine works when a key is present (verified with mocks) and falls back cleanly without one.
- [ ] Whisper transcription endpoint works on a test audio file; Vosk grammar mode works after `signova download-models` (or the exact blocker is documented).
- [ ] Firmware native tests pass; `pio run -e esp32dev` builds (or the blocker is documented with the exact error).
- [ ] Pose Studio can create, edit and save a new sign that then appears in the gloss vocabulary.
- [ ] Evaluation mode runs a 5-sign session and exports a CSV with correct recognition %.
- [ ] All docs in section 9 exist and match the code. Screenshots exist.
- [ ] Double-clicking `Start SIGNOVA.bat` starts the app and opens the dashboard.
- [ ] `signova doctor` runs and every check that can pass on this machine passes.
- [ ] The app works with the internet disconnected (rule-based gloss, local assets; simulate by blocking network in tests).
- [ ] Esc stops and relaxes from every tab; a new sentence interrupts the current one cleanly.
- [ ] Presentation mode works and looks good at 1920×1080 (screenshot saved).
- [ ] Firmware text commands (`help`, `demo`, `test`, `raw`, `set`, `save`) work in the emulator/native tests and are documented.
- [ ] `signova bench` and `signova report` produce files in `data/metrics/` and `reports/`.
- [ ] `MORNING_REPORT.md` written.

## 13. MORNING_REPORT.md (write last, keep it short and honest)
1. What works (with the exact commands to see it).
2. Test results (counts, coverage).
3. What could not be verified without hardware or a microphone, and how to verify it in 10 minutes once available.
4. Decisions you made on your own and why.
5. Known issues and suggested next steps, in priority order.
6. A 60-second demo script for class.
7. "When the hand arrives": the exact first 10 steps for the teammate (flash firmware, Serial Monitor `help`, `test` each joint, calibrate, `demo`, connect the dashboard, run Tier 1 signs, start an evaluation session).

## 14. References (use these in docs; don't invent others — if you add a source, open it first)
- InMoov hand and forearm: https://inmoov.fr/hand-and-forarm/
- Bulgarelli et al. (2016), 3D-printable hand for sign-language reproduction (PARLOMA): https://journals.sagepub.com/doi/10.5772/64113
- Amazing Hand (Pollen Robotics): https://github.com/pollen-robotics/AmazingHand
- Project Aslan (fingerspelling robot arm): https://newatlas.com/aslan-sign-language-robot-arm/50951/
- Adafruit PCA9685 guide: https://learn.adafruit.com/16-channel-pwm-servo-driver
- MG90S specs: https://components101.com/motors/mg90s-metal-gear-servo-motor
- ArduinoJson v7: https://arduinojson.org/v7/tutorial/deserialization/
- faster-whisper: https://github.com/SYSTRAN/faster-whisper
- Vosk models: https://alphacephei.com/vosk/models
- Claude structured outputs: https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- MediaPipe Hand Landmarker: https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker
- ASL-LEX 2.0: https://academic.oup.com/jdsde/article/26/2/263/6142509
- Lifeprint ASL fingerspelling (Dr. Bill Vicars): https://www.lifeprint.com/asl101/fingerspelling/
- Machine translation of sign languages (criticism of sign-language gloves): https://en.wikipedia.org/wiki/Machine_translation_of_sign_languages

Do not stop early. When you think you're finished, re-run the full Definition of Done checklist from a clean clone, fix anything that fails, and only then write the morning report.
