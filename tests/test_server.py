"""Every REST endpoint and the WebSocket, through FastAPI's TestClient (sim mode, no hardware)."""

from __future__ import annotations

import csv
import io
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import serial
from fastapi.testclient import TestClient

from signova.config import HandConfig
from signova.library import Library
from signova.server import create_app


class FakeSpeech:
    """Stands in for faster-whisper / Vosk so server tests don't load models."""

    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str]] = []

    def status(self) -> dict[str, Any]:
        return {
            "whisper": {"installed": True, "loaded": False},
            "vosk": {"installed": True, "model_present": False},
        }

    async def transcribe(self, audio: bytes, engine: str = "whisper") -> tuple[str, float]:
        self.calls.append((audio, engine))
        if engine == "vosk":
            raise RuntimeError("Vosk model missing: run `signova download-models`")
        if audio == b"garbage":
            raise ValueError("cannot decode")
        if audio == b"silence":
            return "", 5.0
        return "I love you", 12.5


class FakeClaude:
    def __init__(self, payload: dict[str, Any]) -> None:
        async def create(**kwargs: Any) -> Any:
            return SimpleNamespace(
                stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(payload))]
            )

        self.messages = SimpleNamespace(create=create)


def make_client(hand: HandConfig, library: Library, tmp_path: Path, **kw: Any) -> TestClient:
    opts: dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "models_dir": tmp_path / "models",
        "mode": "sim",
        "speech": FakeSpeech(),
        "serve_dashboard": False,
    }
    opts.update(kw)
    return TestClient(create_app(hand, library, **opts))


@pytest.fixture
def client(fast_hand: HandConfig, library: Library, tmp_path: Path):
    with make_client(fast_hand, library, tmp_path) as c:
        yield c


def sig(client: TestClient):
    return client.app.state.signova  # type: ignore[attr-defined]


def wait_done(client: TestClient, count: int = 1, timeout: float = 5.0) -> list[dict[str, Any]]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        done = sig(client).bus.history("done")
        if len(done) >= count and not sig(client).performer.running:
            return done
        time.sleep(0.02)
    raise AssertionError("run did not finish")


# ---------------------------------------------------------------------------- state
def test_health_and_state(client: TestClient) -> None:
    assert client.get("/api/health").json()["ok"] is True
    st = client.get("/api/state").json()
    assert "Not a translator" in st["disclaimer"]
    assert [j["name"] for j in st["hand"]["joints"]][:2] == ["thumb", "thumb_rot"]
    lib = {s["id"]: s for s in st["library"]}
    assert lib["U"]["available"] is False and lib["A"]["draft"] is True
    assert st["transport"]["mode"] == "sim" and st["transport"]["simulation"] is True
    assert st["claude"]["available"] is False
    assert "whisper" in st["speech"]
    assert st["settings"] == {"save_recordings": False, "speed": 1.0}


def test_settings(client: TestClient) -> None:
    assert client.post("/api/settings", json={"speed": 9, "save_recordings": True}).json() == {
        "save_recordings": True,
        "speed": 2.0,
    }


# ---------------------------------------------------------------------------- say
def test_say_i_love_you(client: TestClient) -> None:
    r = client.post("/api/say", json={"text": "I love you"})
    assert r.status_code == 200
    body = r.json()
    assert [i["id"] for i in body["gloss"]["items"]] == ["ILY"]
    assert body["steps"] == 1
    done = wait_done(client)
    assert done[-1]["ok"] is True
    types = [e["type"] for e in sig(client).bus.history("transcript", "gloss", "step", "done")]
    assert types == ["transcript", "gloss", "step", "done"]
    step = sig(client).bus.history("step")[0]
    assert step["sign_id"] == "ILY" and step["pose"]["middle"] == 1.0


def test_say_hello(client: TestClient) -> None:
    body = client.post("/api/say", json={"text": "Hello!"}).json()
    assert [i["id"] for i in body["gloss"]["items"]] == ["HELLO"]
    assert body["steps"] == 2
    assert wait_done(client)[-1]["ok"] is True


