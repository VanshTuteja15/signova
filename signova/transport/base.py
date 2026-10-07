"""Common async transport interface: sim, emulator and ESP32-over-USB all look the same.

All joint values are normalised 0..1 in the hand's joint order. Calibration values
(min/max microseconds or servo counts) only exist on the device side.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

LineCallback = Callable[[str, str], None]
"""on_line(direction, text): direction is "tx", "rx" or "sys". Used for the dashboard serial log."""


class TransportError(Exception):
    """A transport problem with a user-facing message."""


class TransportTimeout(TransportError):
    """The hand did not confirm a command in time."""


class Transport(ABC):
    mode: str = "base"

    def __init__(self, joints: list[str], on_line: LineCallback | None = None) -> None:
        self.joints = list(joints)
        self.on_line = on_line
        self.connected = False
        self.detail = ""

    def _log(self, direction: str, text: str) -> None:
        if self.on_line is not None:
            try:
                self.on_line(direction, text)
            except Exception:  # logging must never break motion
                pass

    def _check_vector(self, vector: list[float]) -> list[float]:
        if len(vector) != len(self.joints):
            raise TransportError(f"pose has {len(vector)} values but the hand has {len(self.joints)} joints")
        return [max(0.0, min(1.0, float(v))) for v in vector]

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def hello(self) -> dict[str, Any]: ...

    @abstractmethod
    async def send_pose(
        self, pose_id: str, vector: list[float], ms: int, timeout_s: float | None = None
    ) -> dict[str, Any]:
        """Send a pose and wait until the hand reports it is done (or a newer pose replaced it)."""

    @abstractmethod
    async def stop(self) -> dict[str, Any]: ...

    @abstractmethod
    async def relax(self) -> dict[str, Any]: ...

    @abstractmethod
    async def ping(self) -> dict[str, Any]: ...

    @abstractmethod
    async def cal_get(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def cal_set(
        self, joint: str, min_us: int, max_us: int, inv: bool, rest: float
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def raw(self, joint: str, value: int) -> dict[str, Any]: ...

    def status(self) -> dict[str, Any]:
        return {"mode": self.mode, "connected": self.connected, "detail": self.detail, "joints": self.joints}
