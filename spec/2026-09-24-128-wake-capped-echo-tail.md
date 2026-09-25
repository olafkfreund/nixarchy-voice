---
status: draft
issue: 128
intent: intent/2026-09-24-128-wake-capped-echo-tail.md
---

# Spec: a wake capture's cap counts only the time the microphone was open

Refs #128. Line numbers are from `main` at `50dcf99`, the same commit the
intent cites; they are unchanged on this branch (`37e64e6` adds only the
intent).

Baseline: 1085 tests pass at `50dcf99` (`nix develop -c pytest tests -q`).

## Decisions on the intent's open questions

The intent was approved without answers, so each question is decided here
with its reasoning. Any of them can be rejected at this gate.

1. **(a): `_record` stamps the open, and `_wake_turn` measures from it.
   Teardown stays inside `capped`.**
   - `_record` sets `self._opened = time.monotonic()` on the line before
     `self._mic_shut.clear()` (`local_engine.py:405`). That is after #114's
     tail loop (`:377-389`) and the resets (`:390-391`), and it is the point
     the rest of the engine already treats as "the capture is open":
     `_mic_shut` is cleared there and set again in the `finally` (`:416`).
     Taking it beside the resets instead would be the same instant in
     practice (no `await` between them), but tying it to `_mic_shut.clear()`
     keeps one definition of "open".
   - `_wake_turn` drops its local `opened` (`:478`) and computes
     `capped = time.monotonic() - self._opened >= self.config.wake_max_seconds`
     (`:482`). Nothing else in the method changes.
   - **Not (b), the first level callback.** It is later than the open by
     `pw-record`'s startup, so it undercounts. A recorder that never reports
     a level (the `EngineTestCase.ears` fakes, a broken meter) would leave it
     unset and need a fallback. And it is `_onset`'s job for #86's spoken
     confirm, which is "first loud frame", not "first frame".
   - **Not (c), `record_utterance` reports its cap.** It is the exact answer,
     but it changes the return type shared by `_turn`, `_wake_turn` and every
     recorder fake (`Room.record`, `ears`, `OnsetEars` and the ad-hoc
     lambdas). (a) is already exact enough, see below, for one float.
   - **Why (a) cannot miss a real cap.** `record_utterance` starts its own
     clock after `Popen` (`listen_local.py:310`) and stops on
     `now - started > max_seconds` (`:332`). `_opened` is taken before that
     thread starts, so `now - _opened >= now - started`. A capture that hit
     its cap is always judged capped. #72's rule cannot be weakened by this.
   - **Teardown stays in.** `pw-record`'s terminate-and-reap (up to 2 s,
     `listen_local.py:333-338`) was inside `capped` before #114 and is
     normally short. It can only push a capture that ended just under the
     cap over it, which errs towards "only wake": the safe side of #72.
     Taking it out would need a second stamp from the recorder thread,
     i.e. (c) by another name. Not worth it for a case nobody has seen.
   - `_opened` is initialised to `0.0` in `__init__` beside `_onset` and
     `_last_loud` (`:246-249`), with a one-line comment naming #128. It is
     set on every capture, `_turn`'s too; `_turn` just never reads it.

