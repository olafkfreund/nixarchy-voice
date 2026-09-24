---
status: approved
issue: 114
intent: intent/2026-09-24-114-every-voice-gates-the-mic.md
---

# Spec: her voice shuts the capture, and the capture waits out her echo

Closes #114. Line numbers are `origin/main` `fc33ae2`.

## The intent's open questions, answered

The intent was approved with its four questions left open. Each one is
decided below, with the demonstration that supports it. **Any of these
decisions can be rejected at this gate.** If one is, the design below
changes. The rest of the spec does not need to be rewritten.

The demonstration uses the real `LocalSession`, `_listen_loop`, `_turn`,
`_wake_turn`, `_consent`, `_release` and `_inject`. The mechanism is
prototyped as a subclass that overrides `_say`, `_record` and `_turn`, and it
is run against `origin/main` unchanged. The mouth, microphone and whisper are
fakes that share one room: the recorder reads 50 ms frames, and a frame read
while the mouth is playing contains her sentence. "Whisper" returns what the
capture contained. A stepped clock moves 50 ms per frame and 1 s per
sentence, so what the demonstration shows does not depend on machine load.
The brain is the tests' `FakeBrain`.
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`. No audio.

```
=== BEFORE (origin/main)
  1 typed turn, listening
    spoke (text, mic_open): [("It is three o'clock.", True), ("It is three o'clock.", False)]
    brain.asked: ['what time is it', "It is three o'clock."]
  2 keybind confirm (brain hold 'reboot'), listening
    spoke (text, mic_open): [("It is three o'clock.", True), ("It is three o'clock.", False)]
    brain.asked: ['reboot', "It is three o'clock."]
  3 typed turn while muted, wake word 'oma', reply contains the wake word
    spoke (text, mic_open): [('Oma, close the browser.', True), ('Oma, close the browser.', False)]
    brain.asked: ['what time is it', 'close the browser.']   active=True
    log: gate listening; wake heard 'Oma, close the browser.' — acting on 'close the browser.'
  4 typed cancel (short line), listening
    spoke (text, mic_open): [('Cancelled. reboot was not run.', True), ("It is three o'clock.", False)]
    brain.asked: ['Cancelled. reboot was not run.']
  6 voice turn: noise -> NOT_CAUGHT (short line)
    next capture opened after she finished (s): [('I did not catch that.', 0.03)]
=== AFTER (this spec)
  1 typed turn, listening
    spoke: [("It is three o'clock.", False)]            brain.asked: ['what time is it']
    first word 0.10s after the typed line   next capture opened 0.38s after she finished
  2 keybind confirm (brain hold 'reboot'), listening
    spoke: [("It is three o'clock.", False)]            brain.asked: ['reboot']
    first word 0.10s after the key          next capture opened 0.38s after she finished
  3 typed turn while muted, wake word 'oma', reply contains the wake word
    spoke: [('Oma, close the browser.', False)]         brain.asked: ['what time is it']   active=False
    next capture opened 0.38s after she finished
  4 typed cancel (short line), listening
    spoke: [('Cancelled. reboot was not run.', False)]  brain.asked: []
    next capture opened 0.38s after she finished
  6 voice turn: noise -> NOT_CAUGHT (short line)
    next capture opened after she finished (s): [('I did not catch that.', 0.38)]
  5 typed turn, then the user speaks after the tail
    spoke: [("It is three o'clock.", False), ("It is three o'clock.", False)]
    brain.asked: ['hello', 'what time is it']
    user said 'what time is it' after the tail -> reached brain: True
