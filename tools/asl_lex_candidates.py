"""Suggest new library candidates from ASL-LEX: one-handed signs without path movement.

ASL-LEX (https://asl-lex.org) is a lexical database of ASL signs. Download its CSV yourself and
check its licence; SIGNOVA does not ship ASL-LEX data.

    .venv\\Scripts\\python tools\\asl_lex_candidates.py ASL-LEX_signdata.csv --out candidates.csv

Column names differ between ASL-LEX versions, so they are auto-detected (any column whose name
contains "signtype" / "movement" / "lemma" or "entryid" / "frequency") and can be overridden.
A candidate still needs a fluent signer to confirm the handshape is one this hand can make.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Iterable
from pathlib import Path

NO_MOVEMENT = {"", "none", "no movement", "nomovement", "no_movement", "0", "n/a"}


def find_column(headers: Iterable[str], *needles: str) -> str | None:
    for needle in needles:
        for h in headers:
            if needle in h.lower().replace(" ", "").replace("_", "").replace(".", ""):
                return h
    return None


def filter_candidates(
    rows: list[dict[str, str]],
    type_col: str,
    move_col: str,
    one_handed: str = "one",
    no_movement: set[str] | None = None,
) -> list[dict[str, str]]:
    """Keep one-handed signs (type contains `one_handed`) whose movement is in `no_movement`."""
    stay = no_movement if no_movement is not None else NO_MOVEMENT
    out = []
    for row in rows:
        sign_type = (row.get(type_col) or "").strip().lower().replace(" ", "")
        movement = (row.get(move_col) or "").strip().lower()
        if one_handed in sign_type and movement in stay:
            out.append(row)
    return out


def sort_by_frequency(rows: list[dict[str, str]], freq_col: str | None) -> list[dict[str, str]]:
    if not freq_col:
        return rows

    def key(r: dict[str, str]) -> float:
        try:
            return -float(r.get(freq_col) or 0)
        except ValueError:
            return 0.0

    return sorted(rows, key=key)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="ASL-LEX CSV you downloaded")
    ap.add_argument("--out", default="asl_lex_candidates.csv")
    ap.add_argument("--type-col")
    ap.add_argument("--move-col")
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args(argv)

    with Path(args.csv).open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        headers = reader.fieldnames or []
    type_col = args.type_col or find_column(headers, "signtype")
    move_col = args.move_col or find_column(headers, "pathmovement", "movement")
    if not type_col or not move_col:
        print(
            f"Could not find the sign-type / movement columns. Columns: {', '.join(headers)}", file=sys.stderr
        )
        print("Pass them with --type-col and --move-col.", file=sys.stderr)
        return 2
    name_col = find_column(headers, "lemma", "entryid", "gloss")
    freq_col = find_column(headers, "signfrequency", "frequency")
    picked = sort_by_frequency(filter_candidates(rows, type_col, move_col), freq_col)[: args.limit]
    keep = [c for c in (name_col, type_col, move_col, freq_col) if c]
    with Path(args.out).open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keep, extrasaction="ignore")
        w.writeheader()
        w.writerows(picked)
    print(f"{len(picked)} candidate(s) written to {args.out} (columns: {', '.join(keep)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
