---
status: draft
issue: 80
intent: intent/2026-09-24-80-learned-end-of-turn.md
---

# Spec: the endpoint phase measures the wait, and the numbers say whether a detector is worth it

Refs #80. Line numbers are from `origin/main` at `2172c4f` (the 1.0.0
release merge). The intent cited `fc33ae2`. The references it gives in
`config.py`, `listen_local.py` and `local_engine.py` (`:309-310`,
`:363-365`, `:410-412`) are unchanged at `2172c4f`.

Baseline: 1119 tests collected.

## Decisions on the intent's open questions

The intent was approved with a bare "approve all", so each question is
decided here with its reasoning. Any of them can be rejected at this gate.

1. **Measure first. No detector ships in this change.** The evidence for
   one does not exist yet. There are 0 `heard` lines since 0.8 s was set
   (#72), and 0 `TIMING` lines at all. The `endpoint` span today is not a
   measurement. `_hear` writes `now - hold .. now` (`local_engine.py:410-412`),
   so it reads 0.80 s whatever happened. The deliverable here is the
   measurement the decision needs: a real endpoint time, the configured hold
   next to it, and a count of turns that look cut off.
2. **No candidate is chosen now. Each one is ruled in or out on evidence:**
   - **Silero VAD through whisper-cpp: not an end-of-turn detector here.**
     Checked: the flake's nixpkgs resolves `whisper-cpp` to 1.9.2
     (`nix path-info --inputs-from . nixpkgs#whisper-cpp` →
     `…-whisper-cpp-1.9.2`), and a 1.9.2 `whisper-server --help` on this
     machine lists `--vad`, `--vad-model` and
     `--vad-min-silence-duration-ms`. But those options apply to a clip that
     has already been sent for transcription. They split and trim a finished
     recording. They cannot tell `record_utterance` (`listen_local.py:233`)
     to stop while it is capturing. So they cannot make the turn end sooner.
     They would also need a Silero ggml model packaged in Nix. Out of scope.
   - **smart-turn v3: the candidate if the evidence below triggers.** It is
     BSD-2-Clause, small and audio-only. It needs an ONNX runtime, which is
     the "real change to what the package is" that the intent's constraint
     warns about. That decision belongs at the follow-up's own intent gate,
     with the numbers from this change in front of it.
   - **LiveKit turn-detector: rejected.** It needs streaming text this
     engine does not have (whisper runs once, after the cut). Its licence is
     not OSI, and it is 137-165 MB.
3. **A fragment-aware hold is not tried in this change.** The engine only
   knows the words after whisper has run, which is after the cut
   (`listen_local.py:276`). "Hold longer after a trailing fragment" really
   means "transcribe, see a fragment, reopen the capture and stitch the two
   clips". That is a new capture flow inside `_record`, which #114 is
   rewriting. The report tool below counts fragment endings, so the same
   measurement that would trigger a detector would also justify this
   cheaper step. Named follow-up: **"hold on after a trailing fragment"**.
   It is weighed against smart-turn at the same trigger.
4. **The wake-word path gets the same measurement and keeps the same fixed
   hold.** It goes through the same `_record` and `_hear`
   (`local_engine.py:363-371`), so the measurement costs it nothing extra.
   #72's cut-short check (`capped`, `:364-368`) is timed from `opened` with
   the wall clock, so a measured endpoint does not change it. Any future
   detector goes into `_record`, so it would reach both paths. The follow-up
   must decide explicitly whether the wake path opts in.
5. **Acceptable rate, and what triggers a detector.** The baseline is 3
   fragment endings in 94 `heard` lines (3.2 %). All were under older, longer
   holds. The procedure, which needs only the config and no code, runs over
   blocks of at least 50 `heard` lines with `trace_timings` on:
   - At 0.8 s: **2 or fewer** fragment endings in 50 lines is acceptable
     (at the 3.2 % baseline). **4 or more** means 0.8 s already cuts the
     user off.
   - If 0.8 s is acceptable, set `end_of_speech_seconds = 0.6` for the next
     50 lines. If that is still 2 or fewer, keep 0.6. That saves 0.2 s on
     every turn with no code change, and no detector is needed.
   - **Trigger for the smart-turn follow-up:** 4 or more in 50 at any hold
     of 0.8 s or less. That shows that one fixed number cannot be both
     short and safe for this user, which is the claim #80 makes but cannot
     yet show. The follow-up must also show that the model's CPU time per
     pause, on this machine, is smaller than the hold it saves.
   - The endpoint number (`endpoint - hold`, see Design) shows how much of
     the wait is not the hold at all. If it is large, the recorder's
     teardown is the target, not the hold.

## Design

Nothing new is added to `pyproject.toml` or `nix/package.nix`.
`end_of_speech_seconds` (`config.py:373`) is already tunable from
`config.toml` (`share/config.example.toml:103`, `tests/test_config.py:192`),
so no new knob is added.

1. **Remember the last loud frame.** `_record`'s level callback `watch`
   (`local_engine.py:289-294`) already sets `_onset` from the first frame
   above `silence_level`. It also sets `self._last_loud = time.monotonic()`
   on **every** frame above `silence_level`. This is the same test that
   `record_utterance` uses to move `heard_at` (`listen_local.py:269-270`):
   the same `_level(chunk)` value against the same `config.silence_level`.
   `_last_loud` is reset to `None` at the top of `_record`, next to `_onset`
   (`:287`), so a capture never inherits the previous one's value.
2. **Measure the endpoint** (`_hear`, `local_engine.py:400-423`). The
   trace starts at `self._last_loud` when it is set and not later than
   `now`. Otherwise it falls back to `now - hold`, which is today's
   behaviour. That covers a capture with no level callback and the existing
   test fakes. The ENDPOINT span runs from that start to `now`. It now
   includes everything the user actually waits through between their last
   loud frame and whisper starting: the hold, up to one 50 ms frame of
   granularity, and `pw-record`'s teardown (`listen_local.py:280-285`,
   `terminate` plus a wait of up to 2 s). The last of these is not covered
   today.
