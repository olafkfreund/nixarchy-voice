---
status: approved
issue: 86
intent: intent/2026-09-24-86-spoken-confirm.md
---

# Spec: the engine hears "confirm", and nothing she said can be it

Closes #86.

The engine, not the model, decides whether an utterance is consent. It takes
a spoken confirm only when three things are true:

- the capture started a guard window after her playback really ended;
- she did not say the phrase herself since the latest turn began;
- barge-in is off.

Then it calls the same release path as the keybind. Her spoken prompt stops
naming the phrase, and the screen shows it instead. So no audio she produced
contains the word it would take to approve anything.

## The intent's open questions, answered

The intent was approved without answers to its seven open questions. They are
decided here, and **each decision can be rejected at this gate**. The
evidence is a demonstration on fakes, built the way the intent's own was:
the real `LocalSession`, the real `WarmBrain` gate (`_pre_tool_use`), and the
`ScriptedBrain` model from the #76 tests (`tests/test_local_engine.py:353`).
The microphone, whisper and speakers are faked. The fake microphone reports
the capture's onset (the first frame above `silence_level`) a set time after
it opens. The candidate design was a subclass of `LocalSession` in a scratch
file. None of it is committed. The same cases become the tests below.

The safeguards measured:

- **A. Timing.** The capture's onset must be at least `GUARD` (1.0 s) after
  the last of her speech finished playing. While anything is queued or
  playing, that time is "not yet".
