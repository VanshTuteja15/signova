"""Pure-Python ESP32 emulator implementing the exact SIGNOVA serial protocol (docs/PROTOCOL.md).

It mirrors firmware/signova_hand: 100 Hz motion with minimum-jerk easing, per-joint speed
limit, `done` only for the latest pose (a newer pose supersedes an older one), stop, relax
(move to rest, then outputs off), a 5 s watchdog, calibration persisted to a JSON file, and
the same error replies. `EmulatorSerial` wraps it in a pyserial-like object so the real
`SerialESP32Transport` code path can be tested end-to-end without hardware.
"""

from __future__ import annotations

import contextlib
import json
import math
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import serial  # pyserial, only for SerialException

FW_VERSION = "0.1.0-emu"
MAX_LINE = 512
RELAX_MS = 600
DRIVER_RANGES = {"pca9685": (500, 2500), "feetech": (0, 1023)}
DRIVER_DEFAULTS = {"pca9685": (600, 2300), "feetech": (200, 800)}


def min_jerk(u: float) -> float:
    """s(u) = 10u^3 - 15u^4 + 6u^5 (zero velocity and acceleration at both ends)."""
    u = max(0.0, min(1.0, u))
    return u * u * u * (10.0 - 15.0 * u + 6.0 * u * u)


class Motion:
    """Same algorithm as firmware/signova_hand/src/motion.cpp."""

    def __init__(self, n: int, full_range_ms: int, initial: list[float]) -> None:
        self.n = n
        self.full_range_ms = full_range_ms
        self.pos = list(initial)
        self.start = list(initial)
        self.target = list(initial)
        self.dur = [0.0] * n
        self.t0 = 0
        self.active = False

    def set_target(self, targets: list[float], ms: int, now_ms: int) -> None:
        for i in range(self.n):
            t = max(0.0, min(1.0, targets[i]))
            self.start[i] = self.pos[i]
            self.target[i] = t
            self.dur[i] = max(float(ms), abs(t - self.pos[i]) * self.full_range_ms)
        self.t0 = now_ms
        self.active = True

    def hold(self) -> None:
        self.target = list(self.pos)
        self.start = list(self.pos)
        self.active = False

    def update(self, now_ms: int) -> bool:
        """Advance motion; returns True exactly once when every joint has arrived."""
        if not self.active:
            return False
        elapsed = now_ms - self.t0
        arrived = True
        for i in range(self.n):
            if self.dur[i] <= 0 or elapsed >= self.dur[i]:
                self.pos[i] = self.target[i]
            else:
                self.pos[i] = self.start[i] + (self.target[i] - self.start[i]) * min_jerk(
                    elapsed / self.dur[i]
                )
                arrived = False
        if arrived:
            self.active = False
        return arrived


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


