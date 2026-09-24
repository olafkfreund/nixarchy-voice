---
status: approved
issue: 79
intent: intent/2026-09-24-79-speaking-side-unmeasured.md
---

# Spec: the TIMING line shows when she starts speaking and how long she speaks

Refs #79. Line numbers are from `main` at `b73a3f4` (#114 merged). The
intent cited `fc33ae2`. Since then only `local_engine.py` and
`tests/test_local_engine.py` have moved. Every other reference below is
unchanged.

Baseline: 1126 tests collected (`nix develop -c python3 -m pytest tests -q
--collect-only`).

## Decisions on the intent's open questions

The intent was approved with a bare "approve all", so each question is
decided here with its reasoning. Any of them can be rejected at this gate.

1. **Measure first. The speaking phases ship on their own, and streaming
   ElevenLabs does not ship in this change.** Streaming is less obvious than
   the intent assumes. The mastering chain is
   `elevenlabs_master = "loudnorm=I=-16:TP=-1.5:LRA=11"`
   (`config.py:543`). In its default dynamic mode, ffmpeg's single-pass
   `loudnorm` holds a 3 s look-ahead window before it emits audio. A
   one-sentence clip is shorter than that. So piping the HTTP body through
   ffmpeg into `pw-cat` would still hold the first sample until the clip
   ends, unless the mastering changes as well. That is a change to how she
   sounds, which the `config.py:540-542` comment says is deliberate. The
   gain from streaming is also bounded by numbers nobody has yet: download
   time after the first byte, and decode time. This spec measures both
   separately (the `request`, `download` and `decode` spans below). Named
   follow-up: **"stream ElevenLabs into pw-cat"**. It opens only if
   `download + decode` is at least 0.3 s at the p50 of 30 or more traced
   ElevenLabs sentences, and it must say what replaces single-pass loudnorm.
2. **Piper is not kept warm now.** On this machine it is the fallback, and
   the intent found no `elevenlabs failed` line in `session.log`. A resident
   Piper is also not a small change. `piper --output-raw` does not mark where
   one sentence's audio ends (`feedback.py:170-200`), so a long-lived process
   needs framing, restart handling and a new owner for the process. That is
   not cheap. This spec times each Piper sentence as a whole (see Design,
   item 3). Named follow-up: **"keep Piper resident"**. It opens if Piper
   becomes the configured primary voice, or if `tts elevenlabs failed`
   appears in more than 5 % of spoken sentences over a week.
3. **Sentence N+1 is not synthesised while N plays in this change.**
   Prefetching needs `_say` to stop waiting for each sentence with
   `barge_in` off (`local_engine.py:258-285`), because only then is N+1 in
   the queue while N plays. That wait is the microphone gate (the `_say`
   docstring). #114 (merged in `b73a3f4`) rebuilt that gate: `_mic_shut`
   (`:193`), a capture shut on `_voice_until == inf` (`:330-331`), `_say`
   waiting up to 1 s for the capture to shut (`:269-275`), and the tail
   wait at the top of `_record` (`:303-319`). Changing when `_say` returns
   is a safety change to that gate, not a measurement. Named follow-up: **"synthesise the next
   sentence while this one plays"**. Its prerequisite, #114 merged, is now
   met. Trigger:
   `synth` is at least 25 % of `speak` on turns with two or more sentences.
4. **0.35 s stays as it is, and it belongs to #114.** Two reasons. First,
   the tail is outside every TIMING line. `task.finish().line()` is logged
   at the end of `_answer` (`local_engine.py:539`). Since #114 the tail is
   a wait at the top of the next `_record` (`:303-319`), before the next
   capture. No trace is open then: the next one starts in `_hear`
   (`:453-455`), after that capture. Second, #114 made the tail part of the
   microphone gate. It is timed from `_voice_until`, so only the part not
   already spent is paid. After one of her own replies almost none of it
   is spent (#114's plan, test 6: the capture opens 0.35-0.40 s after her
   last sentence), so a turn still pays nearly all of it. Lowering it needs a
   measurement of the room (speaker lag plus reverb) against the microphone.
   That is a live-hardware test this spec does not do. This spec adds a test
   that the tail is not inside any TIMING total, so a later change cannot
   quietly put the tail into model time.
