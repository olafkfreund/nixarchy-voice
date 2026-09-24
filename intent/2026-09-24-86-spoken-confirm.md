---
status: draft
issue: 86
author: olafkfreund
---

# Intent: a spoken "confirm" must release a held Claude Code action, and her own voice must not

Closes #86.

## Problem

On the local engine, when the brain holds a Claude Code call (Bash, Write, …)
the engine itself says so, out loud, at the end of the turn
(`src/omarchy_voice/local_engine.py:450-455`):

```python
await self._say(f"{held} needs confirming. Say confirm, or cancel.")
```

Saying "confirm" does not work. While something is held the router steps
aside (`local_engine.py:473`), so the transcript goes to the model as an
ordinary user turn, and the model has two moves. Neither releases the hold:

- **`confirm_last`.** The brain allows our own MCP tools unconditionally
  (`claude_backend.py:397-403`), so the call reaches the MCP server, whose
  gate only knows `executor.pending` (`mcp_server.py:138-139`). A Claude Code
  hold is not there. It is a description on the brain
  (`claude_backend.py:414-423`), kept off the executor on purpose
  (`claude_backend.py:276-282`). The answer is "nothing is waiting".
- **Retrying the action.** The policy holds it again (`claude_backend.py:414`).

The only route to `brain.confirm()` is `_local_confirm`, which runs from the
keybind or `listen confirm` (`local_engine.py:584-585`, `633-652`). So the
engine asks for a spoken answer that it cannot act on.

Demonstrated with the real `LocalSession`, the real `ClaudeBrain` gate
(`_pre_tool_use`) and the real MCP server's `confirm_last` handler. The
microphone, whisper and speakers are faked, and so is the model: the
`ScriptedBrain` from the #76 tests, which on "confirm" makes both moves
through the hook. The spoken turn goes through `_turn`: record, then
transcribe, then `_answer`.

```
held:               reboot
engine said:        reboot needs confirming. Say confirm, or cancel.
heard:              ["'commit my notes and reboot'", "'Confirm.'"]
confirm_last hook:  allow
confirm_last says:  ERROR: nothing is waiting for confirmation. Do not call this again.
executor.pending:   None
allowed:            ['git -C ~/notes commit -am wip']
still held:         reboot
engine said:        ok.
```

The reboot never runs and is still held. The session also says nothing
more, because the hold is the same one as before and the engine only
announces a new one (`local_engine.py:451`). To the user, "confirm" was
heard and then ignored.

### Why this is not a one-liner: she can hear herself

To make it work, something has to decide whether a transcript counts as
consent. `_local_confirm` trusts neither the model nor the transcript,
by design (`local_engine.py:634`). The line that asks for consent
contains the consent word, and the microphone opens again right after it.

**When the mic is shut and when it opens (local engine):**

1. `_say` puts the sentence on the speech queue. With `barge_in` off, the
   default (`config.py:302`), it waits for the queue to drain
   (`local_engine.py:214-224`).
2. The speech loop runs `Feedback._speak_now` in a thread
   (`local_engine.py:197-212`). ElevenLabs and Piper both block until
   `pw-cat` exits (`feedback.py:138-165`). `pw-cat` exiting means the last
   sample was handed to PipeWire, not that the room has gone quiet.
3. The turn ends with a fixed `ECHO_TAIL_SECONDS` = 0.35 s sleep
   (`local_engine.py:457-462`, `realtime.py:52`).
