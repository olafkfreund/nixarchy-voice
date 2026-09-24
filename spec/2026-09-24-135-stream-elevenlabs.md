---
status: draft
issue: 135
intent: intent/2026-09-24-135-stream-elevenlabs.md
---

# Spec: start her ElevenLabs voice before the whole clip has arrived

Refs #135. Line numbers are from `main` at `be27af4` (v2.0.0), which is also
the base of this branch. Baseline: 1084 tests collected
(`nix develop -c python3 -m pytest tests -q --collect-only`).

## What was checked for this spec (no API call, no audio)

One local experiment, in a scratch directory and not committed. A 4 s sine
was encoded to `mp3_44100_128` with ffmpeg 9.0.1. The first quarter of it
(1 s of audio) was fed to the ffmpeg command in chunks, and then stdin was
held open for 3 s, the way a download that has not finished yet would hold
it. The table shows when the first PCM byte came out:

| ffmpeg input flags | filter | first PCM byte |
| --- | --- | --- |
| today's (`-i pipe:0`) | today's `loudnorm=I=-16:TP=-1.5:LRA=11` | 3.30 s, at EOF |
| today's | `volume=6dB,alimiter=limit=0.84:level=0` | 3.28 s, at EOF |
| `-f mp3 -probesize 32 -analyzeduration 0 -fflags +nobuffer` | `volume` + `alimiter` | **0.03 s** |

With the whole clip fed over 1 s, loudnorm gave its first byte after 3 s of
audio had arrived, as the intent said. The table adds something the intent
did not know. **Replacing loudnorm alone is not enough.** ffmpeg's input
probing also holds output back until EOF on a short pipe. Both have to
change. The low-latency run gave 94 bytes (47 samples, about 1 ms) less
output, because without the probe the decoder handles the mp3 start delay
differently. That cannot be heard. The test signal was a sine, not speech.

## Decisions on the intent's open questions

The intent was approved without answers, so each question is decided here
with its reasoning. Any decision can be rejected at this gate.

1. **What replaces single-pass loudnorm: a fixed gain, then a limiter.**
   The new default for `elevenlabs_master` (`config.py:494`) is
   `volume=<G>dB,alimiter=limit=0.84:level=0`. `limit=0.84` is -1.5 dBFS,
   today's true-peak ceiling. `level=0` turns off alimiter's auto-level,
   which would otherwise push the output back up to 0 dBFS. The limiter's
   look-ahead is its 5 ms attack, so it works on a stream. `<G>` is
   measured once for the owner's voice (Verification, part C). It is the
   gain that brings his calibration clips to -16 LUFS integrated, loudnorm's
   target today, rounded to 0.5 dB. The implementing PR does not merge with
   a guessed `<G>`.
   The config value is also the way back. With
   `master = "loudnorm=I=-16:TP=-1.5:LRA=11"` under `[elevenlabs]` in
   `config.toml`, the streaming path sounds exactly as it does today: the
   filter sees the same frames. It only waits as long as today, because
   loudnorm holds the output. So there is no second code path and no new
   config key.
   Rejected: `dynaudnorm`. Its delay is frame length × gauss size, and at
   its defaults that is several seconds. At settings short enough to stream,
   it pumps on a one-sentence clip. That is a new sound, not today's sound
   at a steadier level. Also rejected: no mastering at all. The
   `config.py:491-493` comment records why raw output is the wrong voice.
2. **May the voice sound different: yes, within limits. The owner judges it
   by ear, after a number shows the level is right.** A fixed gain gives
   the same average level as loudnorm but not loudnorm's per-clip levelling.
   A quiet sentence stays a little quieter than a loud one, as the API made
   it. Two checks, in this order:
   (a) the number: each calibration clip through the new chain measures
   -16 ± 1.5 LUFS with `ffmpeg -af ebur128` (that is the tolerance, since
   the gain is fixed and the clips differ);
   (b) the ear: the owner listens to the same five clips through both chains
   (part C). His "same voice" or "acceptable" goes on #135 in writing, as
   the intent requires. If he says neither, the PR does not merge.
   The number alone cannot say it is still "the voice you chose". Only the
   person who chose it can.
