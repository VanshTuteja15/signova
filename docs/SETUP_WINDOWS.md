# Setup on Windows 10 / 11

Everything installs **inside the project folder** (`.venv`, `models\`, `.platformio\`). Nothing is
installed globally and no system settings change.

## 1. Python

Install **Python 3.11 or newer** from <https://www.python.org/downloads/> and tick **"Add python.exe to
PATH"** in the installer. Check in PowerShell: `py -3 --version`. (Built and tested with 3.13.)

Node.js is **not** needed.

## 2. Install SIGNOVA

```powershell
cd D:\Signova                     # wherever you cloned the repo
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

The script creates `.venv`, installs the pinned `requirements.txt`, installs the `signova` command,
copies `.env.example` to `.env`, runs `signova check`, `ruff` and the test suite. Options:

| Option | What it adds |
|---|---|
| `-Models` | downloads the speech models (Vosk ~130 MB, Whisper base.en ~145 MB) into `models\` and runs the real-model speech tests |
| `-Firmware` | runs the firmware unit tests (`pio test -e native`, needs a `g++` on PATH, e.g. from MinGW) and builds the ESP32 firmware. The first build downloads ~1 GB of toolchain into `.platformio\` |
| `-SkipTests` | install only |

`-ExecutionPolicy Bypass` applies to that one command only; it does not change your system policy.

## 3. Run

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_sim_demo.ps1        # simulation, no hardware
powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -Mode emulator  # pure-Python ESP32
powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -Mode serial -Port COM5   # real hand
```

The dashboard opens at <http://127.0.0.1:8000>. Stop the server with **Ctrl+C**. The same commands
without the scripts: `.venv\Scripts\signova serve --sim --open`.

## 4. Microphone in Chrome

The dashboard records with the browser's `MediaRecorder`. Chrome allows the microphone on
`http://127.0.0.1` and `http://localhost` (both count as secure origins), so no HTTPS is needed.

* The first time you hold **Hold to talk** (or the Space bar), Chrome asks for permission: click **Allow**.
* If you clicked *Block*: click the icon at the left of the address bar → **Microphone** → *Allow*,
  then reload.
* Windows also has a switch: *Settings → Privacy & security → Microphone → Let desktop apps access
  your microphone* must be on for Chrome.
* The first Whisper transcription loads the model (a few seconds; the dashboard shows "Loading speech
  model"). Run `signova download-models` beforehand so it doesn't download during a demo.

## 5. Claude (optional)

Put your key in `.env` (git-ignored):

```
ANTHROPIC_API_KEY=sk-ant-...
SIGNOVA_MODEL=claude-haiku-4-5
```

Restart the server and choose **Claude (cloud) + validator** as the gloss engine. Without a key
SIGNOVA uses the rule-based gloss and says so.

## 6. Flash the ESP32

### Option A: VS Code + PlatformIO IDE (recommended for the hardware team)

1. Install **VS Code** and the **PlatformIO IDE** extension.
2. *File → Open Folder…* → `firmware\signova_hand`.
3. Plug in the ESP32. In the PlatformIO toolbar (bottom bar) choose the environment **esp32dev**
   (or **esp32dev_feetech** for Feetech servos), then click **Upload (→)**.
4. If the upload hangs at `Connecting....`, hold the board's **BOOT** button until it starts writing.
5. Open the **Serial Monitor** (115200 baud) to see the `{"event":"boot",...}` line, then **close it**:
   only one program can use the COM port, and the dashboard needs it.

### Option B: command line (uses the PlatformIO installed in `.venv`)

```powershell
$env:PLATFORMIO_CORE_DIR = "$PWD\.platformio"     # keep the toolchain inside the project
cd firmware\signova_hand
..\..\.venv\Scripts\pio run -e esp32dev -t upload --upload-port COM5
..\..\.venv\Scripts\pio test -e native            # firmware unit tests on the laptop
```

## 7. USB driver (CP210x / CH340)

Most ESP32 dev boards use a **Silicon Labs CP210x** or **WCH CH340/CH9102** USB-serial chip. If no new
COM port appears in *Device Manager → Ports (COM & LPT)* when you plug the board in:

* CP210x: install the "CP210x Universal Windows Driver" from silabs.com.
* CH340 / CH9102: install the CH341SER driver from wch-ic.com.
* Try another cable: many USB cables are power-only.

`.venv\Scripts\signova ports` lists the ports and marks the ones that look like an ESP32 bridge.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Could not open COM5 … Access is denied` | another program has the port (Arduino IDE / PlatformIO serial monitor, a second SIGNOVA server). Close it |
| `No reply from the ESP32 on COM5` | firmware not flashed, wrong board, or a different baud rate. Flash it and check the boot line in the serial monitor |
| `Firmware joints … do not match hand.yaml` | make `JOINT_NAMES` in `config.h` and `joints` in `config/hand.yaml` identical, same order |
| Servos jitter / ESP32 resets when the hand moves | servo supply too weak or no common ground: see HARDWARE_WIRING.md |
| `Vosk model missing` | `.venv\Scripts\signova download-models` |
| "running scripts is disabled on this system" | use `powershell -ExecutionPolicy Bypass -File …` as shown above |
| port 8000 already in use | `scripts\run.ps1 -HttpPort 8001` |
