---
status: draft
issue: 79
spec: spec/2026-09-24-79-speaking-side-unmeasured.md
---

# Plan: the TIMING line shows when she starts speaking and how long she speaks

Refs #79. Base: `origin/main` `b73a3f4` (#114 merged; 1126 tests collected,
92 in `tests/test_local_engine.py`, 15 in `tests/test_trace.py`, 23 in
`tests/test_elevenlabs.py`). Every `file:line` below was checked against
`b73a3f4`. Step 1 rebases onto #121, which moves lines in `local_engine.py`
and `config.py`, so step 1 re-checks them.

Files that change: `src/omarchy_voice/trace.py`,
`src/omarchy_voice/elevenlabs.py`, `src/omarchy_voice/feedback.py`,
`src/omarchy_voice/local_engine.py`, the new `tools/timing_report.py`, and
`tests/test_trace.py`, `tests/test_elevenlabs.py` and
`tests/test_local_engine.py`. `config.py`, `pyproject.toml`, `nix/` and
`flake.nix` do not change.

## Approved decisions, carried over from the spec

1. **Measure first. Streaming ElevenLabs does not ship here.** ffmpeg's
   single-pass `loudnorm` (`config.py:543`,
   `"loudnorm=I=-16:TP=-1.5:LRA=11"`) holds a 3 s look-ahead in dynamic
   mode. A one-sentence clip is shorter than that, so streaming the body
   through ffmpeg would not move her first sample unless the mastering also
   changed, and the mastering is deliberate (`config.py:540-542`).
   `synth` keeps reading the whole body (`elevenlabs.py:147-148`). This
   change times `request`, `download` and `decode` separately so the gain
   can be judged. **Follow-up gate A, "stream ElevenLabs into pw-cat":**
   open it only if `download + decode` is at least 0.3 s at the p50 of 30 or
   more traced ElevenLabs sentences. It must say what replaces single-pass
   loudnorm.
2. **Piper is not kept warm.** Here it is only the fallback, and
   `piper --output-raw` does not mark where a sentence ends
   (`feedback.py:167-201`). A resident process needs framing, restart
   handling and an owner. Piper sentences are timed as one SPEAK span each.
   **Follow-up gate B, "keep Piper resident":** open it if Piper becomes the
   configured primary voice, or if `tts     elevenlabs failed` appears in
   more than 5 % of spoken sentences over a week.
3. **No prefetch of sentence N+1 while N plays.** Prefetch needs `_say`
   (`local_engine.py:258-278`) to stop waiting with `barge_in` off, and that
   wait is #114's microphone gate (`_mic_shut` `:193`, the capture shut
   `:330-331`, the 1 s wait `:269-275`, the tail wait `:303-320`). That is a
   safety change, not a measurement. **Follow-up gate C, "synthesise the
   next sentence while this one plays":** its prerequisite (#114 merged) is
   met. Open it when `synth` is at least 25 % of `speak` on turns with two or
   more sentences.
4. **The 0.35 s echo tail stays, and it belongs to #114.** It is a wait at
   the top of the next `_record` (`:303-320`), after `_answer` logs its line
   (`:539`) and before the next trace starts in `_hear` (`:453-455`). No
   trace is open to hold it. A new test proves it stays outside every TIMING
   total. Lowering it needs a live room measurement, which this change does
   not make.
5. **`continuations` is fixed here.** New rule: a TURN span counts as a
   continuation only if a TOOL span was opened after the previous TURN span
   opened. The three `ContinuationTests` (`tests/test_trace.py:24-45`) pass
   unchanged. Accepted edge, stated in the docstring: a tool call after the
   last sentence with nothing said after it is not counted.
6. **No new cloud call.** The existing request already goes to `/stream`
   (`elevenlabs.py:141-142`). The URL, body and headers do not change.
7. **No sub-second timestamps in `session.log`.** Every new number is a
   duration inside the TIMING line. The `strftime` stamp
   (`feedback.py:207`) and its readers (`tools/eval_router.py:53`,
   `cli.py:609-615`) do not change.
8. **Model time stops at the sentence** (`local_engine.py:517-520`). New
   order: close the TURN span, `await self._say(sentence)`, then open the
   next TURN span. `_say`'s `_mic_shut` wait falls between the closed TURN
   span and the SPEAK span, so it counts in the total and in `first-audio`
   and in no phase. A routed turn (`:502-505`) has no model time. Its tools
   are still TOOL spans from the executor.
