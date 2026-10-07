"""End-to-end tests of SerialESP32Transport against the in-memory ESP32 emulator."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from signova.transport import TransportError, TransportTimeout
from signova.transport.emulator import EmulatorSerial, ESP32Emulator, replug, unplug
from signova.transport.serial_esp32 import SerialESP32Transport, list_serial_ports

JOINTS = ["thumb", "thumb_rot", "index", "middle", "ring", "pinky", "wrist"]
REST = [0.15, 0.2, 0.15, 0.15, 0.18, 0.2, 0.5]


async def wait_for(cond, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


@pytest.fixture
def emu(tmp_path: Path):
    e = ESP32Emulator(JOINTS, REST, cal_path=tmp_path / "cal.json", watchdog_ms=5000)
    yield e
    e.shutdown()


def make(emu: ESP32Emulator, **kw: Any) -> tuple[SerialESP32Transport, list, list]:
    lines: list[tuple[str, str]] = []
    events: list[dict] = []
    opts: dict[str, Any] = {
        "port": "EMU",
        "serial_factory": lambda port, baud: EmulatorSerial(emu, port),
        "heartbeat_s": 0,
        "boot_wait_s": 0.05,
        "hello_timeout_s": 0.5,
        "reconnect_interval_s": 0.1,
        "on_line": lambda d, t: lines.append((d, t)),
        "on_event": events.append,
    }
    opts.update(kw)
    return SerialESP32Transport(JOINTS, **opts), lines, events


async def test_happy_path(emu: ESP32Emulator) -> None:
    t, lines, events = make(emu)
    await t.connect()
    try:
        st = t.status()
        assert st["connected"] and st["firmware"] == "0.1.0-emu" and st["driver"] == "pca9685"
        assert st["mismatch"] is None
        reply = await t.send_pose("ILY.1", [0, 0, 0, 1, 1, 0, 0.5], 200)
        assert reply["done"] == "ILY.1"
        assert emu.snapshot()["positions"][3] == 1.0
        assert "pong" in await t.ping()
        assert (await t.stop())["ok"] == "stop"
        assert (await t.relax())["ok"] == "relax"
        assert (await t.hello())["ok"] == "hello"
        # boot noise was logged but ignored
        assert any(d == "rx" and "POWERON_RESET" in s for d, s in lines)
        assert any(d == "tx" and '"cmd":"pose"' in s for d, s in lines)
    finally:
        await t.close()
    assert not t.connected


async def test_calibration_roundtrip(emu: ESP32Emulator, tmp_path: Path) -> None:
    t, _, _ = make(emu)
    await t.connect()
    try:
        cal = await t.cal_get()
        assert [c["joint"] for c in cal] == JOINTS
        await t.cal_set("index", 550, 2350, True, 0.1)
        cal = await t.cal_get()
        assert cal[2]["min"] == 550 and cal[2]["inv"] is True and cal[2]["rest"] == 0.1
        assert (tmp_path / "cal.json").exists()
        assert (await t.raw("index", 1500))["ok"] == "raw"
        with pytest.raises(TransportError, match="bad_joint"):
            await t.raw("elbow", 1500)
        with pytest.raises(TransportError, match="bad_json"):
            await t.cal_set("index", 2400, 600, False, 0.1)
    finally:
        await t.close()


async def test_superseded_pose(emu: ESP32Emulator) -> None:
    t, _, _ = make(emu)
    await t.connect()
    try:
        first = asyncio.create_task(t.send_pose("A.1", [1] * 7, 400))
        await asyncio.sleep(0.05)
        second = await t.send_pose("B.2", [0] * 7, 100)
        assert second["done"] == "B.2"
        assert (await first) == {"done": "A.1", "superseded": True}
    finally:
        await t.close()


async def test_stop_resolves_waiting_pose(emu: ESP32Emulator) -> None:
    t, _, _ = make(emu)
    await t.connect()
    try:
        pending = asyncio.create_task(t.send_pose("A.1", [1] * 7, 2000))
        await asyncio.sleep(0.05)
        await t.stop()
        assert (await pending) == {"stopped": True}
    finally:
        await t.close()


async def test_bad_json_from_laptop_gets_error_event(emu: ESP32Emulator) -> None:
    t, lines, events = make(emu)
    await t.connect()
    try:
        assert t._ser is not None
        t._ser.write(b"this is not json\n")
        await wait_for(lambda: any(e.get("event") == "error" for e in events))
        assert "bad_json" in next(e for e in events if e.get("event") == "error")["detail"]
        # transport keeps working afterwards
        assert (await t.send_pose("A.1", [0.5] * 7, 50))["done"] == "A.1"
    finally:
        await t.close()


async def test_firmware_error_fails_matching_pose(emu: ESP32Emulator) -> None:
    t, _, _ = make(emu)
    await t.connect()
    try:
        # bypass the local length check to provoke the firmware's bad_length reply
        t.joints = JOINTS + ["extra"]
        with pytest.raises(TransportError, match="bad_length"):
            await t.send_pose("Z.1", [0.0] * 8, 50, timeout_s=2)
    finally:
        await t.close()


async def test_pose_timeout(emu: ESP32Emulator) -> None:
    t, _, _ = make(emu)
    await t.connect()
    try:
        emu.shutdown()  # firmware hangs: no more replies
        with pytest.raises(TransportTimeout, match="did not confirm"):
            await t.send_pose("A.1", [0.5] * 7, 50, timeout_s=0.3)
        with pytest.raises(TransportTimeout):
            await t.ping()
    finally:
        await t.close()


async def test_no_firmware_reply_on_connect(emu: ESP32Emulator) -> None:
    emu.shutdown()
    t, _, _ = make(emu, hello_attempts=2, hello_timeout_s=0.2)
    with pytest.raises(TransportError, match="No reply from the ESP32"):
        await t.connect()
    assert not t.connected
    await t.close()


async def test_cannot_open_port(emu: ESP32Emulator) -> None:
    unplug(emu)
    t, _, _ = make(emu)
    with pytest.raises(TransportError, match="Could not open EMU"):
        await t.connect()
    await t.close()


async def test_joint_mismatch_blocks_poses_but_not_calibration(tmp_path: Path) -> None:
    emu = ESP32Emulator(["index", "middle"], [0, 0], cal_path=tmp_path / "c.json")
    try:
        t, _, events = make(emu)
        await t.connect()
        assert t.mismatch and "do not match" in t.mismatch
        assert any(e.get("event") == "mismatch" for e in events)
        with pytest.raises(TransportError, match="do not match"):
            await t.send_pose("A.1", [0] * 7, 50)
        assert len(await t.cal_get()) == 2
        await t.close()
    finally:
        emu.shutdown()


async def test_watchdog_without_heartbeat(tmp_path: Path) -> None:
    emu = ESP32Emulator(JOINTS, REST, cal_path=tmp_path / "c.json", watchdog_ms=300)
    try:
        t, _, events = make(emu, heartbeat_s=0)
        await t.connect()
        await t.send_pose("A.1", [1] * 7, 50)
        await wait_for(lambda: any(e.get("event") == "watchdog" for e in events), timeout=3)
        await wait_for(lambda: not emu.outputs_enabled, timeout=3)
        await t.close()
    finally:
        emu.shutdown()


async def test_heartbeat_keeps_hand_alive(tmp_path: Path) -> None:
    emu = ESP32Emulator(JOINTS, REST, cal_path=tmp_path / "c.json", watchdog_ms=400)
    try:
        t, _, events = make(emu, heartbeat_s=0.1)
        await t.connect()
        await t.send_pose("A.1", [1] * 7, 50)
        await asyncio.sleep(1.0)
        assert not any(e.get("event") == "watchdog" for e in events)
        assert emu.outputs_enabled
        await t.close()
    finally:
        emu.shutdown()


async def test_reconnect_after_unplug(emu: ESP32Emulator) -> None:
    t, lines, events = make(emu, heartbeat_s=0.1)
    await t.connect()
    try:
        unplug(emu)
        await wait_for(lambda: not t.connected, timeout=3)
        assert any(e.get("event") == "disconnected" for e in events)
        with pytest.raises(TransportError, match="not connected"):
            await t.send_pose("A.1", [0.5] * 7, 50)
        replug(emu)
        await wait_for(lambda: t.connected, timeout=5)
        assert any(e.get("event") == "reconnected" for e in events)
        assert (await t.send_pose("A.2", [0.5] * 7, 50))["done"] == "A.2"
    finally:
        await t.close()


async def test_missed_pongs_trigger_reconnect(emu: ESP32Emulator) -> None:
    t, _, events = make(emu, heartbeat_s=0.05)
    await t.connect()
    try:
        emu.shutdown()  # device hangs but the port stays open
        await wait_for(lambda: not t.connected, timeout=8)
        assert any("3 pings" in e.get("detail", "") for e in events if e.get("event") == "disconnected")
        emu.start()
        await wait_for(lambda: t.connected, timeout=8)
    finally:
        await t.close()


async def test_disconnect_fails_pending_pose(emu: ESP32Emulator) -> None:
    t, _, _ = make(emu)
    await t.connect()
    try:
        pending = asyncio.create_task(t.send_pose("A.1", [1] * 7, 3000, timeout_s=5))
        await asyncio.sleep(0.05)
        unplug(emu)
        with pytest.raises(TransportError, match="Lost connection"):
            await pending
    finally:
        await t.close()


def test_list_ports_shape() -> None:
    ports = list_serial_ports()
    assert isinstance(ports, list)
    for p in ports:
        assert {"device", "description", "likely_esp32"} <= set(p)