5. **`continuations` is fixed here, not in a separate issue.** This change
   has to split `model-turn` from speaking anyway (Design, item 1). Once it
   does, the current rule, "every TURN span after the first" (`trace.py:86-94`),
   makes every extra sentence a "continuation" in the very line this spec
   ships. The fix is small. A TURN span counts as a continuation only if a
   TOOL span was opened after the previous TURN span opened. The three
   existing `ContinuationTests` (`tests/test_trace.py:24-45`) pass unchanged
   under the new rule. That is the evidence it keeps #26's meaning. One edge
   is accepted and documented in the docstring: a tool call after the last
   sentence, with nothing said after it, is not counted.
6. **The cloud question does not arise in this change.** It makes no new
   ElevenLabs call. It times the existing request, which already goes to the
   `/stream` endpoint (`elevenlabs.py:141-142`). The streaming follow-up
   would use the same endpoint. The follow-up must restate this if it
   changes endpoint or protocol (for example a WebSocket).

Also decided: **no sub-second timestamps in `session.log`.** Every new
number is a duration inside the TIMING line, so the 1 s `strftime` stamp
(`feedback.py:207`) does not limit any of them. Changing the stamp format
would also touch every reader of the log (`tools/eval_router.py:53`,
`cli.py:609-615`) for no measured gain.

## Design

Five changes, all stdlib, none in `pyproject.toml` or `nix/package.nix`.

1. **Model time stops at the sentence** (`local_engine.py:513-520`). Today
   the TURN span closes when a sentence arrives. A new one opens *before*
   `await self._say(sentence)`, so with `barge_in` off the time she spends
   speaking sentence N is counted as model time for N+1. New order: close
   the TURN span, `await self._say(sentence)`, then open the next TURN span.
   Since #114, `_say` first waits up to 1 s for `_mic_shut` (`:269-275`).
   In `_answer` the capture has already shut (`_record`'s `finally`,
   `:343-346`), so the wait is normally nil. Whatever it is, it falls
   between the closed TURN span and the SPEAK span: it counts in the task
   total and in `first-audio`, and in no phase. It is not model time.
   A routed turn (`:503-505`) closes its TURN span before `_run_route`.
   Nothing about the route is model time, and its tools are still recorded
   as TOOL spans by the executor.
2. **New phases in `trace.py`**, next to `ENDPOINT` (`:44`):
   - `SPEAK = "speak"`: one span per sentence, opened by `_speech_loop`
     (`local_engine.py:236-256`) around `self.feedback._speak_now(text)`.
     This covers synthesis plus playback. The span closes in the loop's
     `finally`, before `self._speech.task_done()`, so `_say`'s `join()`
     never returns with it open. It is recorded in the loop, not in
     `Feedback`, so the engine tests' fake mouths (`Mouth`,
     `tests/test_local_engine.py:99`, installed as
     `session.feedback._speak_now = self.mouth` at `:229`) still produce it.
   - `SYNTH = "synth"`: the time before the first sample can play. Named
     spans from a fixed vocabulary, never text: `request` (from `urlopen`
     until the headers arrive, roughly time to first byte), `download` (the
     rest of the body) and `decode` (ffmpeg, `elevenlabs.py:161-170`).
   - `Trace.first_audio` (a property). The start of the first SPEAK span,
     plus the SYNTH spans inside it, minus `Trace.started`. It is the
     number from the end of the user's sentence to her first sound. It is
     printed as `first-audio=X.XXs`. With no SPEAK span it is absent.
   - `line()` breaks SYNTH down by name, as it does SUBPROCESS
     (`trace.py:117-125`). The rule goes from "only SUBPROCESS" to a
     two-entry set.
   Playback time is `speak - synth` and is not stored separately. The
   report tool derives it.
