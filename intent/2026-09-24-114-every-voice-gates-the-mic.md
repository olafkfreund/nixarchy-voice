---
status: draft
issue: 114
author: olafkfreund
---

# Intent: every sentence she speaks keeps the microphone shut

Closes #114.

## Problem

On the local engine the microphone gate is not a flag. It is the shape of
one loop: `_listen_loop` (`src/omarchy_voice/local_engine.py:564-584`)
records, then awaits the whole turn, and only then records again. `_say`
waits for her sentence to drain before it returns (`local_engine.py:244-256`),
and `_answer` sleeps `ECHO_TAIL_SECONDS` (0.35 s, `realtime.py:52`) at the
end of the turn (`local_engine.py:501-506`). While the loop is inside a turn,
nothing records. That is how she stops hearing herself.

Two entry points start a turn outside that loop. They come in over the
control socket and are handed to the event loop as background tasks:

- **typed `listen say`**: `_inject` spawns `_typed` (`local_engine.py:674`),
  which runs `_consent` and then `_answer` (`local_engine.py:677-680`).
- **the confirm key / `listen confirm`**, for a Claude Code hold:
  `_local_confirm` → `_release(wait=False)` spawns the release turn
  (`local_engine.py:724-726`, `765`).

A background turn takes `_turn_lock`, but the listen loop never takes that
lock before it records. So when listening is on, the loop is usually
sitting in `_record` with the capture open (`feedback.mic_open` is True,
`local_engine.py:290`) while the background turn speaks into the room. When
listening is off and a wake word is set, `_wake_turn` has a capture open in
the same way.

`Feedback.speak` has a guard that refuses to speak while `mic_open`
(`feedback.py:128-135`). The local engine does not use `speak`. It calls
`_speak_now` directly (`local_engine.py:232`), on purpose, so that guard is
not in the path.

### Demonstrated

This uses the real `LocalSession`, its speech loop, `_turn` / `_wake_turn`,
`_consent` and `_release`. The mouth, microphone and whisper are fakes that
share one room: the mic reports an onset when the mouth starts, and
"whisper" returns what the mouth played unless a scenario sets something
else. The brain is the tests' `FakeBrain`, which replies "It is three
o'clock." `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`. No audio.

```
--- 1 typed turn, listening
  spoke (text, mic_open): [("It is three o'clock.", True), ("It is three o'clock.", False)]
  brain.asked: ['what time is it', "It is three o'clock."]
--- 2 keybind confirm (brain hold "reboot"), listening
  spoke (text, mic_open): [("It is three o'clock.", True), ("It is three o'clock.", False)]
  brain.asked: ['reboot', "It is three o'clock."]  confirmed: ['reboot']
--- 3 typed turn with a hold; the echo is misheard as "Confirm."
  spoke (text, mic_open): [("It is three o'clock.", True), ('I was still talking. Say it again.', False)]
  log: consent  refused (too soon) 'Confirm.'      still held: reboot
--- 4 typed turn while muted, wake word "oma"; the echo is heard as "Oma, close the browser."
  spoke (text, mic_open): [("It is three o'clock.", True), ...]
  log: wake    heard 'Oma, close the browser.' — acting on 'close the browser.'
  brain.asked: ['what time is it', 'close the browser.']
```

What her echoed speech does once it has been transcribed:

- **It reaches the brain as the user.** In rows 1 and 2 her own reply
  becomes the next user turn, and she answers herself. This is the bug
  HANDOFF.md already describes as "She was hearing herself".
- **It turns listening on and acts.** In row 4 an echo that contains the wake
  word switches listening on (`local_engine.py:370-377`) and runs the rest of
  the sentence. Row 4 was set up to show this can happen. She does not
  normally say "Oma".
- **It is not taken as consent.** In row 3 #86's timing check A refuses it
  (`local_engine.py:702`): the onset landed while `_voice_until` was `inf`.
  The spoken confirm path fails closed, and the harm is limited to the two
  cases above.

### Every path that makes her speak

Every sound on the local engine goes through `_say`. `feedback.speak` is used
only by the realtime engine (`realtime.py:1267`), and notifications are
`notify-send` (`feedback.py:117-123`), which the engine does not play as
sound. `_say` sets `_voice_until = inf` and records the line in `_said`
(`local_engine.py:252-253`). `_speech_loop` sets `_voice_until` to the time
her last `pw-cat` returned (`local_engine.py:238-242`). So **every path
updates #86's clock**. The gaps are in the gate and the echo tail:

