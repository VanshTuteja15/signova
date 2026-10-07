"""Performer: runs sequencer steps on a transport as one cancellable asyncio task.

For each step it publishes a `step` event (the dashboard's 3D hand animates the same pose
with the same timing), sends the pose, waits for the hand's `done` (timeout = move_ms +
done_timeout_extra_ms), then holds. A run always ends with exactly one `done` event:
ok=true, or ok=false with a reason (stopped, timeout, transport error).

End-to-end latency = time from the run's origin (end of speech, or request arrival for
typed text) to the moment the first pose is handed to the transport.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import itertools
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import HandConfig
from .events import EventBus
from .sequencer import Step, total_duration_ms
from .transport.base import Transport, TransportError, TransportTimeout

RUN_LOG_FIELDS = [
    "timestamp",
    "run_id",
    "source",
    "engine",
    "stt_ms",
    "gloss_ms",
    "first_pose_ms",
    "total_ms",
    "steps",
    "ok",
    "reason",
]


@dataclass
class RunMeta:
    source: str = "say"  # say | transcribe | preview | eval | studio
    engine: str = ""
    origin: float = field(default_factory=time.perf_counter)
    stt_ms: float | None = None
    gloss_ms: float | None = None
    mask: bool = False  # evaluation: hide sign ids in events and pose ids so nothing gives the answer away


class Performer:
    def __init__(
        self,
        transport: Transport,
        hand: HandConfig,
        bus: EventBus,
        rest_vector: list[float],
        log_dir: Path | None = None,
    ) -> None:
        self.transport = transport
        self.hand = hand
        self.bus = bus
        self.rest_vector = list(rest_vector)
        self.current = list(rest_vector)
        self.log_dir = log_dir
        self.latencies: deque[float] = deque(maxlen=200)
        self._task: asyncio.Task[dict[str, Any]] | None = None
        self._run_ids = itertools.count(1)
        self._pose_ids = itertools.count(1)
        self.run_id = 0
        self.current_step: Step | None = None

    # ------------------------------------------------------------------ state
    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def latency_stats(self) -> dict[str, Any]:
        vals = list(self.latencies)
        if not vals:
            return {"count": 0, "median_ms": None, "last_ms": None}
        return {
            "count": len(vals),
            "median_ms": round(statistics.median(vals), 1),
            "last_ms": round(vals[-1], 1),
        }

    def _pose_id(self, sign_id: str) -> str:
        return f"{sign_id[:10]}.{next(self._pose_ids) % 10000}"

    # ------------------------------------------------------------------ runs
    async def start(self, steps: list[Step], meta: RunMeta | None = None) -> int:
        """Cancel any current run and start a new one in the background. Returns the run id."""
        await self.cancel(reason="replaced by a new request")
        run_id = next(self._run_ids)
        self.run_id = run_id
        self._task = asyncio.create_task(self._run(run_id, steps, meta or RunMeta()))
        return run_id

    async def perform(self, steps: list[Step], meta: RunMeta | None = None) -> dict[str, Any]:
        """Start a run and wait for it to finish. Returns the `done` payload."""
        await self.start(steps, meta)
        result = await self.wait()
        return result if result is not None else {"ok": False, "reason": "stopped"}

    async def wait(self) -> dict[str, Any] | None:
        """Wait for the current run. Returns its `done` payload, or None if it was cancelled."""
        task = self._task
        if task is None:
            return None
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                return None
            raise  # the caller itself was cancelled

    async def cancel(self, reason: str = "stopped") -> bool:
        task = self._task
        if task is None or task.done():
            return False
        task.cancel(reason)
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        return True

    async def stop(self) -> dict[str, Any]:
        cancelled = await self.cancel("stopped")
        try:
            reply = await self.transport.stop()
        except TransportError as exc:
            self.bus.publish("error", source="transport", message=str(exc))
            reply = {"err": str(exc)}
        self.bus.publish("status", state="stopped", detail="Stopped. The hand holds its current position.")
        return {"cancelled": cancelled, "reply": reply}

    async def relax(self) -> dict[str, Any]:
        cancelled = await self.cancel("relaxed")
        try:
            reply = await self.transport.relax()
        except TransportError as exc:
            self.bus.publish("error", source="transport", message=str(exc))
            reply = {"err": str(exc)}
        self.current = list(self.rest_vector)
        self.bus.publish(
            "status", state="relaxed", detail="Relaxed: hand at rest, servo output off.", pose=self.current
        )
        return {"cancelled": cancelled, "reply": reply}

    async def live_pose(self, vector: list[float], ms: int = 60) -> dict[str, Any]:
        """Pose Studio: send one pose immediately (cancels any running sequence)."""
        await self.cancel("live pose")
        vec = [self.hand.clamp(j, v) for j, v in zip(self.hand.joint_names, vector, strict=True)]
        extra = self.hand.timing.done_timeout_extra_ms
        reply = await self.transport.send_pose(self._pose_id("LIVE"), vec, ms, timeout_s=(ms + extra) / 1000)
        self.current = vec
        return reply

    async def _run(self, run_id: int, steps: list[Step], meta: RunMeta) -> dict[str, Any]:
        t_start = time.perf_counter()
        first_pose_ms: float | None = None
        extra = self.hand.timing.done_timeout_extra_ms
        self.bus.publish(
            "status",
            state="performing",
            run_id=run_id,
            steps=len(steps),
            planned_ms=total_duration_ms(steps),
            detail=f"Performing {len(steps)} step{'s' if len(steps) != 1 else ''}",
        )
        done: dict[str, Any]
        try:
            for step in steps:
                self.current_step = step
                payload = step.to_dict()
                payload["pose"] = dict(zip(self.hand.joint_names, step.pose_vector, strict=True))
                if meta.mask:
                    payload.update(sign_id="?", label="?", context="evaluation")
                self.bus.publish("step", run_id=run_id, total=len(steps), **payload)
                if first_pose_ms is None:
                    first_pose_ms = (time.perf_counter() - meta.origin) * 1000
                    self.latencies.append(first_pose_ms)
                    self.bus.publish(
                        "telemetry",
                        kind="latency",
                        run_id=run_id,
                        first_pose_ms=round(first_pose_ms, 1),
                        stt_ms=meta.stt_ms,
                        gloss_ms=meta.gloss_ms,
                        **{"median_ms": self.latency_stats()["median_ms"]},
                    )
                await self.transport.send_pose(
                    self._pose_id("EVAL" if meta.mask else step.sign_id),
                    step.pose_vector,
                    step.move_ms,
                    timeout_s=(step.move_ms + extra) / 1000,
                )
                self.current = list(step.pose_vector)
                if step.hold_ms > 0:
                    await asyncio.sleep(step.hold_ms / 1000)
            done = {"ok": True, "reason": "finished"}
        except asyncio.CancelledError as exc:
            reason = str(exc.args[0]) if exc.args else "stopped"
            done = {"ok": False, "reason": reason}
            self._finish(run_id, steps, meta, t_start, first_pose_ms, done)
            raise
        except TransportTimeout as exc:
            self.bus.publish("error", source="transport", message=str(exc))
            done = {"ok": False, "reason": "timeout", "message": str(exc)}
        except TransportError as exc:
            self.bus.publish("error", source="transport", message=str(exc))
            done = {"ok": False, "reason": "transport error", "message": str(exc)}
        self._finish(run_id, steps, meta, t_start, first_pose_ms, done)
        return done

    def _finish(
        self,
        run_id: int,
        steps: list[Step],
        meta: RunMeta,
        t_start: float,
        first_pose_ms: float | None,
        done: dict[str, Any],
    ) -> None:
        self.current_step = None
        total_ms = round((time.perf_counter() - t_start) * 1000, 1)
        done.update(
            run_id=run_id,
            steps=len(steps),
            total_ms=total_ms,
            first_pose_ms=round(first_pose_ms, 1) if first_pose_ms is not None else None,
            source=meta.source,
        )
        self.bus.publish("done", **done)
        self._log_run(meta, done)

    def _log_run(self, meta: RunMeta, done: dict[str, Any]) -> None:
        """Append timing numbers (never the text) to data/logs/runs.csv for the evaluation report."""
        if self.log_dir is None:
            return
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            path = self.log_dir / "runs.csv"
            new = not path.exists()
            with path.open("a", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=RUN_LOG_FIELDS)
                if new:
                    w.writeheader()
                w.writerow(
                    {
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                        "run_id": done.get("run_id"),
                        "source": meta.source,
                        "engine": meta.engine,
                        "stt_ms": meta.stt_ms if meta.stt_ms is not None else "",
                        "gloss_ms": meta.gloss_ms if meta.gloss_ms is not None else "",
                        "first_pose_ms": done.get("first_pose_ms")
                        if done.get("first_pose_ms") is not None
                        else "",
                        "total_ms": done.get("total_ms"),
                        "steps": done.get("steps"),
                        "ok": done.get("ok"),
                        "reason": done.get("reason"),
                    }
                )
        except OSError:
            pass  # logging must never break a demo
