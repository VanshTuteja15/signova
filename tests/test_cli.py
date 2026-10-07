"""The `signova` command line (run in-process, data written to a temp folder)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from signova.cli import main
from signova.config import ROOT
from signova.evaluation import gloss_scores, latency_report, percentile, summarize_gloss_eval


@pytest.fixture
def fast_hand_file(tmp_path: Path) -> Path:
    text = (ROOT / "config" / "hand.yaml").read_text(encoding="utf-8")
    text = text.replace("move_ms: 300", "move_ms: 5").replace("word_hold_ms: 800", "word_hold_ms: 5")
    text = text.replace("letter_hold_ms: 450", "letter_hold_ms: 5").replace(
        "full_range_ms: 250", "full_range_ms: 10"
    )
    p = tmp_path / "hand_fast.yaml"
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "data"
    monkeypatch.setenv("SIGNOVA_DATA_DIR", str(d))
    return d


def test_check(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check"]) == 0
    out = capsys.readouterr().out
    assert "unavailable: U" in out and "A B C D E F I J L O S W Y" in out


def test_gloss(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["gloss", "code is so cool"]) == 0
    out = capsys.readouterr().out
    assert "FS:CODE -is FS:SO FS:COOL" in out
    assert "11 poses" in out


def test_gloss_claude_without_key_falls_back(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["gloss", "I love you", "--engine", "claude"]) == 0
    assert "fallback" in capsys.readouterr().out


def test_bad_config_is_friendly(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("joints: []\n", encoding="utf-8")
    assert main(["--hand", str(bad), "check"]) == 2
    assert "Configuration problem" in capsys.readouterr().err


def test_gloss_eval_rules(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["gloss-eval", "--engine", "rules"]) == 0
    out = capsys.readouterr().out
    assert "'exact_pct': 100.0" in out and "'invented_signs_rejected': 0" in out


def test_bench_and_metrics(fast_hand_file: Path, data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--hand", str(fast_hand_file), "bench", "--mode", "emulator", "--runs", "3"]) == 0
    out = capsys.readouterr().out
    assert "3 runs, 0 transport fault(s)" in out
    files = list((data_dir / "logs").glob("repeatability_*.csv"))
    assert len(files) == 1
    rows = list(csv.DictReader(files[0].open(encoding="utf-8")))
    assert [r["ok"] for r in rows] == ["True"] * 3
    assert all(int(r["steps"]) >= 13 for r in rows)  # 1-5, ILY, NO (4 frames), C A B

    assert main(["metrics"]) == 0
    out = capsys.readouterr().out
    assert "Runs logged: 0" in out  # bench runs are not logged to runs.csv (no log_dir)


def test_ports(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["ports"])
    assert code in (0, 1)  # 1 = no serial ports on this machine, with a helpful message
    assert capsys.readouterr().out


def test_metric_helpers(tmp_path: Path) -> None:
    assert percentile([], 50) is None
    assert percentile([1, 2, 3, 4], 50) == 2.5
    runs = tmp_path / "runs.csv"
    runs.write_text(
        "timestamp,run_id,source,engine,stt_ms,gloss_ms,first_pose_ms,total_ms,steps,ok,reason\n"
        "t,1,transcribe,rules,800,2,950,2000,1,True,finished\n"
        "t,2,transcribe,rules,700,2,850,2000,1,True,finished\n"
        "t,3,say,rules,,1,3,1000,1,True,finished\n"
        "t,4,say,rules,,1,,1000,0,False,stopped\n",
        encoding="utf-8",
    )
    rep = latency_report(runs)
    assert rep["runs"] == 4
    assert rep["by_source"]["transcribe"]["median_ms"] == 900.0
    assert rep["by_source"]["say"]["count"] == 1
    assert latency_report(tmp_path / "missing.csv") == {"runs": 0, "by_source": {}}


def test_gloss_scores() -> None:
    s = gloss_scores(["ILY", "FS:CODE"], ["ILY", "FS:CODE"])
    assert s["exact"] and s["f1"] == 1.0
    s = gloss_scores(["ILY"], ["ILY", "-is"])
    assert not s["exact"] and s["precision"] == 1.0 and s["recall"] == 0.5
    assert gloss_scores([], [])["f1"] == 1.0
    summary = summarize_gloss_eval(
        [
            {"text": "a", "reference": ["ILY"], "predicted": ["ILY"], "rejected": 0, "latency_ms": 10},
            {
                "text": "b",
                "reference": ["3"],
                "predicted": ["?x"],
                "rejected": 1,
                "fallback": True,
                "latency_ms": 30,
            },
        ]
    )
    assert (
        summary["exact_pct"] == 50.0 and summary["invented_signs_rejected"] == 1 and summary["fallbacks"] == 1
    )
    assert summarize_gloss_eval([]) == {"sentences": 0}
