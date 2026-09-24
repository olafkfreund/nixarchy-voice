---
status: approved
issue: 80
spec: spec/2026-09-24-80-learned-end-of-turn.md
---

# Plan: the endpoint phase measures the wait, and the numbers say whether a detector is worth it

Refs #80. The issue stays open until decision 5's procedure has run on real
turns. Base: `origin/main` `b73a3f4` (#114 merged, PR #127). 1126 tests
collected there, and 92 `def test_` in `tests/test_local_engine.py`. Every
`file:line` below was checked against `b73a3f4`. This plan lands **last**,
after #111, #110, #121 and #79. Step 1 rebases onto #79, so re-check every
line after that rebase.

Files that change: `src/omarchy_voice/local_engine.py`,
`src/omarchy_voice/trace.py`, `tools/timing_report.py` (created by #79),
`tests/test_local_engine.py` and `tests/test_trace.py`. Nothing changes in
`listen_local.py`, `config.py`, `pyproject.toml` or `nix/package.nix`.

## Approved decisions, carried over from the spec

1. **Measure first. No detector ships in this change.** There are 0 `heard`
   lines since the 0.8 s hold was set (#72), and 0 `TIMING` lines at all.
   Today's `endpoint` span is not a measurement. `_hear` writes
   `now - hold .. now` (`local_engine.py:454-455`), so it always reads
   0.80 s. This change delivers the measurement the decision needs: a real
   endpoint time, the configured hold next to it, and a count of turns that
   look cut off.
2. **No candidate is chosen now. Each one is ruled in or out on evidence.**
   - **Silero VAD through whisper-cpp is rejected as an end-of-turn
     detector.** whisper-cpp 1.9.2's `--vad` options work on a clip that has
     already been sent for transcription. They cannot stop
     `record_utterance` (`listen_local.py:233`) while it is capturing, so
     they cannot end a turn sooner. They would also need a Silero ggml model
     packaged in Nix.
   - **smart-turn v3 is the candidate if decision 5 triggers.** It is
     BSD-2-Clause, small and audio-only, but it needs an ONNX runtime, which
     changes what the package is. That choice belongs at the follow-up's own
     intent gate, with this change's numbers in front of it.
   - **LiveKit turn-detector is rejected.** It needs streaming text, and
     this engine has none (whisper runs once, after the cut). Its licence is
     not OSI, and it is 137-165 MB.
3. **No fragment-aware hold in this change.** The engine only knows the
   words after the cut (`listen_local.py:276`). A fragment-aware hold means
   reopening the capture and stitching the two clips, which is a new capture
   flow inside `_record`. The report counts fragment endings, so the same
   numbers can justify it. Named follow-up: **"hold on after a trailing
   fragment"**, weighed against smart-turn at the same trigger.
4. **The wake path gets the same measurement and keeps the same fixed
   hold.** It uses the same `_record` and `_hear` (`local_engine.py:408`,
   `:414`). #72's `capped` (`:407-411`) is timed from `opened` with the
   wall clock, so this change does not touch it. Any future detector goes
   into `_record` and so reaches both paths. The follow-up must decide
   explicitly whether the wake path opts in.
5. **The acceptable rate, and what triggers a detector.** The baseline is 3
   fragment endings in 94 `heard` lines (3.2 %), all under older, longer
   holds. The procedure needs only config changes. It runs over blocks of at
   least 50 `heard` lines with `trace_timings = true`:
   - At 0.8 s, **2 or fewer** fragment endings in 50 is acceptable. **4 or
     more** means 0.8 s already cuts the user off.
   - If 0.8 s is acceptable, set `end_of_speech_seconds = 0.6` for the next
     50. If that is still 2 or fewer, keep 0.6. That saves 0.2 s per turn,
     and no detector is needed.
   - **Trigger for the smart-turn follow-up: 4 or more fragment endings in
     50 `heard` lines, at any hold of 0.8 s or less.** The follow-up must
     also show that the model's CPU time per pause on this machine is
     smaller than the hold it saves.
   - A large overshoot (`endpoint - hold`) means the recorder's teardown is
     the target, not the hold.
6. **Nothing new in `pyproject.toml` or `nix/package.nix`, and no new
   knob.** `end_of_speech_seconds` (`config.py:373`) is already tunable
   (`share/config.example.toml:103`, `tests/test_config.py:192`).
7. **Remember the last loud frame.** In `_record`'s `watch`
   (`local_engine.py:324-331`), every frame whose level is above
   `config.silence_level` sets `self._last_loud = time.monotonic()`. This is
   the same test `record_utterance` uses to move `heard_at`
   (`listen_local.py:266-270`). `_last_loud` is reset to `None` next to
   `_onset` (`:322`). That is after #114's echo tail wait (`:303-320`), so
   the wait can never set it. `_onset` is not changed. #86's checks keep
   reading it through `_heard_at()` (`:781-784`).
8. **Measure the endpoint** (`_hear`, `local_engine.py:443-466`). The trace
   and the ENDPOINT span start at `self._last_loud` when it is set and not
   later than `now`. Otherwise they start at `now - hold`, which is today's
   behaviour and covers the existing fakes. The span covers the hold, up to
   one 50 ms frame, and `pw-record`'s teardown (`listen_local.py:280-285`).
   It never covers #114's echo tail wait, which runs before the capture
   opens.
9. **Put the hold in the line.** `Trace.hold: float | None`. `_hear` sets
   it. `line()` prints `hold=0.80s` after `continuations=`. It is a
   configured duration, not content.
10. **Extend `tools/timing_report.py`** (added by #79). Add `endpoint`
    n/p50/p95 grouped by `hold`, the overshoot `endpoint - hold`, and a
    `heard` section: the lines in the `--since` range, how many end on a
    fragment, and the rate. **Fragment rule** (in the tool, not the engine):
    the text ends with `,`, `...`, `…`, `-` or `—`, or its last word is one
    of `and or but the a an to of so then with because if`. The tool prints
    counts only, never what was heard.
11. **Rejected, not to be reintroduced here:** shipping smart-turn now,
    whisper-server `--vad`, returning `heard_at` from `record_utterance`
    (it changes a return type that `tools/bench_local.py --live` uses),
    lowering the default to 0.6 s now, printing heard text in the report,
    and changing `listen_local.DEFAULT_HANG_SECONDS = 1.2`
    (`listen_local.py:42`).
12. **Accepted risks.** The endpoint now includes `pw-record` teardown, so
    TIMING totals grow compared with #72's lines. That is a truer number,
    and the PR says so. Level jitter is at most one frame plus
    `--latency 20ms`, the same before and after. The fragment rule misses
    clean-looking half-sentences, so its count is a lower bound. A capped
    capture measures near zero (see below). The trace stays behind
    `trace_timings` (off, `config.py:468`).

### How a capped capture is treated

A capture cut by `max_seconds` (15 s, or `wake_max_seconds` on the wake
path) while the user is still talking has `_last_loud` close to `now`. So
its endpoint is about the teardown alone, and **below the hold**. The engine
records it as measured and does not special-case it. The report uses the
fact that only such a capture can measure below its hold. The `no callback`
fallback measures exactly `hold`. So the report prints `under hold: n`
per hold group, and keeps those lines out of the p50/p95 and overshoot.
They show as a count, not as a faster hold. *(This split is how the plan
realises the spec's "the report reads them as a low tail". Flagged for
review.)*

### Issue #128 is separate

#128 (OPEN) is that the wake path's `capped` counts #114's echo tail wait,
because `opened` (`local_engine.py:407`) is taken before `_record`. This plan
does not fix it and does not change `opened`, `capped` or `:407-411`.

The two do not conflict, and this plan makes #128's fix easier. The natural
fix for #128 is a "capture opened" timestamp taken after the tail wait, just
before the capture opens. That is the spot where this plan adds the
`_last_loud = None` reset (beside `self._onset = None`, `:322`). #128 can
add `self._opened = time.monotonic()` on the next line and measure `capped`
from it. It touches `_wake_turn`'s `capped`, and this plan touches `_hear`.
The only shared text is those adjacent reset lines, so whichever lands
second has a trivial rebase. #128's test can reuse the `SteppedRoom` mixin
and the `Room.teardown` added in step 2.

## Line references after rebasing onto #79 (2026-09-24)

Re-located on `perf/79-speaking-side-unmeasured` (`659df38`), before this
change's own edits. Step 1's baseline there: **1136** passed.

- `local_engine.py`: `__init__`'s `self._onset` `:186` (unchanged);
  `_record` `:298`, its reset `:327`, `watch` `:329-331`; `_wake_turn`
  `:402`, `opened` `:412`, `capped` `:416`; `_hear` `:448`, its ENDPOINT
  lines `:459-460`; `_heard_at` `:789`.
- `trace.py`: `Trace.spans`/`ended` `:76-77`; `line()` `:157-171`, the
  f-string `:169-171` (#79 put `first-audio=` after `continuations=`);
  `parse_line` `:178-193`.
- `tests/test_local_engine.py`: `Room` `:1085`, `SteppedRoomCase` `:1160`,
  `HerVoiceGatesTheMicTests` `:1222`, `SpeakingSideTimingTests` `:1382`,
  `FailureTests` `:1480`, `test_the_trace_starts_when_the_user_stopped_talking`
  `:1610`. `tests/test_trace.py`: `test_parse_line_reads_what_line_writes`
  `:56`.

### Deviation found while implementing (2026-09-24)

- **Step 1's precondition is met by #79's reviewed branch, not main.** The
  lead's instruction: rebase onto `perf/79-speaking-side-unmeasured` and
  build on it; the lead rebases onto `origin/main` once #79 lands.
- **Step 2's mixin move is already done.** #79 extracted `setUp`,
  `no_tail`, `stepped_sleep` and `room` into the base class
  `SteppedRoomCase(EngineTestCase)` (`:1160`), which #114's tests and #79's
  `SpeakingSideTimingTests` share. `EndpointTests(SteppedRoomCase)` uses it,
  and nothing is moved. `HerVoiceGatesTheMicTests` is not edited.
- **Test 3's second recorder is a lambda returning `b"what time is it"`**,
  not the `Ears` fake. `Ears` returns `self.audio` (binary PCM), which the
  `Room`'s transcribe patch would decode into control characters. The
  lambda, like `Ears`, never calls the level callback, which is what the
  test needs.
- **`hold=` sits between `continuations=` and #79's `first-audio=`**, i.e.
  right after `continuations=` as step 5 says.
- **There are no #79 report tests** to keep green: #79 added the tool with
  no test of it. Tests 5 and 6 are the tool's first.
- Steps 4-6 were applied together, then tests 1-4 and 8 checked green.

## Landing order and overlaps

**Order: #111, #110, #121, #79, then #80 last.** Step 1 is a hard
precondition.

- **#111** (`capabilities.py`, `tests/test_cache.py`) and **#110**
  (`tools.py`, `tests/test_compose.py`): no shared file.
- **#121** removes the realtime engine. It touches `local_engine.py`,
  `config.py` and `tests/test_local_engine.py`, but not `_record`, `_hear`
  or `trace.py`. The only effect is moved line numbers.
- **#79** is the real overlap. Both change the one `TIMING` line.
  - `trace.py` `Trace.line()` (`:115-127`). #79 turns the SUBPROCESS-only
    breakdown (`:117-125`) into a two-entry set with SYNTH, and adds a
    `first-audio=X.XXs` token. This plan adds `hold=X.XXs` to the returned
    f-string right after `continuations={self.continuations}` (`:126-127`).
    The same lines, so rebase by hand.
  - `trace.py` `parse_line()` (new in #79). It reads every `key=X.XXs`
    token, so after this plan it returns a `hold` key. The report tool must
    take `hold` out as the grouping key and not print it as a phase. #79's
    `test_parse_line_reads_what_line_writes` gets a `hold` case.
  - `local_engine.py`. #79 wires `Feedback.trace` beside
    `self.executor.trace` (`:499`, `:538`), next to where the line is
    logged (`:539`). It also reorders the TURN span around `_say`
    (`:513-520`). This plan edits `_hear` (`:453-455`) and `_record`
    (`:322`, `:325-326`), so there is no textual overlap in this file.
    There is a semantic one: #79's `Trace.first_audio` is measured from
    `Trace.started`, and this plan moves `started` back to the last loud
    frame. So `first-audio` then counts from the end of the user's speech,
    as the spec intends. #79's tests use fakes that never call the level
    callback, so they fall back to `now - hold` and do not change.
  - `tools/timing_report.py` and `tests/test_trace.py`: #79 creates them,
    and this plan extends them.
  - If #79 is rejected, step 7 creates the tool and `parse_line` itself, and
    nothing else changes.

## Steps

0. **Baseline.** `git fetch origin`. `gh issue view 80` and
   `gh issue view 128` are both OPEN. With
   `export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`, run
   `nix develop -c python3 -m pytest -q` and
   `nix develop -c python3 -m unittest discover -s tests`
   → verify by both passing, **1126** at `b73a3f4`. Record any failure that
   already exists before continuing.
1. **Precondition: #111, #110, #121 and #79 have merged, in that order.**
   `git log origin/main --oneline | grep -E '#(111|110|121|79)\b'`. If #79
   has not merged, **stop and report. Do not implement.** Then
   `git rebase origin/main`, repeat step 0 (record the new count N), and
   re-check every `file:line` in this plan. In particular: `_record`'s reset
   and `watch`, `_hear`'s ENDPOINT lines, `Trace.line()`'s f-string,
   `parse_line`, the tool's phase table, and the test anchors `Room`,
   `HerVoiceGatesTheMicTests` and `FailureTests`
   → verify by a clean rebase, a green baseline, and line numbers confirmed
   or corrected in this file, in the same commit as the code.
2. **`tests/test_local_engine.py`: fakes and engine tests, failing first.**
   - Extract `setUp`, `no_tail`, `stepped_sleep` and `room` from
     `HerVoiceGatesTheMicTests` (`:1169-1213`) into a mixin
     `SteppedRoom`, placed just above that class. The class becomes
     `HerVoiceGatesTheMicTests(SteppedRoom, EngineTestCase)`. This is a
     move only: its 7 tests pass unchanged. The stepped clock is the same:
     `local_engine.time` is patched to `self.now`, the mouth adds 1 s per
     sentence, each `Room` frame adds 0.05 s, and `stepped_sleep` adds
     whatever delay it was asked for.
   - `Room` (`:1084`): add `teardown = 0.0`. On a capture that heard
     something, `record` adds it to `self.case.now` before it returns, to
     stand in for `pw-record`'s terminate and wait. The default of 0 keeps
     #114's tests the same.
   - A new class, `EndpointTests(SteppedRoom, EngineTestCase)`, before
     `FailureTests` (`:1358`). `trace_timings=True`. TIMING lines are read
     from `feedback.LOG_FILE`, as `:1488` does. `endpoint=` and `hold=` are
     parsed with a regex:
     1. `test_the_endpoint_is_measured_not_copied`: `room(session, "what
        time is it")`, `teardown = 0.10`, then `await session._turn()`. The
        endpoint is between `hold + teardown` and `hold + teardown + 0.05`,
        with a 1e-6 tolerance (the spec's example is 0.95 s). On main it is
        0.80.
     2. `test_the_hold_is_in_the_line`: the line has `hold=0.80s`, and
        `hold=1.00s` with `build(..., end_of_speech_seconds=1.0)`.
     3. `test_a_new_capture_forgets_the_last_loud_frame`: one `_turn()`
        through the `Room`, then a second `_turn()` with `record_utterance`
        patched to the plain `Ears` fake, which never calls the level
        callback. The second line has `endpoint=0.80s`.
     4. `test_a_wake_turn_measures_its_endpoint_too`: muted,
        `wake_word="oma"`, `room(session, "oma what time is it")`,
        `teardown = 0.10`, then `await session._wake_turn()`. The brain is
        asked `what time is it`, and the line's endpoint is in the same
        range as test 1.
     8. `test_her_voice_and_its_tail_are_not_endpoint`: `stepped_sleep()`,
        so the real `ECHO_TAIL_SECONDS` runs on the stepped clock.
        `await session._say("One moment.")` (+1 s), then `_turn()` through
        the `Room`, which first waits the 0.35 s tail. The endpoint is in
        test 1's range, not that plus 1.35 s. Second part: #114's test 1
        scenario (`"wait"` room, loop running, `_inject("what time is it")`).
        The capture shut for her voice writes no `TIMING` line with
        `endpoint=`, and the only TIMING line is the typed turn's, which has
        neither `endpoint=` nor `hold=`.
   → verify by `nix develop -c python3 -m pytest -q tests/test_local_engine.py
   -k "EndpointTests or HerVoiceGatesTheMic"` on the unchanged `src/`:
   **1, 2, 4 and 8's first part FAIL** (endpoint 0.80, no `hold=`). 3 and
   8's second part may pass on main. They guard the new code, and step 8's
   mutations prove them. The 7 #114 tests pass. No assertion waits on real
   time.
3. **`tests/test_trace.py`: report tests, failing first.** Import the tool
   the way `tests/test_router.py:15` does (`sys.path` plus `tools`).
   5. `test_fragment_endings_are_counted`: a table test.
      `"turn it down,"`, `"open the"`, `"close it and"` and `"wait…"` count.
      `"what time is it"` and `"open Firefox."` do not.
   6. `test_the_report_prints_no_heard_text`: a fixture log in a temp dir
      with a `heard   'ZEBRA-SENTINEL and'` line and one TIMING line with
      `hold=0.80s`. Run the report, capturing stdout. `ZEBRA-SENTINEL` is
      not in the output, and `1 of 1` fragment endings is.
   → verify by both failing (no fragment function, no heard section).
4. **`src/omarchy_voice/local_engine.py` `__init__` (`:186`) and `_record`
   (`:322-331`).** Add `self._last_loud: float | None = None` after
   `self._onset` in `__init__`. Add `self._last_loud = None` after
   `self._onset = None` (`:322`). In `watch`, before the stop and
   `_ShutForHer` checks: `if value > self.config.silence_level:
   self._last_loud = time.monotonic()` (every loud frame, decision 7).
   `_onset`'s line stays as it is.
   → verify by `grep -n "_last_loud" src/omarchy_voice/local_engine.py`
   showing the 3 lines, and by all `SpokenConsentTests` and
   `HerVoiceGatesTheMicTests` still passing.
5. **`src/omarchy_voice/trace.py`: `Trace.hold` and `line()`.** Add
   `hold: float | None = None` to `Trace` (after `spans`, `:66`). In
   `line()` (`:126-127` at `b73a3f4`, moved by #79), add
   ` hold={self.hold:.2f}s` right after `continuations={...}` only when
   `hold` is not None. Typed and announcement turns have no hold, and their
   lines do not claim one.
   → verify by test 2 still failing only because `_hear` does not set the
   hold yet, and by `RedactionTests` (`tests/test_trace.py:76`) passing.
6. **`local_engine.py` `_hear` (`:453-455`).** Compute `start = self._last_loud
   if self._last_loud is not None and self._last_loud <= now else now -
   hold`. Then `Trace(started=start, hold=hold)` and
   `Span(ENDPOINT, "", start, now)`. Update the docstring: the span is
   measured from the last loud frame and includes teardown, and the echo
   tail is never in it.
   → verify by tests 1, 2, 3, 4 and 8 passing, and by the existing
   `test_the_trace_starts_when_the_user_stopped_talking` (`:1488`) passing
   unchanged (fallback).
7. **`tools/timing_report.py` (#79's).** Add `is_fragment(text) -> bool`
   with decision 10's rule, as a module function so test 5 can call it. In
   the TIMING pass, take `hold` out of `parse_line`'s dict. Group `endpoint`
   by it (`hold=?` for older lines without it). Print n/p50/p95 and the
   overshoot p50/p95 per group, and `under hold: n` for capped captures,
   which are kept out of the percentiles. In a `heard` pass over the same
   `--since` range, parse each `heard   '...'` line with
   `ast.literal_eval` and print `heard: n, fragment endings: k of n (r %)`.
   Never print the text. The docstring states decision 5's procedure and
   its thresholds, so the user can run it without opening the spec.
   → verify by tests 5 and 6 passing, and by #79's report tests passing
   with `hold` absent from the phase table.
8. **Mutation checks.** Apply each one alone, run
   `pytest -q tests/test_local_engine.py -k EndpointTests` plus
   `tests/test_trace.py`, then revert with `git checkout -- src/ tools/`.
   Each must turn the named tests red.
   - D1 (measure, no detector): the `endpoint=` assertion is the
     deliverable. Mutation: `now - hold` again in `_hear` → 1, 4 and 8.
   - D2, D3, D6, D11 (nothing added): checked by
     `git diff origin/main --stat` listing only the five files above, and
     `git diff origin/main -- pyproject.toml nix/ src/omarchy_voice/config.py
     src/omarchy_voice/listen_local.py` being empty. No mutation applies.
   - D4 (the wake path is measured): skip `_last_loud` when `self.active` is
     False, so only the listening path records it → 4.
   - D5 (the threshold numbers): they are in the tool's docstring and the
     follow-up issue (step 10), not in code. Checked by reading. No
     mutation applies.
   - D7a: remove the `_last_loud = None` reset → 3.
   - D7b: set `_last_loud` only on the first loud frame, like `_onset` → 1.
   - D8a: take `start` from a timestamp recorded at the top of `_record`,
     before the tail wait → 8's first part.
   - D8b: make `_record` return `b"x"` instead of `None` for a capture
     shut for her voice, so it reaches `_hear` → 8's second part.
   - D8c: the `<= now` guard is one comparison against a clock that only
     moves forward. It is checked by reading the diff. No test is added.
   - D9: print `hold` unconditionally (`hold=None`) → 8's second part.
     Remove the `hold=` token → 2.
   - D10a: drop `"and"` from the fragment words → 5.
   - D10b: print the matched `heard` line → 6.
   - D10c: include under-hold lines in the percentiles → covered by
     adding one capped line (endpoint 0.05 s at hold 0.80 s) to test 6's
     fixture and asserting `under hold: 1`.
   → verify by every applicable mutation turning its tests red, and by
   `git diff --stat` showing only the intended files after each revert.
9. **Full suites and the flake.** With
   `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` exported, run
   `nix develop -c python3 -m pytest -q`,
   `nix develop -c python3 -m unittest discover -s tests` and
   `nix flake check --no-write-lock-file`
   → verify by all three passing, with the count equal to **N + 7** (N from
   step 1, 5 new in `test_local_engine.py` and 2 in `test_trace.py`).
   `SpokenConsentTests`, `HerVoiceGatesTheMicTests`, `WakeWordTests`,
   `IdleStopTests` and #79's tests pass with no edits beyond step 2's mixin
   move and the `parse_line` hold case.
10. **File the conditional follow-up issue** (a GitHub action, done in the
    implementation turn, not now). `gh issue create --title "End of turn:
    smart-turn v3 or hold on after a trailing fragment, if the fixed hold
    cuts off"`. The body states:
    - The **trigger**: 4 or more fragment endings in a block of 50 `heard`
      lines, with `trace_timings = true`, at any `end_of_speech_seconds`
      of 0.8 s or less, counted by `tools/timing_report.py --since`.
    - "Acceptable" is 2 or fewer in 50. The baseline is 3 in 94 (3.2 %).
    - The step before a detector: 0.8 s → 0.6 s, then keep 0.6 if it stays
      at 2 or fewer.
    - The follow-up must show that smart-turn's CPU time per pause on this
      machine is smaller than the hold it saves. It must weigh "hold on
      after a trailing fragment" against it, decide explicitly whether the
      wake path opts in, and take the ONNX runtime dependency through its
      own intent gate.
    - Silero via whisper-cpp and LiveKit are already rejected (link #80's
      spec).
    - Blocked until #80 has merged and the data exists. Not to be started
      before that.
    → verify by `gh issue view <n>` showing the numbers, and by linking it
    in the PR description.

## Tests

```
export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent
nix develop -c python3 -m pytest -q tests/test_local_engine.py -k EndpointTests  # 5 passed (1, 2, 4, 8 fail before step 6)
nix develop -c python3 -m pytest -q tests/test_local_engine.py -k HerVoiceGatesTheMic  # 7, unchanged
nix develop -c python3 -m pytest -q tests/test_trace.py                          # all pass, 5 and 6 new
nix develop -c python3 -m pytest -q                                              # N + 7, all pass
nix develop -c python3 -m unittest discover -s tests                             # same count, OK
nix flake check --no-write-lock-file                                             # passes
```

No test plays audio, opens a microphone or reaches D-Bus. Every clock is the
stepped fake. No assertion depends on real time passing. Nothing runs in
the background.

## Rollback

One commit on `perf/80-learned-end-of-turn`, on top of #79. Before merge,
drop the branch. After merge, `git revert <sha>`: the endpoint returns to
`now - hold`, `hold=` leaves the line, and the report loses its hold groups
and heard section. #79's parts are untouched. There is no config, state
file or migration. Old and new TIMING lines both parse, because `hold` is
optional. Tracing is off by default (`trace_timings = false`), so a host
that never switched it on sees no change either way. On p620 or razer, it
takes effect on the next `omarchy-voice` rebuild or restart. If the
follow-up issue from step 10 was filed, close it as "not planned" with a
link to the revert.
