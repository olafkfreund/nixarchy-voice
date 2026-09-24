---
status: approved
issue: 135
spec: spec/2026-09-24-135-stream-elevenlabs.md
---

# Plan: start her ElevenLabs voice before the whole clip has arrived

Refs #135. Base: `main` at `be27af4` (v2.0.0), which is also the base of
`perf/135-stream-elevenlabs`. 1084 tests collected
(`nix develop -c python3 -m pytest tests -q --collect-only`, checked for
this plan). 23 of them are in `tests/test_elevenlabs.py`. Every `file:line`
below was checked against `be27af4`. #138 lands first (see "Landing order"),
and it moves lines in `config.py` and `tools/timing_report.py`, so step 1
re-checks them after the rebase.

Files that change: `src/omarchy_voice/elevenlabs.py`,
`src/omarchy_voice/feedback.py`, `src/omarchy_voice/trace.py` (a comment and
a docstring only), `src/omarchy_voice/config.py`, `share/config.example.toml`,
`tools/timing_report.py`, `flake.nix` (one package in the test sandbox),
`tests/test_elevenlabs.py` and `tests/test_trace.py`. `local_engine.py`,
`pyproject.toml` and `nix/package.nix` do not change.

## Approved decisions, carried over from the spec

1. **Mastering is a fixed gain, then a limiter.** The new default for
   `elevenlabs_master` (`config.py:494`, today
   `"loudnorm=I=-16:TP=-1.5:LRA=11"`) is
   `volume=<G>dB,alimiter=limit=0.84:level=0`. `limit=0.84` is -1.5 dBFS,
   today's true-peak ceiling. `level=0` turns off alimiter's auto-level.
   `<G>` is the gain that brings the owner's five calibration clips to
   -16 LUFS integrated (mean), rounded to 0.5 dB. It is measured by the
   owner (owner gate O2), never guessed, and the PR does not merge without
   it. Setting `master = "loudnorm=I=-16:TP=-1.5:LRA=11"` under
   `[elevenlabs]` in `config.toml` restores today's sound (and today's wait,
   because loudnorm holds its output). There is no second code path and no
   new config key. `dynaudnorm` and "no mastering" are rejected
   (`config.py:491-493` records why raw output is wrong).
2. **The voice may sound different, within limits, judged in this order.**
   (a) The number: each calibration clip through the new chain measures
   -16 ± 1.5 LUFS with `ffmpeg -af ebur128`. (b) The ear: the owner listens
   to the same five clips through both chains and writes "same voice" or
   "acceptable" on #135. Anything else and the PR does not merge.
3. **Mid-stream failure depends on whether a sample has played.** The
   boundary is the first PCM chunk handed to `pw-cat`.
   - Before it (no key, HTTP error, connection refused, no body, ffmpeg not
     starting, ffmpeg giving no audio, `pw-cat` not starting):
     `Unavailable`, as today. Piper says the whole sentence, with today's
     `tts     elevenlabs failed (...) — using piper` line
     (`feedback.py:157`).
   - After it (the body stops, ffmpeg dies, `pw-cat` stops taking input):
     a new `elevenlabs.Cut(Unavailable)`. What was piped plays out, the
     mouth returns, and `_speak_now` logs
     `tts     elevenlabs cut off mid-sentence (<reason>)` and returns. No
     Piper, no repeat. The next sentence starts from the top of
     `_speak_now` as normal.
   - On the boundary (the body fails after it started, before ffmpeg has
     output anything): the feeder **kills** ffmpeg instead of closing its
     stdin, so a partial clip is never flushed and played after "nothing
     heard" was decided. Piper says the whole sentence.
4. **Format stays `mp3_44100_128`** (`elevenlabs.py:35`). It works on every
   tier and is today's source, so a before/after difference comes from
   streaming alone. The ffmpeg input gets `-f mp3`, and a comment ties it
   to `FORMAT`. The docs comment at `elevenlabs.py:32-34` gains the tier
   reason. `pcm_24000` and `pcm_44100` are rejected.
5. **#114's mic gate: `_speak_now` returns only when `pw-cat` has exited.**
   The ElevenLabs path ends with `pw-cat`'s stdin closed and
   `pw-cat.wait()`, after the download and after ffmpeg, on every path,
   `Cut` included. `_speech_loop` (`local_engine.py:299-324`), `_say`
   (`:326-346`) and `_voice_until` (`:252`, `:323`, `:335`, `:352`) do not
   change.