3. **Put the hold in the line.** `Trace.hold: float | None`. `_hear` sets
   it. `line()` prints `hold=0.80s` after `continuations=`. It is a
   configured duration, not content. Without it, a TIMING line cannot say
   which setting produced it once the user starts tuning.
4. **Extend `tools/timing_report.py`** (added by #79). The tool gets
   `endpoint` n/p50/p95 grouped by `hold`, the overshoot `endpoint - hold`,
   and a `heard` section: the lines in the `--since` range, how many end on
   a fragment, and the rate. Fragment rule, kept in the tool and not in the
   engine, because it is a reporting heuristic: the text ends with `,`,
   `...`, `…`, `-` or `—`, or its last word is one of `and or but the a an
   to of so then with because if`. It prints counts only. It never prints
   what was heard. Anyone who wants the lines can grep the log themselves.

## Overlap with #114 and #79, and landing order

- **#114** (`fix/114-every-voice-gates-the-mic`, implemented in `32583ba`,
  not merged) rewrites `_record`: a tail wait before the capture opens, a
  new `_ShutForHer` raise inside `watch`, and a `None` return for a capture
  shut for her voice. This spec adds one line to the same `watch` and one
  reset beside `_onset`. The tail wait comes before any frame, so it cannot
  touch `_last_loud`. A capture shut for her returns `None` before `_hear`
  runs, so it is never measured.
- **#79** (spec drafted on `perf/79-speaking-side-unmeasured`) adds
  `trace.parse_line`, `Trace.first_audio` and `tools/timing_report.py`.
  This spec extends all three. With #80, #79's `first-audio` starts from the
  measured end of the user's speech rather than the constant.
- **Order: #114, then #79, then #80.** The plan's step 1 checks that both
  have merged, rebases, and re-checks every line reference. If #79 is
  rejected, item 4 of the Design creates the tool itself, with the parser,
  and nothing else changes.

## Alternatives rejected

- **Ship smart-turn now.** It is a new runtime dependency, and there is
  not one post-#72 turn to justify it (decision 1).
