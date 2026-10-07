# Sign library

`signs/library.yaml` holds every sign the hand can perform. It is validated with a pydantic schema
(`signova/library.py`) every time it is loaded or saved; `signova check` prints a summary.

> **Every pose is a draft** until a fluent ASL signer has checked it. The seed poses are rough
> approximations made for testing the pipeline. Reference for the manual alphabet: Dr. Bill Vicars'
> fingerspelling pages at lifeprint.com. Do not present a sign as correct until a signer has
> reviewed it in Pose Studio (which records the reviewer and date).

## Format

```yaml
version: 1
rest: {thumb: 0.15, thumb_rot: 0.2, index: 0.15, middle: 0.15, ring: 0.18, pinky: 0.2, wrist: 0.5}
signs:
  ILY:
    kind: word            # word | letter | number
    tier: 1               # 1 must have, 2 should have, 3 stretch
    english: ["i love you", "love you", "ily"]   # phrases the rule-based gloss matches
    requires: [thumb, thumb_rot, index, middle, ring, pinky]   # joints the hand must have
    validated_by_signer: false
    reviewer: null        # set by Pose Studio when a signer validates it
    validated_on: null    # YYYY-MM-DD
    notes: "Thumb, index, pinky extended"
    frames:
      - pose: {thumb: 0, thumb_rot: 0, index: 0, middle: 1, ring: 1, pinky: 0}
        hold_ms: 800      # optional; default 800 for words/numbers, 450 per fingerspelled letter
        move_ms: 300      # optional; default from config/hand.yaml
```

Rules the schema enforces:

* Sign IDs are 1–16 characters of `A–Z 0–9 _`. Letters with tier 1–2 are a single letter; numbers use digits.
* Joint values are 0..1. **Missing joints in a frame default to `rest`.**
* An English phrase may belong to only one sign. Phrases are stored lower-case.
* Unknown fields are rejected, so a typo such as `hold: 800` fails loudly instead of being ignored.
* The file is read as YAML 1.2, so `NO` stays a string. Keys that older YAML readers would turn into
  booleans or numbers (`"NO"`, `"1"`, `"Y"`…) are always written quoted.

## Availability

A sign is **available** only if the hand config has every joint in its `requires` list. Unavailable
signs are removed from the gloss vocabulary (rules and Claude's schema enum), can't be fingerspelled,
and are greyed out in the dashboard with the reason. With the default hand:

* **U** and **V** need `spread_index_middle` and are unavailable.
* **J** needs `wrist` and is available.
* With `config/hand.amazing.yaml` (no thumb), every seed sign is unavailable. That is intentional:
  it shows availability following the hardware.

## Seed signs (all drafts)

Values: thumb, thumb_rot, index, middle, ring, pinky (wrist 0.5 unless noted).

| ID | kind | tier | pose | note |
|---|---|---|---|---|
| 1 | number | 1 | .7 .9 0 1 1 1 | |
| 2 | number | 1 | .7 .9 0 0 1 1 | |
| 3 | number | 1 | 0 0 0 0 1 1 | ASL 3 uses thumb, index, middle |
| 4 | number | 1 | .8 .95 0 0 0 0 | same handshape as B (no finger spread) |
| 5 | number | 1 | 0 0 0 0 0 0 | |
| ILY | word | 1 | 0 0 0 1 1 0 | thumb, index, pinky extended |
| A | letter | 1 | .05 .1 1 1 1 1 | |
| B | letter | 1 | .8 .95 0 0 0 0 | same handshape as 4 |
| C | letter | 1 | .35 .45 .45 .45 .45 .45 | |
| D | letter | 1 | .6 .85 0 .72 .75 .78 | |
| E | letter | 1 | .85 1 .8 .8 .8 .8 | |
| F | letter | 1 | .55 .75 .72 0 0 0 | |
| I | letter | 1 | .7 .95 1 1 1 0 | |
| L | letter | 1 | 0 0 0 1 1 1 | |
| O | letter | 1 | .5 .7 .62 .62 .62 .62 | |
| S | letter | 1 | .75 1 1 1 1 1 | |
| W | letter | 1 | .7 .95 0 0 0 1 | |
| Y | letter | 1 | 0 0 1 1 1 0 | |
| NO | word | 2 | 4 frames alternating (.2 .55 .15 .15 1 1) / (.5 .8 .55 .55 1 1), hold 220 ms | index + middle snap onto the thumb |
| J | letter | 3 | (.7 .95 1 1 1 0, wrist .5) → (wrist 1.0), hold 300 ms | requires wrist |
| U, V | letter | 3 | index + middle up; V spread 1.0 | require `spread_index_middle` |

**Known ambiguities.** Without an abduction (spread) joint, **4 and B share a handshape** and **U and V
look the same**. Several letters (G, H, K, P, Q, R, T, X, Z…) need wrist orientation, finger crossing
or motion this hand can't do, so they are not in the library and words containing them are skipped
with the missing letters named.

## How the gloss uses the library

* Multi-word `english` phrases are matched first (longest match), then single words, digits and number
  words.
* A word is fingerspelled only if **every** letter is an available letter sign and it has at most
  8 letters.
* Doubled letters (the two O's in COOL) get a small "bounce": 15% toward rest for 150 ms, then back.

## Adding or changing a sign

**Pose Studio (recommended):** load a sign (or *new sign*), move the sliders (tick *Drive the hand
live* to see it on the real hand), add frames for motion signs, set holds, and **Save to library**.
The server validates the sign, keeps a backup at `signs/library.yaml.bak`, and preserves the
file's comments. The new sign is in the gloss vocabulary immediately: e.g. save `ROCK` with
English phrase `rock on`, then type "rock on" on the Live tab. For a new sign, Pose Studio works out
`requires` from the joints that differ from rest. For an existing sign it keeps the stored list.

**Validation by a signer:** in Pose Studio tick *Validated by a fluent signer*, enter the reviewer and
date, and save. The Library tab then shows a green *validated* badge instead of *draft*.

**By hand:** edit `signs/library.yaml`, run `signova check`, restart the server.
