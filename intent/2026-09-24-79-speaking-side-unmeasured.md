---
status: approved
issue: 79
author: olafkfreund
---

# Intent: know how long she takes to start speaking, then make it shorter

Refs #79. Split out of #72, which dealt with the listening side.

## Problem

The goal is faster answers, and nobody knows how much of a turn goes on
speaking. The speaking path has three fixed costs. None of them is measured.

Line numbers are from `origin/main` (fc33ae2).

1. **ElevenLabs is fetched whole, decoded whole, then played.**
   `elevenlabs.synth` reads the entire MP3 response
   (`src/omarchy_voice/elevenlabs.py:147-148`), then passes all of it
   through ffmpeg (`elevenlabs.py:163`), then returns. Only after that does
   `Feedback._play` start `pw-cat` (`feedback.py:157-165`). So each
   sentence starts one full synthesis round trip plus one full decode late.
   The docstring records this as a known limit (`elevenlabs.py:119-121`).
   On this machine ElevenLabs is the voice and Piper is the fallback. No
   `elevenlabs failed` line appears in `session.log`, so ElevenLabs is
   the path that actually runs.
2. **Piper starts cold for every sentence.** `_speak_piper`
   (`feedback.py:167-200`) runs `piper --model …` once per sentence, so the
   ONNX voice is loaded again every time.
3. **Sentences wait for each other.** `_speech_loop`
   (`local_engine.py:228-248`) synthesises and plays one sentence at a
   time. Sentence N+1 is not sent for synthesis until sentence N has
   finished playing. With `barge_in = false`, which is this machine's
   setting, `_say` also blocks the brain's stream until the sentence has
   played (`local_engine.py:250-261`, `477`).
4. **0.35 s of forced quiet after every reply.** `_answer` sleeps
   `ECHO_TAIL_SECONDS` (`realtime.py:52`) when barge-in is off
   (`local_engine.py:512`). The microphone stays shut for that time, so
   every turn pays it before the next one can start. The realtime engine
   uses the same constant (`realtime.py:771`).

**What is recorded today.** `trace_timings` defaults to off
(`config.py:468`), and this machine's config does not turn it on.
`session.log` has **0 `TIMING` lines**. When the flag is on, the trace
starts when the user stops talking (`local_engine.py:410-412`) and records
`endpoint`, `transcribe`, `model-turn`, `tool`, `subprocess` and so on
(`trace.py:36-45`). **It has no speaking phase.** Each `model-turn` span
closes when the next sentence arrives and a new span opens before `_say`
(`local_engine.py:474-477`). So the time spent playing sentence N is
counted as model time for sentence N+1. The same code counts every
sentence after the first as a `continuation`, including sentences that
did not come after a tool call. Turning the flag on today would still
not show the speaking share that #79 asks for.

**The only numbers available** are rough, at 1 s resolution, from
`session.log` timestamps (11–24 Sep, local engine):

- `heard` → first `say`: 39 turns, median 2 s, max 8 s. This includes
  thinking time and does not include synthesis.
- `say` → next `say` within the same turn: 52 gaps, median 5 s. This is
  roughly synth + play of one sentence + the wait for the next one.

These show the speaking side is not small. They do not show how it breaks
down. It is **unmeasured**.

## Proposed outcome

- A real turn's `TIMING` line separates the time until her first
  sound, the time spent speaking, and the echo tail from model time. It
  still records only phase names and durations.
- Using those numbers, the time from the end of the user's sentence to
  the first audible word is shorter than today's baseline, measured on
  real turns with ElevenLabs. Piper as fallback is measured too.
- Nothing she says leaks back into the microphone as an instruction.

## Affected users and systems

- The local engine: `local_engine.py` (`_speech_loop`, `_say`, `_answer`
  and the echo tail), `feedback.py` (`_speak_now`, `_play`, `_speak_piper`),
  `elevenlabs.py` (`synth`), `trace.py`.
- The realtime engine, through the shared `ECHO_TAIL_SECONDS` constant.
- The Nix package (`nix/package.nix`), which provides piper, ffmpeg and
  pw-cat.
- The user of this machine, who waits through every turn.

## Constraints

- **Overlaps #114, which is being implemented now.** #114 changes
  `_record`, the echo tail and `_voice_until` in `local_engine.py`, because
  those make up the microphone gate. Any change here to the tail or to
  when speech ends must be based on #114 after it merges, not written
  alongside it. The echo tail belongs to that gate: shortening it is a
  safety change, not only a speed change.
- Measure before changing anything. The baseline comes from real
  `TIMING` lines.
- The trace still records no content (`trace.py` docstring). New phases
  are names and durations only.
- No new heavy dependency without an explicit decision. Anything added
  must be packaged in Nix. Piper must keep working offline, because it is
  the fallback for when the cloud is unavailable.
- The ElevenLabs → Piper → espeak-ng fallback order stays. A failure
  still falls through to the next voice and does not go silent.
- `pyproject.toml` depends only on `websockets` and `mcp`. The standard
  library is preferred.

## Open questions

1. Should the speaking phases be measured and shipped first, as their own
   change, with a decision about what to change after seeing real numbers?
   Or should the obvious fix (streaming ElevenLabs into `pw-cat`) ship in
   the same change?
2. Piper is only the fallback here. Is keeping it warm (one process per
   session) worth doing now, or only when Piper is the main voice?
3. Should sentence N+1 be synthesised while sentence N plays? That is
   more audio in flight. It interacts with barge-in and with #114's gate.
4. Should 0.35 s be measured against the real room (speaker lag plus
   reverb) before anyone lowers it? Or does it stay as it is and fall
   under #114?
5. `continuations` counts every sentence after the first as a tool round
   trip (`local_engine.py:474-476`). Should this intent fix that, or
   should it get its own issue?
6. Does it matter that ElevenLabs sends audio to the cloud, given the
   offline constraint? It is already the configured voice, so this is
   about new calls, for example a streaming endpoint, and not the current
   ones.
