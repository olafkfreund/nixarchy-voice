---
status: approved
issue: 113
author: olafkfreund
---

# Intent: on the realtime engine, her own "Confirm?" must not release a held action

Closes #113.

## Problem

On the OpenAI realtime engine, the model decides whether the user said
"confirm". The engine does not check it. Her own voice can reach the model
as a user turn, and the model can report it as the answer. Split out of #86
(spec Q7), which fixed this on the local engine only.

### How consent works there today

Line numbers are from `origin/main` at fc33ae2.

1. A gated tool call (`omarchy_cli reboot`, …) is held in
   `executor.pending`. `_on_response_done` then clears
   `_user_turn_since_hold` (`realtime.py:1203-1206`).
2. The prompt tells the model to say what is held out loud, ask the user to
   confirm it, and later call `confirm_last` with "exactly the words you heard
   them say" (`realtime.py:241-258`). `confirm_last` and `cancel_last` are
   `GATE_TOOLS`. They are realtime-only and added in `to_realtime_tools`
   (`realtime.py:263-312`).
3. The engine checks two things before it releases:
   - **A new user turn since the hold, and not in the same batch.**
     (`realtime.py:1190-1199`.) `_user_turn_since_hold` is set by any
     `input_audio_buffer.speech_started` from the server's VAD
     (`realtime.py:979-981`) or by a typed turn (`_inject`,
     `realtime.py:937`). The engine does not look at the audio that
     triggered it.
   - **The phrase the model reports.** `_confirm(heard_phrase)` accepts a
     whole-utterance `confirm_words` match with no negation
     (`realtime.py:1289-1310`, `session.py:30-51`, defaults
     `config.py:492`). Its docstring says: "The model can still lie about
     the words."
4. The only path that bypasses the model is the keybind / `listen confirm`
   → `_local_confirm` (`realtime.py:955-963`).

### What keeps her voice out: the mic gate only

`_mic_loop` (`realtime.py:714`) streams `pw-record` frames to the server.
With `barge_in` off (the default, `config.py:324`), a frame is **dropped**
while `speaker.is_playing(ECHO_TAIL_SECONDS)` (`realtime.py:770-786`).
`ECHO_TAIL_SECONDS` is 0.35 s (`realtime.py:52`). `is_playing` compares with
`_plays_until`, which `Speaker.write` books from bytes handed over, not from
sound leaving the speaker (`realtime.py:343-344`, `357-365`). After that,
frames go through the silence gate (`_gate_open`, `realtime.py:844`), which
passes anything louder than `silence_level`. With `barge_in` on, nothing is
dropped at all.

Any echo that lasts past 0.35 s of tail, or any echo with `barge_in` on, is
uploaded. The server's VAD then starts a user turn on it
(`speech_started`), and the "new user turn" check passes. What remains is
the model's report, and the model heard audio that ended in her own
"Confirm?".

### Demonstration (fakes only)

The script uses the real `RealtimeSession`, the real `Executor` (dry-run),
the real `_mic_loop` and the real `_on_event`. The websocket is the
`FakeSocket` from `tests/test_realtime.py`. `pw-record` and `pw-cat` are fake
processes. The fake `pw-record` returns a loud 100 ms frame every 100 ms,
standing in for her voice in the room. The server's events and the model's
tool calls are scripted. No OpenAI connection;
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`.

Script: the model holds a reboot. Her reply is 0.5 s of audio with the
transcript "Reboot is held. Confirm?". The mic hears the room for 1.2 s. The
server then sends `speech_started` and an input transcript "Confirm?", and
the model calls `confirm_last("Confirm?")`.

```
held: omarchy reboot
barge_in=False: playback ends +0.50s, tail to +0.85s; mic frames over 1.2 s: dropped 8, uploaded 4
confirm_last output: [dry-run] would run: omarchy reboot
executor.pending: None
   log: HOLD    omarchy reboot
   log: reply   'Reboot is held. Confirm?'
   log: gate    held 8 frame(s) while she spoke
   log: heard   'Confirm?'
   log: confirm 'Confirm?' released: omarchy reboot
----
held: omarchy reboot
barge_in=True: playback ends +0.50s, tail to +0.85s; mic frames over 1.2 s: dropped 0, uploaded 12
confirm_last output: [dry-run] would run: omarchy reboot
executor.pending: None
   ...
   log: confirm 'Confirm?' released: omarchy reboot