6. **Landing: #135 before #137**, and #137's plan is written on #135's
   merged code (spec decision 6 and "Approved with"). The owner's full
   order is #138, #135, #136, #137. #136 shares only `feedback.py`.
7. **No separate measuring day before the build.** The "before" run on
   `main` is owner gate O1 and can start today. Merge rule (O6): the
   branch's `synth` p50 is at least 0.1 s below `main`'s, and O3 is
   accepted. If `synth` does not move by that much, the result goes on #135
   and the PR is closed unmerged. If `synth` falls and `first-audio` does
   not, the measurement is investigated before merge.
8. **`elevenlabs.speak(text, config, timeout=30.0, trace=None) -> None`**
   fetches, decodes, masters and plays, and returns when `pw-cat` has
   exited. `_speak_now` (`feedback.py:141-158`) calls it in place of
   `synth` + `_play` (`:148-149`). `Feedback._play` (`:160-168`) has no
   other caller and is deleted. Checks before the network, each
   `Unavailable`: key and voice id (today `elevenlabs.py:135-137`), `ffmpeg`
   (`:138-139`), and `pw-cat` (moved here from `feedback.py:162-164`).
9. **The pipeline**, in the calling thread plus one feeder thread. All
   stdlib (`urllib`, `subprocess`, `threading`).
   - `urlopen` in the `request` span, as today (`elevenlabs.py:158-159`).
     HTTP and URL errors map to `Unavailable` as today (`:162-166`).
   - ffmpeg argv, exactly:
     `ffmpeg -loglevel quiet -f mp3 -probesize 32 -analyzeduration 0
     -fflags +nobuffer -i pipe:0 -af <master> -f s16le -ar 44100 -ac 1
     -flush_packets 1 pipe:1`, `Popen` with stdin and stdout pipes.
     Replacing loudnorm alone is not enough: ffmpeg's input probing also
     holds output until EOF on a short pipe (spec's table: 3.28 s vs 0.03 s).
   - Feeder thread: `response.read(4096)` → `ffmpeg.stdin.write` until EOF,
     then close stdin. On an exception: record it, kill ffmpeg. An empty
     body is recorded as "ElevenLabs returned no audio", as today (`:167-168`).
   - Calling thread: `ffmpeg.stdout.read1(4096)` in a loop. On the first
     non-empty chunk: close the `buffer` span, start `pw-cat` with today's
     arguments (`feedback.py:166-168`), then write every chunk to it.
     `played` counts bytes written.
   - End: close `pw-cat`'s stdin and `wait()`; `ffmpeg.wait(timeout)`; join
     the feeder. On an error, a non-zero ffmpeg exit, or no PCM:
     `played == 0` raises `Unavailable`, `played > 0` raises `Cut`.
10. **Reaping, in `finally`, on every path.** ffmpeg is killed if still
    running, then waited. `pw-cat` gets stdin closed, then `wait()`; it is
    only killed if the exception is not an `Exception`. The response is
    closed. The feeder is joined with a 1 s timeout. `ponytail:` a feeder
    blocked in `read()` on a stalled socket can outlive the call by up to
    `timeout`; it is a daemon thread that holds no process.
11. **SYNTH spans mean "the wait before the first sample", before and
    after.** New names: `request` (unchanged) and `buffer` (from the headers
    to the first PCM chunk, closed just before `pw-cat` starts).
    `download` and `decode` go. `Trace.first_audio` (`trace.py:119-134`)
    keeps its formula; its docstring changes from "the cloud voice has the
    whole clip before a sample plays" (`:123-124`) to "SYNTH spans are, by
    contract, only the wait before the first sample". The comment at
    `trace.py:49-51` names `request` and `buffer`. `synth=` and
    `first-audio=` mean the same on `main` and on the branch, so both can be
    compared across the change. `play = speak - synth`
    (`tools/timing_report.py:99`) still means "first sample to the mouth
    returning". A span open at a failure closes in `finally`.
12. **`tools/timing_report.py`**: `--until YYYY-MM-DD` (exclusive) next to
    `--since` (`:61-62`, filter at `:74`), and the docstring (`:1-29`) names
    `first-audio` and the SYNTH meaning above. `synth.download`,
    `synth.decode` (before) and `synth.buffer` (after) print as separate
    rows, which shows which build a window came from.
13. **Words**: `config.py:491-494` gets the new default and a comment saying
    what `<G>` was measured from and that `loudnorm` there restores today's
    sound and today's wait. The `elevenlabs.py` module docstring (`:1-14`)
    and the `ponytail:` note in `synth` (`:125-129`) are rewritten to match.
14. **A whole-clip collect mode stays** ("Approved with"; #137's spec,
    `perf/137-prefetch-next-sentence`, decision 4). `synth(text, config,
    timeout=30.0, trace=None) -> tuple[bytes, int]` keeps its signature and
    becomes the same pipeline with the PCM collected into bytes instead of
    written to `pw-cat`: one flag in the calling thread's loop, one ffmpeg
    argv, one mastering. It is not a second copy.
15. **Nothing new records content.** `buffer` is a fixed name; the text is
    only in the request body.
16. **Rejected, and not built**: a fixed pre-buffer before `pw-cat` starts,
    the WebSocket `stream-input` API, `pcm_*` formats, two-pass loudnorm, a
    config switch `elevenlabs_stream`. Nothing changes in `pyproject.toml`
    or `nix/package.nix`.

### Spec ambiguities, resolved here (flagged for the reviewer)

- **R1. Collect mode: this change ships it.** The spec's alternatives say
  "if #135 merges first, #137 adds that mode", but "Approved with" says
  #137 "builds its make-ahead on this change's whole-clip collect mode", and
  #137's spec expects `synth(text, config, trace=None) -> (bytes, rate)` to
  exist. The later approval note wins: `synth` stays, as decision 14.
- **R2. Collect mode never raises `Cut`.** Nothing has played, so every
  failure there is `Unavailable` and partial PCM is discarded. `synth` does
  not require `pw-cat` (the `pw-cat` check is in `speak` only). With a
  `trace`, it records the same `request` and `buffer` spans; #137 passes
  `trace=None`.
- **R3. `<G>` before the owner measures it.** The implementer cannot make
  the 5 API calls. The code lands with `volume=0dB,alimiter=limit=0.84:level=0`
  and the marker comment `# G pending: owner calibration on #135`. The
  merge checklist fails while `grep -n "G pending" src/omarchy_voice/config.py
  share/config.example.toml` finds anything. The lead commits the measured
  value (O4).
- **R4. `share/config.example.toml:209-212` sets `master = "loudnorm=..."`
  uncommented.** The spec does not mention it. Anyone who copied the example
  would keep loudnorm and see no change. The example gets the new default
  and a comment naming the loudnorm rollback. The owner's own `config.toml`
  on p620 has no `master` line (checked); razer is checked in O5.
- **R5. The five calibration sentences are fixed here**, since part D's
  replies are model text and not fixed. They are listed in O2, and O1/O5
  use prompts that produce replies of the same shape.
- **R6. The flake sandbox has no ffmpeg.** The `unit` check's
  `nativeBuildInputs` (`flake.nix:112-128`) lack it, so tests 12 and 13
  would be skipped there, and mutations M1 and M9 would only be caught in
  the dev shell. `pkgs.ffmpeg` is added to that list. The pinned
  `nixpkgs#ffmpeg` is 9.0.1 with `libmp3lame`, `alimiter`, `volume` and
  `loudnorm` (checked for this plan). The dev shell (`flake.nix:58-67`) has
  no ffmpeg either, and uses the host's from `PATH`.
- **R7. The spec cites `RedactionTests` at `test_trace.py:76`.** At
  `be27af4` the class is at `:113`, and the test that pins the SYNTH
  vocabulary is `test_speech_spans_carry_fixed_names_only` at `:159`. Test
  10 edits that one.

## Landing order and overlap

Second of four: **#138, #135, #136, #137.**

- **#138 (end of turn), first.** Its spec moves `is_fragment` out of
  `tools/timing_report.py` and rewrites that docstring, and touches
  `config.py` and `share/config.example.toml`. Step 1 rebases onto it and
  re-checks every `config.py` and `timing_report.py` line above. No logical
  overlap: #138 is the ears, this is the mouth.
- **#136 (resident Piper), after.** It shares `feedback.py` only: it rewrites
  `_speak_piper` (`:170-206`) and adds `PiperWorker`, right below the
  `_play` that this change deletes (`:160-168`). Adjacent hunks; #136
  rebases. `PiperWorker.speak` is a method on its own class and does not
  clash with `elevenlabs.speak`. Its `pw-cat` argv is `_speak_piper`'s own
  (`feedback.py:188`), untouched here.
- **#137 (prefetch), last.** It changes the same seam
  (`feedback.py:148-149`) and `_speak_now`'s signature
  (`_speak_now(text, made=None)`), and its make-ahead calls
  `elevenlabs.synth(text, config, trace=None)`, the collect mode kept by
  decision 14. Its plan is written on this change's merged code, and its
  line numbers in `feedback.py:141-158` move.

## Steps

0. **Baseline, on `main` `be27af4`.** With
   `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` exported for this and
   every later step:
   `nix develop -c python3 -m pytest tests -q --collect-only`,
   `nix develop -c python3 -m pytest tests -q`,
   `nix develop -c python3 -m unittest discover -s tests`
   → verify by 1084 collected, all passing in both runners, and
   `pytest tests/test_elevenlabs.py --collect-only -q` giving 23. Write N =
   the collected count down; later counts are relative to it. Tell the lead
   that owner gate O1 (the "before" run) can start on the installed `main`
   build now (decision 7).
1. **Rebase onto `main` once #138 has merged.** `git rebase origin/main`;
   re-check `config.py:491-494`, `timing_report.py:1-29,61-62,74,99`,
   `share/config.example.toml:209-212`, and `feedback.py:141-168`
   → verify by `git log --oneline origin/main..` showing only this branch's
   docs commits, step 0's runs passing on the new base, and any moved line
   numbers corrected in this plan in the same commit as step 2.
2. **`tests/test_elevenlabs.py`: the fakes and the new tests, first (red).**
   - Harness (module level, no new file): `_Body`, an `urlopen` response
     whose `read(n)` returns scripted chunks, can block on a
     `threading.Event` before a given chunk, and can raise after N chunks;
     `_FakeFfmpeg`, a `Popen` stand-in whose `stdin.write` queues output
     (one PCM chunk per mp3 chunk unless scripted silent), whose
     `stdout.read1` returns queued PCM and then `b""` after stdin closes or
     `kill()`, whose `write` after `kill()` raises `BrokenPipeError`, and
     which records `argv`, `killed`, `stdin_closed`, `waited` and a scripted
     `returncode`; `_FakePwCat`, recording `argv`, `written` and `waited`,
     whose `wait()` can block on an event and sets `wait_entered`. A
     `Popen` factory picks the fake by `argv[0]`. `subprocess.run` is
     patched to raise `AssertionError`, and `shutil.which` to return a
     path, so a test run on `main` never starts a real process.
   - Removed: `SynthTests` (`:220-312`, 5 tests); its behaviour moves to
     the tests below.
   - New class `StreamTests` (spec tests 1-7, plus two):
     1. `test_pw_cat_starts_before_the_body_ends`: chunk 1, then block on
        `body_may_end`. `_FakePwCat.written` is non-empty (waited on an
        event, not a sleep) while `body_may_end` is unset; then set it and
        join.
     2. `test_mp3_is_asked_for_and_ffmpeg_is_told_not_to_probe`: URL has
        `output_format=mp3_44100_128` and the voice id; body has
        `model_id` and no `style`; ffmpeg argv has `-f mp3`,
        `-probesize 32`, `-analyzeduration 0`, `-fflags +nobuffer` and
        `-af volume=2.0` from `_config(elevenlabs_master="volume=2.0")`.
     3. `test_a_failure_before_the_first_sample_is_piper_saying_it_all`:
        through `_speak_now`; the body raises `OSError` before chunk 1.
        Piper (patched `_speak_piper`) gets the whole text; no `pw-cat`
        was started; ffmpeg was started with the streaming argv and killed;
        the log has `elevenlabs failed` and `using piper`.
     4. `test_a_failure_after_the_first_sample_is_a_log_line_not_a_repeat`:
        the body raises after two chunks, both output. Piper not called;
        `pw-cat` got two chunks and was waited; the log has
        `cut off mid-sentence` and not `using piper`.
     5. `test_the_mouth_returns_only_when_pw_cat_does` (#114): body and
        ffmpeg finished, `_FakePwCat.wait` blocks. `_speak_now` on a
        thread; after `wait_entered`, the thread is alive; set the event;
        the thread joins within 2 s.
     6. `test_every_child_is_reaped_on_every_path`: `subTest` over success,
        failure before the first sample, failure after it, ffmpeg exiting 1.
        Every started fake has `waited`; ffmpeg has `killed` on the three
        failure paths.
     7. `test_a_partial_body_with_no_output_yet_is_not_played`: body fails
        after one chunk, ffmpeg scripted silent. ffmpeg `killed` and not
        `stdin_closed`-before-kill; no `pw-cat`; Piper speaks.
     8. `test_missing_ffmpeg_or_pw_cat_is_named_before_the_network_is_touched`:
        `subTest` over each binary missing; `Unavailable` names it;
        `urlopen` not called.
     9. `test_collect_mode_returns_the_pcm_speak_would_play`: the same
        script through `synth` and through `speak`. `synth` returns
        `(bytes, 44100)` equal to what `speak` wrote to `pw-cat`, both
        ffmpeg argvs are equal, `synth` starts no `pw-cat`, and a failure
        after the first chunk raises `Unavailable`, not `Cut` (R2).
   - `FallbackTests` (`:314`): add
     10. `test_no_span_is_left_open_on_a_cut` (spec test 9): a traced `Cut`;
         every span has `ended`, SYNTH names are `["request", "buffer"]`.
     The helper `_speak` (`:329-338`) patches `elevenlabs.speak` instead of
     `synth`. `:356` (#79's test 7) uses the harness and expects
     `["request", "buffer"]`. `:376` patches `speak` and asserts it was
     called once with the text, Piper not called. `:387` and `:397` patch
     `speak`.
   - New class `RealFfmpegTests`, `skipUnless` ffmpeg with `libmp3lame`:
     11. `test_the_default_master_streams` (spec 12): encode 4 s of
         `sine` to mp3 in a temp dir with `subprocess.run` (real ffmpeg, no
         network, no audio device), feed its first quarter to
         `Popen(elevenlabs._ffmpeg_argv(Config().elevenlabs_master))`, hold
         stdin open, and expect PCM on stdout within 1.0 s (a
         `select` deadline); then close stdin and reap.
     12. `test_loudnorm_in_master_still_works` (spec 13): the whole clip
         through `_ffmpeg_argv` with the loudnorm string and with the
         default; after EOF both give PCM, lengths within 0.01 s of audio
         (882 bytes) of each other.
   → verify by `nix develop -c python3 -m pytest -q tests/test_elevenlabs.py`
   on the rebased base with no `src/` change: each of the 12 new tests
   fails (on `main`, `speak`, `Cut` and `_ffmpeg_argv` do not exist, and
   `synth` calls the patched `subprocess.run`), and every other test in the
   file passes except the edited `:356`, `:376`, `:387`, `:397`. Commit
   tests only: `test(elevenlabs): streaming contract, red on main (#135)`.
3. **`tests/test_trace.py`: spans, redaction and the report (red).**
   13. `test_synth_spans_stop_at_the_first_sample` (spec 8): a stepped fake
       clock (`trace_mod.time` patched, `Trace(started=...)` passed
       explicitly, per #79's deviation note). The fake `urlopen` steps
       0.2 s, the first non-empty `read1` steps 0.1 s, `_FakePwCat.wait`
       steps 2.0 s. A SPEAK span is opened around `elevenlabs.speak`, as
       `_speech_loop` does. Asserts: SYNTH names are `request` and `buffer`;
       `synth=0.30s` in `line()`; `first_audio` is the SPEAK start plus
       0.30. Then, **comparability**: a hand-built `main`-shaped trace
       (SPEAK with `request` 0.2, `download` 0.05, `decode` 0.05) and a
       branch-shaped one (`request` 0.2, `buffer` 0.1) give equal
       `first_audio` and equal `synth=` in `line()`.
   14. `test_timing_report_until_is_exclusive` (spec 11), in
       `TimingReportTests` (`:241`): a fixture `session.log` in a temp dir
       with one TIMING line on each of 2026-09-01, -02, -03 (each with a
       `synth` and `speak` field); `--since 2026-09-01 --until 2026-09-03`
       prints `n` 2 for `speak`; `--since 2026-09-03` prints 1. `LOG_FILE`
       patched as `:263` does.
   - Edit `test_speech_spans_carry_fixed_names_only` (`:159`, spec 10):
     use the step 2 harness in place of `subprocess.run` and `_play`, and
     the vocabulary becomes `{"", "request", "buffer"}`.
   → verify by `nix develop -c python3 -m pytest -q tests/test_trace.py`:
   13, 14 and the edited `:159` fail; all else passes. Same commit as
   step 2, or a second `test(...)` commit.
4. **`src/omarchy_voice/elevenlabs.py`: the pipeline** (decisions 3, 4, 8,
   9, 10, 11, 14, 15).
   - `class Cut(Unavailable)` after `:39-40`, docstring: "part of the
     sentence played; the rest is lost, and the caller must not repeat it".
   - `_ffmpeg_argv(master) -> list[str]`, the argv of decision 9, with the
     comment tying `-f mp3` to `FORMAT`. One helper, two callers.
   - `_run(text, config, timeout, trace, play: bool) -> bytes`: the checks
     of decision 8 (`pw-cat` only when `play`), the request body and URL
     unchanged (`:141-156`), then decision 9's pipeline and decision 10's
     `finally`. With `play` false, chunks are appended to a list, and every
     failure is `Unavailable` (R2).
   - `speak(...)` is `_run(..., play=True)`; `synth(...)` is
     `(_run(..., play=False), RATE)`.
   - Module docstring (`:1-14`), `:32-34` (decision 4), the `synth`
     docstring and its `ponytail:` note (`:118-129`), and a docstring on
     `speak` naming the #114 contract and the `Cut` rule.
   → verify by steps 2's tests 1, 2, 4-9, 11, 12 passing (3 and 10 need
   step 5), and `test_trace.py` 13 passing.
5. **`src/omarchy_voice/feedback.py`: the mouth** (decisions 3, 5, 8).
   In `_speak_now` (`:141-158`): replace `:148-149` by
   `elevenlabs.speak(text, self.config, trace=self.trace)`; add
   `except elevenlabs.Cut as exc:` before `except Exception` (`:151`),
   logging `tts     elevenlabs cut off mid-sentence ({exc})` and returning.
   Delete `_play` (`:160-168`), including its `pw-cat` check
   (`:162-164`), which moved to `speak`. Check nothing else calls it:
   `grep -rn "_play(" src tests tools` finds no hit.
   → verify by all of `tests/test_elevenlabs.py` passing.
6. **`src/omarchy_voice/trace.py`: words only** (decision 11). The comment
   at `:49-51` names `"request"` or `"buffer"`; the `first_audio`
   docstring (`:121-126`) states the SYNTH contract. The formula
   (`:128-134`) is untouched.
   → verify by `git diff -U0 -- src/omarchy_voice/trace.py` touching only
   comment and docstring lines, and `tests/test_trace.py` passing except 14.
7. **`tools/timing_report.py`** (decision 12). `--until` beside `--since`
   (`:61-62`), skip `line[:10] >= args.until` when set, next to `:74`;
   docstring usage line (`:3`) and the `first-audio`/SYNTH meaning.
   → verify by test 14 passing, and `nix develop -c python3
   tools/timing_report.py --since 2026-09-01 --until 2026-09-02` on an
   empty temp `XDG_STATE_HOME` printing `no tasks traced` and exiting 0.
8. **`src/omarchy_voice/config.py:491-494` and
   `share/config.example.toml:209-212`** (decisions 1, 13; R3, R4). Default
   `volume=0dB,alimiter=limit=0.84:level=0` with the `G pending` marker;
   the comment says `<G>` is the mean gain to -16 LUFS over the owner's
   five clips, and that `master = "loudnorm=I=-16:TP=-1.5:LRA=11"` restores
   today's sound and today's wait. The example gets the same value and the
   rollback line as a comment.
   → verify by test 11 passing with the new default, and
   `nix develop -c python3 -c "from omarchy_voice.config import Config;
   print(Config().elevenlabs_master)"` printing the new chain.
9. **`flake.nix`, the `unit` check** (R6): add `pkgs.ffmpeg` to
   `nativeBuildInputs` (`flake.nix:112-128`), with a one-line comment that
   `RealFfmpegTests` need it and it is the same ffmpeg the package wraps
   (`nix/package.nix:86`).
   → verify in step 11 that the flake check's pytest summary has two fewer
   skips than on `main`.
10. **Mutation checks, one per decision.** Apply alone, run the named
    tests, then `git checkout -- src/ tools/`:
    - M1 (dec. 1): default `master` back to loudnorm → 11 fails.
    - M2 (dec. 2): owner-only; the merge checklist refuses without the O3
      verdict on #135. Not code.
    - M3 (dec. 3, before): raise `Cut` when `played == 0` → 3 fails.
    - M4 (dec. 3, after): delete the `except elevenlabs.Cut` clause → 4
      fails.
    - M5 (dec. 3, boundary): the feeder closes ffmpeg's stdin instead of
      killing it on an error → 7 fails.
    - M6 (dec. 4): drop `-f mp3` from `_ffmpeg_argv` → 2 fails.
    - M7 (dec. 5): return from `_run` before `pw-cat.wait()` → 5 fails.
    - M8 (dec. 6, 7): not code; checked by the landing order and O6.
    - M9 (dec. 9): drop `-probesize 32 -analyzeduration 0` → 11 fails;
      read the whole body before writing to ffmpeg → 1 fails.
    - M10 (dec. 8): drop the `pw-cat` check → 8 fails.
    - M11 (dec. 10): remove the `finally` reaping → 6 and 10 fail.
    - M12 (dec. 11): close `buffer` when the download ends instead of at
      the first chunk → 1 and 13 fail.
    - M13 (dec. 12): make `--until` inclusive (`>` for `>=`) → 14 fails.
    - M14 (dec. 13, 16): not code; `git diff origin/main --stat` lists only
      the files at the top, and no `pyproject.toml` or `nix/package.nix`.
    - M15 (dec. 14): give `synth` its own argv without `-af` → 9 fails.
    - M16 (dec. 15): name the SYNTH span with the text → the edited
      `test_trace.py:159` fails.
    → verify by each mutation turning its tests red and `git status`
    clean in `src/` and `tools/` after each revert.
11. **Full suites and the flake.**
    `nix develop -c python3 -m pytest tests -q`,
    `nix develop -c python3 -m unittest discover -s tests`,
    `nix flake check --no-write-lock-file`
    → verify by all three passing with N - 5 + 14 tests (**1093** if N is
    still 1084): 12 new in `test_elevenlabs.py`, 2 new in `test_trace.py`,
    5 removed. `RealFfmpegTests` run, not skip, in the flake check. Nothing
    runs in the background; `pgrep -f "pw-cat|ffmpeg -loglevel quiet -f mp3"`
    finds nothing started by the tests.
12. **PR, not merged by the implementer.** Link intent, spec and plan. Say:
    the SYNTH breakdown's names change (`download`/`decode` → `buffer`) but
    `synth=` and `first-audio=` are comparable; `<G>` is pending (R3); the
    owner gate below must be complete before merge. Hand the checklist
    below to the lead.

## Deviation found while implementing (2026-09-25)

- **Step 1 did not run.** #138 is still open and unmerged, and `origin/main`
  is still `be27af4`, so there was nothing to rebase onto. The branch is
  built on `be27af4`, as the lead's setup said. Whichever of #135 and #138
  merges second rebases and re-checks `config.py`,
  `share/config.example.toml` and `tools/timing_report.py`.
- **Step 0: `tests/test_elevenlabs.py` collects 25 tests, not 23.** The
  total of 1084 is right. After this change the file has 25 - 5 + 12 = 32,
  and the suite total is still 1093.
- **Step 2's red run: `:340` and `:348` are red too.** They are unchanged,
  but they use the `_speak` helper, and the plan has that helper patch
  `elevenlabs.speak`. On `main` that name does not exist, so every test that
  uses the helper errors. They pass again once `speak` exists (step 4).
- **Step 4's verify line is too early for tests 4, 5 and 11.** After step 4
  alone, 1, 2, 3, 6, 7, 8, 9, 12 and 13 pass. Tests 4 and 5 go through
  `_speak_now`, so they need `Cut` handled and `_play` gone (step 5). Test 11
  streams `Config().elevenlabs_master`, which is loudnorm until step 8, and
  loudnorm holds its output. Nothing changes in the code; each test is
  checked at the step that makes it pass.
- **The fake ffmpeg keeps its exit code once reaped, like `Popen`.** The
  reaping in `finally` calls `kill()` on every path, and `Popen.kill()` does
  nothing to a child already reaped. The first fake recomputed the exit code
  on a second `wait()`, which made a clean exit look like -9.

## Owner gate before merge (the lead hands this to the owner)

The implementer runs none of this. It costs 5 ElevenLabs calls and plays
audio. Record each result on #135.

- [ ] **O1. "Before" run, on the `main` build (can start at step 0).** Set
  `trace_timings = true` in `config.toml`. Ask these five, repeated, until
  `python3 tools/timing_report.py --since <day>` shows at least 30 `speak`
  samples: "open Firefox", "what time is it", "turn the volume down",
  "what's on my calendar this afternoon", "take a screenshot". Note the
  day.
- [ ] **O2. Measure G to -16 LUFS.** Fetch the five fixed sentences (R5):
  1 "Firefox is open on workspace two." 2 "It's ten past four."
  3 "I've turned the volume down to thirty percent."
  4 "There's nothing on your calendar this afternoon."
  5 "Done. The screenshot is on your clipboard."
  For each N and its text T, with `VOICE` your `voice_id`:
  ```
  curl -sS -X POST \
    "https://api.elevenlabs.io/v1/text-to-speech/$VOICE/stream?output_format=mp3_44100_128" \
    -H @<(printf 'xi-api-key: %s\n' "$(secret-tool lookup service omarchy-voice-elevenlabs)") \
    -H "Content-Type: application/json" \
    -d "{\"text\":\"$T\",\"model_id\":\"eleven_turbo_v2_5\",\"voice_settings\":{\"stability\":0.5,\"similarity_boost\":0.75}}" \
    -o clipN.mp3
  ffmpeg -hide_banner -nostats -i clipN.mp3 -af ebur128 -f null - 2>&1 | grep -A1 "Integrated loudness" | grep " I:"
  ```
  (The key goes in through a file descriptor, not the command line.)
  `G = -16 - mean(I)`, rounded to 0.5 dB. Post the five `I` values and `G`.
- [ ] **O3. The number, then the A/B listen.** For each clip:
  ```
  ffmpeg -i clipN.mp3 -af "loudnorm=I=-16:TP=-1.5:LRA=11" -ar 44100 -ac 1 oldN.wav
  ffmpeg -i clipN.mp3 -af "volume=${G}dB,alimiter=limit=0.84:level=0" -ar 44100 -ac 1 newN.wav
  ffmpeg -hide_banner -nostats -i newN.wav -af ebur128 -f null - 2>&1 | grep -A1 "Integrated loudness" | grep " I:"
  ```
  Each `newN.wav` must read -16 ± 1.5 LUFS. Then listen `pw-play oldN.wav;
  pw-play newN.wav` for all five. Write on #135 "same voice" or
  "acceptable", or reject. A rejection stops the merge.
- [ ] **O4. The lead commits G** in `config.py` and
  `share/config.example.toml`, removing the `G pending` marker, as
  `fix(config): the measured gain for the ElevenLabs master (#135)`, and
  re-runs step 11. `grep -n "G pending"` finds nothing.
- [ ] **O5. "After" run, on the branch build, on a later day.** Install it on
  p620 (and razer if used). On each host, `grep -n master
  ~/.config/omarchy-voice/config.toml` shows no loudnorm override (R4).
  Repeat O1's prompts until 30 `speak` samples. Note any gap heard
  mid-sentence (the underrun risk).
- [ ] **O6. The before/after trace, and the merge rule.** Run
  `python3 tools/timing_report.py --since <O1 day> --until <O5 day>` and
  `--since <O5 day>`. Post the p50 and p95 of `first-audio`, `synth`,
  `speak` and `play` for both windows. **Merge only if the branch's `synth`
  p50 is at least 0.1 s below `main`'s and O3 was accepted.** If `synth`
  did not move by 0.1 s, post that and close the PR unmerged. If `synth`
  fell and `first-audio` did not, investigate before merging.
- [ ] **O7. "merge"**, said by the owner, as its own message.

## Tests

```
export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent
nix develop -c python3 -m pytest -q tests/test_elevenlabs.py tests/test_trace.py
nix develop -c python3 -m pytest tests -q                 # N - 5 + 14, all pass
nix develop -c python3 -m unittest discover -s tests      # same count, OK
nix flake check --no-write-lock-file                      # passes, RealFfmpegTests run
```

No test reaches ElevenLabs, plays audio, starts a real `pw-cat`, or reaches
D-Bus. `RealFfmpegTests` run a local ffmpeg on a generated sine in a temp
dir, and nothing else. Every wait in a threaded test is on an event with a
deadline, not a sleep.

## Rollback

- Before merge: drop the branch.
- After merge, **no rebuild**: `master = "loudnorm=I=-16:TP=-1.5:LRA=11"`
  under `[elevenlabs]` in `config.toml` gives today's sound and today's wait
  on the streaming path (decision 1, test 12).
- After merge, full: `git revert` the merge commit. `speak` and `Cut` go,
  `synth` and `_play` come back, the SYNTH names return to
  `request`/`download`/`decode`, `--until` goes. If #137 has landed on top,
  revert it first: its make-ahead calls this change's collect-mode `synth`.
  #136 reverts independently. No state file or migration. Hosts without
  `[elevenlabs] enabled` never reach this code.