```

Before, rows 1, 2 and 4 show her reply reaching the brain as the user, and
row 3 shows it switching listening on and acting. After, every sentence
plays with `mic_open` False and none of them reaches the brain. Row 3 stays
muted. Each capture opens 0.38 s after her last sentence, which is the 0.35 s
tail plus one frame, including after the short lines. Row 5 shows that a real
user speaking after the tail is still heard. The whole of
`tests/test_local_engine.py` (83 tests, including `SpokenConsentTests` and
`MicrophoneGateTests`) passes with `LocalSession` replaced by the prototype,
both with and without `_answer`'s own tail sleep. The scripts are in the
session scratchpad (`spec114/demo.py`, `spec114/run_suite.py`) and are not
committed.

### 1. Close the capture, or wait for it to end? **Close it.**

When she is about to speak and a capture is open, the capture is abandoned
on its next frame and its audio is discarded. The latency cost is one frame
(50 ms) plus stopping `pw-record` (`listen_local.py:280-285`). The
demonstration shows the first word 0.10 s after the typed line instead of
0.05 s. Waiting would instead cost whatever remains of the capture: up to
`MAX_UTTERANCE_SECONDS` (15 s, `local_engine.py:51`) when listening, and up
to `wake_max_seconds` (8 s, `config.py:430`) for the wake capture. In a
silent room the recorder only returns at that cap (`listen_local.py:278`).

What closing loses: whatever the user was saying in that capture at the
moment they typed a line or pressed the key. They are at the keyboard at
that moment, so this is rare, and they can say it again after she finishes.
Waiting would also not have been enough on its own. A capture that is still
open when she starts would record her anyway.

Rejected alternative: close only if the capture has not heard anything yet
(`_onset is None`), and wait otherwise. Typing and key clicks set the onset,
so in practice this would usually mean waiting. It is also two rules instead
of one.

### 2. Should the short lines get the echo tail? **Yes, and with no extra code.**

The tail moves from the end of `_answer` (`local_engine.py:501-506`) to the
start of `_record`. A capture opens no earlier than `_voice_until +
ECHO_TAIL_SECONDS`, whichever path spoke last. The short lines get the tail
because they go through `_say` like everything else: NOT_CAUGHT (`:325`), the
transcriber line (`:317`), #86's refusals (`:709`), the cancel line (`:716`)
and "Done." (`:749`). Row 6 goes from 0.03 s to 0.38 s. The tail is measured
from when her voice ended, not from when the turn ended, so a turn that said
nothing waits for nothing. A turn that already slept its tail pays nothing
more.

### 3. The muted wake capture: **in scope, and it costs nothing extra.**

`_wake_turn` opens the mic through the same `_record` (`:359`), so the same
rule shuts it and makes it wait out the tail. Row 3: the reply that contains
"Oma" no longer switches listening on or acts. Leaving the wake capture out
would take extra code.

### 4. Should keybind confirm wait for its release turn? **No.**

`_local_confirm` stays `_release(wait=False)` (`:724-726`, `:765`), and
`_inject` still returns "sent" at once (`:674-675`). The control socket's
10 s limit (`:638-641`) is not at risk. The spawned turn's own `_say` is what
shuts the capture now, so the caller does not need to wait. Row 2 shows it.
Making the key wait would have fixed only this one entry point. It would
also hold the socket through a model turn, which can take longer than 10 s.

## Design

One rule, applied at the two places a sentence and a capture meet. The state
it reads is #86's clock `_voice_until`, which is already `inf` from the
moment any path queues a line until her last `pw-cat` returns (`:252`,
`:240-241`). No new clock is added.

1. **Her voice shuts the capture** (`_record`, `:271-299`). The level callback
   `watch` (`:283-288`) already abandons the capture when the toggle flips.
   With `barge_in` off, it also abandons the capture when `_voice_until` is
   `inf`. The callback runs for each frame *before* that frame is kept
   (`listen_local.py:266-271`), and `_say` sets `inf` *before* it queues the
   sentence. So no frame that could contain her voice is ever returned. That
   is the whole guarantee, and it does not depend on timing.

2. **A shut capture is not silence.** A capture abandoned for her voice
   returns `None` rather than `b""`, and `_turn` returns on `None` without
   calling `_idle_stop` (`:307-309`). Without this, a typed line after a
   long silence would switch listening off. The prototype's first run showed
   this. `_wake_turn` already returns on a falsy `pcm` (`:363`). The
   toggle/stop abandon keeps returning `b""`.

3. **`_say` waits for the capture to close before it queues** (`:244-256`).
   An `asyncio.Event` "mic shut" is cleared where `_record` sets `mic_open`
   (`:290`) and set in its `finally` (`:297-299`). With `barge_in` off, `_say`
   awaits it, bounded at 1 s, before `self._speech.put`. The bound is safe
   because point 1 does not depend on the wait. The wait only makes
   `mic_open` False while she plays, which is what the intent's outcome and
   its tests assert. The bound means a stalled `pw-record` cannot wedge a
   typed turn. On the loop's own turns the mic is already shut, so the wait
   returns at once.

4. **The capture waits out her voice and the tail.** At the top of `_record`,
   with `barge_in` off, the recorder waits while `_voice_until` is `inf`
   (`await self._speech.join()`), and then until `_voice_until +
   ECHO_TAIL_SECONDS`. There is a one-tick window in which `_say` has set
   `inf` but has not yet queued the sentence. The wait loop re-checks after
   `join()` for that reason. The plan must not turn it into a busy spin.
   `_drop_queued_speech` (`:258-268`) already makes `_voice_until` finite on
   mute, so a mute releases the wait.

5. **`_answer`'s tail sleep is deleted** (`:501-506`), because point 4
   replaces it. Its comment moves with it.

Unchanged: `_inject` / `_typed` (`:663-680`), `_consent` and check A
(`:682-717`), `_release` (`:728-767`), `_speech_loop` (`:222-242`), the
announcements' between-captures wait (`:572-578`), and the whole `barge_in`
path. With `barge_in` on, points 1, 3 and 4 do nothing, so her voice and the
room remain one stream there, and #86 already refuses a spoken confirm in
that case.

## Alternatives rejected

- **Take `_turn_lock` in the listen loop before recording.** A capture would
  hold the lock for up to 15 s in a silent room. Every typed line would then
  wait for a whole capture, which is question 1's "wait" with its latency.
  It also does not cover the wake capture.
- **Route the local engine through `Feedback.speak`'s `mic_open` guard**
  (`feedback.py:128-135`). That guard drops the sentence. A typed reply
  would be silent.
- **`_local_confirm` → `_release(wait=True)`, and `_inject` awaiting
  `_typed`.** This holds the control socket through a model turn, which
  breaks the 10 s `_control` limit. It also fixes only these two callers.
- **A second recorder, or a separate "hush" flag next to `_voice_until`.**
  The intent's constraint rules out a second recorder. A separate flag would
  be a second clock that has to agree with #86's clock. `_voice_until ==
  inf` already means "she is speaking or about to".
- **Keep `_answer`'s tail as well as the recorder's.** It is harmless, since
  the recorder measures from `_voice_until` and adds nothing after a slept
  tail. But it is dead weight, and it holds `_turn_lock` 0.35 s longer than
  needed.

## Risks

- **#86's consent timing must not change.** `_voice_until`, `_said` and
  checks A, B′ and D are only read, never written, by the new code. Check A's
  guard (1.0 s) is still longer than the tail (0.35 s), so an echo at mic
  open is still refused. `SpokenConsentTests` models exactly this with
  `AT_OPEN = 0.35`, and it passed unchanged against the prototype. One
  observable difference: a spoken "confirm" that overlaps her voice no
  longer reaches `_consent` to be refused with SOON. The capture is
  discarded, and the user says it again. That fails closed, in the same
  direction as before.
- **Lost user speech** when a typed line or key press lands during a capture
  (question 1). This is accepted and stated.
- **Idle stop.** A capture that is shut for her voice must not count as
  silence (Design 2). If that is missed, a typed turn after 600 s
  (`config.py:363`) switches listening off. This was seen in the prototype
  before the fix.
- **The #120 overlap.** `MicrophoneGateTests` covers this code.
  `test_barge_in_lets_the_microphone_stay_open_while_she_talks` is the test
  #120 reports as flaky under load. Its path (`barge_in` on) is not changed
  by this spec, because every new branch is behind `not barge_in`. So #114
  should neither fix nor worsen #120. The new tests must not copy that
  test's shape, a 30 s real-time `Event.wait` on a gated mouth. They use
  events and the stepped clock, as `SpokenConsentTests` does. The plan must
  not edit the barge-in test. #120 owns that. If #120 lands first, rebase
  onto it.
- **Hosts.** Only the local engine on the machine running the daemon
  (p620 or razer). The realtime engine is not touched.

## Verification

New tests in `tests/test_local_engine.py`, all using fakes, a stepped clock
and no real-time waits. They reuse `Mouth`, `Ears` / `OnsetEars` and
`EngineTestCase`, plus a "room" recorder like the demonstration's: frames
that call the level callback and contain her sentence while the mouth is
playing.

1. Typed turn while listening: the fake mouth records `mic_open == False`,
   and the brain is asked only the typed line.
2. Typed confirm and typed cancel of a hold while listening: the same two
   assertions for "Done." or the release reply, and for the cancel line.
3. Keybind release of a brain hold while listening: the same, and
   `_local_confirm` returns before the reply is spoken.
4. Muted with a wake word, and a typed reply that contains the wake word:
   listening stays off, and the brain is asked nothing more.
5. Tail on a short line: after NOT_CAUGHT, the next capture does not open
   before `_voice_until + ECHO_TAIL_SECONDS` (stepped clock).
6. A user utterance after the tail still reaches the brain.
7. A capture shut for her voice does not idle-stop, even with
   `_last_speech` older than `idle_stop_seconds`.

Regression: `SpokenConsentTests`, `MicrophoneGateTests`,
`WatchAnnounceTests` and the rest of the file pass unchanged. Then run
`nix flake check --no-write-lock-file` with
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`. If the check fails only
on the #120 barge-in test under load, report it against #120. Do not
change that test here.