3. **Where the trace reaches the mouth.** `Feedback.trace: Trace | None =
   None`. It is set and cleared in `_answer` next to `executor.trace`
   (`local_engine.py:499`, `:538`). `_speech_loop` reads it for SPEAK.
   `_speak_now` (`feedback.py:138-155`) passes it to
   `elevenlabs.synth(text, config, trace=...)` (`elevenlabs.py:114`).
   `synth` marks its three spans with a `nullcontext` when there is no
   trace. Piper (`feedback.py:167-201`) and espeak-ng add no SYNTH span.
   Their sentence is one SPEAK span, and for them `first-audio` is a lower
   bound because it includes Piper loading the model. That is stated in the
   property's docstring. If a span is open when a voice fails, it closes in
   `finally`, so a fallback to Piper does not leave a SYNTH span running.
   With `barge_in` on, sentences can still be playing after `_answer` logs
   the line. Their spans land on a finished trace and are not reported. This
   limit is documented. This machine runs with `barge_in = false`.
   Speech outside that window runs with the trace cleared and adds no
   spans: the held prompt (`:547-548`) and #120's `_listen_loop` recovery
   lines (`:638-645`). Like `executor.trace`, `Feedback.trace` is not
   cleared if `_answer` is cancelled. The session is ending then
   (`_listen_loop` re-raises), so the only effect is spans on a trace that
   is never logged.
4. **`continuations`** (`trace.py:86-94`): as in decision 5.
5. **A report tool, `tools/timing_report.py`.** It reads
   `session.log`'s `TIMING` lines and prints, per phase, n, p50 and p95, plus
   `first-audio` and the derived `play = speak - synth`. It takes `--since
   YYYY-MM-DD` so a change can be compared before and after. It uses the
   stdlib and `trace._percentile` (nearest rank, `trace.py:146-156`). The
   parsing goes in `trace.py` as `parse_line(line) -> dict[str, float] |
   None`, next to `line()`, so the format and its reader are tested
   together. The tool is about 40 lines. `config.trace_timings` stays off by
   default (`config.py:468`). The measurement period is the user turning it
   on, which the tool's docstring says.

Nothing new records content. Span names are the fixed words above, and
`mark` still has no parameter that could carry text (`trace.py:18-23`).

## Overlap with #114, and landing order

#114 merged in `b73a3f4`, and this spec is checked against it. #114 added
`_mic_shut` (`:193`), the tail wait and the capture shut in `_record`
(`:303-346`) and the `_mic_shut` wait in `_say` (`:269-275`), and it
deleted `_answer`'s tail sleep. This spec edits `_speech_loop`
(`:236-256`), `_answer`'s sentence loop (`:513-520`), the route branch
(`:503-505`) and the trace wiring (`:499`, `:538`). It does not touch
`_say`, `_record` or `_voice_until`. The plan's step 1 re-checks every line
reference against the `main` it is written on. #80 lands after this spec,
because it extends `tools/timing_report.py` and edits `trace.py` next to
these changes.

## Alternatives rejected

- **Stream ElevenLabs in this change.** Loudnorm's look-ahead means it may
  not move first audio for a one-sentence clip (decision 1). Measure it
  first.
- **SPEAK/SYNTH spans inside `Feedback` only.** Every engine test replaces
  `_speak_now` with a fake, so the engine-level phase would be untestable
  without real audio.
- **A `first_audio` hook that `_play` calls.** It is a second channel from
  the mouth to the trace. The property derives the same number from spans
  that already exist.
- **Tracing the echo tail as a phase.** Since #114 it runs at the top of
  the next `_record`, before that turn's trace exists, so no trace is open
  to hold it. Attaching it to the next trace would record close to `0.35`
  on nearly every turn (decision 4). That is the mistake #80 describes for
  `endpoint`.
- **Sub-second log stamps.** See "Also decided".
- **A `timings` CLI subcommand instead of a tool.** It would be permanent
  CLI surface for a measurement period. `tools/` is where `bench_local.py`
  and `eval_router.py` already live.

