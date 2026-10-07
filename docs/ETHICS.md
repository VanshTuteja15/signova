# Ethics and responsible AI

> **Research prototype. Shows ASL handshapes only. Not a translator or a replacement for interpreters.**
> This sentence appears in the dashboard header, the About tab, the README and the CLI.

## Why this matters

The main ethical risk is claiming more than the prototype does. ASL uses the hands, arms, head,
shoulders, torso and face. Facial expressions and other non-manual markers carry grammar. A single
robotic hand that forms handshapes is **not** ASL. Hearing-led sign-language gadgets, such as the
SignAloud gloves, have been widely criticised by Deaf people for exactly this: ignoring non-manual
markers, offering only one-way communication, and solving "assumed needs" instead of what Deaf users
asked for. SIGNOVA should learn from that and say so in the report.

## What the software does about it

| Commitment (project plan) | How it is built in |
|---|---|
| **Honest framing** | disclaimer in the UI header, About tab, README, CLI banner; the gloss note says what was dropped or skipped and why |
| **Constrained AI** | Claude can only output sign IDs from the library: they are an `enum` in the structured-output JSON schema, and a validator rejects unknown IDs, signs this hand can't make and words that can't be fingerspelled. Rejections are counted and shown. Unknown words are fingerspelled or skipped, never guessed |
| **Transparency** | the dashboard shows the transcript, every gloss item (sign / fingerspelled / dropped / skipped with reason), which engine produced it, and a *draft* badge on every sign not yet validated by a signer |
| **Privacy** | audio is processed in memory and never written to disk unless the user ticks the clearly labelled *Save recordings for testing* (the note next to the mic turns orange when it is on). The header shows which speech engine is in use and whether Claude (cloud) is in use. Only the transcript **text** goes to the Anthropic API, never audio. Run logs contain timing numbers only, not what was said |
| **Offline by default** | speech (faster-whisper / Vosk) and the rules gloss run on the laptop; Claude is opt-in and the system falls back to rules without it |
| **Deaf input** | Pose Studio records reviewer and date when a fluent signer validates a pose. The Evaluation tab runs blind recognition sessions. EVALUATION.md asks for the signer's feedback, including negative feedback, to go in the report |
| **Evaluation data** | no names unless a participant label is typed on purpose; CSV files stay in `data/eval/` on the laptop (git-ignored) |

## What the team still has to do

* Find at least one fluent signer (SAIT Accessibility Services, a Calgary Deaf organisation) and have
  them review every sign. Thank or compensate them for their time.
* Get consent before evaluation sessions; check whether SAIT needs an approval for volunteer testing.
* Read and cite the Deaf community's critiques of sign-language gloves (the plan lists two sources
  still to read) and say in the report what Deaf reviewers said they would actually want.
* Present the future work as what Deaf users asked for, not what the team assumed.
* If the optional pose-capture tool (`tools/capture_pose.py`) is used, only use video of people who
  agreed to it.

## Things this prototype must never be used for

* Communicating anything important to a Deaf person in place of an interpreter.
* Claims in the report or demo that it "translates" English to ASL.