3. **Fallback on a mid-stream failure: it depends on whether a sample has
   played.** The boundary is the first PCM chunk handed to `pw-cat`.
   - **Before that** (no key, HTTP error, connection refused, no body,
     ffmpeg failing to start, ffmpeg giving no audio): `Unavailable`, as
     today. Piper says the whole sentence, with the same `tts elevenlabs
     failed (...) — using piper` line (`feedback.py:150-158`). Nothing has
     been heard, so it is exactly today's behaviour.
   - **After that** (the body stops mid-way, ffmpeg dies): no Piper. What
     has already been piped plays out, then the mouth returns. A new
     `Cut(Unavailable)` exception makes `_speak_now` log
     `tts     elevenlabs cut off mid-sentence (<reason>)` and return. The
     next sentence starts from the top of `_speak_now` as normal, so if the
     cloud is still down it goes straight to Piper.
   Why not repeat the whole sentence in Piper: she would say the start of
   the sentence twice, in two voices, and a repeat is heard as a glitch in
   the conversation. Why not let Piper say only the rest: nothing knows
   which words were heard. The cost is part of one sentence lost, with a log
   line, on a path that `session.log` shows 0 times in 3046 lines (#136's
   intent). The intent allows this: "never half spoken and then silent
   *without a line saying why*". Mutation 3 guards it.
   One case sits on the boundary: the body fails after it has started but
   before ffmpeg has output anything. There the feeder **kills** ffmpeg
   instead of closing its stdin. A clean EOF would make ffmpeg flush the
   partial clip, and a half sentence would play after the error was already
   decided as "nothing heard". Killing it keeps the rule exact: if nothing
   was played, Piper says the whole sentence.
4. **Format: stay on `mp3_44100_128`.** It works on every tier, and the
   account's tier is not known. It is also today's format, so a before/after
   difference comes from streaming and not from a new source.
   `pcm_24000` would skip the decode, but ffmpeg is still needed for the
   mastering. The decode is about 1 ms per mp3 frame and runs at the same
   time as the download. It would also mean a lower rate than today and a
   second change to the sound, measured at the same time as the first.
   `pcm_44100` needs Pro. The ffmpeg input gets `-f mp3` because the format
   is fixed by `FORMAT`. A comment ties the two together.
5. **#114's mic gate: `_speak_now` still returns only when `pw-cat` has
   exited.** The new ElevenLabs path ends with `pw-cat`'s stdin closed and
   `pw-cat.wait()`. That is after the download and after ffmpeg, whichever
   finishes last, and on every path, `Cut` included. `_speech_loop`
   (`local_engine.py:299-324`), `_say` (`:326-346`) and `_voice_until` do
   not change. `_voice_until` still becomes finite in the loop's `finally`
   after `_speak_now` returns, so the echo tail is still measured from the
   end of `pw-cat`. Test 5 holds a fake `pw-cat` open after the download
   has ended, and asserts that the mouth has not returned.
6. **#137 lands after #135, and its spec is written on #135's merged
   code.** Both change the same seam, `synth` then `_play`
   (`feedback.py:148-149`). This spec removes the seam: there is no longer
   a whole clip to hand over. #137 has to decide what "prefetch" means once
   the first sample waits only for `request + buffer` (probably: open N+1's
   request while N plays). It cannot decide that before this seam exists.
   Three more reasons. This change stays inside `feedback.py` and
   `elevenlabs.py`, while #137 changes the #114 gate and when the brain's
   tool hooks run, so it carries more risk. Whether #137 is still worth
   doing is its own open question 6, and it can only be answered with this
   change's "after" `synth` number. And #137's gap between sentences is
   measured with the SYNTH spans as this spec redefines them (item 3 of
   Design). #136 (resident Piper) does not touch the ElevenLabs path. The
   only file it shares is `feedback.py`, in `_speak_piper`, so either order
   works for it.
7. **Measure first after all: no separate measuring day before the build.
   The "before" run is the first step of the plan, and nothing merges
   without it.** The owner has overridden gate A, so measuring before the
   spec would only size a change that has already been decided. The same
   `main` run gives the baseline either way. What stays is the merge rule
   in Verification, part D: if `first-audio` does not move, the PR is not
   merged and the result is recorded on #135.

## Design

All stdlib and existing tools (`urllib`, `subprocess`, `threading`, ffmpeg,
pw-cat). Nothing changes in `pyproject.toml` or `nix/package.nix`.