9. **SPEAK phase** (`SPEAK = "speak"`, next to `ENDPOINT`, `trace.py:44`):
   one span per sentence, opened in `_speech_loop` (`local_engine.py:236-256`)
   around `self.feedback._speak_now(text)` (`:246`), with the empty name. It
   closes in the loop's `finally` before `self._speech.task_done()`
   (`:256`). It is recorded in the loop, not in `Feedback`, so the tests'
   fake mouths still produce it.
10. **SYNTH phase** (`SYNTH = "synth"`): named spans from the fixed words
    `request` (from `urlopen` until it returns with the headers), `download`
    (the `read()` of the body) and `decode` (ffmpeg, `elevenlabs.py:161-170`).
    Never text.
11. **`Trace.first_audio`** (a property): the start of the first SPEAK span,
    plus the SYNTH spans inside it, minus `Trace.started`. Printed as
    `first-audio=X.XXs`. Absent when there is no SPEAK span. Docstring: for
    Piper and espeak-ng it is a lower bound, since it includes Piper loading
    its model and has no SYNTH span.
12. **`line()` breaks SYNTH down by name**, as it does SUBPROCESS
    (`trace.py:117-125`). The "only SUBPROCESS" rule becomes a two-entry set.
    Playback (`speak - synth`) is not stored. The report tool derives it.
13. **The trace reaches the mouth through `Feedback.trace`** (`None` by
    default). `_answer` sets and clears it next to `executor.trace`
    (`local_engine.py:499`, `:538`). `_speech_loop` reads it for SPEAK.
    `_speak_now` (`feedback.py:138-155`) passes it as
    `elevenlabs.synth(text, config, trace=...)` (`elevenlabs.py:114`), which
    uses `contextlib.nullcontext()` when it is `None`. Piper and espeak-ng
    add no SYNTH span. A span open when a voice fails is closed by its `with`
    block, so a fallback to Piper leaves nothing running. Documented limits:
    with `barge_in` on, sentences still playing after the line is logged
    land on a finished trace and are not reported. Speech outside `_answer`
    (the held prompt `:547-548`, #120's recovery lines `:638-645`) runs with
    the trace cleared. Like `executor.trace`, it is not cleared when
    `_answer` is cancelled. The session is ending then.
14. **Report tool `tools/timing_report.py`** (about 40 lines, stdlib). It
    reads the TIMING lines of `session.log` (`config.LOG_FILE`,
    `config.py:104`) and prints n, p50 and p95 per phase, plus `first-audio`
    and the derived `play = speak - synth`. It takes `--since YYYY-MM-DD`.
    It uses `trace._percentile` (`trace.py:146-156`). The parser lives in
    `trace.py` as `parse_line(line) -> dict[str, float] | None`, next to
    `line()`. On a log with no TIMING lines it prints `no tasks traced` and
    exits 0. `config.trace_timings` stays off by default (`config.py:468`).
    The docstring says the measurement period is the user turning it on, and
    that model time before and after this change is not like for like.
15. **Nothing new records content.** Span names are `""`, `request`,
    `download` and `decode`. `mark` still has no parameter for text
    (`trace.py:18-23`).
16. **Stdlib only.** No change to `pyproject.toml` or `nix/package.nix`.
17. **`_say`, `_record` and `_voice_until` are not touched.** This change
    edits `_speech_loop`, `_answer`'s sentence loop, route branch and error
    branch, and the trace wiring.
18. **The outcome is not claimed.** This change produces the baseline. The
    follow-ups are judged against it.
19. **Rejected, not to be reintroduced:** streaming here; SPEAK/SYNTH only
    inside `Feedback`; a `first_audio` hook called by `_play`; the echo tail
    as a phase; sub-second log stamps; a `timings` CLI subcommand.

### Spec ambiguities, resolved here (flagged for the reviewer)

- **R1. The routed turn.** Design item 1 says a routed turn "closes its
  TURN span before `_run_route`", but test 5 expects no `model-turn` in the
  line, and a closed span still prints `model-turn=0.00s`. Resolution: the
  first TURN span opens only on the model branch, after `_route` returns
  nothing (it moves from `:500` into the `else` at `:506`). A routed turn
  then has no TURN span at all. Side effect: `_route`'s own time (a
  `hyprctl` query, `:551-560`) leaves `model-turn` on model turns too. That
  is correct, since it is not model time, and it still counts in the total.