4. `_listen_loop` calls `_turn`, which calls `record_utterance`
   (`local_engine.py:533-536`, `256-258`). A capture starts on the first
   frame above `silence_level` (0.02). It ends after
   `end_of_speech_seconds` (0.8 s, #72) of quiet
   (`listen_local.py:261-277`, `config.py:329`). #72 changed how a capture
   *ends*, not when the mic *opens*. Nothing drops frames while she speaks,
   unlike the realtime engine. The local engine's only guard is the order
   of these steps plus the 0.35 s.

With `barge_in` on, `_say` does not wait, and the turn skips the echo tail.
The mic reopens while the model's reply and the engine's "Say confirm, or
cancel." are still queued or playing. In that mode the prompt is fed
straight into the next capture.

**Would whisper give back her "confirm"?** The engine line ends in "…or
cancel", so the likeliest tail fragment is "cancel", not "confirm". Today
that fragment goes to the model as text. For "confirm" to be picked up,
the captured audio would have to be "confirm" and nothing else, because
`_matches` accepts the whole utterance only, plus filler words
(`session.py:30-51`). "Say confirm, or cancel" does not match. That is the
protection today. It is thin for four reasons:

- The model speaks first, in its own words. The hold message tells it to
  "ask them to confirm it out loud" (`claude_backend.py:419-421`). A reply
  ending "…Confirm?" or "Go ahead?" is plausible, and both are default
  `confirm_words` (`config.py:463`). With `barge_in` on, those words are
  in the room while the mic is open.
- Whisper turns her voice into fragments. HANDOFF.md, "She was hearing
  herself" (realtime engine, speakers, no echo cancel): her own name came
  back as `'어마'` and she answered it. Her sentence came back split into two
  turns. `'Бела.'` became a keypress. A fragment that happens to be
  "Confirm." is the same failure with a word from the confirm list.
- The local engine shows the same timing in the session log. On
  2026-09-13, three times, a capture that `clean()` threw away came about
  2 s after her last sentence (20:12:18, 20:13:26, 20:14:26, each after a
  `usage` line). That is 0.35 s of tail, then 0.8 s of hang, then whisper.
  It does not prove it was her voice, because the rejected text is not
  logged. It does show that something loud enough to start a capture
  arrives right after she stops.
- The echo tail is a guess about the room, set once for the realtime
  engine's frame-drop gate (`realtime.py:47-52`).

A naive fix is "if the transcript is a confirm word, call
`_local_confirm`". It opens the same door the realtime frame gate was
built to close: a phantom utterance runs something. This time the
something is the one action the gate exists to stop.

### What "spoken confirm" means on the realtime engine

- The model hears the audio and calls `confirm_last(heard_phrase)`
  (`realtime.py:263-297`). This process decides whether it counts:
  - it must come from a new user turn after the hold, never the response
    that held it (`realtime.py:1190-1198`). `_user_turn_since_hold` is set
    on `input_audio_buffer.speech_started` (`realtime.py:978-981`) or on a
    typed turn (`realtime.py:937`);
  - the phrase must be a whole-utterance `confirm_words` match with no
    negation (`realtime.py:1289-1310`, `session.py:30-51`).
- **Echo exposure: reduced, not zero.** While `barge_in` is off, mic frames
  are dropped while she is playing and for 0.35 s after
  (`realtime.py:770-786`). Her voice is not uploaded, so it cannot start a
  speech turn. Past the tail, or with `barge_in` on, an echo *is* a new
  user turn and passes the first check. The model then reports the
  words. Its own docstring says the model "can still lie about the words"
  (`realtime.py:1295-1297`). The consent is only as good as the model's
  honesty and the frame gate's timing.
- Every hold there is our own tool in `executor.pending`. No Claude Code
  holds, so #86 does not arise there.

The MCP server's `confirm_last` adds `CONFIRM_DELAY` (2 s after the hold,
`mcp_server.py:36-49`, `141-145`) against an agent chaining its own
confirm. That guards timing. It does not guard the source of the audio.

## Proposed outcome

- On the local engine, after a Claude Code hold, the user can release it by
  voice. The release goes through the #76 release turn
  (`brain.confirm()` → `_answer(text, release=held)`), which runs exactly
  that call once.
- Her own playback cannot release a hold: not the engine's prompt, not
  the model's reply, not their tail through speakers, with `barge_in` on or
  off.
- A spoken attempt that is refused is answered out loud. No more
  silence.
- The keybind / `listen confirm` path is unchanged. Holds of our own tools
  keep working, and their spoken release should end up behaving the same
  way.

## Affected users and systems

