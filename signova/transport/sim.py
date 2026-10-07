"""Simulation transport: no hardware. Waits `ms` per pose and reports done.

It still writes the protocol JSON lines to the serial log so the dashboard looks the same
as with a real hand.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from .base import LineCallback, Transport, TransportError

DEFAULT_CAL = {"min": 600, "max": 2300, "inv": False}


class SimTransport(Transport):
    mode = "sim"

    def __init__(
        self, joints: list[str], rest: list[float] | None = None, on_line: LineCallback | None = None
    ) -> None:
        super().__init__(joints, on_line)
        self.rest = list(rest) if rest else [0.0] * len(joints)
        self.current = list(self.rest)
        self.relaxed = False
        self._t0 = time.monotonic()
        self.cal = [
            {"joint": j, "ch": i, **DEFAULT_CAL, "rest": self.rest[i]} for i, j in enumerate(self.joints)
        ]

    def _millis(self) -> int:
        return int((time.monotonic() - self._t0) * 1000)

    def _tx(self, obj: dict[str, Any]) -> None:
        self._log("tx", json.dumps(obj, separators=(",", ":")))

    def _rx(self, obj: dict[str, Any]) -> dict[str, Any]:
        self._log("rx", json.dumps(obj, separators=(",", ":")))
        return obj

    async def connect(self) -> None:
        self.connected = True
        self.detail = "simulation (no hardware)"
        self._log("sys", "# simulation mode: no hardware attached")

    async def close(self) -> None:
        self.connected = False

    async def hello(self) -> dict[str, Any]:
        self._tx({"cmd": "hello"})
        return self._rx({"ok": "hello", "fw": "sim", "driver": "sim", "joints": self.joints})

    async def send_pose(
        self, pose_id: str, vector: list[float], ms: int, timeout_s: float | None = None
    ) -> dict[str, Any]:
        vec = self._check_vector(vector)
        ms = max(0, int(ms))
        self._tx({"cmd": "pose", "id": pose_id, "j": [round(v, 3) for v in vec], "ms": ms})
        self.relaxed = False
        await asyncio.sleep(ms / 1000)
        self.current = vec
        return self._rx({"done": pose_id, "t": self._millis()})

    async def stop(self) -> dict[str, Any]:
        self._tx({"cmd": "stop"})
        return self._rx({"ok": "stop"})

    async def relax(self) -> dict[str, Any]:
        self._tx({"cmd": "relax"})
        self.current = list(self.rest)
        self.relaxed = True
        return self._rx({"ok": "relax"})

    async def ping(self) -> dict[str, Any]:
        return {"pong": self._millis()}

    async def cal_get(self) -> list[dict[str, Any]]:
        self._tx({"cmd": "cal_get"})
        cal = [dict(c) for c in self.cal]
        self._rx({"cal": cal})
        return cal

    async def cal_set(self, joint: str, min_us: int, max_us: int, inv: bool, rest: float) -> dict[str, Any]:
        self._tx({"cmd": "cal_set", "joint": joint, "min": min_us, "max": max_us, "inv": inv, "rest": rest})
        entry = next((c for c in self.cal if c["joint"] == joint), None)
        if entry is None:
            raise TransportError(f"unknown joint {joint!r}")
        if not (min_us < max_us):
            raise TransportError("min must be below max")
        entry.update({"min": int(min_us), "max": int(max_us), "inv": bool(inv), "rest": float(rest)})
        return self._rx({"ok": "cal_set"})

    async def raw(self, joint: str, value: int) -> dict[str, Any]:
        if joint not in self.joints:
            raise TransportError(f"unknown joint {joint!r}")
        self._tx({"cmd": "raw", "joint": joint, "us": int(value)})
        return self._rx({"ok": "raw"})

    def status(self) -> dict[str, Any]:
        return {**super().status(), "firmware": "sim", "driver": "sim", "relaxed": self.relaxed}
