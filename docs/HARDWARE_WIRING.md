# Hardware wiring

> **Safety first.** Servos can pinch fingers, strip gears and pull hundreds of milliamps each, several
> amps at stall. Never power servos from the ESP32 or from USB. Use a separate supply and
> **connect all grounds together**.

## Parts (default hand: InMoov-style, PCA9685)

| Part | Qty | Notes |
|---|---|---|
| ESP32 dev board (ESP32-WROOM-32 "DevKit") | 1 | USB-serial bridge is usually CP210x or CH340: see SETUP_WINDOWS.md for drivers |
| PCA9685 16-channel PWM board (Adafruit 815 or clone) | 1 | I2C address 0x40 (default) |
| Finger servos (thumb, index, middle, ring, pinky) | 5 | size from the hand design: InMoov uses standard servos (HK15298 / MG946R class) |
| Thumb rotation servo | 1 | |
| Wrist rotation servo | 1 | MG996R class |
| 5–6 V servo supply | 1 | sized to the **stall current** (see below) |
| Electrolytic capacitor on the servo rail | 1 | ~100 µF per servo, e.g. 1000 µF / 10 V for 7–10 servos |
| Dupont wires, screw terminals, a power switch you can reach | | |

## ESP32 ↔ PCA9685

| ESP32 pin | PCA9685 pin | Why |
|---|---|---|
| 3V3 | VCC | logic power for the PCA9685 chip (3.3 V logic is fine) |
| GND | GND | **common ground**, required |
| GPIO 21 | SDA | I2C data (`I2C_SDA` in `config.h`) |
| GPIO 22 | SCL | I2C clock (`I2C_SCL`) |
| (optional) any free GPIO | OE | set `PCA9685_OE_PIN` in `config.h` to cut all outputs at once on relax |

```
   ESP32 DevKit                       PCA9685 board                         Servos
 ┌─────────────┐                  ┌──────────────────┐
 │        3V3  ├──────────────────┤ VCC              │        ch0 ── thumb
 │        GND  ├──────────┬───────┤ GND              │        ch1 ── thumb_rot
 │     GPIO21  ├──────────┼───────┤ SDA              │        ch2 ── index
 │     GPIO22  ├──────────┼───────┤ SCL              │        ch3 ── middle
 │    USB ═════╪══ laptop │       │                  │        ch4 ── ring
 └─────────────┘          │       │ V+ ◄─────┐  GND ◄┼──┐     ch5 ── pinky
                          │       └──────────┼───────┘  │     ch6 ── wrist
                          │                  │          │
                          │   ┌──────────────┴──────────┴─┐
                          └───┤ GND   5–6 V SERVO SUPPLY   │  + ~100 µF per servo across V+ / GND
                              └────────────────────────────┘    (mind the polarity!)
```

### Servo power

* Feed the servo supply into the PCA9685's **V+ screw terminal**, not into the ESP32.
* **Never** power servos from the ESP32's 3V3 or 5V pin or from the laptop's USB: brown-outs reset
  the ESP32 mid-demo, and you can damage the board or the laptop port.
* **Size the supply to the stall current.** Look up each servo's stall current at your voltage
  (an MG996R-class servo is around 2.5 A at 6 V), then add them up for the servos that can stall
  together. A hand clenching a fist stalls five finger servos at once. A supply that sags makes servos
  jitter and resets the board. Measure the real stall current with a bench supply before buying.
* Put an electrolytic capacitor across V+ and GND close to the servos: about **100 µF per servo**
  (Adafruit's PCA9685 guide recommends this rule of thumb). Many PCA9685 boards have a pad for it.
* **Common ground:** the supply's GND, the PCA9685 GND and the ESP32 GND must be connected.
  Without it the PWM signal has no reference and servos twitch randomly.

### Channel map

The channel for each joint is `JOINT_CHANNELS` in `firmware/signova_hand/include/config.h`
(default `thumb 0, thumb_rot 1, index 2, middle 3, ring 4, pinky 5, wrist 6`). The joint **order**
must match `config/hand.yaml`. The laptop checks this when it connects.

## Feetech serial-bus servos (Amazing Hand layout): UNTESTED ON HARDWARE

Build with `pio run -e esp32dev_feetech`. The SCS bus is a **half-duplex single-wire UART** at
1 Mbps, so the ESP32 needs a half-duplex adapter between its UART and the bus (Feetech's URT-1 /
FE-URT-1 board, or a 74HC126 tri-state buffer circuit):

| ESP32 | Adapter |
|---|---|
| GPIO 17 (TX2) | TX / D-in |
| GPIO 16 (RX2) | RX / D-out |
| GND | GND (common with the servo supply) |

Servo IDs come from `JOINT_CHANNELS` (default 1–7). Positions are sent once per 10 ms as one
`SYNC_WRITE` broadcast (no replies, so no bus collisions), and relax turns torque off (register 40).
Use the supply voltage your servos specify (SCS0009: see its datasheet). The packet format is
unit-tested, but the driver has **never run on real servos**: test one servo first.

## Status LED (GPIO 2)

| Pattern | Meaning |
|---|---|
| slow blink (1 Hz) | powered, waiting for the laptop |
| solid | laptop connected (message within the last 2 s) |
| fast flicker | moving |
| double blink | the watchdog relaxed the hand (no message for 5 s) |
| short bursts every other second | servo driver not found (PCA9685 didn't answer on I2C: check SDA/SCL, VCC, GND) |

## Before the first power-on

1. Servo horns **off** (or tendons slack), so a wrong calibration can't break anything.
2. Check the polarity of the servo supply and the capacitor.
3. Grounds connected (supply ↔ PCA9685 ↔ ESP32).
4. Flash the firmware, open the dashboard, **Calibration** tab → Connection → *ESP32 over USB* → Connect.
5. Follow [CALIBRATION.md](CALIBRATION.md) joint by joint, then attach the horns and tendons.
