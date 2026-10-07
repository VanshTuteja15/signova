# Evaluation

The project plan judges SIGNOVA on four numbers, measured the same way before and after tuning, plus
speech accuracy. Every number below comes from a file on the laptop, so the report's results section
is charts over those files.

| Metric | Target (plan) | How it is measured | Tool |
|---|---|---|---|
| Sign recognition | ≥ 80% of Tier 1 signs | a fluent signer names each sign in random order without seeing the dashboard | dashboard **Evaluation** tab → `data/eval/<session>.csv` + `_summary.csv` |
| End-to-end latency | ≤ 3 s median | end of speech (button released) → first pose sent, over 20 spoken sentences | logged automatically to `data/logs/runs.csv`; `signova metrics` |
| Repeatability | 0 faults, ≤ 1 wrong pose | same 10-sign sequence 20 times | `signova bench --mode serial --port COM5 --runs 20` + observer notes |
| Gloss accuracy | Claude beats the rules baseline; 0 invented signs | 30+ sentences vs reference glosses | `signova gloss-eval -v` |
| Speech accuracy (WER) | reported, no target | word error rate on the same sentences, quiet room | see below |

## 1. Sign recognition session with a fluent signer

**Before the session**

* Get informed consent (what is recorded: the signer's typed answers and confidence, optionally a
  participant label such as "P1"; **no names unless you type one on purpose**). Offer thanks or
  compensation for their time. Check with your instructor whether SAIT needs an approval for
  volunteer testing (an open question in the plan).
* Calibrate the hand ([CALIBRATION.md](CALIBRATION.md)) and run `signova bench` once to check for
  faults.
* Seat the signer in front of the **hand**, facing the palm. They must not see the laptop screen
  (the Evaluation tab never shows the answer, and the Live tab shows "?" during evaluation runs, but
  the 3D hand would still give it away).

**Running it** (operator at the laptop, **Evaluation** tab)

1. Number of signs (e.g. 2 × the 18 Tier 1 signs = 36), tiers, random order, optional participant label → **Start session**.
2. **Perform next sign**: the hand performs a sign chosen at random. **Repeat** performs it again if
   the signer asks.
3. Type exactly what the signer says or signs back ("A", "letter A", "I love you", "3", "three" all
   match). Pick their confidence (1 = guess, 5 = certain) → **Record answer**.
4. After the last sign the page shows recognition % per sign and overall, and saves two CSVs in
   `data/eval/`. Use **Export trials CSV** / **Export summary CSV** to download them.
5. Ask open questions and write the signer's feedback down, **including negative feedback**: which
   handshapes were unclear, what they would actually want. It goes in the report (see ETHICS.md).
6. Mark the poses the signer confirmed as validated in Pose Studio (reviewer + date).

**CSV columns**

* `<session>.csv`: session_id, participant, trial, sign_id, tier, guess, confidence, correct (0/1),
  performed_at, answered_at, response_s
* `<session>_summary.csv`: sign_id, shown, correct, recognition_pct, plus an `OVERALL` row

## 2. End-to-end latency

Every run appends a row to `data/logs/runs.csv`: timestamp, source (`transcribe` for speech, `say`
for typed text), engine, `stt_ms`, `gloss_ms`, `first_pose_ms`, total_ms, steps, ok, reason. The text
itself is never logged.

For the report: speak 20 sentences with the mic (hold-to-talk), then run

```powershell
.venv\Scripts\signova metrics
```

and report the **median and p90 of `first_pose_ms` for source `transcribe`**. It includes the upload,
speech-to-text, gloss and sequencing. The dashboard's Live tab shows the same numbers per sentence.

## 3. Repeatability

```powershell
.venv\Scripts\signova bench --mode serial --port COM5 --runs 20
# default sequence: "one two three four five i love you no cab" = 10 signs (1 2 3 4 5 ILY NO C A B)
```

The tool counts **transport faults** (timeouts, errors, lost connection) and writes
`data/logs/repeatability_<timestamp>.csv`. **Wrong poses can only be judged by watching the hand.**
Have a second person tick each run on paper and record the count next to the CSV. Without hardware,
`--mode emulator` exercises the whole protocol path.

## 4. Gloss accuracy (Claude vs the rules baseline)

```powershell
.venv\Scripts\signova gloss-eval -v                 # both engines (Claude needs ANTHROPIC_API_KEY)
.venv\Scripts\signova gloss-eval --engine rules     # baseline only
```

For each sentence in `tests/data/sentences.yaml` the tool compares the engine's gloss with the
reference and prints exact-match %, mean token F1, **invented signs** (items the validator had to
reject; the target is 0 reaching the hand, which the validator guarantees) and how often Claude fell
back to the rules engine.

> **Honest caveat.** The 40 references in `tests/data/sentences.yaml` are the team's draft references and
> currently equal the rule-based output (they double as the rules engine's regression tests), so the
> baseline scores 100% on them by construction. **Before reporting**, have the signer (or the
> sign-language owner) write the reference gloss for each sentence: ASL-like order (time / topic
> first), which words should be dropped, and so on. Save it as a separate file with the same format and
> pass it with `--refs`. Only then is "Claude beats baseline" meaningful.

## 5. Speech accuracy (WER)

1. Tick **Save recordings for testing** on the Live tab (clearly labelled; the files go to
   `data/recordings/` on this laptop only). Say the 30 sentences in a quiet room.
2. Transcribe each file with both engines:
   `.venv\Scripts\signova transcribe data\recordings\<file>.webm --engine whisper`
3. Word error rate = (substitutions + deletions + insertions) / words in the reference. Report it
   for `base.en` and, if time allows, `small.en` (`SIGNOVA_WHISPER_MODEL=small.en` in `.env`).
4. Untick the option and delete the recordings when you are done.

Vosk grammar mode is "demo-safe": it only recognises library phrases, number words and spelled
letters, so free-form sentences come back empty. That is by design. Report it separately.

## Report template

| Metric | Value | Target | Notes |
|---|---|---|---|
| Recognition, Tier 1 | __ % (n = __ trials, __ signers) | ≥ 80% | per-sign table from `_summary.csv` |
| Median latency (speech) | __ ms (p90 __ ms, n = 20) | ≤ 3000 ms | `signova metrics` |
| Repeatability | __ faults, __ wrong poses / 20 runs | 0 / ≤ 1 | `repeatability_*.csv` + observer |
| Gloss exact match | rules __ % · Claude __ % | Claude > rules | `signova gloss-eval`, signer-written refs |
| Invented signs reaching the hand | 0 | 0 | validator |
| WER (Whisper base.en) | __ % | — | 30 sentences |