- **R2. The error branch.** `_answer`'s `except` (`:523-530`) calls
  `_say("Something went wrong with that.")` with a TURN span open, which
  would count her speech as model time, against decision 8. The spec does
  not mention it. Resolution: close `turn` before that `_say`. The close at
  `:535-536` is idempotent (`_Open.close`, `trace.py:134-136`).
- **R3. Test 3's value on `main`.** The spec says main gives
  `continuations=2` for three sentences. It gives 3: one TURN span at
  `:500` plus one per sentence at `:519` is four spans, minus one. The test
  asserts 0 on the new code, so this changes nothing but the expectation
  written in step 2.
- **R4. Where `first-audio` sits, and what `parse_line` returns.** Not
  fixed by the spec. Resolution: `TIMING  T.TTs continuations=N
  first-audio=X.XXs phase=…`, with `first-audio` right after
  `continuations`. `parse_line` reads every `key=value` token generically
  (`total`, `continuations`, `first-audio`, each phase) and turns a
  breakdown `synth=0.30s(request=0.10 …)` into `synth.request` etc. So #80's
  `hold=0.80s` (its spec puts it after `continuations=`) parses with no
  parser change.
- **R5. Line references.** The spec cites `Mouth` at
  `tests/test_local_engine.py:99`. It is `:100`. `_say` spans `:258-278`,
  not `:258-285`.
- **R6. The stepped clock.** The spec's clock patches `local_engine.time`.
  Spans read `trace_mod.time.monotonic` (`trace.py:57`, `:65`, `:74`,
  `:136`), so the tests patch both with the same clock.

## Landing order and overlap

Order: **#111, #110, #121, #79, #80.** #79 is fourth.

- **#121 (lands before this)** removes the realtime engine. Exact overlap:
  - `local_engine.py:40-44`: the `from .realtime import (ECHO_TAIL_SECONDS,
    WATCH_POLL_SECONDS, _run_until_done, watch_headline, watch_message)`
    block is replaced by those helpers moved into `local_engine.py`. Every
    `local_engine.py` line below `:44` shifts. `ECHO_TAIL_SECONDS` becomes a
    `local_engine` module global, so `mock.patch.object(local_engine,
    "ECHO_TAIL_SECONDS", …)` and test 9's read of it work unchanged.
  - `feedback.py:79-82`: #121 rewrites the "Set by the realtime session"
    comment above `self.mic_open` (`:83`). This plan adds `self.trace = None`
    in the same `__init__`.
  - `config.py`: #121 removes realtime keys (`:387-424`), so `:468`,
    `:540-543` shift. This plan cites them but does not edit `config.py`.
  - `tests/test_local_engine.py`: #121 edits imports and tests at `:671`,
    `:1818-1822`, `:1832` and `:1946`. This plan adds a class before
    `FailureTests` (`:1358`) and hoists helpers out of
    `HerVoiceGatesTheMicTests` (`:1159-1220`). No shared hunk.
  - #121 deletes realtime tests, so the baseline count drops. Step 1
    records the new count.
- **#80 (lands after this, and rebases onto it)** adds a measured endpoint
  to the same TIMING line. Exact overlap:
  - `trace.py`: #80 adds `Trace.hold` and prints `hold=0.80s` after
    `continuations=` in `line()` (`trace.py:126-127`, the same `return` this
    plan edits for `first-audio`). Textual conflict there, resolved by
    #80's rebase. R4 makes `parse_line` accept `hold=` as is.
  - `Trace.started`: #80 moves it from `now - hold` to the last loud frame
    (`_hear`, `local_engine.py:443-466`). `first_audio` subtracts
    `Trace.started`, so it gets the measured start for free. No textual
    overlap: this plan does not edit `_hear`.
  - `tools/timing_report.py` and `trace.parse_line`: created here, extended
    by #80 (endpoint by `hold`, overshoot, fragment counts).
  - `tests/test_trace.py` and `tests/test_local_engine.py`: both add tests.
    #80 reuses the stepped clock this plan hoists into `SteppedRoomCase`.
  - `local_engine.py`: #80 edits `watch` in `_record` (`:324-331`) and
    `__init__`. This plan edits neither.

## Steps

0. **Baseline.** `git fetch origin`; `gh issue view 79` shows OPEN. Then
   `export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`,
   `nix develop -c python3 -m pytest tests -q` and
   `nix develop -c python3 -m unittest discover -s tests`
   → verify by both passing with 1126 tests at `b73a3f4`. Record any
   failure that exists before continuing.
