from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from typing import Any

import pytest

from signova.config import HandConfig
from signova.events import EventBus
from signova.gloss import rule_gloss
from signova.library import Library
from signova.performer import Performer, RunMeta
from signova.sequencer import Sequencer
from signova.transport import SimTransport, TransportError, TransportTimeout


class FailingTransport(SimTransport):
    def __init__(self, *a: Any, exc: Exception, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.exc = exc

    async def send_pose(
        self, pose_id: str, vector: list[float], ms: int, timeout_s: float | None = None
    ) -> Any:
        raise self.exc


@pytest.fixture
def setup(fast_hand: HandConfig, library: Library, tmp_path: Path):
    bus = EventBus()
    lines: list[tuple[str, str]] = []
    transport = SimTransport(
        fast_hand.joint_names, library.rest_vector(), on_line=lambda d, t: lines.append((d, t))
    )
    performer = Performer(transport, fast_hand, bus, library.rest_vector(), log_dir=tmp_path / "logs")
    seq = Sequencer(library, fast_hand.timing)
    return bus, transport, performer, seq, lines, tmp_path


async def test_happy_path_event_order(setup, library: Library) -> None:
    bus, transport, performer, seq, lines, tmp_path = setup
    await transport.connect()
    steps = seq.build(rule_gloss("I love you, so", library).items)
    done = await performer.perform(steps, RunMeta(source="say", engine="rules"))
    assert done["ok"] is True and done["steps"] == 3
    types = [e["type"] for e in bus.history("status", "step", "done")]
    assert types == ["status", "step", "step", "step", "done"]
    step_events = bus.history("step")
    assert [e["sign_id"] for e in step_events] == ["ILY", "S", "O"]
    assert step_events[0]["pose"]["middle"] == 1.0
    assert performer.current == steps[-1].pose_vector
    latency = bus.history("telemetry")[0]
    assert latency["kind"] == "latency" and latency["first_pose_ms"] >= 0
    assert performer.latency_stats()["count"] == 1
    # the sim transport logs protocol lines
    assert any('"cmd":"pose"' in t for d, t in lines if d == "tx")
    assert any('"done"' in t for d, t in lines if d == "rx")


async def test_run_log_has_numbers_not_text(setup, library: Library) -> None:
    bus, transport, performer, seq, lines, tmp_path = setup
    await performer.perform(seq.build(rule_gloss("I love you", library).items), RunMeta(engine="rules"))
    rows = list(csv.DictReader((tmp_path / "logs" / "runs.csv").open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["ok"] == "True" and rows[0]["engine"] == "rules"
    assert "love" not in (tmp_path / "logs" / "runs.csv").read_text(encoding="utf-8").lower()


async def test_stop_cancels_run(setup, library: Library, fast_hand: HandConfig) -> None:
    bus, transport, performer, seq, lines, tmp_path = setup
    steps = seq.build(rule_gloss("I love you", library).items)
    steps[0].hold_ms = 5000
    await performer.start(steps)
    await asyncio.sleep(0.1)
    assert performer.running
    result = await performer.stop()
    assert result["cancelled"] is True
    assert not performer.running
    done = bus.history("done")
    assert len(done) == 1 and done[0]["ok"] is False and done[0]["reason"] == "stopped"
    assert any('"cmd":"stop"' in t for _, t in lines)


async def test_new_run_replaces_old(setup, library: Library) -> None:
    bus, transport, performer, seq, lines, tmp_path = setup
    long_steps = seq.build(rule_gloss("I love you", library).items)
    long_steps[0].hold_ms = 5000
    await performer.start(long_steps)
    await asyncio.sleep(0.05)
    done = await performer.perform(seq.build(rule_gloss("so", library).items))
    assert done["ok"] is True
    reasons = [e["reason"] for e in bus.history("done")]
    assert reasons == ["replaced by a new request", "finished"]


async def test_relax_returns_to_rest(setup, library: Library) -> None:
    bus, transport, performer, seq, lines, tmp_path = setup
    await performer.perform(seq.build(rule_gloss("I love you", library).items))
    await performer.relax()
    assert performer.current == library.rest_vector()
    assert transport.relaxed
    assert bus.history("status")[-1]["state"] == "relaxed"


@pytest.mark.parametrize(
    ("exc", "reason"),
    [(TransportTimeout("hand did not confirm"), "timeout"), (TransportError("port gone"), "transport error")],
)
async def test_transport_failures_end_run(fast_hand: HandConfig, library: Library, exc, reason) -> None:
    bus = EventBus()
    t = FailingTransport(fast_hand.joint_names, library.rest_vector(), exc=exc)
    performer = Performer(t, fast_hand, bus, library.rest_vector())
    done = await performer.perform(
        Sequencer(library, fast_hand.timing).build(rule_gloss("so", library).items)
    )
    assert done["ok"] is False and done["reason"] == reason
    assert bus.history("error")[0]["message"] == str(exc)
    assert len(bus.history("done")) == 1


async def test_stop_with_failing_transport_reports_error(fast_hand: HandConfig, library: Library) -> None:
    class BadStop(SimTransport):
        async def stop(self) -> Any:
            raise TransportError("not connected")

    bus = EventBus()
    performer = Performer(BadStop(fast_hand.joint_names), fast_hand, bus, library.rest_vector())
    result = await performer.stop()
    assert result["cancelled"] is False
    assert bus.history("error")[0]["message"] == "not connected"


async def test_live_pose_clamps(setup) -> None:
    bus, transport, performer, seq, lines, tmp_path = setup
    await performer.live_pose([2.0, -1, 0.5, 0.5, 0.5, 0.5, 0.5], ms=5)
    assert performer.current[0] == 1.0 and performer.current[1] == 0.0


async def test_empty_run_finishes(setup) -> None:
    bus, transport, performer, seq, lines, tmp_path = setup
    done = await performer.perform([])
    assert done["ok"] is True and done["steps"] == 0 and done["first_pose_ms"] is None


async def test_wait_without_run(setup) -> None:
    performer = setup[2]
    assert await performer.wait() is None
    assert await performer.cancel() is False
