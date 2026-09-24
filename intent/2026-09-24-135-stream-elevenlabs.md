---
status: draft
issue: 135
author: olafkfreund
---

# Intent: start her ElevenLabs voice before the whole clip has arrived

Refs #135. Follow-up from #79 (PR #133), decision 1.

## Problem

When the cloud voice speaks a sentence, nothing plays until three things
have finished, one after the other:

1. **The request.** `synth` POSTs to the `/stream` endpoint with
   `output_format=mp3_44100_128` (`src/omarchy_voice/elevenlabs.py:35`,
   `:152-156`). The endpoint streams, but the code does not use that.
2. **The whole body.** `mp3 = response.read()` waits for the last byte
   (`elevenlabs.py:160-161`).
3. **The whole decode and mastering.** One `ffmpeg` run takes the full mp3
   on stdin and returns the full PCM (`elevenlabs.py:174-185`). Its filter
   is `elevenlabs_master`, `"loudnorm=I=-16:TP=-1.5:LRA=11"`
   (`config.py:494`). Single-pass loudnorm in dynamic mode holds a ~3 s
   look-ahead, so even a streamed input would not give out a first sample
   for a one-sentence clip until the input ends. The mastering is
   deliberate: raw API output does not match the voice auditioned on the
   website (`config.py:491-493`, `elevenlabs.py:170-173`).

Only then does `Feedback._play` hand the PCM to `pw-cat --playback --raw`
in one `subprocess.run` (`feedback.py:160-168`, called at `:148-149`).

The `ponytail:` note in `synth` names this exact cost and the gate for
fixing it (`elevenlabs.py:125-129`).

**Gate A is overridden.** #79's spec and plan (decision 1,
`spec/2026-09-24-79-speaking-side-unmeasured.md:30-38`,
`plan/2026-09-24-79-speaking-side-unmeasured.md:23-35`) said to open this
only if `download + decode` is at least 0.3 s at the p50 of 30 or more
traced ElevenLabs sentences. **On 2026-09-24 the owner chose to build it
now, without that data.** There is no data: `trace_timings` is `False` by
default (`config.py:419`) and not set in this machine's `config.toml`, and
`session.log` has **0 `TIMING` lines** (110 `say` lines). So the size of
the gain is not known. It could be most of a second, or nothing worth the
change.

## Proposed outcome

- Her first sample plays before the last byte of the clip has arrived.
  How much sooner is a measured number, not a claim.
- **The change ships with a before/after measurement.** With
  `trace_timings = true`, the same set of sentences is traced on `main`
  and on the branch, and the p50 of `first-audio` and of the SYNTH phase
  is reported for both. The `TIMING` line already carries `first-audio=`
  (`trace.py:170-171`), but `tools/timing_report.py` today summarises
  only `synth`, `speak` and `play = speak - synth` (`:7`, `:99`), so it
  needs to read `first-audio` too. If the branch does not move `first-audio`, that is recorded
  as the result, and the change is not merged on faith.
- `Trace.first_audio` (`trace.py:119-134`) still means "her first
  sample". Today it adds every SYNTH span inside the first SPEAK span,
  because the whole clip is fetched first. Once playback overlaps the
  download, that sum overstates the wait, so the measurement must change
  with the code or the "after" number is wrong.
- Degrade, never mute still holds. Any failure falls back to Piper and
  logs `tts     elevenlabs failed` (`feedback.py:150-158`). A sentence is
  never half spoken and then silent without a line saying why.
- She still sounds like the voice the user chose, or the owner has
  accepted in writing how she differs.

## Affected users and systems

- `src/omarchy_voice/elevenlabs.py` (`synth`, `FORMAT`, `RATE`).
- `src/omarchy_voice/feedback.py` (`_speak_now`, `_play`). `synth`
  returns a whole `(bytes, rate)` today, and `_play` expects one.
- `src/omarchy_voice/config.py` (`elevenlabs_master`, and anything a new
  mastering needs).
- `src/omarchy_voice/trace.py` (`first_audio`) and
  `tools/timing_report.py`.
- `local_engine._say` (`local_engine.py:326-345`). With `barge_in` off,
  which is this machine's setting, it waits for each sentence to finish.
  That wait is #114's microphone gate.
- The user of this machine: `[elevenlabs] enabled = true` with a voice id
  set, so every spoken sentence goes through this path.

## Constraints

- **No call to the ElevenLabs API and no audio played while designing.**
  The key is the user's and each call costs quota. Evidence comes from the
  docs and the code.
- **Output format by account tier.** ElevenLabs docs: PCM at 44.1 kHz
  (`pcm_44100`) needs the Pro tier or above; lower PCM rates such as
  `pcm_24000` and mp3 are available below it. `elevenlabs.py:32-34`
  already chose mp3 for this reason. The account's tier is not known here.
- `optimize_streaming_latency` is **deprecated** per ElevenLabs' help
  centre ("no longer recommended for reducing latency"). It is not a lever.
- #114's guarantees hold (closed, merged): her voice shuts the capture, and
  the capture waits out her echo. The `_mic_shut` wait
  (`local_engine.py:336-342`) must still bracket the real playback.
- No new Python dependency. `pyproject.toml` has only `websockets` and
  `mcp`. `urllib`, `subprocess`, `ffmpeg` and `pw-cat` are already used.
- Every child process (`ffmpeg`, `pw-cat`) is reaped on every path,
  including a mid-stream failure and a cancelled turn.

## Open questions

1. **What replaces single-pass loudnorm?** #79's gate required an answer.
   Candidates, none chosen: a fixed gain and limiter measured once per
   voice (a two-pass `loudnorm` offline, then `volume` + `alimiter` live,
   no look-ahead beyond the limiter's few ms); `dynaudnorm` with a short
   frame; or no mastering, with a config switch back to the current path.
2. **May the voice sound different?** Any of the above changes how she
   sounds (`config.py:491-493` says the mastering is what makes her the
   chosen voice). Who judges, and by what: an A/B listen by the owner, or
   an integrated-loudness number from `ffmpeg -af ebur128` on saved clips?
3. **Fallback on a mid-stream failure.** Before the first sample plays,
   Piper can take the whole sentence, as today. After part of it has
   played, is the rest spoken by Piper (a voice change mid-sentence), the
   whole sentence repeated by Piper, or only a log line?
4. **Format.** `pcm_24000` (or `pcm_44100` if the account is Pro) skips
   the mp3 decode but still needs mastering. Streaming mp3 through ffmpeg
   keeps today's format. Which, given the unknown tier?
5. **#114's mic gate.** With `barge_in` off, `_say` waits until playback
   ends. Streaming must not end that wait early (when the download ends
   rather than when `pw-cat` does), or her tail reaches the open mic.
6. **#137 (prefetch, open, gate C).** Streaming shortens the first
   sentence's wait; prefetch hides sentence N+1's behind N. Do both touch
   `synth`'s return type, and which lands first so the other is designed
   on top of it? Is #137 still worth opening if streaming makes `synth`
   small against `speak`?
7. **Measure first after all?** A day with `trace_timings = true` on
   `main` gives the "before" numbers anyway. Should that run before the
   spec is written, so the design can be sized to the real wait?

Sources:
[Stream speech](https://elevenlabs.io/docs/api-reference/text-to-speech/stream),
[Can I reduce API latency?](https://elevenlabs.io/docs/help-center/technical/can-i-reduce-api-latency)
(both via Context7, 24 Sep 2026).