class ESP32Emulator:
    def __init__(
        self,
        joints: list[str],
        rest: list[float] | None = None,
        driver: str = "pca9685",
        cal_path: Path | None = None,
        full_range_ms: int = 250,
        watchdog_ms: int = 5000,
        clock: Callable[[], float] = time.monotonic,
        autostart: bool = True,
    ) -> None:
        if driver not in DRIVER_RANGES:
            raise ValueError(f"unknown driver {driver!r}")
        self.joints = list(joints)
        self.n = len(joints)
        self.rest = list(rest) if rest else [0.0] * self.n
        self.driver = driver
        self.cal_path = cal_path
        self.watchdog_ms = watchdog_ms
        self._clock = clock
        self._t0 = clock()
        self.motion = Motion(self.n, full_range_ms, self.rest)
        self.pending_id: str | None = None
        self.relaxing = False
        self.outputs_enabled = False  # like the firmware: outputs off until the first command
        self.raw_override: dict[int, int] = {}
        self.watchdog_fired = False
        self.last_rx = self.millis()
        self.cal = self._load_cal()
        self.commands_seen = 0
        self.plugged = True

        self._inbuf = bytearray()
        self._in_lock = threading.Lock()
        self._out: queue.Queue[bytes] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if autostart:
            self.start()

    # ------------------------------------------------------------------ time / io
    def millis(self) -> int:
        return int((self._clock() - self._t0) * 1000 + 1e-6)  # epsilon: 1233.9999 -> 1234

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="signova-esp32-emulator", daemon=True)
            self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)

    def feed(self, data: bytes) -> None:
        with self._in_lock:
            self._inbuf.extend(data)

    def read_line(self, timeout: float = 0.05) -> bytes | None:
        try:
            return self._out.get(timeout=timeout)
        except queue.Empty:
            return None

    def boot(self) -> None:
        """What a real ESP32 prints after the auto-reset that happens when the port opens."""
        self._out.put(b"ets Jun  8 2016 00:22:57\r\n")
        self._out.put(b"rst:0x1 (POWERON_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT)\r\n")
        self._emit({"event": "boot", "fw": FW_VERSION, "driver": self.driver})

    def _emit(self, obj: dict[str, Any]) -> None:
        self._out.put((json.dumps(obj, separators=(",", ":")) + "\n").encode())

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.process_input()
            for msg in self.tick():
                self._emit(msg)
            time.sleep(0.01)  # 100 Hz, same as the firmware

    def process_input(self) -> None:
        with self._in_lock:
            if b"\n" not in self._inbuf and len(self._inbuf) <= MAX_LINE:
                return
            data = bytes(self._inbuf)
            self._inbuf.clear()
        *lines, tail = data.split(b"\n")
        if len(tail) > MAX_LINE:
            lines.append(tail)
            tail = b""
        with self._in_lock:
            self._inbuf[:0] = tail
        for raw in lines:
            for reply in self.handle_line(raw.decode("utf-8", errors="replace")):
                self._emit(reply)

    # ------------------------------------------------------------------ calibration
    def _default_cal(self) -> list[dict[str, Any]]:
        lo, hi = DRIVER_DEFAULTS[self.driver]
        return [
            {"joint": j, "ch": i, "min": lo, "max": hi, "inv": False, "rest": round(self.rest[i], 3)}
            for i, j in enumerate(self.joints)
        ]

    def _load_cal(self) -> list[dict[str, Any]]:
        default = self._default_cal()
        if self.cal_path is None or not self.cal_path.exists():
            return default
        try:
            stored = json.loads(self.cal_path.read_text(encoding="utf-8"))
            by_joint = {c["joint"]: c for c in stored if isinstance(c, dict) and "joint" in c}
        except (OSError, ValueError, TypeError):
            return default  # like NVS: a corrupt store falls back to defaults
        for entry in default:
            saved = by_joint.get(entry["joint"])
            if saved:
                for key in ("min", "max", "inv", "rest"):
                    if key in saved:
                        entry[key] = saved[key]
        return default

    def _save_cal(self) -> None:
        if self.cal_path is None:
            return
        self.cal_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cal_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.cal, indent=2), encoding="utf-8")
        tmp.replace(self.cal_path)

    def output_value(self, i: int) -> int:
        """Servo command for joint i (PCA9685 microseconds or Feetech position counts)."""
        if i in self.raw_override:
            return self.raw_override[i]
        c = self.cal[i]
        v = max(0.0, min(1.0, self.motion.pos[i]))
        if c["inv"]:
            v = 1.0 - v
        return round(c["min"] + (c["max"] - c["min"]) * v)

    def snapshot(self) -> dict[str, Any]:
        return {
            "positions": [round(p, 4) for p in self.motion.pos],
            "outputs": [self.output_value(i) for i in range(self.n)],
            "enabled": self.outputs_enabled,
            "moving": self.motion.active,
            "relaxing": self.relaxing,
        }

    # ------------------------------------------------------------------ protocol
    @staticmethod
    def _err(code: str, detail: str, cmd: str | None = None, pose_id: str | None = None) -> dict[str, Any]:
        e: dict[str, Any] = {"err": code, "detail": detail}
        if cmd:
            e["cmd"] = cmd
        if pose_id is not None:
            e["id"] = pose_id
        return e

    def _joint_index(self, obj: dict[str, Any]) -> int | None:
        name = obj.get("joint")
        return self.joints.index(name) if isinstance(name, str) and name in self.joints else None

    def handle_line(self, line: str) -> list[dict[str, Any]]:
        line = line.strip()
        if not line:
            return []
        self.last_rx = self.millis()
        self.watchdog_fired = False
        if len(line) > MAX_LINE:
            return [self._err("bad_json", f"line longer than {MAX_LINE} bytes")]
        try:
            obj = json.loads(line)
        except ValueError:
            return [self._err("bad_json", "could not parse JSON")]
        if not isinstance(obj, dict) or not isinstance(obj.get("cmd"), str):
            return [self._err("bad_json", 'expected an object with a "cmd" string')]
        cmd = obj["cmd"]
        self.commands_seen += 1
        handler = getattr(self, f"_cmd_{cmd}", None)
        if handler is None:
            return [self._err("unknown_cmd", f"unknown command {cmd[:24]!r}", cmd[:24])]
        return handler(obj)

    def _cmd_hello(self, obj: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"ok": "hello", "fw": FW_VERSION, "driver": self.driver, "joints": self.joints}]

    def _cmd_ping(self, obj: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"pong": self.millis()}]

    def _cmd_pose(self, obj: dict[str, Any]) -> list[dict[str, Any]]:
        pose_id = str(obj.get("id", ""))[:16]
        j = obj.get("j")
        if not isinstance(j, list) or len(j) != self.n or not all(_is_number(v) for v in j):
            return [self._err("bad_length", f"j must be an array of {self.n} numbers", "pose", pose_id)]
        ms = obj.get("ms", 300)
        if not _is_number(ms):
            return [self._err("bad_json", "ms must be a number", "pose", pose_id)]
        ms = int(max(0, min(10000, ms)))
        self.raw_override.clear()
        self.relaxing = False
        self.outputs_enabled = True
        self.motion.set_target([float(v) for v in j], ms, self.millis())
        self.pending_id = pose_id  # any older pose id is superseded and never reported
        return []

    def _cmd_stop(self, obj: dict[str, Any]) -> list[dict[str, Any]]:
        self.motion.hold()
        self.pending_id = None
        self.relaxing = False
        return [{"ok": "stop"}]

    def _cmd_relax(self, obj: dict[str, Any]) -> list[dict[str, Any]]:
        self._start_relax()
        return [{"ok": "relax"}]

    def _start_relax(self) -> None:
        self.pending_id = None
        self.raw_override.clear()
        self.relaxing = True
        self.motion.set_target(self.cal_rest(), RELAX_MS, self.millis())

    def cal_rest(self) -> list[float]:
        return [float(c["rest"]) for c in self.cal]

    def _cmd_cal_get(self, obj: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"cal": [dict(c) for c in self.cal]}]

    def _cmd_cal_set(self, obj: dict[str, Any]) -> list[dict[str, Any]]:
        i = self._joint_index(obj)
        if i is None:
            return [self._err("bad_joint", f"unknown joint {str(obj.get('joint'))[:24]!r}", "cal_set")]
        lo, hi = DRIVER_RANGES[self.driver]
        entry = dict(self.cal[i])
        for key in ("min", "max"):
            if key in obj:
                if not _is_number(obj[key]) or not lo <= obj[key] <= hi:
                    return [self._err("bad_json", f"{key} must be a number {lo}..{hi}", "cal_set")]
                entry[key] = int(obj[key])
        if "inv" in obj:
            if not isinstance(obj["inv"], bool):
                return [self._err("bad_json", "inv must be true or false", "cal_set")]
            entry["inv"] = obj["inv"]
        if "rest" in obj:
            if not _is_number(obj["rest"]) or not 0 <= obj["rest"] <= 1:
                return [self._err("bad_json", "rest must be a number 0..1", "cal_set")]
            entry["rest"] = round(float(obj["rest"]), 3)
        if entry["min"] >= entry["max"]:
            return [self._err("bad_json", "min must be below max", "cal_set")]
        self.cal[i] = entry
        self._save_cal()
        return [{"ok": "cal_set"}]

    def _cmd_raw(self, obj: dict[str, Any]) -> list[dict[str, Any]]:
        i = self._joint_index(obj)
        if i is None:
            return [self._err("bad_joint", f"unknown joint {str(obj.get('joint'))[:24]!r}", "raw")]
        lo, hi = DRIVER_RANGES[self.driver]
        value = obj.get("us")
        if not _is_number(value):
            return [self._err("bad_json", "us must be a number", "raw")]
        self.raw_override[i] = int(max(lo, min(hi, value)))
        self.outputs_enabled = True
        self.relaxing = False
        return [{"ok": "raw"}]

    # ------------------------------------------------------------------ 100 Hz tick
    def tick(self) -> list[dict[str, Any]]:
        now = self.millis()
        out: list[dict[str, Any]] = []
        if self.motion.update(now):
            if self.relaxing:
                self.relaxing = False
                self.outputs_enabled = False  # no holding torque, no heat
            elif self.pending_id is not None:
                out.append({"done": self.pending_id, "t": now})
                self.pending_id = None
        if (
            not self.watchdog_fired
            and now - self.last_rx > self.watchdog_ms
            and (self.outputs_enabled or self.motion.active)
        ):
            self.watchdog_fired = True
            self._start_relax()
            out.append({"event": "watchdog", "detail": f"no message for {self.watchdog_ms} ms, relaxing"})
        return out


