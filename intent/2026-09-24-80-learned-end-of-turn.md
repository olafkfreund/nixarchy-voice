---
status: draft
issue: 80
author: olafkfreund
---

# Intent: stop deciding "the sentence is over" with a fixed stretch of quiet

Refs #80. Split out of #72, which replaced the 1.5 s hold with 0.8 s.

## Problem

The local engine decides a sentence has ended when it hears a fixed
stretch of quiet. That one number cannot be right for both kinds of
pause:

- If it is too short, it cuts off someone who stops to think in the
  middle of an instruction. The first half goes to whisper and the brain
  as a complete instruction.
- If it is too long, everyone else waits through dead air at the start
  of every turn. The top goal is faster answers, and this wait comes
  before any other work begins.

Where the number is (on `origin/main`, fc33ae2):

- `end_of_speech_seconds: float = 0.8` (`src/omarchy_voice/config.py:373`).
  The comment says to raise it if you are cut off and to look at the
  `heard` lines (`config.py:370-372`).
- It is passed as `hang` to `_record` for normal turns
  (`local_engine.py:309-310`) and for wake-word turns
  (`local_engine.py:363-365`). The actual cut is made in
  `listen_local.record_utterance`: after speech has been heard, recording
  stops once `now - heard_at > hang` (`listen_local.py:276`). "Speech"
  means one RMS level threshold (`silence_level`, 0.02, `config.py:364`).
  Pausing, trailing off and finishing all look the same to it.
- The default in the function signature is still 1.2 s
  (`listen_local.py:42`). The engine always passes the configured value.

**What is recorded today.** With `trace_timings` on (`config.py:468`,
off by default), `_hear` records an `endpoint` span of exactly `hold`
seconds (`local_engine.py:410-412`, `trace.py:41-44`). That span is the
configured constant, not a measurement, so it is always 0.80 s. It shows
what the hold costs. It cannot show whether a different rule would have
ended the sentence sooner or later. This machine's config leaves the flag
off, and `session.log` has **0 `TIMING` lines**.

**The cut-off side is also unmeasured.** Out of 94 `heard` lines in
`session.log` (11–24 Sep), 3 end on a fragment such as `,`, `...`, "and"
or "the". All 3 were recorded under the old holds. **No `heard` line
appears after #72 merged** (24 Sep 12:26). So 0.8 s has not been tried on
a real turn yet. How often it cuts people off, and how much dead air it
adds, is unknown.

## Proposed outcome

- On real turns, the time from the user's last word to the start of
  transcription is shorter than today's 0.8 s for a sentence that has
  clearly finished.
- A pause in the middle of an instruction is cut off **no more often**
  than with the fixed hold. `heard` lines are the evidence.
- The `endpoint` phase in `TIMING` records how long end-of-turn
  detection actually took, not a copy of the constant. The effect of any
  change can then be read from real turns.
- If a learned detector is missing or fails, the engine falls back to
  the fixed hold. Listening never stops because of it.

## Affected users and systems

- `listen_local.record_utterance` and `local_engine.py` (`_record`,
  `_turn`, `_wake_turn`, `_hear`), `config.py`, `trace.py`.
- The wake-word path. It uses the same hold, and #72 relies on the
  timing to tell whether a one-breath wake was cut short.
- `nix/package.nix`. It already builds `whisper-cpp-vulkan` (lines 19
  and 32). Any model file or runtime would have to be packaged there.
- The user of this machine, who pays this wait on every turn and is the
  one cut off when it is wrong.

## Constraints

- **No new heavy dependency without an explicit decision.**
  `pyproject.toml` depends only on `websockets` and `mcp`. An ONNX
  runtime, PyTorch or a model framework would be a real change to what
  the package is.
- Offline and local. End-of-turn detection must not send audio anywhere.
  Every model and runtime is fetched and pinned by Nix, not downloaded at
  run time.
- CPU cost matters. This decision runs on every audio frame while the
  microphone is open.
- #72 (merged) set 0.8 s and added the `endpoint` phase. #120 (merged)
  changed `_listen_loop`. #114 (in progress) changes `_record`. Anything
  here must be based on #114 after it merges.
- Decide from real `TIMING` lines and `heard` lines, as #80 says. Today
  there are none from after #72.

## Open questions

1. **Measure first?** Should there first be a baseline: `trace_timings`
   turned on for a period, the `endpoint` phase changed to record real
   detection time, and a count of `heard` lines that look cut off? Or is
   the case for a detector clear enough already?
2. **Which candidate, if any.** Checked 24 Sep 2026. None has been
   chosen:
   - **Silero VAD**: MIT, about 2.2 MB ONNX. Issue #80 says whisper-cpp
     1.9.2 ships it (`whisper-server --vad`), which could mean no new
     runtime. This has **not been checked** against the whisper-cpp that
     `nix/package.nix` pins. It detects speech and silence, not
     end-of-turn, so it may only replace the RMS threshold.
   - **smart-turn v3** (pipecat-ai): BSD-2-Clause, about 8M parameters
     (reported as 8.7 MB int8), Whisper-Tiny encoder with a classifier.
     It reads the raw audio. It needs an ONNX runtime, which would be a
     new dependency.
   - **LiveKit turn-detector**: LiveKit Model License (not OSI), about
     137–165 MB quantized ONNX. It works on the transcript text, so it
     needs streaming STT this engine does not have. Probably out of
     scope. Listed for completeness.
3. Is a cheaper step, such as a longer hold after a trailing fragment and
   a shorter one after a finished sentence, worth trying before any
   model?
4. Does the wake-word path get the same detector, or does it keep the
   fixed hold?
5. What false cut-off rate is acceptable against how much dead air is
   saved?

Sources: [smart-turn](https://github.com/pipecat-ai/smart-turn),
[smart-turn-v3 on HF](https://huggingface.co/pipecat-ai/smart-turn-v3),
[Silero VAD](https://github.com/snakers4/silero-vad),
[LiveKit turn-detector](https://huggingface.co/livekit/turn-detector).