## Risks

- **Thread safety.** SYNTH spans are appended from the `to_thread` worker,
  and SPEAK and TURN spans from the event loop. `list.append` is atomic in
  CPython, and the only concurrent reader, `line()`, runs after `_say` has
  joined (`barge_in` off). Risk is low. It is documented where
  `Feedback.trace` is set.
- **Changing what `model-turn` means.** Model time on this machine will
  fall in the TIMING line once speaking is taken out of it. A before-and-after
  comparison across this change is not like for like. The tool's docstring
  and the PR say so.
- **`continuations` edge** (decision 5): it undercounts one tool call at
  the very end. It no longer overcounts every sentence, which is the larger
  error.
- **No host risk.** The trace is behind `trace_timings`. With it off,
  every new branch is `if task`/`if trace` and does nothing. Both engines
  and every host run with it off by default.

## Verification

Fakes only: a stepped clock (as in `SpokenConsentTests`,
`tests/test_local_engine.py:756`), a fake mouth that moves it on, a fake
`urlopen` and a fake `subprocess.run` (as in `SynthTests`,
`tests/test_elevenlabs.py:219`). No audio, network or pw-cat.

Tests written first, and they must **fail on `main`**:

1. `test_speaking_is_not_model_time` (local engine). The brain yields two
   sentences, and the mouth moves the clock on 1.0 s per sentence. Expect
   `speak=2.00s`, and `model-turn` below 1.0 s. On main there is no
   `speak`, and `model-turn` includes 1.0 s.
2. `test_first_audio_is_counted_from_the_end_of_the_sentence`. endpoint
   0.8 + transcribe 0.2 + first sentence 0.5 + synth 0.3 gives
   `first-audio=1.80s`.
3. `test_sentences_without_tools_are_not_continuations`. Three sentences
   and no tool give `continuations=0` (main: 2).
4. `test_a_tool_between_sentences_is_one_continuation`. This passes on
   main, and it guards the new rule.
5. `test_a_routed_turn_has_no_model_time`. On main, `model-turn` is
   present.
6. `test_synth_is_split_request_download_decode` (`test_elevenlabs.py`).
   There are three SYNTH spans with exactly those names, and the sentence
   text does not appear in `line()`.
7. `test_a_failed_cloud_voice_leaves_no_open_span`. `synth` raises
   `Unavailable` during `download`, and Piper speaks. Every span has
   `ended` set.
8. `test_parse_line_reads_what_line_writes` (`test_trace.py`). It is a
   round trip, including the `synth(...)` breakdown and `first-audio`.
9. `test_the_echo_tail_is_outside_the_timing_line`. The tail wait in
   `_record` is left on (not patched to 0). A typed turn's TIMING total
   does not include `ECHO_TAIL_SECONDS`, and the TIMING line is logged
   before the next `_record` starts its tail wait. This test exists to
   catch a later regression, and it passes on main.
10. `RedactionTests` (`test_trace.py:76`) gets the new phases. No span name
    is outside the fixed vocabulary.

Mutation checks. Each one must turn at least one test red, and then be
reverted:
- open the next TURN span before `_say` again → 1;
- drop the "TOOL since the previous TURN" condition → 3;
- leave the SYNTH spans out of `first_audio` → 2;
- pass `text` as a span name → 6 and 10;
- remove the `finally` close in `synth` → 7.

Runners, with `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` exported:
- `nix develop -c pytest tests -q`: all pass. The count is 1126 plus the
  new tests.
- `nix develop -c python3 -m unittest discover -s tests`: same count, all
  pass.
- `nix flake check --no-write-lock-file`: passes.
- `nix develop -c python3 tools/timing_report.py --since 2026-09-01`
  against a fixture log in a temp `XDG_STATE_HOME` prints the per-phase
  table, and exits 0 on a log with no TIMING lines ("no tasks traced").

The outcome ("first audible word sooner than today's baseline") is **not**
claimed by this change. This change produces the baseline. The follow-ups
in decisions 1 to 3 are judged against it.
