"""SIGNOVA FastAPI server: REST + one WebSocket (/ws) streaming events, and the dashboard.

Binds to 127.0.0.1 by default. Errors come back as {"detail": "<friendly message>"}; stack
traces only ever go to the server console.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .. import DISCLAIMER, __version__
from ..config import ROOT, ConfigError, HandConfig, default_data_dir, format_validation_error
from ..evaluation import EvalError, EvalSession
from ..events import EventBus
from ..gloss import GlossService
from ..library import Frame, Library, Sign
from ..performer import Performer, RunMeta
from ..sequencer import Sequencer, clamp_speed, total_duration_ms
from ..speech import SpeechManager
from ..transport import SimTransport, Transport, TransportError
from ..transport.emulator import EmulatorSerial, ESP32Emulator
from ..transport.serial_esp32 import SerialESP32Transport, SerialFactory, list_serial_ports

log = logging.getLogger("signova")
DASHBOARD_DIR = ROOT / "dashboard"
MAX_AUDIO_BYTES = 10 * 1024 * 1024
Mode = Literal["sim", "emulator", "serial"]


# ---------------------------------------------------------------------------- request bodies
class SayRequest(BaseModel):
    text: str = Field(max_length=500)
    engine: Literal["rules", "claude"] = "rules"
    speed: float | None = None


class PoseRequest(BaseModel):
    pose: dict[str, float] | None = None
    vector: list[float] | None = None
    ms: int = Field(default=60, ge=0, le=3000)


class SignUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["word", "letter", "number"]
    tier: int = Field(ge=1, le=3)
    english: list[str] = Field(default_factory=list)
    requires: list[str] | None = None
    validated_by_signer: bool = False
    reviewer: str | None = None
    validated_on: str | None = None
    notes: str = ""
    frames: list[Frame] = Field(min_length=1, max_length=32)


class TransportRequest(BaseModel):
    mode: Mode
    port: str | None = None


class CalibrationRequest(BaseModel):
    joint: str
    min: int
    max: int
    inv: bool = False
    rest: float = Field(ge=0.0, le=1.0)


class RawRequest(BaseModel):
    joint: str
    us: int


class EvalStartRequest(BaseModel):
    count: int = Field(default=5, ge=1, le=200)
    tiers: list[int] = Field(default_factory=lambda: [1])
    randomize: bool = True
    participant: str = Field(default="", max_length=40)
    seed: int | None = None


class EvalAnswerRequest(BaseModel):
    guess: str = Field(max_length=80)
    confidence: int | None = Field(default=None, ge=1, le=5)


class SettingsRequest(BaseModel):
    save_recordings: bool | None = None
    speed: float | None = None


# ---------------------------------------------------------------------------- transports
class TransportManager:
    """Creates and swaps transports (sim / emulator / serial) and maps device events to bus events."""

    def __init__(
        self,
        hand: HandConfig,
        library: Library,
        bus: EventBus,
        data_dir: Path,
        serial_factory: SerialFactory | None = None,
    ) -> None:
        self.hand = hand
        self.library = library
        self.bus = bus
        self.data_dir = data_dir
        self.serial_factory = serial_factory
        self.emulator: ESP32Emulator | None = None
        self.transport: Transport = self._make("sim", None)
        self._lock = asyncio.Lock()
        self.last_error: str | None = None
        self.on_change: Callable[[Transport], None] | None = None

    @property
    def mode(self) -> str:
        return self.transport.mode

    def _on_line(self, direction: str, line: str) -> None:
        self.bus.publish("telemetry", kind="serial", dir=direction, line=line[:400])

    def _on_event(self, msg: dict[str, Any]) -> None:
        ev = msg.get("event")
        detail = str(msg.get("detail", ""))
        if ev == "watchdog":
            self.bus.publish(
                "error", source="hand", message="The hand relaxed itself: no message for 5 s (watchdog)."
            )
        elif ev == "disconnected":
            self.bus.publish("error", source="transport", message=f"Lost connection to the hand ({detail}).")
            self.bus.publish("status", state="transport", transport=self.status())
        elif ev == "reconnected":
            self.bus.publish("status", state="transport", transport=self.status(), detail="Reconnected.")
        elif ev in ("mismatch", "error"):
            self.bus.publish("error", source="hand", message=detail)
        elif ev == "boot":
            self.bus.publish("telemetry", kind="device", detail=f"ESP32 booted (firmware {msg.get('fw')})")

    def _ensure_emulator(self) -> ESP32Emulator:
        if self.emulator is None:
            self.emulator = ESP32Emulator(
                self.hand.joint_names,
                self.library.rest_vector(),
                driver=self.hand.driver,
                cal_path=self.data_dir / "emulator_cal.json",
                full_range_ms=self.hand.limits.full_range_ms,
                watchdog_ms=self.hand.limits.watchdog_ms,
            )
        return self.emulator

    def _make(self, mode: str, port: str | None) -> Transport:
        joints = self.hand.joint_names
        tc = self.hand.transport
        if mode == "sim":
            return SimTransport(joints, self.library.rest_vector(), on_line=self._on_line)
        if mode == "emulator":
            emu = self._ensure_emulator()
            return SerialESP32Transport(
                joints,
                port="EMULATOR",
                baud=tc.baud,
                on_line=self._on_line,
                on_event=self._on_event,
                serial_factory=lambda p, b: EmulatorSerial(emu, p),
                heartbeat_s=tc.heartbeat_s,
                mode_name="emulator",
                boot_wait_s=0.1,
            )
        if mode == "serial":
            return SerialESP32Transport(
                joints,
                port=port or tc.port,
                baud=tc.baud,
                on_line=self._on_line,
                on_event=self._on_event,
                serial_factory=self.serial_factory,
                heartbeat_s=tc.heartbeat_s,
            )
        raise TransportError(f"Unknown transport mode {mode!r}")

    async def switch(self, mode: str, port: str | None = None) -> dict[str, Any]:
        """Connect the new transport first; only replace the old one if that worked."""
        async with self._lock:
            new = self._make(mode, port)
            try:
                await new.connect()
            except TransportError as exc:
                self.last_error = str(exc)
                with contextlib.suppress(Exception):
                    await new.close()
                raise
            old, self.transport = self.transport, new
            self.last_error = None
            if self.on_change is not None:
                self.on_change(new)
            with contextlib.suppress(Exception):
                await old.close()
        st = self.status()
        self.bus.publish("status", state="transport", transport=st, detail=f"Hand transport: {mode}")
        return st

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self.transport.close()
        if self.emulator is not None:
            self.emulator.shutdown()

    def status(self) -> dict[str, Any]:
        st = dict(self.transport.status())
        st["last_error"] = self.last_error
        st["simulation"] = st.get("mode") == "sim"
        if self.emulator is not None and st.get("mode") == "emulator":
            st["emulator"] = self.emulator.snapshot()
        return st


# ---------------------------------------------------------------------------- application state
class Signova:
    def __init__(
        self,
        hand: HandConfig,
        library: Library,
        data_dir: Path,
        models_dir: Path,
        claude_client: Any | None = None,
        speech: SpeechManager | None = None,
        serial_factory: SerialFactory | None = None,
    ) -> None:
        self.hand = hand
        self.library = library
        self.data_dir = data_dir
        self.bus = EventBus()
        self.gloss = GlossService(library, claude_client=claude_client)
        self.sequencer = Sequencer(library, hand.timing)
        self.transports = TransportManager(hand, library, self.bus, data_dir, serial_factory)
        self.performer = Performer(
            self.transports.transport, hand, self.bus, library.rest_vector(), log_dir=data_dir / "logs"
        )
        self.transports.on_change = self._transport_changed
        self.speech = speech or SpeechManager(
            library, models_dir, on_status=lambda m: self.bus.publish("status", state="speech", detail=m)
        )
        self.eval: EvalSession | None = None
        self.save_recordings = False
        self.speed = hand.timing.speed

    def _transport_changed(self, transport: Transport) -> None:
        self.performer.transport = transport

    async def start(self, mode: str, port: str | None) -> None:
        self.bus.bind_loop()
        try:
            await self.transports.switch(mode, port)
        except TransportError as exc:
            if mode == "sim":
                raise
            self.bus.publish("error", source="transport", message=f"{exc} Falling back to simulation mode.")
            await self.transports.switch("sim")
            self.transports.last_error = str(exc)

    async def shutdown(self) -> None:
        with contextlib.suppress(Exception):
            await self.performer.cancel("server shutting down")
        await self.transports.close()

    def refresh_library(self) -> None:
        """Pick up edits to signs/library.yaml made outside the app, without a restart."""
        try:
            if self.library.reload_if_changed():
                log.info("library.yaml changed on disk; reloaded")
                self.bus.publish("status", state="library", detail="Sign library reloaded from disk")
        except Exception as exc:
            log.warning("library.yaml changed but is invalid: %s", exc)
            self.bus.publish(
                "error",
                source="library",
                message=f"signs/library.yaml has an error, still using the previous version: {exc}",
            )

    def state(self) -> dict[str, Any]:
        self.refresh_library()
        return {
            "version": __version__,
            "disclaimer": DISCLAIMER,
            "hand": {
                "name": self.hand.name,
                "driver": self.hand.driver,
                "joints": [j.model_dump() for j in self.hand.joints],
                "timing": self.hand.timing.model_dump(),
                "limits": self.hand.limits.model_dump(),
            },
            "rest": self.library.rest_pose(),
            "library": self.library.to_api(),
            "letters": sorted(self.library.letters),
            "transport": self.transports.status(),
            "speech": self.speech.status(),
            "claude": self.gloss.claude_status(),
            "settings": {"save_recordings": self.save_recordings, "speed": self.speed},
            "latency": self.performer.latency_stats(),
            "running": self.performer.running,
            "current_pose": dict(zip(self.hand.joint_names, self.performer.current, strict=True)),
            "eval": self.eval.progress() if self.eval else None,
        }

    async def say(
        self,
        text: str,
        engine: str,
        origin: float,
        source: str = "say",
        stt_ms: float | None = None,
        speed: float | None = None,
    ) -> dict[str, Any]:
        self.refresh_library()
        result = await self.gloss.gloss(text, engine)
        self.bus.publish("gloss", **result.model_dump())
        steps = self.sequencer.build(result.items, speed if speed is not None else self.speed)
        meta = RunMeta(
            source=source, engine=result.engine, origin=origin, stt_ms=stt_ms, gloss_ms=result.latency_ms
        )
        run_id = await self.performer.start(steps, meta)
        return {
            "gloss": result.model_dump(),
            "steps": len(steps),
            "planned_ms": total_duration_ms(steps),
            "run_id": run_id,
        }

    async def preview(self, sign_id: str, source: str = "preview", mask: bool = False) -> dict[str, Any]:
        steps = self.sequencer.for_sign(sign_id, self.speed)
        run_id = await self.performer.start(steps, RunMeta(source=source, mask=mask))
        return {"run_id": run_id, "steps": len(steps), "planned_ms": total_duration_ms(steps)}


# ---------------------------------------------------------------------------- app factory
def create_app(
    hand: HandConfig,
    library: Library,
    data_dir: Path | None = None,
    models_dir: Path | None = None,
    mode: str | None = None,
    port: str | None = None,
    claude_client: Any | None = None,
    speech: SpeechManager | None = None,
    serial_factory: SerialFactory | None = None,
    serve_dashboard: bool = True,
) -> FastAPI:
    data_dir = data_dir or default_data_dir()
    models_dir = models_dir or ROOT / "models"
    sig = Signova(hand, library, data_dir, models_dir, claude_client, speech, serial_factory)
    start_mode = mode or hand.transport.mode

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await sig.start(start_mode, port)
        yield
        await sig.shutdown()

    app = FastAPI(
        title="SIGNOVA", version=__version__, lifespan=lifespan, docs_url="/api/docs", redoc_url=None
    )
    app.state.signova = sig

    # ------------------------------------------------------------------ errors
    @app.exception_handler(TransportError)
    async def _transport_error(request: Request, exc: TransportError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(EvalError)
    async def _eval_error(request: Request, exc: EvalError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ConfigError)
    async def _config_error(request: Request, exc: ConfigError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unexpected error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "detail": f"Something went wrong ({type(exc).__name__}). Details are in the server console."
            },
        )

    # ------------------------------------------------------------------ state
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    @app.get("/api/state")
    async def get_state() -> dict[str, Any]:
        return sig.state()

    @app.post("/api/settings")
    async def settings(body: SettingsRequest) -> dict[str, Any]:
        if body.save_recordings is not None:
            sig.save_recordings = body.save_recordings
        if body.speed is not None:
            sig.speed = clamp_speed(body.speed)
        st = {"save_recordings": sig.save_recordings, "speed": sig.speed}
        sig.bus.publish("status", state="settings", settings=st)
        return st

    # ------------------------------------------------------------------ say / transcribe
    @app.post("/api/say")
    async def say(body: SayRequest) -> dict[str, Any]:
        origin = time.perf_counter()
        text = body.text.strip()
        sig.bus.publish("transcript", text=text, source="typed", engine=None, stt_ms=None)
        return await sig.say(text, body.engine, origin, source="say", speed=body.speed)

    @app.post("/api/transcribe")
    async def transcribe(
        audio: UploadFile = File(...),
        engine: Literal["whisper", "vosk"] = Form("whisper"),
        auto_sign: bool = Form(True),
        gloss_engine: Literal["rules", "claude"] = Form("rules"),
        t_end: float | None = Form(None),
    ) -> dict[str, Any]:
        origin = time.perf_counter()
        if t_end is not None:
            # Browser timestamp (epoch ms) of the moment speech ended; same machine, same clock.
            age_s = time.time() - t_end / 1000
            if 0 <= age_s <= 60:
                origin -= age_s
        data = await audio.read(MAX_AUDIO_BYTES + 1)
        if not data:
            raise HTTPException(400, "The recording was empty. Hold the button while you speak.")
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "The recording is too long (10 MB max).")
        sig.refresh_library()  # the Vosk grammar is built from the library
        sig.bus.publish("status", state="transcribing", detail=f"Transcribing with {engine}...")
        try:
            text, stt_ms = await sig.speech.transcribe(data, engine)
        except RuntimeError as exc:
            sig.bus.publish("error", source="speech", message=str(exc))
            raise HTTPException(503, str(exc)) from exc
        except Exception as exc:
            log.exception("transcription failed")
            msg = "Could not decode or transcribe the recording. Try again, or type the sentence instead."
            sig.bus.publish("error", source="speech", message=msg)
            raise HTTPException(400, msg) from exc
        if sig.save_recordings:
            _save_recording(sig.data_dir, data, audio.filename or "")
        sig.bus.publish("transcript", text=text, source="mic", engine=engine, stt_ms=stt_ms)
        result: dict[str, Any] = {
            "text": text,
            "stt_ms": stt_ms,
            "engine": engine,
            "saved": sig.save_recordings,
        }
        if not text:
            result["note"] = "No speech recognised."
            sig.bus.publish("status", state="idle", detail="No speech recognised.")
            return result
        if auto_sign:
            result.update(await sig.say(text, gloss_engine, origin, source="transcribe", stt_ms=stt_ms))
        return result

    # ------------------------------------------------------------------ motion
    @app.post("/api/sign/{sign_id}")
    async def preview_sign(sign_id: str) -> dict[str, Any]:
        sid = sig.library.resolve_id(sign_id)
        if sid is None:
            raise HTTPException(404, f"No sign {sign_id!r} in the library.")
        if not sig.library.available(sid):
            raise HTTPException(409, f"{sid} can't be performed: {sig.library.unavailable_reason(sid)}.")
        return {"sign_id": sid, **await sig.preview(sid)}

    @app.post("/api/pose")
    async def live_pose(body: PoseRequest) -> dict[str, Any]:
        joints = sig.hand.joint_names
        if body.vector is not None:
            if len(body.vector) != len(joints):
                raise HTTPException(422, f"vector needs {len(joints)} values ({', '.join(joints)})")
            vec = body.vector
        else:
            pose = body.pose or {}
            unknown = [k for k in pose if k not in joints]
            if unknown:
                raise HTTPException(422, f"unknown joint(s): {', '.join(unknown)}")
            vec = list(sig.performer.current)  # joints not mentioned keep their current value
            for j, v in pose.items():
                vec[joints.index(j)] = v
        reply = await sig.performer.live_pose(vec, body.ms)
        current = dict(zip(joints, sig.performer.current, strict=True))
        sig.bus.publish("telemetry", kind="pose", pose=current)
        return {"pose": current, "reply": reply}

    @app.post("/api/stop")
    async def stop() -> dict[str, Any]:
        return await sig.performer.stop()

    @app.post("/api/relax")
    async def relax() -> dict[str, Any]:
        return await sig.performer.relax()

    # ------------------------------------------------------------------ library
    @app.get("/api/library")
    async def get_library() -> list[dict[str, Any]]:
        sig.refresh_library()
        return sig.library.to_api()

    @app.put("/api/library/{sign_id}")
    async def put_sign(sign_id: str, body: SignUpdate) -> dict[str, Any]:
        data = body.model_dump()
        if data["requires"] is None:
            data["requires"] = sig.library.suggest_requires(Sign.model_validate({**data, "requires": []}))
        if data["validated_by_signer"] and not data.get("validated_on"):
            data["validated_on"] = datetime.now().date().isoformat()
        try:
            sign = Sign.model_validate(data)
        except ValueError as exc:
            raise HTTPException(422, format_validation_error(exc)) from exc
        sid = sign_id.strip().upper()
        sig.library.upsert(sid, sign)
        sig.bus.publish("status", state="library", detail=f"Saved sign {sid}")
        return {"sign": sig.library.sign_view(sid), "letters": sorted(sig.library.letters)}

    @app.delete("/api/library/{sign_id}")
    async def delete_sign(sign_id: str) -> dict[str, Any]:
        sid = sig.library.resolve_id(sign_id)
        if sid is None:
            raise HTTPException(404, f"No sign {sign_id!r} in the library.")
        sig.library.delete(sid)
        sig.bus.publish("status", state="library", detail=f"Deleted sign {sid}")
        return {"deleted": sid}

    # ------------------------------------------------------------------ transport + calibration
    @app.get("/api/transport")
    async def get_transport() -> dict[str, Any]:
        return sig.transports.status()

    @app.post("/api/transport")
    async def set_transport(body: TransportRequest) -> dict[str, Any]:
        await sig.performer.cancel("transport changed")
        return await sig.transports.switch(body.mode, body.port)

    @app.get("/api/ports")
    async def ports() -> list[dict[str, Any]]:
        return await asyncio.to_thread(list_serial_ports)

    @app.get("/api/calibration")
    async def get_calibration() -> dict[str, Any]:
        cal = await sig.transports.transport.cal_get()
        return {"mode": sig.transports.mode, "driver": sig.transports.status().get("driver"), "cal": cal}

    @app.post("/api/calibration")
    async def set_calibration(body: CalibrationRequest) -> dict[str, Any]:
        if body.joint not in sig.hand.joint_names:
            raise HTTPException(422, f"Unknown joint {body.joint!r}.")
        if body.min >= body.max:
            raise HTTPException(422, "min must be below max.")
        reply = await sig.transports.transport.cal_set(body.joint, body.min, body.max, body.inv, body.rest)
        cal = await sig.transports.transport.cal_get()
        sig.bus.publish("status", state="calibration", detail=f"Saved calibration for {body.joint}")
        return {"reply": reply, "cal": cal}

    @app.post("/api/raw")
    async def raw(body: RawRequest) -> dict[str, Any]:
        if body.joint not in sig.hand.joint_names:
            raise HTTPException(422, f"Unknown joint {body.joint!r}.")
        await sig.performer.cancel("raw calibration move")
        return await sig.transports.transport.raw(body.joint, body.us)

    # ------------------------------------------------------------------ evaluation
    @app.post("/api/eval/start")
    async def eval_start(body: EvalStartRequest) -> dict[str, Any]:
        sig.eval = EvalSession(
            sig.library,
            count=body.count,
            tiers=body.tiers,
            randomize=body.randomize,
            participant=body.participant,
            seed=body.seed,
        )
        progress = sig.eval.progress()
        sig.bus.publish("eval", state="started", **progress)
        return progress

    def _session() -> EvalSession:
        if sig.eval is None:
            raise EvalError("Start an evaluation session first.")
        return sig.eval

    @app.post("/api/eval/next")
    async def eval_next() -> dict[str, Any]:
        session = _session()
        trial = session.next()
        await sig.preview(trial.sign_id, source="eval", mask=True)
        progress = session.progress()
        sig.bus.publish("eval", state="performing", **progress)
        return {**progress, "trial": trial.index + 1}

    @app.post("/api/eval/repeat")
    async def eval_repeat() -> dict[str, Any]:
        session = _session()
        trial = session.repeat()
        await sig.preview(trial.sign_id, source="eval", mask=True)
        return {**session.progress(), "trial": trial.index + 1}

    @app.post("/api/eval/answer")
    async def eval_answer(body: EvalAnswerRequest) -> dict[str, Any]:
        session = _session()
        progress = session.answer(body.guess, body.confidence)
        if session.finished:
            paths = session.write_csv(sig.data_dir / "eval")
            progress["saved"] = paths
            sig.bus.publish("eval", state="finished", **progress)
        else:
            sig.bus.publish("eval", state="answered", **progress)
        return progress

    @app.get("/api/eval/summary")
    async def eval_summary() -> dict[str, Any]:
        return _session().summary()

    @app.get("/api/eval/export")
    async def eval_export(kind: Literal["trials", "summary"] = "trials") -> FileResponse:
        session = _session()
        if not session.finished or not session.saved_paths:
            raise EvalError("Finish the session before exporting.")
        path = Path(session.saved_paths[kind])
        return FileResponse(path, media_type="text/csv", filename=path.name)

    # ------------------------------------------------------------------ websocket
    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = sig.bus.subscribe()
        await websocket.send_json(
            {
                "type": "status",
                "state": "connected",
                "transport": sig.transports.status(),
                "running": sig.performer.running,
                "ts": time.time(),
            }
        )

        async def pump() -> None:
            while True:
                await websocket.send_json(await queue.get())

        async def drain() -> None:
            while True:
                await websocket.receive_text()  # clients may send keep-alives; we ignore them

        tasks = [asyncio.create_task(pump()), asyncio.create_task(drain())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except WebSocketDisconnect:
            pass
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, Exception):
                    await t
            sig.bus.unsubscribe(queue)

    if serve_dashboard and DASHBOARD_DIR.is_dir():
        app.mount("/", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")
    return app


def _save_recording(data_dir: Path, data: bytes, filename: str) -> None:
    """Only called when the user ticked "save recordings for testing"."""
    ext = (
        Path(filename).suffix
        if Path(filename).suffix in (".webm", ".ogg", ".wav", ".mp4", ".m4a")
        else ".webm"
    )
    out = data_dir / "recordings"
    try:
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}{ext}").write_bytes(data)
    except OSError:
        log.warning("could not save recording")