1. **`elevenlabs.synth` becomes `elevenlabs.speak(text, config, timeout=30.0,
   trace=None) -> None`**. It fetches, decodes, masters and plays, and
   returns when `pw-cat` has exited. `_speak_now` (`feedback.py:146-158`)
   calls it in place of `synth` + `_play`. `Feedback._play`
   (`feedback.py:160-168`) has no other caller, so it is deleted. Checks
   before the network, each an `Unavailable`: key and voice id (as today),
   `ffmpeg` (as today), `pw-cat` (moved here from `_play`).
2. **The pipeline**, in the calling thread, plus one feeder thread:
   - `urlopen` → the `request` span, unchanged (`elevenlabs.py:158-159`).
     HTTP and URL errors map to `Unavailable` as today.
   - `ffmpeg -loglevel quiet -f mp3 -probesize 32 -analyzeduration 0
     -fflags +nobuffer -i pipe:0 -af <master> -f s16le -ar 44100 -ac 1
     -flush_packets 1 pipe:1`, with `Popen` stdin and stdout pipes.
   - **Feeder thread**: `response.read(4096)` → `ffmpeg.stdin.write` until
     EOF, then close stdin. On an exception it records the error and kills
     ffmpeg (decision 3). An empty body is recorded as "ElevenLabs returned
     no audio", as today.
   - **Calling thread**: `ffmpeg.stdout.read1(4096)` in a loop. On the first
     non-empty chunk it closes the `buffer` span, starts `pw-cat` with
     today's arguments (`feedback.py:166-168`), and from then on writes each
     chunk to it. `played` counts the bytes written.
   - **End**: close `pw-cat`'s stdin and `wait()`; `ffmpeg.wait(timeout)`;
     join the feeder. If there was an error, or ffmpeg exited non-zero, or
     no PCM came out: `played == 0` raises `Unavailable`, and `played > 0`
     raises `Cut`.
   - **Reaping, in `finally`, on every path**: ffmpeg is killed if it is
     still running, then waited. `pw-cat` gets stdin closed, then `wait()`.
     It is only killed if the exception is not an `Exception` (the thread is
     being torn down). The response is closed. The feeder is joined with a
     1 s timeout. It exits on its next failed write to the killed ffmpeg.
     `ponytail:` a feeder blocked in `read()` on a stalled socket can
     outlive the call by up to `timeout`. It is a daemon thread that holds
     no process.
3. **SYNTH spans mean "the wait before the first sample", before and
   after.** Today's names are `request`, `download` and `decode`, and all
   three come before the first sample, because the whole clip is fetched
   first. The new names are `request` (unchanged) and `buffer` (from the
   headers to the first PCM chunk out of ffmpeg, closed just before `pw-cat`
   starts). Both come before the first sample too. The rest of the download
   and decode overlaps playback, so it is not SYNTH. It is part of `speak`,
   like playback. So:
   - `Trace.first_audio` (`trace.py:119-134`) keeps its formula. Its
     docstring changes from "the cloud voice has the whole clip before a
     sample plays" to "SYNTH spans are, by contract, only the wait before
     the first sample". The overstatement the intent feared cannot happen,
     because no span that overlaps playback is SYNTH.
   - `synth=` means the same on `main` and on the branch: the time from
     handing her the sentence to her first sample being ready. So does
     `first-audio=`. Both can be compared across the change. The breakdown
     inside `synth(...)` changes names, which is expected.
   - `play = speak - synth` in `tools/timing_report.py` (`:99`) still means
     "from the first sample to the mouth returning".
   - A `span` that is open when a failure happens closes in `finally`, as
     #79's test 7 requires today.
4. **`tools/timing_report.py`** already prints `first-audio`. `parse_line`
   reads every `key=value` (`trace.py:182-197`), and the report prints every
   key it finds. The intent's "summarises only synth, speak and play" is
   about the docstring, not the code. Two changes: the docstring names
   `first-audio` and the SYNTH meaning above, and a `--until YYYY-MM-DD`
   (exclusive) is added next to `--since`. With it, the "before" and "after"
   windows can be cut from one `session.log`. The breakdown keys
   `synth.download` and `synth.decode` (before) and `synth.buffer` (after)
   print as separate rows, so the table shows which build a window came
   from.
5. **`config.py:491-494`**: the new default, and a comment that says what
   `<G>` was measured from, and that `loudnorm` there restores today's sound
   and today's wait. The module docstring and the `ponytail:` note in
   `synth` (`elevenlabs.py:125-129`) are rewritten to match. The docs
   comment at `elevenlabs.py:32-34` gains the tier reason from decision 4.

Nothing new records content. `buffer` is a fixed name, and the text is still
only in the request body.