- **B. Lexical, model only.** Refuse if the matched phrase appears in anything
  the model said in the latest turn (the lead's option b).
- **B′. Lexical, everything.** Refuse if the matched phrase appears in anything
  spoken since the latest turn began: the model's sentences, the engine's own
  lines, and the held description. For this to leave the real case working,
  her spoken prompt must not name the phrase (see Q1).
- **C. Keybind-only for some holds.** Not needed by any case below (see Q3).
- **D. No spoken confirm while `barge_in` is on.**

Cases: **E** is her own voice, **R** is the user. "At mic open" means onset
0 s after the capture opens, 0.35 s after playback, which is the tail. "Late"
means onset 1.0 s after it opens, 1.35 s after playback, which is past the
guard.

| Case | naive | A | B | A+B+D | **A+B′+D** |
| --- | --- | --- | --- | --- | --- |
| E1 prompt echo "Confirm.", at mic open | RAN | refused A | RAN | refused A | **refused A** |
| E1′ prompt "Confirm.", late | RAN | RAN | RAN | RAN | RAN ¹ |
| E1″ late "Confirm.", held command is `git commit -m confirm && reboot` | RAN | RAN | RAN | RAN | **refused B′** |
| E2 reply ends "Confirm?", echo at mic open | RAN | refused A | refused B | refused A | **refused A** |
| E2′ reply ends "Go ahead?", echo late | RAN | RAN | refused B | refused B | **refused B′** |
| E3 barge_in on, prompt echo while she is still playing | RAN | refused A | RAN | refused D | **refused D** |
| E4 prompt tail "cancel", at mic open | cancelled | kept | cancelled | kept | **kept** |
| R1 user "confirm" after a pause | RAN | RAN | RAN | RAN | **RAN** |
| R2 user "confirm" 0.2 s after mic open | RAN | refused A | RAN | refused A | refused A ² |
| R3 user "confirm", her reply said "Please confirm." | RAN | RAN | refused B | refused B | refused B′ ² |
| R4 user "cancel" after a pause | cancelled | cancelled | cancelled | cancelled | **cancelled** |
| R5 barge_in on, user "confirm" after she stopped | RAN | refused A | RAN | refused D | refused D ² |

RAN means the held `reboot` passed the real gate in a release turn.

¹ Under B′ her prompt no longer contains "confirm". A transcript of exactly
"Confirm." that arrives past the guard therefore cannot be her echo. It is
the user, or whisper inventing a word from noise. Code cannot tell those two
apart, and no safeguard here claims to. That residue is the same as today's
risk from any phantom utterance. It is not the echo that #86 is about.

² The cost. Each is refused out loud with a way forward: say it again, or
use the key. None fails silently and none runs anything.

What the table shows:

- **A alone** stops every echo that arrives in the tail. That includes E3,
  because an onset while she is playing is always too soon. A does not stop
  an echo that arrives late.
- **B (model only)** stops the model's own "Confirm?" whenever it arrives.
  It cannot stop the engine's prompt, and today's prompt says "confirm"
  (E1, E1′, E1″).
- **B′ with the new prompt** is the only variant under which "she said the
  word" is impossible by construction. Her prompt, her refusal lines and her
  model's reply are all checked. The held description is checked too, which
  is how E1″ is caught. So no playback of hers can contain the phrase that
  releases. A is kept as well, for the tail, for cancel (the prompt still
  says "cancel", so B′ cannot guard it), and for speech that plays while the
  mic is open (see Risks).
- **D** makes the barge-in case unconditional instead of a timing argument.

### Q1. Which safeguard, or which combination?

**A + B′ + D, with a new spoken prompt that does not name the phrase.** The
phrase is shown on screen (the bar and the "Waiting for confirmation"
notification that `_settle` already sends, `local_engine.py:187-194`). The
spoken prompt becomes:

> "{held} is waiting for you. Say the word on the screen to run it, or cancel."

Why not the exact phrase alone after a guard window (A)? Because it rests on
a physical assumption: that her sound is gone within `GUARD` of `pw-cat`
exiting. B′ does not rest on physics. If she never said the word, her echo
cannot be the word.

Why not the wake word plus confirm? `wake_word` defaults to empty
(`config.py:396`), so most users have none. It also only helps if she never
says the wake word herself, and nothing enforces that.

The cost is R3: the model says "confirm" anyway, against its instruction
(Q2), and then the user's spoken confirm is refused for that turn with "use
the key".

### Q2. Who decides consent on the local engine?

**The engine.** While something is held (`_held()`), a transcript that is a
whole-utterance `confirm_words` or `cancel_words` match with no negation
(`session.py:30-51`, `allow_negation=False`) is consumed before the router and
before the model. The router already steps aside while something is held
(`local_engine.py:473`). A matched utterance never reaches the model,
whether it is accepted or refused.

The model is taken out of consent altogether:

- the brain's gate denies `mcp__omarchy__confirm_last` (Design 4);
- its hold instructions stop telling it to ask for, or say, the phrase.

The realtime engine cannot do this, because there the model is the ear.

### Q3. Are some holds keybind-only?

**No, not in this issue.** In the table, no case needs C. B′ already covers
the one case where a tier would have helped, a held command whose own text
contains the phrase (E1″). A tier is also a new policy concept. Today every
`confirm_patterns` entry (`config.py:108-128`) is already the user's own
choice of "ask me first", and the keybind still works for all of them. If
the approver wants a tier anyway (shutdown, reboot, `nixos-rebuild`), it is
a list checked at the top of the consent step, and it can be added here.
**Rejectable.**

### Q4. `barge_in = true`?

**Spoken confirm is refused**, out loud: "With barge-in on I cannot tell your
voice from mine. Use the key." Spoken cancel still works, under A. The prompt
and the notification then point to the key only.

Echo-cancel detection is not attempted. Nothing in the repo can detect it
today, and `barge_in` is already the user's statement that their room
allows it. Loosening this, for example "allowed when A passes", is a later
issue with its own evidence.

### Q5. Our own tools' holds (`executor.pending`)?

**Same engine path.** `_local_confirm` already releases `executor.pending`
first (`local_engine.py:633-640`), so a spoken release of either kind of hold
goes through one function. Today the model can release `executor.pending` on
the local engine through `confirm_last`. The only checks there are the
phrase it reports and `CONFIRM_DELAY` (`mcp_server.py:131-151`). Design 4
closes that.

Typed turns (`listen say confirm`, `_inject`, `local_engine.py:619-631`) keep
working through the same consent step. A typed turn has no audio, so A, B′
and D do not apply to it. It is the user at the keyboard, which is the same
trust as `listen confirm`.

### Q6. Spoken cancel?

**Engine-side too, with A only.** A self-cancel from the prompt's "…or
cancel" tail is the most likely echo (E4), and it silently loses the action
the user is in the middle of approving. A stops it.

B′ cannot apply, because the prompt must say "cancel" for spoken cancel to
be discoverable. D does not apply either: cancelling is safe for the
machine, and A already refuses a cancel captured during her playback.

A refused cancel is consumed and gets "I was still talking. Say it again."
It is not passed to the model, which could call `cancel_last` on it.

### Q7. The realtime engine?

**Out of scope. The lead files a new issue:**

> realtime: a spoken confirm is only as good as the model's report of the
> words. An echo past the frame gate's tail, or any echo with barge_in on,
> is a new user turn, and the model reports it as the user's phrase
> (`realtime.py:770-786`, `1190-1198`, `1289-1310`).

On that engine every hold is `executor.pending` (intent, "What spoken
confirm means on the realtime engine"). So #86's failure, where "confirm"
cannot release a hold, does not arise there. Nothing in this spec touches
`realtime.py`.

## Design

All in `src/omarchy_voice/local_engine.py` except item 4. Line numbers are
from `origin/main` at 93c6dac.

1. **When she last made a sound.** Add `self._voice_until: float = 0.0` to
   `LocalSession.__init__`.
   - `_say` (`:214-224`) sets it to `math.inf` before it queues a sentence.
   - `_speech_loop` (`:197-212`), in its `finally`, sets it to
     `time.monotonic()` once `self._speech.empty()`.

   So it is "now or later" while anything is queued or playing, and the
   return of the last `pw-cat` otherwise.

2. **When the capture started.** `_record`'s `watch` (`:247-250`) already
   sees each frame's level. It is the same `_level(chunk)` that
   `record_utterance` compares with `silence_level`
   (`listen_local.py:264-268`). `watch` stores `self._onset =
   time.monotonic()` on the first value above `config.silence_level`, and
   `_record` resets it to `None` on entry. `listen_local` does not change.

3. **What she said.** Add `self._said: list[str]`. `_say` appends every text,
   whether it is engine or model speech. `_answer` (`:379`) clears it on
   entry. With barge_in off, the latest playback before any capture is
   exactly what was said since the latest `_answer` began.

4. **The model is out of consent** (`claude_backend.py`):
   - In `_decide`, before the `mcp__omarchy__` allow (`:397`), deny
     `mcp__omarchy__confirm_last`. The message: "Only the user can release
     a held action, by voice or key; the engine hears it, not you. Do not
     call this." This also covers `cli ask`, whose tty prompt releases holds
     itself (`cli.py:153-179`).
   - The Claude Code hold message (`:419-421`) and
     `executor.confirm_instruction` (set in `_options` after
     `build_server`, `:544`, overriding `mcp_server.py:120-124`) both
     become: "…needs the user's confirmation, which they give the engine
     directly. Stop here. Say it is waiting; do not ask them to confirm and
     do not say confirm, go ahead or yes do it."

   `cancel_last` stays allowed, because it only makes things safer.

5. **The consent step.** Add `async def _consent(self, text, onset) -> bool`.
   It returns True when it consumed the utterance. It is called in three
   places:
   - `_turn`, before `_answer` (`:289`), with `self._onset`;
   - `_wake_turn`'s one-breath path, before `_answer(rest, …)` (`:337`),
     with that capture's onset;
   - `_inject`, before it spawns (`:630`), with `onset=None`, meaning typed.

   Rules, in order:
   - Nothing `_held()`, or no match → False. The turn goes on as today.
   - Match `confirm_words` and `cancel_words` with `allow_negation=False`.
   - Confirm from audio, in this order: barge_in on → say BARGE. Otherwise,
     `onset - _voice_until < config.spoken_confirm_guard_seconds` → say
     SOON. Otherwise, `_normalize(phrase)` is a substring of
     `_normalize(s)` for any `s` in `_said` → say SELF. Substring, not
     word, so "confirming" counts too.
   - Cancel from audio: SOON under the same timing rule.
   - Accepted confirm → `await self._release(wait=True)`. Accepted cancel →
     `await self._local_cancel()`, then say "Cancelled. {held} was not run."
   - Every path is logged: `consent  refused (too soon|said it|barge-in)
     {text!r}` or `confirm spoken release: {held}`.

6. **One release path.** Move the body of `_local_confirm` (`:633-652`) into
   `_release(wait: bool)`.
   - The keybind calls `wait=False` and is unchanged: it spawns the release
     turn.
   - The spoken path awaits `_answer(text, release=held)`, so with barge_in
     off the mic stays shut through the release turn and its echo tail.
   - For `executor.pending`, the spoken path also says the outcome ("Done."
     or the first sentence of the failure, as `_run_route` does,
     `:500-510`), and writes `brain.note` a line so the model knows.

7. **What she says.** The prompt at `:455` becomes the Q1 line. With
   barge_in on it becomes "{held} is waiting for you. Use the key to run it,
   or say cancel." The `_settle` notification body (`:192`) gains 'Say
   "{confirm_words[0]}", or press the confirm key.' With barge_in on it
   gains only the key. New module constants:
   - SOON = "I was still talking. Say it again."
   - SELF = "I said that word myself, so I cannot take it from the room. Use
     the key."
   - BARGE = the Q4 line.

   None of these fixed lines contains any default confirm phrase. A test
   checks that.

8. **Config.** Add `spoken_confirm_guard_seconds: float = 1.0`
   (`config.py`, next to `barge_in` at `:302`), with a comment: measured
   from the return of the last `pw-cat`, which already includes the 0.35 s
   echo tail. It is a room calibration knob. Raise it if the log shows
   `consent refused` lines that were echoes.

No change to `listen_local.py`, `session._matches`, whisper's vocabulary,
`realtime.py`, or the #76 release-turn contract.

## Alternatives rejected

- **"If the transcript is a confirm word, call `_local_confirm`" (naive).**
  In the table it releases on every echo case.
- **B, lexical on the model's text only, keeping "Say confirm, or cancel."**
  It leaves the engine's own prompt as the one voice that says the word. E1′
  and E1″ release.
- **A alone.** It rests entirely on the room and on `pw-cat`'s exit time.
  E1′ and E2′ release.
- **Letting the model decide through a `confirm_last` that knows brain
  holds.** That makes consent as good as the model's report of the words,
  which is the realtime engine's weakness (Q7), brought to the one engine
  that can avoid it.
- **Wake word plus confirm.** It defaults off and is not enforced against
  her own speech (Q1).
- **Keybind-only tier.** Not needed by any case (Q3). The approver can pick
  it instead.
- **Echo-cancel detection for barge_in.** Nothing can detect it today (Q4).
- **Adding confirm words to whisper's prompt.** Forbidden by the intent: it
  makes the word likelier to appear from noise.

## Risks

- **Friction (R2, R3, R5).**
  - A user who answers within 1.0 s of her last syllable is asked again.
  - A model that says "confirm" despite its instruction forces the key for
    that turn.
  - barge_in users must use the key.

  All three are spoken refusals, and the guard is a knob. This affects every
  host running the local engine with the Claude brain.
- **Her prompt no longer says the word.** Someone across the room who cannot
  see the screen has to know it already. It is the default "confirm", and it
  is documented.
- **`tts_command`.** When it is set, `_speak_now` runs it with
  `subprocess.run` (`feedback.py:139-142`). A command that backgrounds its
  own playback returns early, so A's clock starts too soon. B′ still holds
  in that case; A does not. Document it next to the knob.
- **Speech into an open mic that already exists today.** `_inject` and the
  keybind's `_local_confirm` start `_answer` in the background while
  `_listen_loop` may be recording (`:630`, `:650`). With barge_in off, that
  speech still reaches an open mic. A catches it for consent, because
  `_voice_until` is `inf` while she plays, so any onset is too soon. For
  ordinary turns it stays as it is. Worth its own issue; the lead decides.
- **A hallucinated "Confirm."** One that arrives past the guard, when she
  never said the word, still releases (¹). That is today's phantom-utterance
  risk, unchanged.
- **The `confirm_last` denial applies to every `ClaudeBrain`**, including
  `cli ask` and verify-gate. No flow there relies on the model confirming:
  cli's tty prompt releases holds itself.

## Verification

New tests in `tests/test_local_engine.py`, class `SpokenConsentTests`. They
use `ScriptedBrain` and a fake microphone that reports onset through
`on_level` after a set delay, with a fake whisper and a fake mouth, as in the
demonstration. Each asserts on `brain.allowed` or `executor.pending`, what
was spoken, and the log line:

1. E1: the prompt echo "Confirm." at mic open → nothing runs, SOON is
   spoken, the hold remains.
2. E2: the reply ends "Confirm?", echo at mic open → nothing runs. E2′: the
   reply ends "Go ahead?", echo past the guard → nothing runs, SELF is
   spoken.
3. E1″: the held command contains "confirm", late "Confirm." → nothing runs.
4. E3: barge_in on, gated mouth, "Confirm." during playback → nothing runs,
   BARGE is spoken.
5. E4: "cancel" at mic open → the hold remains.
6. R1: the user says "confirm" past the guard → the held call runs exactly
   once through the release turn, `_approved` is spent, and the mic did not
   reopen before the release turn ended.
7. R1 for `executor.pending` (`omarchy_cli reboot`) → released, and the
   outcome is spoken.
8. R4: "cancel" past the guard → cancelled and spoken. "don't confirm" →
   not consumed, goes to the model.
9. Typed `listen say confirm` → released with no timing check.
10. No hold, "confirm" → goes to the model as today.
11. No fixed engine line (prompt, SOON, SELF, BARGE, NOT_CAUGHT) contains a
    default confirm phrase.

In `tests/test_claude_backend.py`:

- `_pre_tool_use` denies `mcp__omarchy__confirm_last`;
- neither the hold message nor `executor.confirm_instruction` tells the model
  to ask for or call a confirmation.

Existing suites stay green unchanged, apart from wording asserts on the old
prompt: `HoldTests` (`test_local_engine.py:491`) and `ReleaseTurnTests`
(`:393`).

Then:

```
DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent nix flake check --no-write-lock-file
```

Fakes only: no microphone, whisper, speakers or live desktop.
