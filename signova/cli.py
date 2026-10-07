"""`signova` command line: serve the dashboard, gloss a sentence, download models, list ports."""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
import threading
import webbrowser
from pathlib import Path

from . import DISCLAIMER, __version__
from .config import ROOT, ConfigError, HandConfig, default_data_dir, load_dotenv_once, load_hand_config
from .library import Library


def _load(args: argparse.Namespace) -> tuple[HandConfig, Library]:
    hand = load_hand_config(args.hand)
    library = Library.load(hand, args.library)
    return hand, library


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .server import create_app

    hand, library = _load(args)
    mode = (
        "sim" if args.sim else "emulator" if args.emulator else "serial" if args.serial is not None else None
    )
    port = args.serial or None
    app = create_app(hand, library, data_dir=default_data_dir(), mode=mode, port=port)
    url = f"http://{args.host}:{args.port}"
    print(f"SIGNOVA {__version__} - {DISCLAIMER}")
    print(f"Dashboard: {url}   (Ctrl+C to stop)")
    if args.open:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_gloss(args: argparse.Namespace) -> int:
    from .gloss import GlossService
    from .sequencer import Sequencer, total_duration_ms

    hand, library = _load(args)
    result = asyncio.run(GlossService(library).gloss(args.text, args.engine))
    print("gloss :", " ".join(result.short()) or "(nothing to sign)")
    for item in result.items:
        extra = f"  ({item.reason})" if item.reason else ""
        print(f"  {item.type:<4} {item.word}{extra}")
    print("engine:", result.engine + (f" (fallback: {result.fallback_reason})" if result.fallback else ""))
    print("note  :", result.note)
    steps = Sequencer(library, hand.timing).build(result.items)
    print(f"steps : {len(steps)} poses, about {total_duration_ms(steps) / 1000:.1f} s")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    hand, library = _load(args)
    print(f"hand   : {hand.name} - joints {', '.join(hand.joint_names)}")
    avail = library.available_ids()
    missing = [s for s in library.ids() if s not in avail]
    print(f"library: {len(library.ids())} signs, {len(avail)} available on this hand")
    for sid in missing:
        print(f"  unavailable: {sid} - {library.unavailable_reason(sid)}")
    print("letters:", " ".join(sorted(library.letters)))
    drafts = [s for s in library.ids() if not library.get(s).validated_by_signer]
    print(f"drafts : {len(drafts)} sign(s) not yet validated by a fluent signer")
    return 0


def cmd_ports(args: argparse.Namespace) -> int:
    from .transport.serial_esp32 import list_serial_ports

    ports = list_serial_ports()
    if not ports:
        print(
            "No serial ports found. Plug in the ESP32 (data-capable USB cable) and install the CP210x/CH340 driver."
        )
        return 1
    for p in ports:
        tag = f"  <- looks like an ESP32 ({p['bridge']})" if p["likely_esp32"] else ""
        print(f"{p['device']:<8} {p['description']}{tag}")
    return 0


def cmd_download_models(args: argparse.Namespace) -> int:
    models = ROOT / "models"
    ok = True
    if not args.whisper_only:
        from .speech.vosk_stt import DEFAULT_MODEL, MODEL_URL, download_model

        print(f"Downloading Vosk model {DEFAULT_MODEL} (~128 MB) from {MODEL_URL}")

        def progress(done: int, total: int) -> None:
            if total:
                sys.stdout.write(f"\r  {done / 1e6:6.1f} / {total / 1e6:.1f} MB")
                sys.stdout.flush()

        try:
            path = download_model(models, progress=progress)
            print(f"\n  ready: {path}")
        except Exception as exc:  # network problems are common; explain and continue
            ok = False
            print(f"\n  Vosk download failed: {exc}")
    if not args.vosk_only:
        from .speech.whisper_stt import WhisperSTT

        stt = WhisperSTT(args.whisper_model, download_root=models / "whisper", on_status=print)
        try:
            stt.load()
            print(f"  Whisper {stt.model_name} ready in {stt.load_ms} ms")
        except Exception as exc:
            ok = False
            print(f"  Whisper download failed: {exc}")
    return 0 if ok else 1