```

With `barge_in` off, the gate dropped the frames during playback and the
tail, then uploaded the rest. In both modes the held reboot was released.
The log line `heard 'Confirm?'` is word for word the end of the line she
had just said. The engine had both strings and compared neither.

The demo scripts the model's call. It does not show that a live model
*would* call `confirm_last` on an echo. It shows that nothing in this
process would stop it if it did. HANDOFF.md ("She was hearing herself")
records the live model treating her echo as user speech: "Multiple crowd",
`'어마'`, `'Бела.'` pressing CTRL+R.

### What the engine can see to apply #86's checks

#86 decides consent in the engine (`local_engine.py:682-716`): the capture
must start at least `spoken_confirm_guard_seconds` (1.0 s,
`config.py:325-331`) after her voice ended (check A); the confirm phrase
must not appear in anything she said this turn (check B′,
`local_engine.py:183`, `253`, `705`); with `barge_in` on, no spoken confirm
(check D).

| Needed | Realtime has it? |
| ------ | ---------------- |
| When her audio ended | An estimate: `speaker._plays_until`, booked per byte written (`realtime.py:364`). There is no real end-of-playback signal. The local engine has the same limit. |
| When the user's turn started | Only the arrival time of `speech_started` (`realtime.py:979`). Its `audio_start_ms` counts from the start of the *uploaded* buffer. Dropped frames and gated frames are not in that buffer, so it does not map to wall-clock time without more bookkeeping. |
| What she said this turn | Yes. `response.output_audio_transcript.delta/done` (`realtime.py:991-998`). Today it is logged and thrown away. It is not kept per turn. |
| What the user said | Only if `realtime_transcribe_model` is set. It is **off by default** (`config.py:397-409`, sent at `realtime.py:538-539`). When on, `conversation.item.input_audio_transcription.completed` carries `item_id` and `transcript`, and today it is only logged (`realtime.py:1004-1007`). It comes from a second model, not the one that heard the audio. The engine has not verified whether it can arrive after the `response.done` that carries `confirm_last`. |
| Barge-in state | Yes. `config.barge_in`. |

So check D ports directly. B′ is possible against the model's reported
`heard_phrase` today, and against the server's transcript if transcription
is on. A needs a new clock: note `_plays_until` when `speech_started`
arrives, and compare the two.

### Is realtime still used here?

Not today. `~/.config/omarchy-voice/config.toml` sets no `[realtime]
engine`, so `realtime_engine` defaults to `"local"` (`config.py:392`). The
session log has 21 `engine=realtime` starts on 2026-09-11 and 2026-09-12;
the last one is at 2026-09-12 16:26:58. Every start after it is
`engine=local` (37 local starts from 2026-09-12 to 2026-09-22). The 22 `session_expired` errors (60-minute cap)
are all from that period. During it, 63 `heard` lines show that input
transcription was switched on then. The engine is still shipped, documented
as the `"openai"` choice, and one config line away.

## Proposed outcome

- On the realtime engine, audio the session produced cannot release a held
  action: her reply, her ask, or their echo through speakers, with
  `barge_in` on or off. This holds whatever the model reports.
- A refused spoken confirm is told to the model (and so to the user) as a
  refusal that names the keybind. It does not fail silently.
- The keybind / `listen confirm` path is unchanged.
- The #86 local engine behaviour is unchanged.

## Affected users and systems

- `src/omarchy_voice/realtime.py`: `_on_event` (speech_started, reply
  transcript, input transcript), `_on_response_done`'s `confirm_last`
  branch, `_confirm`, the "Confirmations" prompt block, and the `GATE_TOOLS`
  descriptions.
- `src/omarchy_voice/config.py`: `barge_in`, `spoken_confirm_guard_seconds`
  (its comment says it is local-only) and `realtime_transcribe_model`, if
  the checks need it.
- `tests/test_realtime.py`.
- Users who choose `engine = "openai"`. Not this machine today. When it was
  used, it ran on speakers with no echo cancel (Focusrite Scarlett Solo,
  HANDOFF.md).

## Constraints

- The gate must not weaken. The model's word alone never releases a hold.
  Neither does any audio the session produced.
- Keybind / `listen confirm` keeps working for every hold.
- Negations never count (`_matches(..., allow_negation=False)`).
- Nothing new is metered by default. Turning on input transcription costs a
  second model over every second of input (`config.py:403-408`). A fix that
  needs it must say so, and must not quietly enable it.
- No change to `local_engine.py` behaviour. A helper shared with #86 is fine
  if it changes no local result.
- Tests use fakes only: no websocket, microphone or speakers.

## Open questions

1. **Port #86's checks, or stop spoken confirm on realtime?** Options:
   - **(a) Port A, B′ and D.** Check D: no spoken confirm with `barge_in`.
     Check B′: refuse if the confirm phrase is in her reply transcript this
     turn. Check A: refuse if `speech_started` arrived less than
     `spoken_confirm_guard_seconds` after `_plays_until`. The model is still
     the ear, so a lie or a mishearing that passes these checks still
     releases.
   - **(b) Keybind-only confirm on realtime.** `confirm_last` refuses
     everything. The prompt tells the model to point at the keybind. This
     is the simplest and closes the hole completely. It costs hands-free
     confirm on an engine nobody here currently runs.
   - **(c) Deprecate spoken confirm there**, as (b), and also drop
     `confirm_last` from `GATE_TOOLS` so the model cannot try it.
     `cancel_last` stays.
2. **If (a): whose words does B′ check?** The model's `heard_phrase` only,
   or the server's input transcript too? The second needs
   `realtime_transcribe_model` on, which is off by default and not free.
   Should spoken confirm be refused when transcription is off?
3. **If (a): is `speech_started` arrival time good enough as the onset?**
   It lags the real onset by the VAD's detection delay, which makes A
   stricter, not looser. Or must `audio_start_ms` be mapped to wall-clock
   time?
4. **Spoken cancel.** Leave `cancel_last` model-driven as it is? A
   self-cancel from her echo loses the user's action but runs nothing.
5. **Is the realtime engine worth this at all?** It has been unused since
   2026-09-12. Should this issue also decide whether it stays supported, or
   is that a separate issue?
