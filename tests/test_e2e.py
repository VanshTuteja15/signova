"""Headless end-to-end: real uvicorn server in sim mode, real WebSocket client, real HTTP."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
import websockets

from signova.config import HandConfig
from signova.library import Library
from signova.server import create_app


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def server(fast_hand: HandConfig, library: Library, tmp_path: Path):
    app = create_app(
        fast_hand, library, data_dir=tmp_path / "data", models_dir=tmp_path / "models", mode="sim"
    )
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", ws="auto"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not srv.started:
        if time.time() > deadline:
            raise RuntimeError("server did not start")
        time.sleep(0.05)
    yield f"127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=5)


async def test_say_i_love_you_streams_events_in_order(server: str) -> None:
    async with websockets.connect(f"ws://{server}/ws") as ws:
        hello = json.loads(await asyncio.wait_for(ws.recv(), 5))
        assert hello["type"] == "status" and hello["state"] == "connected"
        assert hello["transport"]["mode"] == "sim"

        async with httpx.AsyncClient() as http:
            r = await http.post(f"http://{server}/api/say", json={"text": "I love you"})
            assert r.status_code == 200
            assert [i["id"] for i in r.json()["gloss"]["items"]] == ["ILY"]

        events = []
        while True:
            ev = json.loads(await asyncio.wait_for(ws.recv(), 5))
            events.append(ev)
            if ev["type"] == "done":
                break

    order = [e["type"] for e in events if e["type"] in ("transcript", "gloss", "step", "done")]
    assert order == ["transcript", "gloss", "step", "done"]
    assert events[-1]["ok"] is True
    step = next(e for e in events if e["type"] == "step")
    assert step["sign_id"] == "ILY"
    latency = next(e for e in events if e["type"] == "telemetry" and e.get("kind") == "latency")
    assert latency["first_pose_ms"] < 3000  # the plan's target: hand moves within 3 s
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)


async def test_dashboard_and_state_over_http(server: str) -> None:
    async with httpx.AsyncClient() as http:
        page = await http.get(f"http://{server}/")
        assert page.status_code == 200 and "SIGNOVA" in page.text
        st = (await http.get(f"http://{server}/api/state")).json()
        assert st["transport"]["mode"] == "sim"