2. **Elapsed time only. `_last_loud` is the wrong thing to use.**
   `_last_loud` marks the last loud frame of the capture (#80), not when the
   microphone opened, so it cannot replace the start of the interval. As an
   extra "was it cut off mid-word" signal it adds nothing: a capture only
   runs to its cap if the hold never expired, so its last loud frame is
   always within `hang` of the end. A capture with no loud frame returns
   `b""` and never reaches the `capped` check. The only captures where it
   would differ are the ones the intent names as its weakness: TV noise
   keeps it fresh, so combining it would still say "capped", and there is
   no case where it would correctly say "not capped" when elapsed time says
   "capped". It also stays #80's: read only by `_hear`'s ENDPOINT span, reset
   only in `_record`. `capped` does not read it.

3. **Stepped clock for the new tests, both cases, and the old cap test
   stays where it is.**
   - Both the short case (only the echo tail) and the long case (she is
     still speaking when the wake capture is asked for) are tested. The
     long case is the one that bites in practice (intent: replies of
     5-10 s); the short case is the one that proves the tail itself is out.
   - A stepped-clock cap test is added beside them: a capture that really
     runs to the cap after a wait still only wakes. That is #72's rule on
     the path this change touches.
   - `test_a_recording_cut_off_by_the_cap_only_wakes`
     (`tests/test_local_engine.py:1687`) is **not** moved. With
     `wake_max_seconds=0`, `now - _opened >= 0` is always true, so it still
     passes on the real clock and still guards #72 cheaply. Moving it would
     duplicate the new stepped cap test.

## Design

One file of code, one of tests.

`src/omarchy_voice/local_engine.py`:

- `__init__`: `self._opened = 0.0`, commented "when the latest capture
  opened, after her echo tail: a wake capture's cap is timed from it (#128)".
- `_record`: `self._opened = time.monotonic()` immediately before
  `self._mic_shut.clear()`.
- `_wake_turn`: remove `opened = time.monotonic()`; `capped` reads
  `self._opened`. The #72 comment above it gains "from when the microphone
  opened, not from when the wait for her began (#128)".

No new config knob, no change to `record_utterance`, no change to `_turn`.

### Invariants (must hold after the change)

- **#114:** the tail loop, `ECHO_TAIL_SECONDS`, `_ShutForHer`, the `None`
  return and the `_mic_shut` clear/set pair behave exactly as now. The new
  line adds no `await`, so nothing can run between the stamp and the clear.
- **#86:** `_onset` is reset and set as now; `_heard_at()` is unchanged.
- **#80:** `_last_loud` is reset and set as now; `_hear`'s ENDPOINT span
  reads it and nothing else does.
- **#120:** `_listen_loop`'s recovery is untouched. The change adds no new
  exception path: `_opened` is always set before the capture thread starts,
  and it has a value from `__init__` before any capture.
- **#72:** a capture that ran to `wake_max_seconds` is never acted on.

## Alternatives rejected

- **Subtract the tail wait from `opened`** (time the loop, subtract it).
  Same answer with more code and a second place that knows about the loop.
- **(b) first level callback** and **(c) recorder reports its cap**:
  decision 1.
- **Combine with `_last_loud`**: decision 2.
- **Move the tail wait out of `_record` into its callers.** Undoes #114's
  "every capture waits, whoever opens it". Far larger blast radius.

## Risks

- **Only where the wake word is on.** p620 has `wake_word = ""`, so this
  machine's behaviour does not change at all. It may be on elsewhere (any
  host with `wake_word` set and `barge_in` off); there it only turns some
  "wake only" outcomes into "acted on", and only for captures that ended
  under the cap.
- **A wrongly placed stamp.** If `_opened` were taken before the tail loop,
  the bug would stay; if after the capture, every capture would read as
  uncapped and #72 would break. Both are caught by the mutation checks below.
- **Stale value.** `_opened` is per-session state like `_onset`. `_record`
  is only entered from the listen loop, one capture at a time (`:421`,
  `:479`), so a second capture cannot overwrite it between `_record`
  returning and `_wake_turn` reading it.

## Verification

Fakes only. Never the microphone. Every run with
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`. Tests go in a new
`WakeCapTests(SteppedRoomCase)` next to `HerVoiceGatesTheMicTests`, using
#114's `Room` and `stepped_sleep()` so the tail wait moves the fake clock.
Each drives the real `_listen_loop` with `active = False`,
`_wake_ready = True`, `wake_word="oma"`, and a first `"wait"` capture that
her reply shuts (as `test_her_reply_does_not_wake_listening`, `:1314`).

Tests written first; the first two must **fail on `main`**:

1. `test_a_wake_right_after_her_echo_tail_is_acted_on`. A one-sentence
   reply via `_inject`, then a wake capture of "Oma, close the browser."
   The capture itself runs about 1.15 s on the Room (0.5 s of speech, the
   0.6 s hold, one frame). `wake_max_seconds` is set between the capture
   and capture + `ECHO_TAIL_SECONDS` (the plan pins the value). Expect
   `brain.asked` to end with `"close the browser."`. Main: capped, only wakes.
2. `test_a_wake_while_she_is_still_speaking_is_acted_on`. A reply of
   several sentences (1 s each on the Room) is still playing when the wake
   capture is asked for, so `_record` waits in `_speech.join()`.
   `wake_max_seconds` is set below the reply's length and above the capture.
   Expect the instruction to be asked, and `room.opens` to show the capture
   opened at least `ECHO_TAIL_SECONDS` after `_voice_until` (#114 kept).
   Main: wait ≥ cap, only wakes.
3. `test_a_wake_capture_cut_by_the_cap_after_a_reply_only_wakes`. Same
   reply, but `wake_max_seconds` shorter than the user's 0.5 s of speech,
   so the Room stops on its cap mid-word. Expect `active` true and
   `brain.asked` without the instruction. Passes on main and after (#72).

Existing tests unchanged and green: `WakeWordTests`, `OneBreathTests`
(including `:1687`), `HerVoiceGatesTheMicTests`, the #80 endpoint tests and
the #86 spoken-confirm tests. Full suite: 1085 + 3 = 1088 pass.

Mutation checks (each applied alone, then reverted; each must turn at least
one of the tests above red):

- Stamp `_opened` before the tail loop → tests 1 and 2 fail.
- Restore the local `opened` in `_wake_turn` → tests 1 and 2 fail.
- Stamp `_opened` after `record_utterance` returns → test 3 fails.
- `capped = False` → test 3 and `:1687` fail.
