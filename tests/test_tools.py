"""tools/: ASL-LEX candidate filter (synthetic CSV) and the capture script's CLI."""

from __future__ import annotations

import csv
import importlib.util
import subprocess
import sys
from pathlib import Path

from signova.config import ROOT


def load_tool(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROWS = [
    {"LemmaID": "apple", "SignType.2.0": "OneHanded", "Movement.2.0": "None", "SignFrequency(Z)": "0.5"},
    {
        "LemmaID": "book",
        "SignType.2.0": "SymmetricalOrAlternating",
        "Movement.2.0": "None",
        "SignFrequency(Z)": "1.2",
    },
    {"LemmaID": "cat", "SignType.2.0": "OneHanded", "Movement.2.0": "Straight", "SignFrequency(Z)": "0.9"},
    {"LemmaID": "yes", "SignType.2.0": "OneHanded", "Movement.2.0": "", "SignFrequency(Z)": "2.1"},
]


def test_filter_and_sort() -> None:
    tool = load_tool("asl_lex_candidates")
    assert tool.find_column(list(ROWS[0]), "signtype") == "SignType.2.0"
    assert tool.find_column(list(ROWS[0]), "pathmovement", "movement") == "Movement.2.0"
    assert tool.find_column(list(ROWS[0]), "nothing") is None
    picked = tool.filter_candidates(ROWS, "SignType.2.0", "Movement.2.0")
    assert [r["LemmaID"] for r in picked] == ["apple", "yes"]
    ranked = tool.sort_by_frequency(picked, "SignFrequency(Z)")
    assert [r["LemmaID"] for r in ranked] == ["yes", "apple"]


def test_asl_lex_cli(tmp_path: Path) -> None:
    tool = load_tool("asl_lex_candidates")
    src = tmp_path / "asl.csv"
    with src.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(ROWS[0]))
        w.writeheader()
        w.writerows(ROWS)
    out = tmp_path / "out.csv"
    assert tool.main([str(src), "--out", str(out)]) == 0
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert [r["LemmaID"] for r in rows] == ["yes", "apple"]
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n", encoding="utf-8")
    assert tool.main([str(bad)]) == 2


def test_capture_pose_help() -> None:
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "capture_pose.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0 and "MediaPipe" in r.stdout