def cmd_transcribe(args: argparse.Namespace) -> int:
    from .speech import SpeechManager

    hand, library = _load(args)
    mgr = SpeechManager(library, ROOT / "models", on_status=print)
    text, ms = asyncio.run(mgr.transcribe(Path(args.file).read_bytes(), args.engine))
    print(f"{args.engine} ({ms:.0f} ms): {text!r}")
    return 0


def cmd_metrics(args: argparse.Namespace) -> int:
    from .evaluation import latency_report

    data = default_data_dir()
    rep = latency_report(data / "logs" / "runs.csv")
    print(f"Runs logged: {rep['runs']}  (data/logs/runs.csv)")
    for src, s in rep["by_source"].items():
        print(
            f"  {src:<10} n={s['count']:<4} median {s['median_ms']} ms   p90 {s['p90_ms']} ms   max {s['max_ms']} ms"
        )
    summaries = sorted((data / "eval").glob("*_summary.csv")) if (data / "eval").exists() else []
    print(f"Evaluation sessions: {len(summaries)}  (data/eval/)")
    for path in summaries[-5:]:
        rows = list(csv.DictReader(path.open(encoding="utf-8")))
        overall = next((r for r in rows if r["sign_id"] == "OVERALL"), None)
        if overall:
            print(f"  {path.name}: {overall['correct']}/{overall['shown']} = {overall['recognition_pct']}%")
    return 0