1. **Precondition: #111, #110 and #121 have merged.**
   `gh pr list --state merged --search 121` and
   `git log origin/main --oneline | grep -E '#(111|110|121)'`. If #121 has
   not merged, **stop and report. Do not implement.** Otherwise
   `git rebase origin/main`, repeat step 0 and record the new count N.
   Re-check every `file:line` in this plan (the overlap list above names
   what moves) → verify by a clean rebase, a green baseline, and the
   corrected lines written into this file in the same commit as the code.
2. **Tests first, and see them fail.** No `src/` change yet.
   - *Shared fixture.* Move `setUp` (stepped clock), `no_tail`,
     `stepped_sleep`, `room` and `looping` out of `HerVoiceGatesTheMicTests`
     (`tests/test_local_engine.py:1169-1220`) into a new
     `class SteppedRoomCase(EngineTestCase)` right after `class Room`
     (`:1084`), and make `HerVoiceGatesTheMicTests` subclass it. Moved, not
     copied. Its `setUp` patches both `local_engine.time` and
     `trace_mod.time` with the same `SimpleNamespace(monotonic=…)` (R6).
     The #114 tests must still pass after the move.
   - *Mouth.* `Room.mouth` (`:1110-1118`) already steps the clock 1.0 s per
     sentence and returns at once with no capture open. Tests 1 to 3 and 5
     use `self.room(session)` with an empty script and drive `_answer`
     directly. Test 2 wraps it: before `room.mouth(text)`, if
     `getattr(session.feedback, "trace", None)` is set, open
     `mark(trace_mod.SYNTH, "request")`, step `self.now += 0.3`, close.
   - *Brain.* `FakeBrain(sentences=…)` (`:40`). Where model time must be
     non-zero, a subclass steps `self.case.now` before each yield.
   - New class `SpeakingSideTimingTests(SteppedRoomCase)`, placed before
     `FailureTests` (`:1358`), `trace_timings=True`:
     1. `test_speaking_is_not_model_time`: brain yields two sentences with
        0.25 s before each. Expect `speak=2.00s`, `model-turn=0.50s`, and
        every SPEAK span named `""`. Main: no `speak`, `model-turn=2.50s`.
     2. `test_first_audio_is_counted_from_the_end_of_the_sentence`: drive
        `_turn()` with `build()`'s `Ears` (hold 0.8) left in place: the
        mouth is a bare `Room(self, session, ()).mouth`, not `self.room()`,
        which would replace the recorder. `transcribe` re-patched to step 0.2,
        brain steps 0.5 then yields, the mouth adds 0.3 of SYNTH. Expect
        `first-audio=1.80s`. Main: absent.
     3. `test_sentences_without_tools_are_not_continuations`: three
        sentences, no tool → `continuations=0`. Main: 3 (R3).
     5. `test_a_routed_turn_has_no_model_time`: as `RouterTests.
        test_a_routed_turn_is_timed` (`:1611`) → a TIMING line with no
        `model-turn`. Main: present.
     9. `test_the_echo_tail_is_outside_the_timing_line`: tail left on (no
        `no_tail`), `stepped_sleep()`, `room(session, "wait")`, `looping`,
        then `_inject("what time is it")`. Wrap `session.feedback.log` to
        record `self.now` when a TIMING line is written. Expect the line's
        total below `1.0 + ECHO_TAIL_SECONDS` (one 1.0 s sentence), and the
        TIMING clock at or before the start of the first `slept` delay equal
        to the tail. Passes on main. Guards decision 4.
   - `tests/test_trace.py`:
     4. `ContinuationTests.test_a_tool_between_sentences_is_one_continuation`:
        spans TURN, TURN, TOOL, TURN built by hand → 1. Passes on main.
     8. `test_parse_line_reads_what_line_writes`: a trace with TURN, SPEAK,
        SYNTH `request`/`download`/`decode`, SUBPROCESS `grim` →
        `parse_line(t.line())` gives `total`, `continuations`,
        `first-audio`, `speak`, `synth`, `synth.request`, `subprocess.grim`
        to 2 dp. `parse_line("heard   'x'")` is `None`. Main: no
        `parse_line`.
     10. `RedactionTests.test_speech_spans_carry_fixed_names_only`: run
         `Feedback._speak_now(SECRETS[0])` with `trace` set, fake `urlopen`
         and `subprocess.run` (as in `SynthTests`), `_play` patched. Every
         SPEAK/SYNTH span name is in `{"", "request", "download",
         "decode"}`, the SYNTH spans exist, and no secret is in `line()`.
         Main: no SYNTH spans.
   - `tests/test_elevenlabs.py`:
     6. `SynthTests.test_synth_is_split_request_download_decode`: fakes as
        in `test_mp3_is_requested_and_decoded_locally` (`:223-253`),
        `synth("hello there", cfg, trace=t)` → exactly three SYNTH spans,
        names `request`, `download`, `decode` in order, all ended, and
        "hello there" not in `t.finish().line()`. Main: `TypeError`.
     7. `FallbackTests.test_a_failed_cloud_voice_leaves_no_open_span`: real
        `synth`, `response.read` raises `OSError` (→ `Unavailable` during
        `download`), `_speak_piper` patched, `Feedback.trace` set. Piper
        was called, the `request` and `download` spans exist, every span
        has `ended` set, and there is no `decode`. Main: no spans.
   → verify by `nix develop -c python3 -m pytest -q tests/test_local_engine.py
   tests/test_trace.py tests/test_elevenlabs.py` on unchanged `src/`:
   **1, 2, 3, 5, 6, 7, 8 and 10 FAIL** for the reason given; **4 and 9
   pass**; every `HerVoiceGatesTheMicTests` test still passes. No assertion
   sleeps real time and then checks. Waits are Events, `until`, or the
   stepped clock.
