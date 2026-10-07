"""`signova` command line: serve the dashboard, gloss a sentence, download models, list ports."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
import webbrowser
from pathlib import Path

from . import DISCLAIMER, __version__
from .config import ROOT, ConfigError, default_data_dir, load_dotenv_once, load_hand_config
from .library import Library


def _load(args: argparse.Namespace) -> tuple[object, Library]:
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
    app = create_app(hand, library, data_dir=default_data_dir(), mode=mode, port=port)  # type: ignore[arg-type]
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
    steps = Sequencer(library, hand.timing).build(result.items)  # type: ignore[attr-defined]
    print(f"steps : {len(steps)} poses, about {total_duration_ms(steps) / 1000:.1f} s")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    hand, library = _load(args)
    print(f"hand   : {hand.name} - joints {', '.join(hand.joint_names)}")  # type: ignore[attr-defined]
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