## Alternatives rejected

- **Buffering a fixed amount (for example 0.5 s of PCM) before starting
  `pw-cat`, to ride out a slow download.** That is a guess made without
  data. `pw-cat` has its own buffer, and an underrun in the middle of a
  sentence is only a short gap. If the live run hears gaps, the plan can add
  it with the number measured.
- **An ElevenLabs WebSocket (`stream-input`).** It is a new protocol and
  would need the `websockets` client on a new path. It is built for text
  that arrives while it is spoken, and our sentences arrive whole. The
  endpoint stays `/stream`, as in #79's decision 6.
- **`pcm_24000` or `pcm_44100`**: see decision 4.
- **Two-pass loudnorm per clip.** The first pass needs the whole clip, and
  that is the wait this change removes.
- **Keeping `synth() -> (bytes, rate)` beside the new function, for #137.**
  That is a function with no caller. #137 designs its own seam on this code
  (decision 6).
- **A config switch `elevenlabs_stream`.** Setting `master` back to loudnorm
  already restores today's sound. A second path would double the tests for
  a rollback that the revert commit gives anyway.

## Risks

- **She sounds different.** Decision 2 is the gate. If the owner rejects
  it, the PR does not merge. If a regression is found after merge,
  `master = "loudnorm=..."` in `config.toml` puts back today's sound with no
  rebuild.
- **`<G>` is tuned for one voice.** Another voice id could be a few dB off.
  The limiter still holds the peaks at -1.5 dBFS. `master` is the per-user
  override, and the comment at `config.py:491` says so. Only this machine
  and razer use ElevenLabs.
- **Underruns on a slow network** give a short gap mid-sentence where today
  there is a longer silence before it. Part D asks the owner to note any
  gap he hears.
- **A lost sentence tail** on a mid-stream failure (decision 3). It is
  logged every time.
- **Threads and processes.** There is one more thread per cloud sentence.
  ffmpeg and `pw-cat` are reaped on every path (tests 6 and 7). The mouth
  already runs in `asyncio.to_thread` (`local_engine.py:312`), so this does
  not add load to the event loop. #120's pool load is not affected, because
  the feeder is a plain `threading.Thread`, not a pool worker.
- **The realtime engine and the CLI** use `Feedback._speak_now` too, and
  they get the same behaviour. Neither depends on `_play`.
- **Hosts:** p620 (this machine) and razer, both `[elevenlabs] enabled`.
  Hosts without ElevenLabs never reach the new code.

## Verification

### A. Tests with fakes only