def cmd_gloss_eval(args: argparse.Namespace) -> int:
    from ruamel.yaml import YAML

    from .evaluation import gloss_scores, summarize_gloss_eval
    from .gloss import GlossService

    hand, library = _load(args)
    cases = YAML(typ="safe", pure=True).load(Path(args.refs).read_text(encoding="utf-8"))
    svc = GlossService(library)
    engines = ["rules", "claude"] if args.engine == "both" else [args.engine]

    async def run(engine: str) -> list[dict[str, object]]:
        rows = []
        for case in cases:
            res = await svc.gloss(case["text"], engine)
            rows.append(
                {
                    "text": case["text"],
                    "reference": [str(g) for g in case["gloss"]],
                    "predicted": res.short(),
                    "rejected": res.rejected,
                    "fallback": res.fallback,
                    "latency_ms": res.latency_ms,
                }
            )
        return rows

    for engine in engines:
        rows = asyncio.run(run(engine))
        summary = summarize_gloss_eval(rows)
        print(f"\n[{engine}] {summary}")
        if args.verbose:
            for r in rows:
                if not gloss_scores(r["predicted"], r["reference"])["exact"]:
                    print(
                        f"  {r['text']!r}\n    ref : {' '.join(r['reference'])}\n    got : {' '.join(r['predicted'])}"
                    )
        if engine == "claude" and summary.get("fallbacks"):
            print(
                "  note: Claude was unavailable for some sentences, so the rules engine answered (see 'fallbacks')."
            )
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    """Repeatability: perform the same sequence N times on a transport and log faults."""
    from datetime import datetime

    from .events import EventBus
    from .gloss import rule_gloss
    from .performer import Performer, RunMeta
    from .sequencer import Sequencer
    from .server.app import TransportManager

    hand, library = _load(args)
    data = default_data_dir()

    async def run() -> list[dict[str, object]]:
        bus = EventBus()
        bus.bind_loop()
        tm = TransportManager(hand, library, bus, data)
        await tm.switch(args.mode, args.port or None)
        perf = Performer(tm.transport, hand, bus, library.rest_vector())
        steps = Sequencer(library, hand.timing).build(rule_gloss(args.text, library).items)
        rows = []
        try:
            for i in range(args.runs):
                done = await perf.perform(steps, RunMeta(source="bench"))
                rows.append(
                    {
                        "run": i + 1,
                        "ok": done["ok"],
                        "reason": done.get("reason"),
                        "steps": done["steps"],
                        "total_ms": done["total_ms"],
                        "first_pose_ms": done["first_pose_ms"],
                    }
                )
                print(
                    f"run {i + 1:>2}: {'ok' if done['ok'] else 'FAULT'}  {done['total_ms']:.0f} ms  {done.get('reason')}"
                )
            await perf.relax()
        finally:
            await tm.close()
        return rows

    rows = asyncio.run(run())
    out = data / "logs" / f"repeatability_{datetime.now():%Y%m%d-%H%M%S}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["run"])
        w.writeheader()
        w.writerows(rows)
    faults = sum(not r["ok"] for r in rows)
    print(f"{len(rows)} runs, {faults} transport fault(s). Saved {out}")
    print("Wrong poses can only be judged by watching the hand: note them next to this CSV.")
    return 0 if faults == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="signova", description=f"SIGNOVA research prototype. {DISCLAIMER}")
    p.add_argument("--version", action="version", version=f"signova {__version__}")
    p.add_argument("--hand", default=None, help="hand config YAML (default config/hand.yaml)")
    p.add_argument("--library", default=None, help="sign library YAML (default signs/library.yaml)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="start the server and dashboard")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--sim", action="store_true", help="simulation mode, no hardware (default)")
    g.add_argument(
        "--emulator", action="store_true", help="pure-Python ESP32 emulator over the real protocol"
    )
    g.add_argument("--serial", nargs="?", const="", metavar="PORT", help="real ESP32, e.g. --serial COM5")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--open", action="store_true", help="open the dashboard in the browser")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("gloss", help="gloss a sentence and show the poses (no hardware)")
    s.add_argument("text")
    s.add_argument("--engine", choices=["rules", "claude"], default="rules")
    s.set_defaults(func=cmd_gloss)

    s = sub.add_parser("check", help="validate the hand config and sign library")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("ports", help="list serial ports")
    s.set_defaults(func=cmd_ports)

    s = sub.add_parser(
        "download-models", help="download the Vosk grammar model and the Whisper model into models/"
    )
    s.add_argument("--vosk-only", action="store_true")
    s.add_argument("--whisper-only", action="store_true")
    s.add_argument("--whisper-model", default=None, help="base.en (default) or small.en")
    s.set_defaults(func=cmd_download_models)

    s = sub.add_parser("transcribe", help="transcribe an audio file (for testing speech engines)")
    s.add_argument("file")
    s.add_argument("--engine", choices=["whisper", "vosk"], default="whisper")
    s.set_defaults(func=cmd_transcribe)

    s = sub.add_parser("metrics", help="latency and evaluation numbers for the report")
    s.set_defaults(func=cmd_metrics)

    s = sub.add_parser("gloss-eval", help="gloss accuracy vs reference sentences (rules baseline vs Claude)")
    s.add_argument("--refs", default=str(ROOT / "tests" / "data" / "sentences.yaml"))
    s.add_argument("--engine", choices=["rules", "claude", "both"], default="both")
    s.add_argument("-v", "--verbose", action="store_true", help="print every sentence that differs")
    s.set_defaults(func=cmd_gloss_eval)

    s = sub.add_parser("bench", help="repeatability: same sequence N times, log faults")
    s.add_argument("--mode", choices=["sim", "emulator", "serial"], default="emulator")
    s.add_argument("--port", default="")
    s.add_argument("--runs", type=int, default=20)
    s.add_argument(
        "--text",
        default="one two three four five i love you no cab",
        help="sentence to repeat (default: 10 signs = 1 2 3 4 5 ILY NO C A B)",
    )
    s.set_defaults(func=cmd_bench)
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv_once()
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except ConfigError as exc:
        print(f"Configuration problem: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