def test_library_file_edits_apply_without_restart(client: TestClient, library_path: Path) -> None:
    import os

    assert client.post("/api/say", json={"text": "howdy"}).json()["gloss"]["items"][0]["type"] != "sign"
    wait_done(client)
    text = library_path.read_text(encoding="utf-8").replace('"hi", "hey"', '"hi", "hey", "howdy"')
    library_path.write_text(text, encoding="utf-8")
    st = library_path.stat()
    os.utime(library_path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    body = client.post("/api/say", json={"text": "howdy"}).json()
    assert [i["id"] for i in body["gloss"]["items"]] == ["HELLO"]
    wait_done(client)


def test_say_code_is_so_cool(client: TestClient) -> None:
    body = client.post("/api/say", json={"text": "code is so cool"}).json()
    items = body["gloss"]["items"]
    assert [(i["type"], i["word"]) for i in items] == [
        ("fs", "code"),
        ("drop", "is"),
        ("fs", "so"),
        ("fs", "cool"),
    ]
    assert body["steps"] == 11  # C O D E S O C O (bounce) O L
    wait_done(client)
    letters = [e["sign_id"] for e in sig(client).bus.history("step") if e["kind"] == "letter"]
    assert "".join(letters) == "CODESOCOOL"


def test_say_claude_without_key_falls_back(client: TestClient) -> None:
    body = client.post("/api/say", json={"text": "I love you", "engine": "claude"}).json()
    assert body["gloss"]["engine"] == "rules" and body["gloss"]["fallback"] is True
    assert "ANTHROPIC_API_KEY" in body["gloss"]["note"]


def test_say_with_mocked_claude(fast_hand: HandConfig, library: Library, tmp_path: Path) -> None:
    payload = {
        "items": [
            {"type": "sign", "id": "ILY", "word": "I love you", "reason": ""},
            {"type": "sign", "id": "MADEUP", "word": "x", "reason": ""},
        ],
        "note": "ok",
    }
    with make_client(fast_hand, library, tmp_path, claude_client=FakeClaude(payload)) as c:
        assert c.get("/api/state").json()["claude"]["available"] is True
        body = c.post("/api/say", json={"text": "I love you x", "engine": "claude"}).json()
        assert body["gloss"]["engine"] == "claude"
        assert body["gloss"]["rejected"] == 1
        assert body["steps"] == 1


def test_say_empty_and_too_long(client: TestClient) -> None:
    body = client.post("/api/say", json={"text": "   "}).json()
    assert body["steps"] == 0
    assert wait_done(client)[-1]["ok"] is True
    assert client.post("/api/say", json={"text": "x" * 501}).status_code == 422
    assert client.post("/api/say", json={"text": "hi", "engine": "gpt"}).status_code == 422


# ---------------------------------------------------------------------------- transcribe
def test_transcribe_auto_sign(client: TestClient) -> None:
    r = client.post(
        "/api/transcribe",
        files={"audio": ("clip.webm", b"fake-webm-bytes", "audio/webm")},
        data={"engine": "whisper", "auto_sign": "true", "t_end": str(time.time() * 1000)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "I love you" and body["stt_ms"] == 12.5
    assert [i["id"] for i in body["gloss"]["items"]] == ["ILY"]
    wait_done(client)
    transcript = sig(client).bus.history("transcript")[-1]
    assert transcript["source"] == "mic" and transcript["engine"] == "whisper"
    assert not (Path(sig(client).data_dir) / "recordings").exists()  # not saved by default


def test_transcribe_without_signing_and_saving(client: TestClient) -> None:
    client.post("/api/settings", json={"save_recordings": True})
    body = client.post(
        "/api/transcribe",
        files={"audio": ("clip.webm", b"abc", "audio/webm")},
        data={"auto_sign": "false"},
    ).json()
    assert body["text"] == "I love you" and "gloss" not in body and body["saved"] is True
    saved = list((Path(sig(client).data_dir) / "recordings").glob("*.webm"))
    assert len(saved) == 1


def test_transcribe_errors(client: TestClient) -> None:
    empty = client.post("/api/transcribe", files={"audio": ("a.webm", b"", "audio/webm")})
    assert empty.status_code == 400
    vosk = client.post(
        "/api/transcribe", files={"audio": ("a.webm", b"x", "audio/webm")}, data={"engine": "vosk"}
    )
    assert vosk.status_code == 503 and "download-models" in vosk.json()["detail"]
    bad = client.post("/api/transcribe", files={"audio": ("a.webm", b"garbage", "audio/webm")})
    assert bad.status_code == 400 and "type the sentence" in bad.json()["detail"]
    silent = client.post("/api/transcribe", files={"audio": ("a.webm", b"silence", "audio/webm")}).json()
    assert silent["text"] == "" and silent["note"] == "No speech recognised."


# ---------------------------------------------------------------------------- motion
def test_preview_sign(client: TestClient) -> None:
    assert client.post("/api/sign/ily").json()["sign_id"] == "ILY"
    wait_done(client)
    assert client.post("/api/sign/U").status_code == 409
    assert client.post("/api/sign/NOPE").status_code == 404


def test_live_pose(client: TestClient) -> None:
    r = client.post("/api/pose", json={"pose": {"index": 1.0}, "ms": 10})
    assert r.status_code == 200
    pose = r.json()["pose"]
    assert pose["index"] == 1.0 and pose["thumb"] == 0.15  # others keep their current value
    r = client.post("/api/pose", json={"vector": [0, 0, 0, 0, 0, 0, 2], "ms": 10})
    assert r.json()["pose"]["wrist"] == 1.0
    assert client.post("/api/pose", json={"vector": [0, 0]}).status_code == 422
    assert client.post("/api/pose", json={"pose": {"elbow": 1}}).status_code == 422


def test_stop_and_relax(client: TestClient) -> None:
    client.post("/api/say", json={"text": "code is so cool"})
    r = client.post("/api/stop")
    assert r.status_code == 200
    assert client.post("/api/relax").status_code == 200
    states = [e.get("state") for e in sig(client).bus.history("status")]
    assert "stopped" in states and "relaxed" in states


# ---------------------------------------------------------------------------- library
def test_library_create_use_delete(client: TestClient) -> None:
    body = {
        "kind": "word",
        "tier": 2,
        "english": ["rock on"],
        "notes": "made in Pose Studio",
        "frames": [{"pose": {"index": 0.0, "middle": 1.0, "ring": 1.0, "pinky": 0.0}, "hold_ms": 600}],
    }
    r = client.put("/api/library/rock", json=body)
    assert r.status_code == 200, r.text
    sign = r.json()["sign"]
    assert sign["id"] == "ROCK" and sign["available"] is True
    assert set(sign["requires"]) == {"index", "middle", "ring", "pinky"}
    say = client.post("/api/say", json={"text": "rock on"}).json()
    assert [i["id"] for i in say["gloss"]["items"]] == ["ROCK"]
    wait_done(client)
    assert any(s["id"] == "ROCK" for s in client.get("/api/library").json())
    assert client.delete("/api/library/rock").json() == {"deleted": "ROCK"}
    assert client.delete("/api/library/rock").status_code == 404


def test_library_validation(client: TestClient) -> None:
    bad_tier = {"kind": "word", "tier": 9, "frames": [{"pose": {}}]}
    assert client.put("/api/library/X", json=bad_tier).status_code == 422
    dup = {"kind": "word", "tier": 1, "english": ["love you"], "frames": [{"pose": {}}]}
    r = client.put("/api/library/LOVE2", json=dup)
    assert r.status_code == 422 and "used by both" in r.json()["detail"]
    bad_id = {"kind": "word", "tier": 1, "frames": [{"pose": {}}]}
    assert client.put("/api/library/bad id!", json=bad_id).status_code == 422


def test_validated_by_signer_gets_date(client: TestClient) -> None:
    body = {
        "kind": "letter",
        "tier": 1,
        "requires": ["thumb", "thumb_rot", "index", "middle", "ring", "pinky"],
        "validated_by_signer": True,
        "reviewer": "Reviewer A",
        "frames": [
            {"pose": {"thumb": 0.05, "thumb_rot": 0.1, "index": 1, "middle": 1, "ring": 1, "pinky": 1}}
        ],
    }
    sign = client.put("/api/library/A", json=body).json()["sign"]
    assert sign["validated_by_signer"] is True and sign["draft"] is False
    assert sign["validated_on"] and sign["reviewer"] == "Reviewer A"


# ---------------------------------------------------------------------------- transport + calibration
def test_sim_calibration(client: TestClient) -> None:
    cal = client.get("/api/calibration").json()
    assert cal["mode"] == "sim" and len(cal["cal"]) == 7
    r = client.post(
        "/api/calibration", json={"joint": "index", "min": 550, "max": 2350, "inv": False, "rest": 0.1}
    )
    assert r.status_code == 200 and r.json()["cal"][2]["min"] == 550
    assert (
        client.post("/api/calibration", json={"joint": "x", "min": 1, "max": 2, "rest": 0}).status_code == 422
    )
    assert (
        client.post("/api/calibration", json={"joint": "index", "min": 9, "max": 2, "rest": 0}).status_code
        == 422
    )
    assert client.post("/api/raw", json={"joint": "index", "us": 1500}).json() == {"ok": "raw"}
    assert client.post("/api/raw", json={"joint": "elbow", "us": 1500}).status_code == 422


def test_emulator_mode_end_to_end(client: TestClient) -> None:
    st = client.post("/api/transport", json={"mode": "emulator"}).json()
    assert st["mode"] == "emulator" and st["connected"] is True and st["firmware"] == "0.1.0-emu"
    body = client.post("/api/say", json={"text": "I love you"}).json()
    assert body["steps"] == 1
    assert wait_done(client)[-1]["ok"] is True
    snap = client.get("/api/transport").json()["emulator"]
    assert snap["positions"][3] == 1.0
    # calibration save/load through the real serial protocol
    r = client.post(
        "/api/calibration", json={"joint": "index", "min": 520, "max": 2380, "inv": True, "rest": 0.12}
    )
    assert r.status_code == 200
    cal = client.get("/api/calibration").json()["cal"]
    assert cal[2] == {"joint": "index", "ch": 2, "min": 520, "max": 2380, "inv": True, "rest": 0.12}
    assert (Path(sig(client).data_dir) / "emulator_cal.json").exists()
    assert client.post("/api/raw", json={"joint": "index", "us": 1500}).json() == {"ok": "raw"}
    lines = [e["line"] for e in sig(client).bus.history("telemetry") if e.get("kind") == "serial"]
    assert any('"cmd":"hello"' in ln for ln in lines) and any('"done"' in ln for ln in lines)
    assert client.post("/api/transport", json={"mode": "sim"}).json()["mode"] == "sim"


def test_serial_failure_keeps_previous_transport(
    fast_hand: HandConfig, library: Library, tmp_path: Path
) -> None:
    def factory(port: str, baud: int) -> Any:
        raise serial.SerialException("could not open port 'COM9'")

    with make_client(fast_hand, library, tmp_path, serial_factory=factory) as c:
        r = c.post("/api/transport", json={"mode": "serial", "port": "COM9"})
        assert r.status_code == 503 and "Could not open COM9" in r.json()["detail"]
        st = c.get("/api/transport").json()
        assert st["mode"] == "sim" and "COM9" in st["last_error"]


def test_startup_serial_failure_falls_back_to_sim(
    fast_hand: HandConfig, library: Library, tmp_path: Path
) -> None:
    def factory(port: str, baud: int) -> Any:
        raise serial.SerialException("no device")

    with make_client(fast_hand, library, tmp_path, mode="serial", port="COM7", serial_factory=factory) as c:
        assert c.get("/api/transport").json()["mode"] == "sim"
        errors = [e["message"] for e in sig(c).bus.history("error")]
        assert any("Falling back to simulation" in m for m in errors)


def test_ports(client: TestClient) -> None:
    assert isinstance(client.get("/api/ports").json(), list)


# ---------------------------------------------------------------------------- evaluation
def test_evaluation_session(client: TestClient) -> None:
    assert client.post("/api/eval/next").status_code == 409  # no session yet
    r = client.post("/api/eval/start", json={"count": 5, "tiers": [1], "seed": 7})
    assert r.json()["total"] == 5
    assert client.post("/api/eval/answer", json={"guess": "A"}).status_code == 409  # nothing performed yet
    session = sig(client).eval
    order = [t.sign_id for t in session.trials]
    for i, sid in enumerate(order):
        nxt = client.post("/api/eval/next").json()
        assert nxt["trial"] == i + 1 and "sign_id" not in nxt
        assert client.post("/api/eval/next").status_code == 409  # must answer first
        assert client.post("/api/eval/repeat").status_code == 200
        hidden = client.get("/api/eval/summary").json()
        assert hidden["revealed"] is False and "per_sign" not in hidden
        guess = sid if i != 2 else "definitely wrong"
        res = client.post("/api/eval/answer", json={"guess": guess, "confidence": 4}).json()
        assert "correct" not in res
    wait_done(client, count=1)
    summary = client.get("/api/eval/summary").json()
    assert summary["revealed"] is True and summary["finished"] is True
    assert summary["overall_pct"] == 80.0
    # eval runs never reveal the sign in events or pose ids
    eval_steps = [e for e in sig(client).bus.history("step") if e.get("context") == "evaluation"]
    assert eval_steps and all(e["sign_id"] == "?" for e in eval_steps)
    serial_lines = [e["line"] for e in sig(client).bus.history("telemetry") if e.get("kind") == "serial"]
    assert any('"id":"EVAL.' in ln for ln in serial_lines)

    trials_csv = client.get("/api/eval/export?kind=trials")
    assert trials_csv.status_code == 200 and trials_csv.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(trials_csv.text)))
    assert len(rows) == 5 and sum(int(r["correct"]) for r in rows) == 4
    summary_rows = list(csv.DictReader(io.StringIO(client.get("/api/eval/export?kind=summary").text)))
    assert summary_rows[-1]["sign_id"] == "OVERALL" and summary_rows[-1]["recognition_pct"] == "80.0"
    assert client.post("/api/eval/next").status_code == 409  # finished


def test_eval_export_before_finish(client: TestClient) -> None:
    client.post("/api/eval/start", json={"count": 2})
    assert client.get("/api/eval/export").status_code == 409
    assert client.post("/api/eval/start", json={"count": 2, "tiers": [9]}).status_code == 409


# ---------------------------------------------------------------------------- websocket + errors
def test_websocket_streams_run(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert first["type"] == "status" and first["state"] == "connected"
        client.post("/api/say", json={"text": "I love you"})
        seen: list[str] = []
        for _ in range(50):
            ev = ws.receive_json()
            seen.append(ev["type"])
            if ev["type"] == "done":
                break
        assert seen.index("transcript") < seen.index("gloss") < seen.index("step") < seen.index("done")


def test_unexpected_error_is_friendly(fast_hand: HandConfig, library: Library, tmp_path: Path) -> None:
    app = create_app(
        fast_hand,
        library,
        data_dir=tmp_path,
        models_dir=tmp_path,
        mode="sim",
        speech=FakeSpeech(),
        serve_dashboard=False,
    )
    with TestClient(app, raise_server_exceptions=False) as c:

        async def boom() -> None:
            raise ValueError("internal detail")

        app.state.signova.performer.stop = boom
        r = c.post("/api/stop")
        assert r.status_code == 500
        assert "Traceback" not in r.text and "internal detail" not in r.text
        assert "server console" in r.json()["detail"]


def test_dashboard_is_served(fast_hand: HandConfig, library: Library, tmp_path: Path) -> None:
    with make_client(fast_hand, library, tmp_path, serve_dashboard=True) as c:
        r = c.get("/")
        assert r.status_code == 200 and "SIGNOVA" in r.text
