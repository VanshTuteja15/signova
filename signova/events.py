"""In-process event bus feeding the dashboard WebSocket.

Event types: status, transcript, gloss, step, done, error, telemetry, eval.
Every event is a dict {"type", "seq", "ts", ...payload}. `publish` is safe to call from
other threads (the serial reader thread): it hops onto the bound asyncio loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections import deque
from typing import Any

EVENT_TYPES = ("status", "transcript", "gloss", "step", "done", "error", "telemetry", "eval")


class EventBus:
    def __init__(self, history: int = 500, queue_size: int = 2000) -> None:
        self._subs: set[asyncio.Queue[dict[str, Any]]] = set()
        self._history: deque[dict[str, Any]] = deque(maxlen=history)
        self._seq = 0
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue_size = queue_size

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop or asyncio.get_running_loop()

    def publish(self, type_: str, **data: Any) -> dict[str, Any]:
        if type_ not in EVENT_TYPES:
            raise ValueError(f"unknown event type {type_!r}")
        with self._lock:
            self._seq += 1
            event = {"type": type_, "seq": self._seq, "ts": round(time.time(), 3), **data}
            self._history.append(event)
        loop = self._loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop is not None and running is not loop:
            with contextlib.suppress(RuntimeError):  # loop closed during shutdown
                loop.call_soon_threadsafe(self._fanout, event)
        else:
            self._fanout(event)
        return event

    def _fanout(self, event: dict[str, Any]) -> None:
        for q in list(self._subs):
            if q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()  # drop the oldest event for slow clients
            q.put_nowait(event)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subs.discard(q)

    def history(self, *types: str) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._history)
        return [e for e in events if not types or e["type"] in types]

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()
