---
status: approved
issue: 137
author: olafkfreund
---

# Intent: synthesise the next sentence while this one plays

Closes #137. Follow-up from #79 (PR #133), decision 3.

## Why now: gate C is overridden

#79's approved plan (`plan/2026-09-24-79-speaking-side-unmeasured.md`,
decision 3) opened this issue behind **gate C**: build it only when `synth`
is at least 25 % of `speak` on turns with two or more sentences. Its
prerequisite, #114 merged, is met. The measurement is not. **No traced
multi-sentence turn has been collected yet.**

**On 2026-09-24 the owner chose to build this now, overriding gate C.** The
spec must still say how the gain is shown with #79's trace. The trace is how
we check the gain after the change, not a gate before it.

## Problem

Her sentences are made and played one after another. Nothing is made while
something else plays. On a reply of several sentences, each gap between two
sentences contains a whole ElevenLabs round trip and an ffmpeg decode. In
that gap the room is silent.

### The speech path today (at `be27af4`)

- **Sentences arrive one at a time.** `_answer`
  (`src/omarchy_voice/local_engine.py:585-594`) pulls
  `brain.ask_stream` (`claude_backend.py:870`), an async generator, and
  awaits `_say(sentence)` for each sentence before it pulls the next one.
- **With `barge_in` off, `_say` is the #114 microphone gate**
  (`local_engine.py:326-346`). It sets `_voice_until = inf`, appends the
  line to `_said`, waits up to 1 s for `_mic_shut`, queues the text on
  `_speech`, then `join()`s. So sentence N+1 is **not even pulled from the
  brain** until N has finished playing. The generator is suspended during
  that time. The model's tool hooks (`executor.on_action`,
  `claude_backend.py:390`, `:445`) run while the generator is being pulled.
  So the gate also means that no new action starts while she is talking.
- **With `barge_in` on, `_say` does not `join()`.** Sentences pile up on
  `_speech`. But `_speech_loop` (`:299-324`) still takes one text at a time
  and runs `Feedback._speak_now` to completion, synthesis and playback,
  before it takes the next one. So there is no overlap in this mode either.
- **`_speak_now`** (`feedback.py:141-158`) is one blocking call per
  sentence. The order is: `tts_command`, if set; then ElevenLabs
  `synth` → `_play`; then Piper; then espeak-ng.
- **Where synthesis and playback can be separated today.** Only on the
  ElevenLabs branch. `elevenlabs.synth` (`elevenlabs.py:116`) returns the
  whole clip as `(pcm, rate)` after the request, the full download and the
  ffmpeg decode. `_play` (`feedback.py:160-168`) then pipes it into
  `pw-cat`. The two calls sit one after the other at `feedback.py:148-149`.
  Piper is a single `piper | pw-cat` pipeline (`:170-203`). `tts_command`
  and espeak-ng are single subprocesses that both make and play the sound.
  Those three have no clip to prefetch as they stand.
- **Timing.** `_speech_loop` opens one SPEAK span per sentence around
  `_speak_now` (`:302-321`). ElevenLabs' SYNTH spans are inside it
  (`trace.py:49-55`). Gate C's ratio would come from these spans.
- **The clocks the gate relies on.** `_speech_loop` sets `_voice_until` to
  the time `pw-cat` returned, and only when the queue is empty (`:322-323`).
  `_record` waits while `_voice_until` is `inf`, then waits out
  `ECHO_TAIL_SECONDS` (0.35 s, `:48`) from that time (`:371-387`). Its
  level callback drops the capture if `_voice_until` becomes `inf`
  (`:401-402`). `_drop_queued_speech` (`:348-359`) empties the queue on a
  toggle or mute.

### What this issue is, and what it is not

The goal is to **make sentence N+1's audio (ElevenLabs network and ffmpeg)
while sentence N plays**. N+1 then starts playing as soon as N ends.

It is **not** to stop waiting for playback. With `barge_in` off, the
session still waits for her last sentence to finish, and for its echo to
die, before the microphone opens. The wait for playback is #114's safety
gate. Only the synthesis moves earlier. The playback still waits its turn.

## Proposed outcome

- On a reply of two or more sentences through ElevenLabs, sentence N+1's
  clip has been fetched and decoded while N played. The gap between the end
  of N's `pw-cat` and the start of N+1's is only the time to start `pw-cat`,
  not a round trip and a decode.
- Nothing else changes for what the user hears or when the microphone is
  open. Sentences are played in order, one at a time, and never overlap. The
  capture opens after the same last sentence, with the same echo tail.
- A single sentence, and the voices that cannot be split (Piper,
  `tts_command`, espeak-ng), behave as they do today.
- A clip that was prefetched but must not be played (because of a barge-in,
  a cancel, a mute, a toggle, a stop or a turn failure) is thrown away, not
  played later.
- #79's trace can show the change. For each sentence it shows either the
  wait for its clip or no wait.

## Affected users and systems

- `src/omarchy_voice/local_engine.py`: `_say`, `_speech_loop`,
  `_drop_queued_speech`, and possibly `_answer`'s loop over `ask_stream`.
- `src/omarchy_voice/feedback.py`: `_speak_now`, and the seam between
  `elevenlabs.synth` and `_play`.
- `src/omarchy_voice/elevenlabs.py`: only if the spec moves where `synth`
  is called. No API change is intended.