| Path | Where | Mic shut while she speaks? | Echo tail before the mic reopens? | `_voice_until` |
| --- | --- | --- | --- | --- |
| Voice turn reply, held prompt, error line | `_turn` → `_answer`, `:471`, `:480`, `:499` | yes, the loop awaits it | yes, `:506` | yes |
| Wake turn "Oma, do X" | `_wake_turn` → `_answer`, `:376-377` | yes | yes | yes |
| Watch announcement (#74) | `_listen_loop` → `_answer(from_user=False)`, `:572-577` | yes | yes | yes |
| Routed command line (#71) | `_run_route`, `:538`, `:554`, inside `_answer` | yes | yes | yes |
| "I did not catch that" / transcriber missing | `_turn`, `:325`, `:317` | yes | **no**: `_turn` returns and the loop records again | yes |
| #86 refusals SOON / SELF / BARGE, from voice | `_consent`, `:709` | yes | **no** | yes |
| Spoken cancel line | `_consent`, `:716` | yes | **no** | yes |
| Spoken release of an executor hold: "Done." / failure line | `_release(wait=True)`, `:749` | yes | **no** | yes |
| Spoken release of a brain hold | `_release(wait=True)` → `_answer`, `:763` | yes | yes | yes |
| **Keybind confirm, brain hold** | `_local_confirm` → spawned `_answer`, `:765` | **no** (row 2) | the tail only delays the lock, and the mic is already open | yes |
| Keybind confirm, executor hold | `_release(wait=False)`, `:739` (speaks only `if wait`) | nothing is spoken | n/a | n/a |
| Keybind / CLI cancel | `_local_cancel`, `:769-781` | nothing is spoken | n/a | n/a |
| **Typed `listen say` turn** | `_inject` → spawned `_typed` → `_answer`, `:674` | **no** (rows 1, 4) | same as the keybind row | yes |
| **Typed confirm / cancel** (#86) | spawned `_typed` → `_consent` → `_release(wait=True)` or `:716` | **no** | no | yes |
| Mute mid-reply | `_drop_queued_speech`, `:258-268` | no new speech | n/a | yes: kept at `inf` while speaking (`:260-262`) |

### Is there a hole in #86's check A?

None was found. Check A refuses when `onset - _voice_until < guard`
(`local_engine.py:702`). `_voice_until` is `inf` from the moment any path
queues a line until the last queued line has finished playing. If
concurrent turns queue lines one after another, the `if self._speech.empty()`
test (`:240`) keeps it at `inf` until the last one ends. So a capture whose
onset falls during her speech, or up to `spoken_confirm_guard_seconds`
(1.0 s) after it, is refused on every path in the table, background turns
included. Row 3 shows this.

Two limits apply, and both come from design choices rather than from a
missing update:

- Check A only runs on a whole-utterance confirm or cancel word
  (`_consent`, `:690-694`). Anything else she says into an open capture goes
  to the brain unguarded (rows 1, 2 and 4).
- It fails closed in the other direction too. If a user says "confirm" while
  a keybind release turn happens to be speaking, they are refused ("I was
  still talking"). That is the correct result, but it is a symptom of the
  same overlap.

## Proposed outcome

- No sentence she speaks, from any entry point, plays while a capture is
  open. Typed turns and keybind release turns wait for the microphone to be
  shut, or shut it, the same way a voice turn does.
- The capture does not reopen until `ECHO_TAIL_SECONDS` after her last
  sentence, on every path. That includes the short lines that currently skip
  the tail: "I did not catch that", #86's refusals, the spoken cancel line
  and the executor release "Done."
- #86's check A and its clock are unchanged and still pass.
- One test per entry path shows that a fake mic records nothing while the
  fake mouth is playing: typed turn, typed confirm and cancel, keybind
  release of a brain hold, and the wake capture while muted.

## Affected users and systems

- `src/omarchy_voice/local_engine.py`: `_listen_loop`, `_record`,
  `_inject` / `_typed`, `_release`, `_turn`, `_consent`.
- `tests/test_local_engine.py`: new cases built on the existing `Mouth`,
  `Ears` and `SpokenConsentTests` fakes.
- Anyone using the local engine with the confirm key or `listen say` bound,
  with listening on or with a wake word set.
- Not the realtime engine. Its speech goes through `Feedback.speak`, which
  already refuses while `mic_open`.

## Constraints

- Must not weaken #86. Checks A, B′ and D, `_voice_until` and `_said` keep
  their meaning. The existing `SpokenConsentTests` pass unchanged.
- Must not block the control socket. `_control` waits at most 10 s
  (`local_engine.py:638-641`), and `_inject` returns "sent" before the reply
  on purpose (`:663-668`). A fix must not make the keybind wait for her
  sentence.
- One microphone and one recorder (`_listen_loop` docstring, `:564-569`).
  Do not add a second coroutine that can open it.
- `barge_in = true` is a separate case. There her voice and the room are one
  stream by design, and #86 already refuses a spoken confirm there. This
  intent changes nothing when barge-in is on.
- Fakes only in tests. No live audio, and nothing that depends on this
  machine's load (a stepped clock, as in `SpokenConsentTests`).

## Open questions

1. **Close an open capture, or wait for it to end?** When a background turn
   is ready to speak and the loop is recording, it can either abandon the
   capture (as a toggle does through `_Interrupted`, `:286-287`) or wait for
   it to finish, which in a silent room can take up to
   `MAX_UTTERANCE_SECONDS` (`:51`, 15 s per the comment at `:574`). Abandoning loses anything the user was saying at
   that moment. Waiting delays the typed or keybind reply by up to one full
   capture. The #74 announcements chose to wait and accepted that ceiling
   (`:573-575`).
2. **Should the short no-tail lines (NOT_CAUGHT, the refusals, cancel,
   "Done.") get the echo tail as well?** Check A already covers consent for
   them, because its guard (1.0 s) is longer than the tail. The risk is
   only a tail echo that reaches the brain, and `clean()` usually rejects a
   fragment. This is cheap to include, but it goes beyond the issue as
   filed.
3. **The muted wake capture** (row 4). Is it in scope? A typed turn while
   muted speaks into the wake recorder. Only a reply containing the wake
   word does harm, and the reply would then switch listening on.
4. **Should `_local_confirm` for a brain hold wait for its release turn**,
   as the spoken path does (`wait=True`)? That shuts the mic the simple way,
   but it holds the control socket through a model turn, which can take
   longer than the 10 s `_control` allows.