- **whisper-server `--vad`.** It works on a finished clip, not on a live
  capture (decision 2).
- **Return `heard_at` from `record_utterance`.** That changes the return
  type of a function with other callers (`tools/bench_local.py --live`) for
  a value the level callback already sees. The callback is the engine's
  existing seam (`_onset`, #86).
- **Lower the default to 0.6 s now.** That is a guess. The procedure in
  decision 5 reaches it by changing the config, with evidence.
- **Log the heard text in the report.** The log already has it. The tool
  reports rates so its output can be shared without content.
- **Fix `listen_local.DEFAULT_HANG_SECONDS = 1.2`** (`listen_local.py:42`).
  The engine always passes the configured hold, and the other caller
  (`bench_local.py --live`) is a bench. It is left alone.

## Risks

- **The endpoint now includes `pw-record` teardown.** If that is slow,
  TIMING totals grow compared with #72's lines. That is a truer number, not
  a regression, and the report shows it as overshoot. It is noted in the PR.
- **Level jitter.** The value is taken when the frame is read, which is at
  most one frame (50 ms) plus `--latency 20ms` after the sound. The same
  offset applies before and after any change, so comparisons hold.
- **The fragment rule is a heuristic.** "Turn off the" is caught. "Open
  Firefox and then" is caught by "then". A clean-looking half-sentence
  ("open the browser" when the user meant "…on workspace two") is missed.
  So the count is a lower bound, and the thresholds in decision 5 are set
  against a baseline counted by the same rule.
- **No host risk.** The trace is behind `trace_timings` (off by default).
  `_last_loud` is one float write per loud frame on the recorder thread.
  The capture itself is not changed.

## Verification

Fakes only. Use the stepped clock from `SpokenConsentTests`
(`tests/test_local_engine.py:755`), and a recorder fake like `OnsetEars`
(`:173`) that drives the level callback (the 5th positional argument),
loud and then quiet, moving the clock 0.05 s per frame. No audio.

Tests written first, and they must **fail on `main`**:

1. `test_the_endpoint_is_measured_not_copied`. The last loud frame is at
   t, then there are quiet frames to t+0.85, then a teardown step of 0.10 s.
   Expect `endpoint=0.95s` (main: `0.80s`).
2. `test_the_hold_is_in_the_line`. Expect `hold=0.80s`, and `hold=1.00s`
   with `end_of_speech_seconds=1.0`.
3. `test_a_new_capture_forgets_the_last_loud_frame`. A second capture
   whose fake never calls the level callback falls back to `hold` and does
   not reuse the first capture's frame.
4. `test_a_wake_turn_measures_its_endpoint_too`. It goes through
   `_wake_turn` with `wake_word="oma"` and a one-breath instruction.
5. `test_fragment_endings_are_counted` (report tool). A table test of the
   rule: `"turn it down,"`, `"open the"`, `"close it and"` and `"wait…"`
   count. `"what time is it"` and `"open Firefox."` do not.
6. `test_the_report_prints_no_heard_text`. A fixture log with a `heard`
   line whose text is a sentinel string. The sentinel is not in the
   tool's output.
7. The existing `test_the_trace_starts_when_the_user_stopped_talking`
   (`tests/test_local_engine.py:1213`) stays green unchanged. Its fake never
   calls the level callback, so it now guards the fallback.

Mutation checks. Each one must turn a test red, and then be reverted:
- use `now - hold` again in `_hear` → 1 and 4;
- remove the `_last_loud = None` reset → 3;
- set `_last_loud` only on the first loud frame, like `_onset` → 1;
- drop `"and"` from the fragment words → 5;
- print the matched line → 6.

Runners, with `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` exported:
- `nix develop -c pytest tests -q`: all pass. The count is the count after
  #79 plus the new tests.
- `nix develop -c python3 -m unittest discover -s tests`: same count, all
  pass.
- `nix flake check --no-write-lock-file`: passes.

The intent's outcome, "shorter than 0.8 s for a clearly finished sentence,
with no more cut-offs", is reached, if at all, by decision 5's procedure on
real turns. This change makes the procedure possible. It does not claim the
outcome.