3. **`trace.py`**: `SPEAK`, `SYNTH` beside `ENDPOINT` (`:44`);
   `continuations` (`:86-94`) to decision 5's rule, docstring naming the
   edge; `first_audio` property (decision 11); `phase_seconds` unchanged;
   `subprocess_seconds` (`:102-113`) generalised to a breakdown for
   `{SUBPROCESS, SYNTH}` used by `line()` (`:115-127`); `first-audio` after
   `continuations` (R4); `parse_line` after `line()`. Module docstring
   (`:9-13`) lists `tools/timing_report.py` as a reader.
   → verify by tests 3, 4 and 8 passing and `ContinuationTests`,
   `SubprocessPhase` (`:126-181`) passing unchanged.
4. **`elevenlabs.py` `synth` (`:114-171`)**: add `trace=None`. Wrap the
   `urlopen(...)` call in `request`, `response.read()` in `download`, the
   `subprocess.run` in `decode`, each `with trace.mark(SYNTH, "…") if trace
   else contextlib.nullcontext():`. The URL, body and exception mapping do
   not change. Update the `ponytail:` note (`:120-122`) to point at gate A.
   → verify by test 6 passing and all `SynthTests` and
   `tools/bench_local.py`'s call (`:114`, no `trace`) unchanged.
5. **`feedback.py`**: `self.trace = None` in `Feedback.__init__` (`:74-83`)
   with a comment on thread safety (SYNTH appended from the worker thread,
   `list.append` atomic, `line()` read after `_say` joined). `_speak_now`
   (`:145`) passes `trace=self.trace`.
   → verify by tests 7 and 10 passing and `FallbackTests` (`:294-372`)
   unchanged.
6. **`local_engine.py` `_speech_loop` (`:236-256`)**: before `to_thread`,
   `span = trace.mark(trace_mod.SPEAK) if (trace := self.feedback.trace)
   else None`; in `finally`, `if span: span.close()` before `task_done()`.
   → verify by `speak=` appearing in test 1's line.
7. **`local_engine.py` `_answer` (`:498-539`)**: `self.feedback.trace = task`
   beside `:499`, cleared beside `:538`. Move the first `task.mark(TURN)`
   from `:500` into the model branch (R1). In the sentence loop
   (`:517-520`): close `turn`, `await self._say(sentence)`, then
   `turn = task.mark(TURN)`. In `except` (`:523-530`), close `turn` before
   `_say` (R2). Rewrite the comment at `:511-516` to say model time ends at
   the sentence.
   → verify by tests 1, 2, 3, 5 and 9 passing, and
   `test_a_routed_turn_is_timed` (`:1611`),
   `test_the_trace_starts_when_the_user_stopped_talking` (`:1488`) and
   `SpokenConsentTests` unchanged.