No network, no audio, no real `pw-cat`. Everything runs with
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`. The fakes are an
`urlopen` whose response yields scripted chunks and can raise after N of
them, and a fake `Popen` for ffmpeg and for `pw-cat`, each with a stdout,
`wait` and `kill` driven by the test. The shape follows `SynthTests` and
`FallbackTests` (`tests/test_elevenlabs.py:220`, `:314`). They replace the
current `synth` tests (`:224-309`), whose behaviour moves to `speak`.

Tests are written first, and they must fail on `main` (`speak` does not
exist there):

1. `test_pw_cat_starts_before_the_body_ends`: the fake response yields one
   chunk and then blocks on an event. `pw-cat` receives PCM before the event
   is set.
2. `test_mp3_is_asked_for_and_ffmpeg_is_told_not_to_probe`: the URL has
   `output_format=mp3_44100_128`, and the ffmpeg argv has `-f mp3`,
   `-probesize 32`, `-analyzeduration 0` and the configured `-af`.
3. `test_a_failure_before_the_first_sample_is_piper_saying_it_all`: the
   response raises before the first chunk. Piper's fake gets the whole
   text, `pw-cat` never started, and the log has `elevenlabs failed`.
4. `test_a_failure_after_the_first_sample_is_a_log_line_not_a_repeat`:
   the response raises after two chunks and ffmpeg has output. Piper is not
   called, and the log has `cut off mid-sentence`.
5. `test_the_mouth_returns_only_when_pw_cat_does` (the #114 gate): the
   download and ffmpeg are finished, and the fake `pw-cat.wait` blocks on an
   event. `_speak_now`, run on a thread, has not returned. Once the event is
   set, it returns.
6. `test_every_child_is_reaped_on_every_path`: parametrised over success,
   failure before the first sample, failure after it, and ffmpeg exiting
   non-zero. Each fake process has `wait()` called, and ffmpeg is killed on
   the failure paths.
7. `test_a_partial_body_with_no_output_yet_is_not_played`: the body fails
   after one chunk and ffmpeg has not output anything. ffmpeg is killed,
   not given EOF. `pw-cat` never starts, and Piper speaks.
8. `test_synth_spans_stop_at_the_first_sample` (`test_trace.py`,
   stepped clock): request 0.2 s, then 0.1 s to the first ffmpeg chunk, then
   2.0 s of download and playback. SYNTH is `request` and `buffer` only,
   `synth=0.30s`, and `first_audio` is the SPEAK start plus 0.30.
9. `test_no_span_is_left_open_on_a_cut` (extends #79's test 7).
10. `RedactionTests` (`test_trace.py:76`): `buffer` is added to the
    vocabulary, and the text never appears in `line()`.
11. `test_timing_report_until_is_exclusive` (fixture log in a temp
    `XDG_STATE_HOME`).
12. `test_the_default_master_streams`: uses a **real local ffmpeg** and is
    skipped if it is missing. No network and no audio. It encodes 4 s of
    `sine` to mp3 in a temp dir and feeds the first second into the argv
    that `speak` builds, with the default `master`. It holds stdin open and
    expects PCM on stdout within 1.0 s. This repeats the table above as a
    test.
13. `test_loudnorm_in_master_still_works`: the same argv with the loudnorm
    string gives the same length of PCM once stdin is closed. This proves
    the rollback value from decision 1.

### B. Mutation checks

Each mutation is applied alone, must turn at least one test red, and is then
reverted:

- close `buffer` when the download ends instead of at the first chunk → 1, 8;
- return from `speak` before `pw-cat.wait()` → 5;
- after the first sample, fall back to Piper anyway → 4;
- before the first sample, raise `Cut` instead of `Unavailable` → 3;
- on a feeder error, close ffmpeg's stdin instead of killing it → 7;
- drop `-probesize 32 -analyzeduration 0` → 12;
- put `loudnorm` back as the default `master` → 12;
- remove the `finally` reaping → 6, 9.

### C. Calibration and listening (the owner, before the PR merges)

This costs 5 API calls. The owner runs it by hand. Nothing in this spec or
the plan makes the calls.

1. Download five fixed sentences, the ones used in part D, with the same
   URL, model and voice settings as `speak`, to `clip1.mp3` ... `clip5.mp3`
   (a `curl` line is given in the plan).
2. For each clip, measure the raw input loudness with `ffmpeg -i clipN.mp3
   -af ebur128 -f null -`. Then `<G> = -16 - mean(I)`, rounded to 0.5 dB.
   Commit `<G>` into the default in `config.py`.
3. Render each clip to WAV through both chains, today's loudnorm and the new
   `volume=<G>dB,alimiter=...`. Check that each new WAV measures -16 ± 1.5
   LUFS (decision 2a). The owner listens to both, one after the other
   (decision 2b), and records his verdict on #135.

### D. Live before/after (the owner, with `trace_timings = true`)

1. Set `trace_timings = true` in `config.toml`. Run the `main` build. Speak
   the same fixed set of prompts, whose replies are short (one or two
   sentences), until `timing_report` shows at least 30 `speak` samples.
   Note the date.
2. Install the branch build. Repeat the same prompts on a later day.
3. Run `python3 tools/timing_report.py --since <main day> --until <branch
   day>` and `--since <branch day>`. Report the p50 and p95 of
   `first-audio`, `synth`, `speak` and `play` for both windows on #135.
4. **Merge rule:** the branch's `synth` p50 is at least 0.1 s below
   `main`'s, **and** part C is accepted. If `synth` does not move by that
   much, that is the result: it is written on #135 and the PR is closed
   unmerged. `first-audio` should fall by about the same amount. If
   `synth` falls and `first-audio` does not, the measurement is wrong, and
   that is investigated before the PR merges. The owner also notes any gap
   heard mid-sentence (the underrun risk).

### E. Runners

With `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` exported:

- `nix develop -c pytest tests -q`: all pass, 1084 minus the replaced
  `synth` tests plus the new tests. The plan states the exact count.
- `nix develop -c python3 -m unittest discover -s tests`: same count, all
  pass.
- `nix flake check --no-write-lock-file`: passes. The package build runs the
  tests. Test 12 is skipped there if the build sandbox has no ffmpeg, and
  the plan checks whether it has.
