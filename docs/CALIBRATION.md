# Calibration

Calibration maps each joint's **normalised value (0 = open, 1 = closed)** to what its servo needs.
It lives on the ESP32 (NVS flash) and is the hardware team's job. Changing it never touches the
sign library.

```
servo command = min + (max − min) × v          (v = 1 − v first if "invert" is on)
```

| Field | Meaning |
|---|---|
| `ch` | PCA9685 channel / Feetech servo ID (fixed in `config.h`) |
| `min` | servo command for value 0.0 (finger fully **open**). PCA9685: microseconds; Feetech: position counts |
| `max` | servo command for value 1.0 (finger fully **closed** / thumb fully across) |
| `inv` | flip the direction (a servo mounted mirror-image) |
| `rest` | normalised resting position for relax and the watchdog (a slightly curled, relaxed hand) |

Defaults (`config.h`): PCA9685 min 600 µs, max 2300 µs; Feetech 200 – 800 counts. Hard limits:
500 – 2500 µs / 0 – 1023 counts.

## Practise first (no hardware)

`signova serve --emulator` (or Calibration tab → Mode *ESP32 emulator* → Connect) runs the exact
same protocol against a simulated ESP32 that stores calibration in `data/emulator_cal.json`.
Everything below works there.

## Step by step

> **Move slowly the first time.** Keep one hand on the servo power switch. Horns or tendons should be
> detached for steps 1–4.

1. Plug in the ESP32. On the dashboard open **Calibration** → Connection → Mode *ESP32 over USB* →
   choose the port (or *auto*) → **Connect**. The header pill turns green and shows the port.
   Then **Load from hand**.
2. Pick one joint, e.g. **Index**. Move its **raw slider** slowly from the middle (≈1450 µs) toward
   one end while watching the servo. Each move is sent as `{"cmd":"raw"}` about 10 times per second.
3. Find the position where the finger is **fully open** without straining: press **Set min**.
   Find **fully closed**: press **Set max**. If "open" ended up at the high end, set them anyway and
   tick **Invert**.
4. Move the slider to a relaxed, slightly curled position and press **Set rest here** (it converts
   the raw value to 0..1 using your min/max/invert).
5. Press **Save to ESP32**. The values are written to NVS and survive power cycles. The row shows
   the stored values, and an *unsaved* marker if you changed something without saving.
6. Press **Test sweep** to run min → max → min in small steps. If the servo buzzes or stalls at an
   end, narrow that end by 20–50 µs and save again.
7. Repeat for every joint, then attach the horns / tendons and repeat steps 3–6 with the real load
   (tendon hands need the final numbers *with* tendons attached).
8. Check with real signs: **Library** → Preview **A**, **B**, **5**, **ILY**. Or open **Pose Studio**,
   tick *Drive the hand live* and move single sliders.

## Tips

* A servo that hums at rest is being pushed against something: move `rest` or the end stop.
* Tendons stretch. Re-check min/max before every demo (the plan's risk table).
* Calibration is per hand. Swapping the ESP32 to another hand means calibrating again.
* `{"cmd":"cal_get"}` / `cal_set` / `raw` are documented in [PROTOCOL.md](PROTOCOL.md) if you want a
  serial terminal instead. Close the dashboard's connection first: only one program can hold a COM port.
* If the laptop says the **joints don't match**, `JOINT_NAMES` in `config.h` and `joints` in
  `config/hand.yaml` differ. Calibration still works in that state, but poses are blocked until
  you fix one of them.
