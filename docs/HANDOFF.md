# Handoff: what is done, what still needs the hand / a mic / a key

Written at the end of the overnight build (2026-10-07). The software is complete and tested in
simulation and against a protocol-exact emulator. These are the things that **could not be
verified tonight**, each with a ~10-minute check.

## Verified tonight (no hardware)

* `pytest`: all Python tests green; `ruff` clean (counts in `MORNING_REPORT.md`).
* Dashboard driven end-to-end in Chrome by Playwright, no console errors; screenshots in `docs/screenshots/`.
* Emulator mode end-to-end from the dashboard, including calibration save/load through the real serial transport.
* Whisper (`base.en`) and Vosk (`vosk-model-en-us-0.22-lgraph`) transcription of offline-TTS speech
  (Windows SAPI voice → webm/opus like Chrome sends), including the `/api/transcribe` endpoint.
* Firmware: 19 native unit tests pass (`pio test -e native`); `pio run -e esp32dev` and
  `pio run -e esp32dev_feetech` build with `-Wall` and no warnings in our code
  (RAM 7.3%, flash 24.2%).

## Not verified, and how to verify

### 1. A real microphone in Chrome (≈ 5 min)
Tonight's speech tests used synthetic TTS audio, not a person on a laptop mic.
1. `scripts\run_sim_demo.ps1`, allow the microphone, hold **Hold to talk**, say "I love you".
2. Expect: transcript "I love you.", gloss chip ILY, 3D hand signs it, latency readout under 3 s.
3. Repeat with Vosk selected ("i love you", "three"). Free sentences come back empty in Vosk mode by design.

### 2. The ESP32 firmware on a real board (≈ 10 min)
Built but never flashed.
1. Flash (`docs/SETUP_WINDOWS.md` §6), open the serial monitor at 115200: expect `{"event":"boot","fw":"0.1.0",...}`.
   With no PCA9685 attached the boot line carries the warning *PCA9685 not found on I2C*; that's expected.
2. Type `{"cmd":"hello"}` → `{"ok":"hello",...,"joints":[...]}`; `{"cmd":"ping"}` → `{"pong":...}`;
   `{"cmd":"x"}` → `{"err":"unknown_cmd",...}`. Close the monitor.
3. `scripts\run.ps1 -Mode serial -Port COMx` → header pill shows the port; Live → "I love you" → a
   `{"done":...}` line arrives in the serial log.
4. Unplug the USB cable for 3 s, plug it back: the dashboard shows the disconnect and reconnects by itself.
5. Stop the server while a pose is held: after 5 s the LED double-blinks (watchdog) and the servos go limp.

### 3. Servos, power and calibration (hardware team, ≈ 10 min per joint)
* Wire per `HARDWARE_WIRING.md` (separate supply sized to stall current, ~100 µF per servo, common ground).
* Confirm **relax really removes holding torque** on your PCA9685 board (`setPWM(ch, 0, 4096)` = full off).
  If your board has OE wired, set `PCA9685_OE_PIN` in `config.h`.
* Calibrate every joint (`CALIBRATION.md`), then preview A, B, 5, ILY from the Library tab.
* Tune `full_range_ms` / `move_ms` in `config/hand.yaml` **and** `FULL_RANGE_MS` in `config.h` for your servos.

### 4. Feetech SCS driver (untested on hardware)
The SCS packet format (write, sync write, torque, big-endian, checksum) is unit-tested, but the driver
has never moved a real servo. Test with **one** servo first (`pio run -e esp32dev_feetech`). The Amazing
Hand's differential fingers (two servos per finger) need a firmware "mixer" that maps one normalised
joint to two servo positions. That is not written (future work).

### 5. The Claude gloss engine with a real key (≈ 5 min)
Tonight it was tested with a mocked client only (schema, enum, validator, every failure mode → fallback).
1. Put `ANTHROPIC_API_KEY` in `.env`, restart, choose *Claude (cloud) + validator*.
2. Try "Tomorrow I will see you, Lisa" and check the note explains the ordering.
3. `signova gloss-eval -v` gives Claude's exact-match %, invented signs (should be 0 reaching the hand) and fallbacks.

### 6. Every sign needs a fluent signer
All 22 seed poses are drafts from the project plan. Run the Evaluation tab with a signer
(`EVALUATION.md`) and mark confirmed signs as validated in Pose Studio.

## Known issues / limitations

* **Vosk + spelled letters:** "C. O. D. E." spoken by the TTS voice came back as "cod" (one letter lost).
  Whisper got "C-O-D-E" (handled: the rules join hyphen/dot-spelled letters into one word).
* **Gloss references:** `tests/data/sentences.yaml` equals the rules output, so the baseline scores 100% on it.
  A signer-written reference set is needed before comparing engines (see EVALUATION.md §4).
* **3D hand** is an approximation (box palm, capsule fingers, thumb as a 3-segment chain). It is meant
  to show timing and roughly what each joint does, not to judge handshape quality.
* **faster-whisper 1.2.1 + PyAV ≥ 15:** faster-whisper's own decoder is incompatible with current PyAV;
  SIGNOVA decodes audio itself (`signova/speech/audio.py`). Keep that if you upgrade either library.
* The emulator rounds per-joint durations slightly differently from the firmware (float vs integer ms).
  Irrelevant for timing, but don't write tests that compare exact `t` values across the two.
* Timing limits live in two places: `limits.watchdog_ms` / `limits.full_range_ms` in `config/hand.yaml`
  (used by the emulator and the laptop) and `WATCHDOG_MS` / `FULL_RANGE_MS` in `config.h` (the real
  firmware). Both are 5000 / 250 today. Change them together.

## Suggested next steps (priority order)

1. Flash + calibrate the real hand; run `signova bench --mode serial --runs 20`.
2. Signer review session; validate or fix poses in Pose Studio.
3. Measure real latency over 20 spoken sentences (`signova metrics`); try `small.en` if accuracy is poor.
4. Write signer-made reference glosses and compare Claude vs rules.
5. Add a finger-spread joint (unlocks U/V and separates 4/B) and wrist flex (G, H, P, Q).