8. **`tools/timing_report.py`** (new): argparse `--since`, reads
   `config.LOG_FILE`, keeps lines whose stamp date is on or after `--since`,
   `trace.parse_line`, prints per key n/p50/p95 with `_percentile`, plus
   `play = speak - synth`. Docstring per decision 14.
   → verify by `XDG_STATE_HOME=$(mktemp -d)`, a fixture `session.log` with
   three TIMING lines, `nix develop -c python3 tools/timing_report.py
   --since 2026-09-01` printing the table and exiting 0, then an empty log
   printing `no tasks traced` and exiting 0. Remove the temp dir.
9. **Mutation checks.** One at a time, run the named tests, then
   `git checkout -- src/`:
   - M1 (dec. 8): open the next TURN span before `_say` again → 1 fails.
   - M2 (dec. 8, R1): open the first TURN span at `:500` again → 5 fails.
   - M3 (dec. 5): count every TURN after the first → 3 fails.
   - M4 (dec. 11): leave SYNTH out of `first_audio` → 2 fails.
   - M5 (dec. 10, 15): pass `text` as the SYNTH name → 6 and 10 fail; pass
     `text` as the SPEAK name → 1 fails.
   - M6 (dec. 13): replace the `with` around `download` by a bare `mark()`
     closed only on success → 7 fails.
   - M7 (dec. 4): add `await asyncio.sleep(ECHO_TAIL_SECONDS)` in `_answer`
     before `task.finish()` → 9 fails.
   - M8 (dec. 14): drop the `(…)` breakdown from `parse_line` → 8 fails.
   - M9 (dec. 12): drop SYNTH from the breakdown set → 8 fails.
   - Dec. 9's "close before `task_done`" cannot be observed on one event
     loop (the loop reaches `finally` before `_answer` resumes). Checked by
     reading the diff, as #114's M6 was.
   - Non-code decisions, checked on the final diff:
     `git diff origin/main --stat` shows only the files listed at the top
     (dec. 16, 7); `git diff origin/main -- src/omarchy_voice/elevenlabs.py`
     keeps `/stream` and one whole `response.read()` (dec. 1, 6);
     `git diff -U0 origin/main -- src/omarchy_voice/local_engine.py` has no
     hunk inside `_say`, `_record` or `_drop_queued_speech` (dec. 3, 17).
   → verify by each mutation turning its tests red and `git status` clean
   in `src/` after each revert.
10. **Full suites and the flake.** With
    `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`:
    `nix develop -c python3 -m pytest tests -q`,
    `nix develop -c python3 -m unittest discover -s tests`,
    `nix flake check --no-write-lock-file`
    → verify by all three passing with N + 10 tests (1136 if N is still
    1126). Nothing runs in the background, and no process is left.
11. **PR.** Link intent, spec and plan. Say that `model-turn` falls because
    speaking left it, so before/after lines are not like for like.
12. **After merge: file the three follow-ups**, each linking #79 and quoting
    its gate verbatim:
    - "stream ElevenLabs into pw-cat" — gate A (decision 1).
    - "keep Piper resident" — gate B (decision 2).
    - "synthesise the next sentence while this one plays" — gate C
      (decision 3).
    → verify by `gh issue list --search "in:title pw-cat OR resident OR
    synthesise"` showing all three OPEN, then notify #80's owner that
    `perf/80-learned-end-of-turn` can rebase.

## Tests

```
export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent
nix develop -c python3 -m pytest -q tests/test_local_engine.py -k "SpeakingSideTiming or HerVoiceGatesTheMic"
nix develop -c python3 -m pytest -q tests/test_trace.py tests/test_elevenlabs.py
nix develop -c python3 -m pytest tests -q                 # N + 10, all pass
nix develop -c python3 -m unittest discover -s tests      # same count, OK
nix flake check --no-write-lock-file                      # passes
```

No test plays audio, opens a microphone, reaches ElevenLabs or OpenAI, or
reaches D-Bus. No assertion depends on real time passing.

## Rollback

One commit series on `perf/79-speaking-side-unmeasured`. Before merge, drop
the branch. After merge, `git revert` the merge commit: `continuations` goes
back to the old rule, SPEAK/SYNTH and `first-audio` leave the line, and
`tools/timing_report.py` goes. If #80 has landed on top, revert #80 first,
since it extends `parse_line` and the tool. No config key, state file or
migration. `trace_timings` is off by default, so a host that never turned it
on sees no change either way.
