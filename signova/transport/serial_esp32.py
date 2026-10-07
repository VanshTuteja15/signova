"""ESP32 over USB serial: newline-terminated JSON at 115200 baud (docs/PROTOCOL.md).

* a background reader thread turns lines into messages and hands them to the asyncio loop
* request/response commands (hello, stop, relax, ping, cal_get, cal_set, raw) are matched by
  their reply key; poses are matched by id via {"done": id}
* sending a new pose supersedes any older pose still waiting (the firmware only reports the
  latest one), so the older waiter resolves with {"superseded": true}
* a heartbeat pings every second (the firmware relaxes the hand after 5 s of silence);
  three missed pongs or a serial error trigger an automatic reconnect loop
* on connect the firmware's joint list is checked against hand.yaml; a mismatch blocks poses
  with a clear message (calibration still works so the hand can be fixed)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections.abc import Callable
from typing import Any

import serial
from serial.tools import list_ports

from .base import LineCallback, Transport, TransportError, TransportTimeout

SerialFactory = Callable[[str, int], Any]
EventCallback = Callable[[dict[str, Any]], None]

# USB-serial bridges commonly found on ESP32 dev boards.
ESP32_VIDS = {0x10C4: "CP210x", 0x1A86: "CH340/CH9102", 0x303A: "Espressif USB", 0x0403: "FTDI"}
REPLY_KEYS = {
    "hello": "hello",
    "stop": "stop",
    "relax": "relax",
    "cal_set": "cal_set",
    "raw": "raw",
    "ping": "pong",
    "cal_get": "cal",
}


def list_serial_ports() -> list[dict[str, Any]]:
    ports = []
    for p in list_ports.comports():
        bridge = ESP32_VIDS.get(p.vid or -1)
        ports.append(
            {
                "device": p.device,
                "description": p.description or "",
                "hwid": p.hwid or "",
                "likely_esp32": bridge is not None,
                "bridge": bridge,
            }
        )
    ports.sort(key=lambda d: (not d["likely_esp32"], d["device"]))
    return ports


def _default_factory(port: str, baud: int) -> Any:
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud
    ser.timeout = 0.05
    ser.write_timeout = 1.0
    ser.open()
    return ser


class SerialESP32Transport(Transport):
    mode = "serial"

    def __init__(
        self,
        joints: list[str],
        port: str | None = None,
        baud: int = 115200,
        on_line: LineCallback | None = None,
        on_event: EventCallback | None = None,
        serial_factory: SerialFactory | None = None,
        heartbeat_s: float = 1.0,
        mode_name: str = "serial",
        hello_attempts: int = 3,
        hello_timeout_s: float = 1.5,
        boot_wait_s: float = 0.3,
        reconnect: bool = True,
        reconnect_interval_s: float = 1.0,
    ) -> None:
        super().__init__(joints, on_line)
        self.mode = mode_name
        self.port = port
        self.baud = baud
        self.on_event = on_event
        self._factory = serial_factory or _default_factory
        self.heartbeat_s = heartbeat_s
        self.hello_attempts = hello_attempts
        self.hello_timeout_s = hello_timeout_s
        self.boot_wait_s = boot_wait_s
        self.reconnect_enabled = reconnect
        self.reconnect_interval_s = reconnect_interval_s

        self.firmware: str | None = None
        self.driver: str | None = None
        self.device_joints: list[str] | None = None
        self.mismatch: str | None = None
        self.reconnecting = False
        self.missed_pongs = 0

        self._ser: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reader: threading.Thread | None = None
        self._reader_stop = threading.Event()
        self._write_lock = threading.Lock()
        self._cmd_locks: dict[str, asyncio.Lock] = {}
        self._cmd_waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._pose_waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._closing = False
        self._generation = 0
        self._ever_connected = False

    # ------------------------------------------------------------------ connection
    def _pick_port(self) -> str:
        if self.port:
            return self.port
        ports = list_serial_ports()
        likely = [p for p in ports if p["likely_esp32"]]
        if likely:
            return str(likely[0]["device"])
        if ports:
            return str(ports[0]["device"])
        raise TransportError("No serial ports found. Plug in the ESP32 with a data-capable USB cable.")

    async def connect(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._closing = False
        await self._open_and_handshake()
        if self.heartbeat_s > 0 and self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat())

    async def _open_and_handshake(self) -> None:
        port = self._pick_port()
        try:
            ser = await asyncio.to_thread(self._factory, port, self.baud)
        except (serial.SerialException, OSError, ValueError) as exc:
            raise TransportError(
                f"Could not open {port}: {exc}. Check the cable, close the Arduino/PlatformIO serial "
                "monitor, and install the CP210x or CH340 USB driver if the port is missing."
            ) from exc
        self.port = port
        self._ser = ser
        self._generation += 1
        self._start_reader(self._generation)
        self._log("sys", f"# opened {port} at {self.baud} baud")
        await asyncio.sleep(self.boot_wait_s)  # opening the port resets most ESP32 boards
        last_exc: Exception | None = None
        for _ in range(self.hello_attempts):
            try:
                await self._hello(self.hello_timeout_s)
                break
            except TransportError as exc:
                last_exc = exc
        else:
            self._close_serial()
            raise TransportError(
                f"No reply from the ESP32 on {port}. Is the SIGNOVA firmware flashed and the baud rate "
                f"{self.baud}? ({last_exc})"
            )
        self.connected = True
        self._ever_connected = True
        self.missed_pongs = 0
        self.detail = f"{port} - firmware {self.firmware} ({self.driver})"
        if self.mismatch:
            self.detail += " - JOINT MISMATCH"

    def _start_reader(self, generation: int) -> None:
        self._reader_stop = threading.Event()
        self._reader = threading.Thread(
            target=self._read_loop, args=(self._ser, self._reader_stop, generation), daemon=True
        )
        self._reader.start()

    def _read_loop(self, ser: Any, stop: threading.Event, generation: int) -> None:
        while not stop.is_set():
            try:
                raw = ser.readline()
            except (serial.SerialException, OSError, TypeError, AttributeError) as exc:
                if not stop.is_set():
                    self._threadsafe(self._on_disconnect, f"serial error: {exc}", generation)
                return
            if not raw:
                continue
            text = raw.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            self._log("rx", text)
            if not text.startswith("{"):
                continue  # boot noise from the ESP32 ROM
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            if isinstance(msg, dict):
                self._threadsafe(self._dispatch, msg, generation)

    def _threadsafe(self, fn: Callable[..., None], *args: Any) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(fn, *args)

    def _close_serial(self) -> None:
        self._reader_stop.set()
        ser, self._ser = self._ser, None
        if ser is not None:
            with contextlib.suppress(Exception):
                ser.close()

    def _fail_waiters(self, exc: Exception) -> None:
        for fut in [*self._cmd_waiters.values(), *self._pose_waiters.values()]:
            if not fut.done():
                fut.set_exception(exc)
        self._cmd_waiters.clear()
        self._pose_waiters.clear()

    def _on_disconnect(self, reason: str, generation: int | None = None) -> None:
        if generation is not None and generation != self._generation:
            return  # stale reader from a previous connection
        was_connected = self.connected
        self.connected = False
        self._close_serial()
        self._fail_waiters(TransportError("Lost connection to the hand."))
        if self._closing:
            return
        self.detail = f"disconnected ({reason}); retrying"
        if was_connected:
            self._log("sys", f"# connection lost: {reason}")
            self._event({"event": "disconnected", "detail": reason})
        if (
            self.reconnect_enabled
            and self._ever_connected
            and (self._reconnect_task is None or self._reconnect_task.done())
        ):
            self._reconnect_task = asyncio.get_running_loop().create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        self.reconnecting = True
        try:
            while not self._closing and not self.connected:
                await asyncio.sleep(self.reconnect_interval_s)
                if self._closing:
                    return
                try:
                    await self._open_and_handshake()
                except TransportError as exc:
                    self.detail = f"disconnected; retrying ({exc})"
                    continue
                self._log("sys", f"# reconnected to {self.port}")
                self._event({"event": "reconnected", "detail": self.detail})
        finally:
            self.reconnecting = False

    async def close(self) -> None:
        self._closing = True
        for task in (self._heartbeat_task, self._reconnect_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._heartbeat_task = None
        self._reconnect_task = None
        self.connected = False
        self._close_serial()
        self._fail_waiters(TransportError("Transport closed."))
        self.detail = "closed"

    async def _heartbeat(self) -> None:
        while not self._closing:
            await asyncio.sleep(self.heartbeat_s)
            if not self.connected:
                continue
            try:
                await self._request({"cmd": "ping"}, timeout_s=1.0, log=False)
                self.missed_pongs = 0
            except TransportError:
                self.missed_pongs += 1
                if self.missed_pongs >= 3 and self.connected:
                    self._on_disconnect("no reply to 3 pings", self._generation)

    # ------------------------------------------------------------------ messaging
    def _event(self, msg: dict[str, Any]) -> None:
        if self.on_event is not None:
            with contextlib.suppress(Exception):
                self.on_event(msg)

    def _write(self, obj: dict[str, Any], log: bool = True) -> None:
        ser = self._ser
        if ser is None:
            raise TransportError("The hand is not connected.")
        line = json.dumps(obj, separators=(",", ":"))
        try:
            with self._write_lock:
                ser.write((line + "\n").encode())
        except (serial.SerialException, OSError) as exc:
            self._on_disconnect(f"write failed: {exc}", self._generation)
            raise TransportError("Lost connection to the hand.") from exc
        if log:
            self._log("tx", line)

    def _dispatch(self, msg: dict[str, Any], generation: int | None = None) -> None:
        if generation is not None and generation != self._generation:
            return
        if "done" in msg:
            fut = self._pose_waiters.pop(str(msg["done"]), None)
            if fut is not None and not fut.done():
                fut.set_result(msg)
            return
        if "err" in msg:
            err = TransportError(f"ESP32 error {msg.get('err')}: {msg.get('detail', '')}".strip())
            cmd = msg.get("cmd")
            if cmd == "pose":
                fut = self._pose_waiters.pop(str(msg.get("id", "")), None)
            elif isinstance(cmd, str) and cmd in REPLY_KEYS:
                fut = self._cmd_waiters.pop(REPLY_KEYS[cmd], None)
            else:
                fut = None
            if fut is not None and not fut.done():
                fut.set_exception(err)
            else:
                self._event({"event": "error", "detail": str(err)})
            return
        if "event" in msg:
            self._event(msg)
            return
        key = msg.get("ok") if isinstance(msg.get("ok"), str) else None
        if key is None:
            key = "pong" if "pong" in msg else "cal" if "cal" in msg else None
        if key is not None:
            fut = self._cmd_waiters.pop(key, None)
            if fut is not None and not fut.done():
                fut.set_result(msg)

    async def _request(self, obj: dict[str, Any], timeout_s: float = 1.5, log: bool = True) -> dict[str, Any]:
        key = REPLY_KEYS[obj["cmd"]]
        lock = self._cmd_locks.setdefault(key, asyncio.Lock())
        async with lock:
            fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            self._cmd_waiters[key] = fut
            try:
                self._write(obj, log=log)
                return await asyncio.wait_for(fut, timeout_s)
            except TimeoutError as exc:
                raise TransportTimeout(
                    f"The hand did not answer {obj['cmd']} within {timeout_s:.1f} s."
                ) from exc
            finally:
                if self._cmd_waiters.get(key) is fut:
                    del self._cmd_waiters[key]

    def _require(self) -> None:
        if not self.connected:
            raise TransportError(
                "The hand is not connected." + (" Reconnecting..." if self.reconnecting else "")
            )

    # ------------------------------------------------------------------ public API
    async def _hello(self, timeout_s: float) -> dict[str, Any]:
        reply = await self._request({"cmd": "hello"}, timeout_s=timeout_s)
        self.firmware = str(reply.get("fw", "?"))
        self.driver = str(reply.get("driver", "?"))
        dj = reply.get("joints")
        self.device_joints = [str(j) for j in dj] if isinstance(dj, list) else None
        if self.device_joints is not None and self.device_joints != self.joints:
            self.mismatch = (
                f"Firmware joints {self.device_joints} do not match hand.yaml {self.joints}. "
                "Fix JOINT_NAMES in firmware config.h or the joints in config/hand.yaml so they list the "
                "same joints in the same order."
            )
            self._log("sys", "# " + self.mismatch)
            self._event({"event": "mismatch", "detail": self.mismatch})
        else:
            self.mismatch = None
        return reply

    async def hello(self) -> dict[str, Any]:
        self._require()
        return await self._hello(self.hello_timeout_s)

    async def send_pose(
        self, pose_id: str, vector: list[float], ms: int, timeout_s: float | None = None
    ) -> dict[str, Any]:
        self._require()
        if self.mismatch:
            raise TransportError(self.mismatch)
        vec = self._check_vector(vector)
        ms = max(0, int(ms))
        for old_id, old in list(self._pose_waiters.items()):
            if not old.done():
                old.set_result({"done": old_id, "superseded": True})
            del self._pose_waiters[old_id]
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pose_waiters[pose_id] = fut
        timeout = timeout_s if timeout_s is not None else ms / 1000 + 1.5
        try:
            self._write({"cmd": "pose", "id": pose_id, "j": [round(v, 4) for v in vec], "ms": ms})
            return await asyncio.wait_for(fut, timeout)
        except TimeoutError as exc:
            raise TransportTimeout(
                f"The hand did not confirm pose {pose_id} within {timeout:.1f} s. Check servo power."
            ) from exc
        finally:
            if self._pose_waiters.get(pose_id) is fut:
                del self._pose_waiters[pose_id]

    async def stop(self) -> dict[str, Any]:
        self._require()
        for fut in self._pose_waiters.values():
            if not fut.done():
                fut.set_result({"stopped": True})
        self._pose_waiters.clear()
        return await self._request({"cmd": "stop"})

    async def relax(self) -> dict[str, Any]:
        self._require()
        return await self._request({"cmd": "relax"})

    async def ping(self) -> dict[str, Any]:
        self._require()
        return await self._request({"cmd": "ping"}, timeout_s=1.0)

    async def cal_get(self) -> list[dict[str, Any]]:
        self._require()
        reply = await self._request({"cmd": "cal_get"})
        cal = reply.get("cal")
        if not isinstance(cal, list):
            raise TransportError("The hand sent a calibration reply in an unexpected format.")
        return [dict(c) for c in cal if isinstance(c, dict)]

    async def cal_set(self, joint: str, min_us: int, max_us: int, inv: bool, rest: float) -> dict[str, Any]:
        self._require()
        return await self._request(
            {
                "cmd": "cal_set",
                "joint": joint,
                "min": int(min_us),
                "max": int(max_us),
                "inv": bool(inv),
                "rest": rest,
            }
        )

    async def raw(self, joint: str, value: int) -> dict[str, Any]:
        self._require()
        return await self._request({"cmd": "raw", "joint": joint, "us": int(value)})

    def status(self) -> dict[str, Any]:
        return {
            **super().status(),
            "port": self.port,
            "baud": self.baud,
            "firmware": self.firmware,
            "driver": self.driver,
            "device_joints": self.device_joints,
            "mismatch": self.mismatch,
            "reconnecting": self.reconnecting,
        }
