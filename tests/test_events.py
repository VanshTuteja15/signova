from __future__ import annotations

import asyncio
import threading

import pytest

from signova.events import EventBus
from signova.transport import SimTransport, TransportError


async def test_publish_and_subscribe() -> None:
    bus = EventBus()
    bus.bind_loop()
    q = bus.subscribe()
    ev = bus.publish("status", state="idle")
    assert ev["seq"] == 1 and ev["type"] == "status"
    assert (await asyncio.wait_for(q.get(), 1))["state"] == "idle"
    bus.unsubscribe(q)
    bus.publish("status", state="x")
    assert q.empty()


async def test_publish_from_thread() -> None:
    bus = EventBus()
    bus.bind_loop()
    q = bus.subscribe()
    t = threading.Thread(target=lambda: bus.publish("telemetry", kind="serial", line="hi"))
    t.start()
    t.join()
    ev = await asyncio.wait_for(q.get(), 1)
    assert ev["line"] == "hi"


async def test_slow_subscriber_drops_oldest() -> None:
    bus = EventBus(queue_size=3)
    q = bus.subscribe()
    for i in range(5):
        bus.publish("telemetry", n=i)
    assert [q.get_nowait()["n"] for _ in range(3)] == [2, 3, 4]


def test_unknown_type_rejected() -> None:
    with pytest.raises(ValueError):
        EventBus().publish("bogus")


def test_history_filter_and_clear() -> None:
    bus = EventBus(history=3)
    for t in ("status", "step", "done", "step"):
        bus.publish(t)
    assert [e["type"] for e in bus.history()] == ["step", "done", "step"]
    assert len(bus.history("step")) == 2
    bus.clear_history()
    assert bus.history() == []


async def test_sim_transport_protocol_shapes() -> None:
    lines: list[tuple[str, str]] = []
    t = SimTransport(["a", "b"], [0.1, 0.2], on_line=lambda d, s: lines.append((d, s)))
    await t.connect()
    assert t.status()["connected"] and t.status()["mode"] == "sim"
    hello = await t.hello()
    assert hello["joints"] == ["a", "b"]
    assert (await t.send_pose("X.1", [0.5, 2.0], 0))["done"] == "X.1"
    assert t.current == [0.5, 1.0]
    with pytest.raises(TransportError):
        await t.send_pose("X.2", [0.5], 0)
    cal = await t.cal_get()
    assert cal[1]["joint"] == "b" and cal[1]["rest"] == 0.2
    await t.cal_set("a", 500, 2400, True, 0.3)
    assert (await t.cal_get())[0]["inv"] is True
    with pytest.raises(TransportError):
        await t.cal_set("zz", 500, 2400, False, 0.3)
    with pytest.raises(TransportError):
        await t.cal_set("a", 2400, 500, False, 0.3)
    assert (await t.raw("a", 1500))["ok"] == "raw"
    with pytest.raises(TransportError):
        await t.raw("zz", 1500)
    assert "pong" in await t.ping()
    await t.relax()
    assert t.current == [0.1, 0.2] and t.status()["relaxed"]
    await t.stop()
    await t.close()
    assert not t.connected
    # a failing logger must never break motion
    t.on_line = lambda d, s: 1 / 0
    await t.send_pose("Y", [0, 0], 0)
