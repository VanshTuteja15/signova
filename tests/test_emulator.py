"""Unit tests for the pure-Python ESP32 emulator (same rules as the firmware)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from signova.transport.emulator import ESP32Emulator, Motion, min_jerk

JOINTS = ["thumb", "thumb_rot", "index", "middle", "ring", "pinky", "wrist"]
REST = [0.15, 0.2, 0.15, 0.15, 0.18, 0.2, 0.5]


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t

    def advance(self, ms: float) -> None:
        self.t += ms / 1000


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def emu(clock: Clock, tmp_path: Path) -> ESP32Emulator:
    return ESP32Emulator(JOINTS, REST, cal_path=tmp_path / "cal.json", clock=clock, autostart=False)


def send(emu: ESP32Emulator, obj: object) -> list[dict]:
    return emu.handle_line(json.dumps(obj) if not isinstance(obj, str) else obj)


def run_ticks(emu: ESP32Emulator, clock: Clock, ms: int) -> list[dict]:
    out: list[dict] = []
    for _ in range(ms // 10):
        clock.advance(10)
        out.extend(emu.tick())
    return out


def test_min_jerk_curve() -> None:
    assert min_jerk(0) == 0 and min_jerk(1) == 1
    assert min_jerk(0.5) == pytest.approx(0.5)
    assert min_jerk(-1) == 0 and min_jerk(2) == 1
    # symmetric and monotonic
    assert min_jerk(0.25) == pytest.approx(1 - min_jerk(0.75))
    vals = [min_jerk(i / 20) for i in range(21)]
    assert vals == sorted(vals)


def test_motion_speed_limit() -> None:
    m = Motion(2, full_range_ms=250, initial=[0.0, 0.0])
    m.set_target([1.0, 0.1], ms=50, now_ms=0)
    assert m.dur == [250.0, 50.0]  # full range can't be faster than 250 ms
    assert not m.update(100)
    assert m.pos[1] == pytest.approx(0.1)
    assert 0 < m.pos[0] < 1
    assert m.update(250)
    assert m.pos == [1.0, 0.1]
    assert not m.update(300)  # reports arrival only once


def test_hello(emu: ESP32Emulator) -> None:
    (reply,) = send(emu, {"cmd": "hello"})
    assert reply == {"ok": "hello", "fw": "0.1.0-emu", "driver": "pca9685", "joints": JOINTS}


def test_pose_done_after_motion(emu: ESP32Emulator, clock: Clock) -> None:
    assert send(emu, {"cmd": "pose", "id": "A", "j": [0.05, 0.1, 1, 1, 1, 1, 0.5], "ms": 300}) == []
    assert emu.outputs_enabled
    out = run_ticks(emu, clock, 290)
    assert out == []
    out = run_ticks(emu, clock, 30)
    assert out[0]["done"] == "A" and isinstance(out[0]["t"], int)
    assert emu.motion.pos[2] == 1.0


def test_pose_clamps_targets(emu: ESP32Emulator, clock: Clock) -> None:
    send(emu, {"cmd": "pose", "id": "X", "j": [5, -3, 0, 0, 0, 0, 0], "ms": 0})
    run_ticks(emu, clock, 400)
    assert emu.motion.pos[0] == 1.0 and emu.motion.pos[1] == 0.0


def test_newer_pose_supersedes(emu: ESP32Emulator, clock: Clock) -> None:
    send(emu, {"cmd": "pose", "id": "A", "j": [1] * 7, "ms": 300})
    run_ticks(emu, clock, 100)
    send(emu, {"cmd": "pose", "id": "B", "j": [0] * 7, "ms": 300})
    out = run_ticks(emu, clock, 500)
    assert [m.get("done") for m in out] == ["B"]


def test_stop_holds_without_done(emu: ESP32Emulator, clock: Clock) -> None:
    send(emu, {"cmd": "pose", "id": "A", "j": [1] * 7, "ms": 300})
    run_ticks(emu, clock, 100)
    assert send(emu, {"cmd": "stop"}) == [{"ok": "stop"}]
    held = list(emu.motion.pos)
    assert run_ticks(emu, clock, 500) == []
    assert emu.motion.pos == held


def test_relax_goes_to_rest_then_disables(emu: ESP32Emulator, clock: Clock) -> None:
    send(emu, {"cmd": "pose", "id": "A", "j": [1] * 7, "ms": 0})
    run_ticks(emu, clock, 300)
    assert send(emu, {"cmd": "relax"}) == [{"ok": "relax"}]
    assert emu.outputs_enabled
    assert run_ticks(emu, clock, 700) == []  # no done for relax motion
    assert emu.motion.pos == pytest.approx(REST)
    assert not emu.outputs_enabled


def test_ping_and_cal_get(emu: ESP32Emulator, clock: Clock) -> None:
    clock.advance(1234)
    assert send(emu, {"cmd": "ping"}) == [{"pong": 1234}]
    (reply,) = send(emu, {"cmd": "cal_get"})
    assert len(reply["cal"]) == 7
    assert reply["cal"][2] == {"joint": "index", "ch": 2, "min": 600, "max": 2300, "inv": False, "rest": 0.15}


def test_cal_set_persists(emu: ESP32Emulator, clock: Clock, tmp_path: Path) -> None:
    assert send(
        emu, {"cmd": "cal_set", "joint": "index", "min": 550, "max": 2350, "inv": True, "rest": 0.1}
    ) == [{"ok": "cal_set"}]
    stored = json.loads((tmp_path / "cal.json").read_text())
    assert stored[2]["min"] == 550 and stored[2]["inv"] is True
    again = ESP32Emulator(JOINTS, REST, cal_path=tmp_path / "cal.json", clock=clock, autostart=False)
    assert again.cal[2]["max"] == 2350 and again.cal[2]["rest"] == 0.1


def test_corrupt_cal_file_falls_back(tmp_path: Path, clock: Clock) -> None:
    (tmp_path / "cal.json").write_text("{nope", encoding="utf-8")
    emu = ESP32Emulator(JOINTS, REST, cal_path=tmp_path / "cal.json", clock=clock, autostart=False)
    assert emu.cal[0]["min"] == 600


def test_output_mapping_and_invert(emu: ESP32Emulator, clock: Clock) -> None:
    send(emu, {"cmd": "pose", "id": "A", "j": [1, 0, 0.5, 0, 0, 0, 0], "ms": 0})
    run_ticks(emu, clock, 300)
    assert emu.output_value(0) == 2300 and emu.output_value(1) == 600
    assert emu.output_value(2) == 1450
    send(emu, {"cmd": "cal_set", "joint": "thumb", "inv": True})
    assert emu.output_value(0) == 600


def test_raw(emu: ESP32Emulator) -> None:
    assert send(emu, {"cmd": "raw", "joint": "index", "us": 1500}) == [{"ok": "raw"}]
    assert emu.output_value(2) == 1500
    send(emu, {"cmd": "raw", "joint": "index", "us": 99999})
    assert emu.output_value(2) == 2500  # clamped to the PCA9685 range


@pytest.mark.parametrize(
    ("line", "code"),
    [
        ("this is not json", "bad_json"),
        ("[1,2,3]", "bad_json"),
        ('{"nocmd": 1}', "bad_json"),
        ('{"cmd": "dance"}', "unknown_cmd"),
        ('{"cmd": "pose", "id": "A", "j": [0, 0]}', "bad_length"),
        ('{"cmd": "pose", "id": "A", "j": "x"}', "bad_length"),
        ('{"cmd": "pose", "id": "A", "j": [0,0,0,0,0,0,"x"]}', "bad_length"),
        ('{"cmd": "pose", "id": "A", "j": [0,0,0,0,0,0,0], "ms": "slow"}', "bad_json"),
        ('{"cmd": "cal_set", "joint": "elbow"}', "bad_joint"),
        ('{"cmd": "cal_set", "joint": "index", "min": 2400, "max": 600}', "bad_json"),
        ('{"cmd": "cal_set", "joint": "index", "min": 10}', "bad_json"),
        ('{"cmd": "cal_set", "joint": "index", "inv": "yes"}', "bad_json"),
        ('{"cmd": "cal_set", "joint": "index", "rest": 3}', "bad_json"),
        ('{"cmd": "raw", "joint": "elbow", "us": 1500}', "bad_joint"),
        ('{"cmd": "raw", "joint": "index"}', "bad_json"),
        ("x" * 600, "bad_json"),
    ],
)
def test_errors_never_crash(emu: ESP32Emulator, line: str, code: str) -> None:
    (reply,) = emu.handle_line(line)
    assert reply["err"] == code
    assert reply["detail"]


def test_pose_error_carries_id(emu: ESP32Emulator) -> None:
    (reply,) = send(emu, {"cmd": "pose", "id": "Q", "j": [0]})
    assert reply["cmd"] == "pose" and reply["id"] == "Q"


def test_watchdog_relaxes(emu: ESP32Emulator, clock: Clock) -> None:
    send(emu, {"cmd": "pose", "id": "A", "j": [1] * 7, "ms": 0})
    out = run_ticks(emu, clock, 4900)
    assert [m for m in out if "event" in m] == []
    out = run_ticks(emu, clock, 300)
    assert any(m.get("event") == "watchdog" for m in out)
    run_ticks(emu, clock, 800)
    assert emu.motion.pos == pytest.approx(REST)
    assert not emu.outputs_enabled
    # fires only once until the laptop talks again
    assert [m for m in run_ticks(emu, clock, 6000) if "event" in m] == []


def test_watchdog_quiet_when_idle_from_boot(emu: ESP32Emulator, clock: Clock) -> None:
    assert run_ticks(emu, clock, 6000) == []


def test_messages_feed_the_watchdog(emu: ESP32Emulator, clock: Clock) -> None:
    send(emu, {"cmd": "pose", "id": "A", "j": [1] * 7, "ms": 0})
    for _ in range(10):
        run_ticks(emu, clock, 1000)
        send(emu, {"cmd": "ping"})
    assert emu.outputs_enabled


def test_byte_stream_framing(emu: ESP32Emulator) -> None:
    emu.feed(b'{"cmd":"pi')
    emu.process_input()
    assert emu.read_line(0.01) is None
    emu.feed(b'ng"}\n{"cmd":"hello"}\n')
    emu.process_input()
    first = json.loads(emu.read_line(0.1) or b"{}")
    second = json.loads(emu.read_line(0.1) or b"{}")
    assert "pong" in first and second["ok"] == "hello"


def test_feetech_driver_ranges(clock: Clock) -> None:
    emu = ESP32Emulator(["index"], [0.0], driver="feetech", clock=clock, autostart=False)
    assert emu.cal[0]["min"] == 200 and emu.cal[0]["max"] == 800
    assert send(emu, {"cmd": "hello"})[0]["driver"] == "feetech"
    assert send(emu, {"cmd": "cal_set", "joint": "index", "max": 2000})[0]["err"] == "bad_json"
    with pytest.raises(ValueError):
        ESP32Emulator(["x"], driver="dynamixel", autostart=False)