- `src/omarchy_voice/trace.py`: only if a new span or field is needed for
  the SYNTH/SPEAK overlap.
- `tests/test_local_engine.py`, and `tests/test_feedback.py` if
  `_speak_now` is split.
- Everyone using the local engine with ElevenLabs configured, with
  `barge_in` either on or off.
- Not the realtime engine. It speaks through `Feedback.speak`, which runs
  `_speak_now` on a thread for each line.

## Constraints

This change is on a safety path. Each item below must still hold, and each
one needs a test.

- **#114's guarantees hold, with `barge_in` off.** Her voice shuts the
  capture: `_voice_until` is `inf` from the moment a sentence is handed to
  `_say` until the last queued sentence has finished playing. `_say` still
  waits for `_mic_shut` before any sentence plays. The capture still waits
  out `ECHO_TAIL_SECONDS` from the end of her last `pw-cat`. Prefetching is
  not playing. It must not set `_voice_until` or end it early. Only the
  return of `pw-cat` for the last queued sentence may make `_voice_until`
  finite.
- **The wait for playback stays.** With `barge_in` off, `_answer` must not
  go on to capture, to a held prompt, or to the end of the turn until her
  last sentence has played. The number of sentences the pipeline holds is
  bounded. It has no queue of unplayed clips beyond N+1.
- **No action while she speaks, unless the spec says otherwise and the
  owner approves it.** To see N+1 during N, `ask_stream` must be pulled
  while she is talking. That allows the brain's tool hooks to run during
  her sentence. Today, with `barge_in` off, they cannot. Whether this is
  allowed is decided in the spec (open question 2). It must not happen by
  accident.
- **#86's consent timing holds.** `_voice_until` keeps its meaning: `inf`
  while anything is queued or playing, then the time the last `pw-cat`
  returned. Check A's guard is measured from it. `_said` still contains
  every line she was given to say before a capture can be judged. Adding a
  line when it is prefetched rather than when it is played fails closed and
  is acceptable. Adding it only after it plays is not. `SpokenConsentTests`
  pass unchanged.
- **#120's recovery holds.** A turn that raises is still logged and
  apologised for. After `TURN_FAILURES_TO_MUTE` (3) failures in a row it
  still mutes, with the pause `TURN_FAILURE_PAUSE` (`local_engine.py:163-164`,
  `:716-724`). A failed or cancelled prefetch never counts as a turn
  failure. It never leaves a thread running into the next turn, and it does
  not make the thread-pool load worse in the way #120 measured.
- **Barge-in on keeps its behaviour.** Sentences are still queued without
  a `join()`. The capture may stay open while she talks. #86 still refuses a
  spoken confirm. A barge-in, and `_drop_queued_speech`, stop her and throw
  away every unplayed sentence, prefetched clips included.
- **Degrade, never mute.** If a prefetch fails (network, quota, ffmpeg), the
  sentence still falls through to Piper or espeak-ng in order, as
  `_speak_now` does today. It is never silently skipped.
- **Tests use fakes only.** No ElevenLabs calls and no audio are played.
  Tests run with `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`. Timing
  is checked with a stepped clock, as in `SpokenConsentTests` and #114's
  tests. It must not depend on how loaded the machine is (#120).

## Open questions

1. **Where does the prefetch live: in the mouth or in the feedback layer?**
   - *The mouth* (`_speech_loop` in `local_engine.py`): it looks at the next
     item on `_speech` and asks for its clip while the current one plays.
     `_speak_now` is split into "make" and "play". This keeps the one
     queue and the one clock.
   - *The feedback layer* (`Feedback`): a clip cache keyed by the text. The
     engine does not change. But `Feedback` knows nothing of turns, of
     cancelling, or of `_voice_until`.
2. **With `barge_in` off, how does N+1 reach the mouth before N ends?**
   `_say` `join()`s, so N+1 has not been pulled from `ask_stream` yet. One
   way is to pull the stream one sentence ahead of what is spoken. Then the
   tool hooks can run while she talks (see Constraints). Another way is to
   prefetch only sentences that are already queued. That helps with
   barge-in on, but not with barge-in off, which is the default. The
   approver must decide which one, or whether to hold the next sentence's
   text without letting any action run.
3. **Cancelling a prefetched clip.** What happens to a clip, or a request
   in flight, on barge-in, cancel, mute, toggle, stop, or a turn that
   raises? Is the HTTP request abandoned or left to finish and be dropped?
   `synth` is a blocking call on a thread, so it cannot be cancelled cleanly
   today.
4. **Interaction with #135 (stream ElevenLabs into `pw-cat`) and #136
   (keep Piper resident).** #135 changes where `synth` ends and `_play`
   begins, which is the same seam. #136 may give Piper a split between
   making and playing that it lacks today. Which lands first, and does this
   design stay the same after either one? Should Piper be prefetched here
   at all, or left to #136?
5. **Cost.** Each clip that is prefetched but never played (after a
   barge-in, cancel, mute, or a turn that fails) still uses ElevenLabs
   quota. Is one extra sentence at most per interruption acceptable? Should
   prefetch be off by default, or behind a config flag?
6. **The evidence after the override.** Gate C was not met before the build.
   What should the trace show afterwards for the change to stay? A
   suggestion: the median gap between sentences on a multi-sentence turn
   with ElevenLabs drops by about the median SYNTH time. If it does not,
   the change is reverted.
