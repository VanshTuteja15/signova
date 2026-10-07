# Serial protocol (laptop ⇄ ESP32)

This is the contract between the laptop software and the hand firmware. Either side can change
freely as long as this stays the same. Three implementations follow it and are tested against
each other's rules:

| Implementation | File | Tests |
|---|---|---|
| ESP32 firmware | `firmware/signova_hand/src/protocol.cpp`, `main.cpp` | `pio test -e native` |
| Pure-Python ESP32 emulator | `signova/transport/emulator.py` | `tests/test_emulator.py` |
| Laptop transport | `signova/transport/serial_esp32.py` | `tests/test_serial_transport.py` (against the emulator) |

## Framing

* USB serial, **115200 baud, 8N1**.
* **One JSON object per line**, terminated by `\n` (a `\r` before it is ignored). Blank lines are ignored.
* Lines longer than **512 bytes** are discarded and answered with a `bad_json` error.
* All joint values are **normalised 0.0 – 1.0** (0 = open / neutral, 1 = fully closed / rotated).
  Servo microseconds or positions exist only inside the firmware's calibration.
* The `j` array lists joints in the order the firmware reports in `hello`. It must match
  `config/hand.yaml`.

## Commands (laptop → ESP32)

| Command | Reply |
|---|---|
| `{"cmd":"hello"}` | `{"ok":"hello","fw":"0.1.0","driver":"pca9685","joints":["thumb","thumb_rot","index","middle","ring","pinky","wrist"]}` (+ `"warn"` if the servo board did not answer) |
| `{"cmd":"pose","id":"A.12","j":[0.05,0.1,1,1,1,1,0.5],"ms":300}` | none right away; `{"done":"A.12","t":123456}` when the motion completes |
| `{"cmd":"stop"}` | `{"ok":"stop"}`, and the hand holds its current position |
| `{"cmd":"relax"}` | `{"ok":"relax"}`, then the hand glides to rest (600 ms) and servo output switches off |
| `{"cmd":"ping"}` | `{"pong":123456}` (the ESP32's `millis()`) |
| `{"cmd":"cal_get"}` | `{"cal":[{"joint":"thumb","ch":0,"min":600,"max":2300,"inv":false,"rest":0.15}, ...]}` |
| `{"cmd":"cal_set","joint":"index","min":550,"max":2350,"inv":false,"rest":0.1}` | `{"ok":"cal_set"}`, saved to NVS flash (survives reboot) |
| `{"cmd":"raw","joint":"index","us":1500}` | `{"ok":"raw"}`, and drives that one servo directly (calibration only) |

### pose

* `id` is any string up to 16 characters. The laptop uses `<SIGN>.<counter>`, e.g. `ILY.7`
  (evaluation runs use `EVAL.<n>` so the serial log doesn't give the answer away).
* `j` must contain exactly one number per joint, otherwise `bad_length`.
* `ms` is the requested transition time, default 300, clamped to 0 – 10000.
* Every target is clamped to 0..1 and then mapped inside the joint's calibration
  (`min..max` µs, optionally inverted), so a pose can never drive a servo past its calibrated range.
* **Speed limit:** each joint takes `max(ms, |Δ| × 250 ms)` (`FULL_RANGE_MS` in `config.h`), so no
  joint goes from 0 to 1 faster than 250 ms even if `ms` is smaller.
* Motion is planned at 100 Hz with **minimum-jerk** easing `s(u) = 10u³ − 15u⁴ + 6u⁵`.
* `done` is sent once, when **all** joints have arrived. Its `t` is the ESP32 `millis()` at arrival.
* **Superseding:** a new `pose` replaces the current one immediately, starting from wherever the joints
  are. The older pose's `done` is **never** sent. The laptop resolves its own wait for the older id
  as "superseded".
* `stop` also cancels the pending `done`.

### cal_set

* Fields other than `joint` are optional: only the ones you send change.
* `min` / `max`: PCA9685 microseconds 500 – 2500, or Feetech position counts 0 – 1023. After merging, `min` must be below `max`.
* `inv`: `true` flips the direction (useful when a servo is mounted mirrored).
* `rest`: normalised 0..1 rest position, used by `relax` and the watchdog.
* Stored in NVS (`Preferences` namespace `signova`, one blob per joint with a magic number and
  version). Corrupt or out-of-range blobs fall back to the defaults in `config.h`. The emulator stores
  the same data in `data/emulator_cal.json`.

### raw

* `us` is microseconds (PCA9685) or position counts (Feetech), clamped to the hard limits.
* The joint stays at the raw value until the next `pose` or `relax`. Raw moves bypass motion
  planning, so move the slider slowly (see CALIBRATION.md).

## Errors

Bad input never crashes the firmware; it answers with:

```json
{"err":"bad_json","detail":"could not parse JSON"}
```

| `err` | When |
|---|---|
| `bad_json` | unparseable line, not an object, no `cmd` string, line too long, or a field with the wrong type or out of range (`ms`, `min`, `max`, `inv`, `rest`, `us`) |
| `unknown_cmd` | `cmd` is not one of the commands above |
| `bad_joint` | `cal_set` / `raw` name a joint the firmware doesn't have |
| `bad_length` | `pose` `j` is not an array of exactly N numbers |

**Extension (backwards compatible):** when the firmware knows which command failed, the error also
carries `"cmd"`, and for poses `"id"`, e.g.
`{"err":"bad_length","detail":"j must be an array of 7 numbers","cmd":"pose","id":"A.3"}`.
The laptop uses these to fail the right waiting request at once instead of waiting for a timeout.

## Unsolicited events (ESP32 → laptop)

| Event | Meaning |
|---|---|
| `{"event":"boot","fw":"0.1.0"}` (+ `"detail"` warning) | firmware started (opening the USB port resets most ESP32 boards) |
| `{"event":"watchdog","detail":"no message for 5000 ms, relaxing"}` | no line received for 5 s while the servos were active: the hand glides to rest and switches outputs off |

Lines that don't start with `{` (the ESP32 ROM bootloader prints some after reset) are ignored by the laptop.

## Safety rules in the firmware

1. Clamp every target to 0..1, then to the joint's calibrated range.
2. Per-joint speed limit (full range in no less than 250 ms).
3. **Watchdog:** if no message arrives for 5 s, move to rest and switch outputs off. Any received
   line resets it. The laptop pings once per second, so the watchdog only fires if the laptop
   crashes, the cable is pulled, or the server is stopped.
4. Outputs are off at power-up until the first `pose` or `raw` (no surprise movement on boot).
5. Relax switches PWM fully off (PCA9685 `setPWM(ch, 0, 4096)`) or torque off (Feetech), so idle
   servos don't hold, heat up or buzz.

## Laptop behaviour (`SerialESP32Transport`)

* On connect: open the port, wait 0.3 s for the reset, then send `hello` up to 3 times (1.5 s each).
  No answer gives a clear error: *"No reply from the ESP32 on COM5. Is the SIGNOVA firmware flashed…"*.
* If the firmware's joint list differs from `config/hand.yaml`, poses are refused with an
  explanation. Calibration commands still work, so you can fix the hand.
* Heartbeat: `ping` every second. Three missed pongs, or any serial error, mark the hand as
  disconnected and start an automatic reconnect loop (every second, until it works or you
  switch mode). Pending commands fail at once with "Lost connection to the hand."
* Each `pose` waits for its `done` for `move_ms + 1500 ms` (`done_timeout_extra_ms`) before
  reporting a timeout ("Check servo power").

## Example session

```
→ {"cmd":"hello"}
← {"ok":"hello","fw":"0.1.0","driver":"pca9685","joints":["thumb","thumb_rot","index","middle","ring","pinky","wrist"]}
→ {"cmd":"pose","id":"ILY.1","j":[0,0,0,1,1,0,0.5],"ms":300}
← {"done":"ILY.1","t":5321}
→ {"cmd":"ping"}
← {"pong":6320}
→ {"cmd":"pose","id":"C.2","j":[0.35,0.45,0.45,0.45,0.45,0.45,0.5],"ms":300}
→ {"cmd":"pose","id":"O.3","j":[0.5,0.7,0.62,0.62,0.62,0.62,0.5],"ms":300}     (C.2 superseded)
← {"done":"O.3","t":6911}
→ {"cmd":"pose","id":"X","j":[1,2]}
← {"err":"bad_length","detail":"j must be an array of 7 numbers","cmd":"pose","id":"X"}
→ {"cmd":"relax"}
← {"ok":"relax"}
```

Try it without hardware: `signova serve --emulator`, then watch the **Serial & event log** on the
dashboard's Live tab.