class EmulatorSerial:
    """pyserial-like wrapper (write / readline / close) around an ESP32Emulator.

    Opening it simulates the ESP32 auto-reset: boot noise plus a {"event":"boot"} line.
    `unplug()` makes every call raise SerialException until `replug()`, to test reconnects.
    """

    def __init__(self, emulator: ESP32Emulator, port: str = "EMULATOR", boot: bool = True) -> None:
        if not emulator.plugged:
            raise serial.SerialException(f"could not open port {port!r}: device not present")
        self.emulator = emulator
        self.port = port
        self.is_open = True
        if boot:
            emulator.boot()

    def _check(self) -> None:
        if not self.is_open:
            raise serial.SerialException("port is closed")
        if not self.emulator.plugged:
            raise serial.SerialException("device disconnected")

    def write(self, data: bytes) -> int:
        self._check()
        self.emulator.feed(data)
        return len(data)

    def readline(self) -> bytes:
        self._check()
        return self.emulator.read_line(timeout=0.05) or b""

    def reset_input_buffer(self) -> None:
        self._check()
        with contextlib.suppress(queue.Empty):
            while True:
                self.emulator._out.get_nowait()

    def flush(self) -> None:
        self._check()

    def close(self) -> None:
        self.is_open = False


def unplug(emulator: ESP32Emulator) -> None:
    emulator.plugged = False


def replug(emulator: ESP32Emulator) -> None:
    emulator.plugged = True