- `src/omarchy_voice/local_engine.py`: `_answer`'s hold announcement,
  `_turn`/`_record`, `_route`'s skip while held, `_local_confirm`.
- `src/omarchy_voice/claude_backend.py`: `confirm()`, `pending`, the hold
  message the model sees.
- `src/omarchy_voice/mcp_server.py`: `confirm_last`/`cancel_last` as the
  brain's model sees them. On the local engine they currently know only
  `executor.pending`.
- `src/omarchy_voice/session.py` `_matches`, `config.confirm_words`.
- Possibly `feedback.py` (when playback has really ended) and
  `realtime.py` if the safeguard is shared.
- Anyone on the default local engine with the Claude brain, on speakers.
  This machine is one: a Focusrite Scarlett Solo is both mic and line out,
  with no echo cancel (HANDOFF.md).

## Constraints

- The gate must not weaken. A release must not be possible from the
  model's word alone, or from any audio the session produced itself.
- The #76 contract stands: a confirmation releases exactly the held call,
  once, via the release turn. Nothing else from the utterance runs again,
  and an unused approval dies with its release turn.
- Dry-run still refuses on the confirmed path.
- Negations ("don't confirm") never count (`_matches(...,
  allow_negation=False)`).
- Do not add confirm words to whisper's vocabulary prompt
  (`listen_local.py:85-105`). The prompt biases decoding towards its
  words, so it would make "confirm" likelier to appear from noise.
- Tests use fakes only: no microphone, whisper, speakers or live desktop.

## Candidate safeguards (for the spec to choose; not decided here)

1. **Engine-side exact phrase, after a guard window.** The engine checks
   the transcript itself, before the model sees it. The capture must have
   *started* after playback truly ended plus a guard window, and must be
   only a confirm phrase. Needs a real "playback ended" time, not "`pw-cat`
   returned". Still vulnerable to a late reflection that says exactly
   "confirm".
2. **A phrase the prompt does not contain.** The prompt asks for words it
   never says itself, and the model is told not to say them either (e.g.
   "yes, do it"), so her echo cannot contain them. Changes the spoken prompt
   and possibly `confirm_words`. The model's own reply is not under our
   control, so it would also need filtering, or the rule that only the
   engine's line asks.
3. **Keybind for destructive actions.** A spoken confirm releases only a
   lower tier (e.g. `omarchy update`). Shutdown/reboot/`rm`-class holds
   still need the keybind, and the prompt says so.
4. **Wake word plus confirm** ("Oma, confirm"). Her own voice does not
   use the wake word, and matching reuses `heard_wake_word` /
   `after_wake_word`. Costs a longer phrase, and only works when a wake
   word is configured (`config.py:396` defaults to empty).

These combine, for example 1 with 3.

## Open questions

1. **Which safeguard, or which combination?** Is "exact phrase after a
   guard window" alone acceptable on speakers, or must spoken release also
   need a phrase the prompt does not contain, or the wake word?
2. **Who decides consent on the local engine?** The engine, matching the
   transcript before the model sees it, or the model through a
   `confirm_last` that learns about brain holds? The first keeps the model
   out of consent entirely, which the realtime engine cannot do.
3. **Are some holds keybind-only?** If yes, which ones: shutdown, reboot,
   suspend, `rm`, `nixos-rebuild`? What does the prompt say for them?
4. **`barge_in = true`.** Is spoken release allowed at all in that mode?
   Or only with PipeWire echo-cancel detected, and otherwise refused with
   "use the keybind"?
5. **Our own tools on the local engine.** Should a spoken confirm for an
   `executor.pending` hold take the same engine-side path? Today the model
   can reach it through `confirm_last`, which checks only the phrase and
   `CONFIRM_DELAY`.
6. **Spoken cancel.** Should it get the same treatment? Her prompt ends in
   "cancel", so a tail self-cancel is the likelier echo. It is harmless to
   the machine but loses the user's pending action.
7. **Realtime engine.** Fix its echo exposure (the model reporting an echo
   as the user's words) in this issue, or file it separately?
