---
status: draft
issue: 128
author: olafkfreund
---

# Intent: a wake capture's cap counts only the time the microphone was open

Refs #128. Introduced by #127 (#114). Line numbers are from `main` at
`50dcf99` (after #138).

## Problem

The wake path decides whether a one-breath instruction ("Oma, turn it
down") is acted on or only wakes listening (#72). It calls a capture
"capped" by timing the whole `_record` call, and since #114 that call
starts by waiting out her voice. So the wait is counted as capture time.

How it is computed today (`src/omarchy_voice/local_engine.py`):

- `_wake_turn` takes `opened = time.monotonic()` (`:478`) **before**
  `await self._record(self.config.wake_max_seconds, hold)` (`:479`), then
  `capped = time.monotonic() - opened >= self.config.wake_max_seconds`
  (`:482`). `rest and not capped` (`:491`) is the only way the instruction
  runs; otherwise it logs `wake    heard …` and only wakes.
- Between `opened` and the microphone opening, `_record` now does, with
  `barge_in` off (the default, `config.py:345`):
  - a loop (`:377-389`) that, while `_voice_until == math.inf`, awaits
    `self._speech.join()` (`:380`), i.e. **the rest of whatever she is
    saying**, then sleeps until `_voice_until + ECHO_TAIL_SECONDS`
    (`:385-389`, `ECHO_TAIL_SECONDS = 0.35`, `:48`);
  - the resets `self._onset = None`, `self._last_loud = None` (`:390-391`);
  - `self._mic_shut.clear()` (`:405`), the point where the capture really
    opens, then `record_utterance` in a thread.
- After the capture: the `finally` sets `_mic_shut` (`:416`), and
  `record_utterance` has already terminated `pw-record` with up to a 2 s
  wait (`listen_local.py:333-338`). That teardown is also inside
  `opened`…`capped`, but it was there before #114 and is normally short.
- `record_utterance` enforces its own cap from its own start
  (`listen_local.py:310`, `:332`, `now - started > max_seconds`). So the
  audio is never longer than `wake_max_seconds`; only the engine's
  judgement of "capped" is wrong.

The result: a capture that opened after she spoke is judged capped when
**wait + capture ≥ `wake_max_seconds`**, not when the capture hit its cap.
A complete instruction is dropped to "wake only" and has to be said again.

How often this bites (estimate, no microphone used):

- `wake_max_seconds = 8.0` (`config.py:397`). The hold is now 0.6 s
  (`end_of_speech_seconds`, `config.py:362`, #138).
- The echo tail alone is 0.35 s. With only the tail, a capture must run
  7.65 s to be misjudged. "Oma, turn it down" is about 2 s of speech plus
  the 0.6 s hold, so the tail alone almost never trips it.
- The larger term is waiting for her to finish. She speaks while listening
  is off: a typed line (`_inject`, see `test_her_reply_does_not_wake_listening`,
  `tests/test_local_engine.py:1314`), a typed or keybind confirm. If the
  wake capture is entered while a reply is still playing, the whole
  remaining reply is counted. Her replies in `session.log` often run 5-10 s
  (for example the 15 Sep lines at 23:17:27 and 23:18:23). Any wait of
  roughly 5 s or more, followed by a prompt one-breath instruction, is
  enough. A wait of 8 s or more marks **every** such capture capped.
- It only affects the **first** wake capture after she speaks; the next one
  has no wait.
- The log cannot confirm a rate. `~/.local/state/omarchy-voice/session.log`
  (`config.LOG_FILE`, `config.py:103`) has 1342 `wake` lines, all on
  12 Sep, before #72 added one-breath instructions and before #114: 1333
  `ignored`, 8 `listening locally`, 1 `heard 'Oma?'`, 0 `acting on`. This
  machine's `config.toml` now has `wake_word = ""`, so the wake path is off
  here and has produced nothing since. `trace_timings = true` has only just
  been turned on and records no `wake` data.

So the impact is low, as the issue says: it needs the wake word enabled,
a reply while listening is off, and an instruction given promptly after it.
But when it happens it is silent: the user sees a wake, not a failure.

## Proposed outcome

- `capped` is true only when the capture itself ran to `wake_max_seconds`.
  Time spent waiting for her voice and its echo tail does not count.
- A one-breath wake instruction that follows a reply, and is shorter than
  the cap, is acted on exactly as it is in a quiet room.
- A capture that really hits the cap still only wakes (#72's rule is kept,
  and `test_a_recording_cut_off_by_the_cap_only_wakes`,
  `tests/test_local_engine.py:1687`, still passes).
- A test proves it on the stepped clock: a wake capture right after a reply,
  shorter than the cap, is acted on.

## Affected users and systems

- `src/omarchy_voice/local_engine.py`: `_wake_turn` (`:468-511`) and the
  capture-open point in `_record` (`:361-417`).
- `tests/test_local_engine.py`: the wake tests (`WakeWordTests`, `:1651`) and
  `SteppedRoomCase` / `HerVoiceGatesTheMicTests` (`:1167`, `:1230`).
- Anyone with `wake_word` set and `barge_in` off. Not this machine today
  (`wake_word = ""`).
- Not the normal `_turn` path: it has no `capped` check.

## Constraints

- Keep #114's gate intact: the echo tail wait, `_ShutForHer`, `_mic_shut`
  and the `None` return must behave exactly as now.
- Keep #80's `_last_loud` semantics for `_hear`'s ENDPOINT span.
- Keep #72's rule: a capture that hit the cap is never acted on.
- `record_utterance`'s signature is shared with `_turn` and many test fakes
  (`Room.record`); changing what it returns has a wide blast radius.
- Never record from the microphone, in tests or to measure. Tests use the
  `Room` fake and the stepped clock; runs use
  `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`.
- Small change: no new config knob.

## Open questions

1. **Where does the "capture opened" timestamp come from?**
   (a) `_record` records `self._opened = time.monotonic()` beside the
   `_onset`/`_last_loud` resets (`:390-391`) or at `_mic_shut.clear()`
   (`:405`), as #80's plan suggested (`plan/2026-09-24-80-learned-end-of-turn.md`,
   "Issue #128 is separate"); (b) `_wake_turn` measures from the first level
   callback instead; or (c) `record_utterance` reports whether it stopped on
   its cap, which is the true answer but changes a shared return type.
   Should `capped` still include `pw-record`'s teardown, as it does now?
2. **Should `capped` use `_last_loud`?** A capture that hit the cap was
   still loud at its end, so `_last_loud` near the end of the capture is a
   sharper "cut off mid-word" signal than elapsed time. But it measures
   something different: a room that goes quiet just before the cap would
   no longer count as capped, and TV noise (1333 `ignored` lines on 12 Sep)
   keeps `_last_loud` fresh. Keep it elapsed-time only, or combine?
3. **Test approach.** Reuse #114's `SteppedRoomCase` with `stepped_sleep`
   so the tail wait moves the fake clock, drive a reply (`_inject`) while
   listening is off, then a wake capture of, say, 3 s with
   `wake_max_seconds` just above 3 s + tail, and assert the instruction is
   asked. Should it also cover the long case (her still speaking when the
   wake capture starts, wait ≥ `wake_max_seconds`), and should
   `test_a_recording_cut_off_by_the_cap_only_wakes`, which uses
   `wake_max_seconds=0` on the real clock, move to the stepped clock too?
