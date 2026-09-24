---
status: approved
issue: 86
spec: spec/2026-09-24-86-spoken-confirm.md
---

# Plan: the engine hears "confirm", and nothing she said can be it

Closes #86. Every line number below was checked against `origin/main` at
`93c6dac`, and each one matches the spec. This branch holds only docs commits
on top of `93c6dac`. Where the spec was silent or had a gap, this plan says
how it resolves it, under "Resolved ambiguities". Those resolutions are for
the approver to accept or reject at this gate.

## Approved decisions, carried over from the spec

1. **The engine decides consent, not the model.** While something is held
   (`LocalSession._held()`, `local_engine.py:176-185`), a transcript that is a
   whole-utterance match for `confirm_words` or `cancel_words` is consumed
   before the router and before the model. It is matched with
   `session._matches(..., allow_negation=False)` (`session.py:30-51`). A
   matched utterance never reaches the model, whether it is accepted or
   refused. The router already steps aside while something is held
   (`local_engine.py:473`).
2. **Safeguard A (timing).** A spoken confirm or cancel from audio is refused
   when `onset - _voice_until < config.spoken_confirm_guard_seconds`
   (default **1.0 s**):
   - `onset` is the time of the first mic frame above `silence_level`.
   - `_voice_until` is the time the last `pw-cat` returned. It is `math.inf`
     while anything is queued or playing.
3. **Safeguard B′ (lexical, everything she said).** A spoken confirm is
   refused when `_normalize(phrase)` is a **substring** of `_normalize(s)` for
   any `s` in `_said`. `_said` holds everything spoken since the latest
   `_answer` began: the model's sentences, the engine's own lines, and the
   held description spoken in the prompt. Because it is a substring test,
   "confirming" counts.
4. **Safeguard D.** No spoken confirm while `barge_in` is on. It is refused
   out loud with BARGE. Spoken cancel still works then, under A.
5. **Check order for a confirm from audio:** D (BARGE), then A (SOON), then
   B′ (SELF). **For a cancel from audio: A only** (SOON). B′ cannot apply,
   because the prompt must say "cancel". D does not apply, because cancelling
   is safe.
6. **The spoken prompt no longer names the word.** The word moves to the
   screen.
   - `local_engine.py:455` becomes `"{held} is waiting for you. Say the word
     on the screen to run it, or cancel."`
   - With barge_in on, it becomes `"{held} is waiting for you. Use the key to
     run it, or say cancel."`
   - The `_settle` notification body (`:192`) gains `Say
     "{confirm_words[0]}", or press the confirm key.`
   - With barge_in on, the notification gains only the key.
7. **Fixed lines** (new module constants in `local_engine.py`):
   - `SOON = "I was still talking. Say it again."`
   - `SELF = "I said that word myself, so I cannot take it from the room. Use the key."`
   - `BARGE = "With barge-in on I cannot tell your voice from mine. Use the key."`
   - The accepted cancel line is `"Cancelled. {held} was not run."`

   No fixed engine line contains a default confirm phrase, and a test checks
   that.
8. **One release path.** The body of `_local_confirm` (`:633-652`) moves into
   `_release(wait: bool)`.
   - The keybind calls it with `wait=False`, and its behaviour does not
     change.
   - The spoken path calls it with `wait=True`. It awaits `_answer(text,
     release=held)`, so with barge_in off the mic stays shut through the
     release turn and its echo tail.
   - For an `executor.pending` hold, the spoken path also says the outcome:
     "Done.", or the first sentence of the failure, as `_run_route` does
     (`:500-510`). It also writes a `brain.note` line.
9. **Our own tools' holds (`executor.pending`) use the same engine path.** The
   brain's gate denies `mcp__omarchy__confirm_last`, so the model cannot
   release anything.
   - The deny goes in `ClaudeBrain._decide`, before the `mcp__omarchy__`
     allow (`claude_backend.py:397`).
   - Its message: "Only the user can release a held action, by voice or key;
     the engine hears it, not you. Do not call this."
   - The denial applies to every `ClaudeBrain`, including `cli ask`, whose
     tty prompt releases holds itself (`cli.py:153-179`), and verify-gate.
   - `cancel_last` stays allowed.
10. **The model's hold instructions stop asking for the word.**
    - The Claude Code hold message (`claude_backend.py:419-421`) becomes:
      `"{description!r} needs the user's confirmation, which they give the
      engine directly. Stop here. Say it is waiting; do not ask them to
      confirm and do not say confirm, go ahead or yes do it."`
    - `executor.confirm_instruction` gets the same sentence, without the
      description. It is set in `ClaudeBrain._options` after `build_server`
      (`:544`), which overrides `mcp_server.py:120-124` for this brain only.
      `Executor`'s own default (`tools.py:1687-1689`) is unchanged, because
      the realtime engine still uses it.
11. **Typed turns** (`listen say confirm`, `_inject`, `:619-631`) go through
    the same consent step with `onset=None`. A, B′ and D do not apply to them:
    it is the user at the keyboard, the same trust as `listen confirm`.
12. **Q3: no keybind-only tier.**
13. **Q4: echo-cancel detection is not attempted.**
14. **Q7: the realtime engine is out of scope.** `realtime.py` is not
    touched, and the lead filed it as **#113** (open).
15. **Config.** Add `spoken_confirm_guard_seconds: float = 1.0` next to
    `barge_in` (`config.py:302`). Its comment says:
    - it is measured from the return of the last `pw-cat`, which already
      includes the 0.35 s echo tail;
    - it is a room calibration knob: raise it if the log shows `consent
      refused` lines that were echoes;
    - a `tts_command` that backgrounds its own playback returns early, so A
      starts too soon. B′ still holds in that case (`feedback.py:139-142`).
16. **Logging.** Every path writes one line:
    - `consent  refused (too soon|said it|barge-in) {text!r}`
    - `confirm spoken release: {held}`
    - `cancel  {held}`
17. **Not changed:** `listen_local.py` (the onset comes from the existing
    `on_level` callback, `listen_local.py:266-268`), `session._matches`,
    whisper's vocabulary, `realtime.py`, and the #76 release-turn contract.

**Rejected, and not to be reintroduced while implementing:**
- the naive design, which releases on any match;
- B alone, which checks only the model's text;
- A alone;
- letting the model decide through a `confirm_last` that knows brain holds;
- wake word plus confirm;
- a keybind-only tier;
- echo-cancel detection;
- adding confirm words to whisper's prompt.

### The demonstration table (approved; every row becomes a test)

E is her own voice and R is the user. "At mic open" means the onset is 0 s
after the capture opens, which is 0.35 s after playback ends. "Late" means the
onset is 1.0 s after the capture opens, which is 1.35 s after playback ends.

| Row | Case | Expected under A+B′+D | Test (`SpokenConsentTests`) |
| --- | --- | --- | --- |
| E1 | prompt echo "Confirm.", at mic open | refused A | `test_e1_echo_at_mic_open_is_too_soon` |
| E1′ | "Confirm.", late, the new prompt has no "confirm" | RAN (residue ¹) | `test_e1p_late_confirm_is_taken_residue` |
| E1″ | late "Confirm.", held command `git commit -m confirm && reboot` | refused B′ | `test_e1pp_held_command_names_the_word` |
| E2 | reply ends "Confirm?", echo at mic open | refused A | `test_e2_reply_confirm_echo_at_mic_open` |
| E2′ | reply ends "Go ahead?", echo late | refused B′ | `test_e2p_reply_go_ahead_echo_late` |
| E3 | barge_in on, echo while she is still playing | refused D | `test_e3_barge_in_echo_while_playing` |
| E4 | prompt tail "cancel", at mic open | kept | `test_e4_cancel_echo_at_mic_open_keeps_hold` |
| R1 | user "confirm" after a pause | RAN | `test_r1_user_confirm_after_pause_runs_once` |
| R2 | user "confirm" 0.2 s after mic open | refused A ² | `test_r2_user_confirm_too_soon_is_asked_again` |
| R3 | user "confirm", her reply said "Please confirm." | refused B′ ² | `test_r3_model_said_the_word_forces_the_key` |
| R4 | user "cancel" after a pause | cancelled | `test_r4_user_cancel_after_pause` |
| R5 | barge_in on, user "confirm" after she stopped | refused D ² | `test_r5_barge_in_user_confirm_refused` |

¹ A "Confirm." that arrives past the guard when she never said the word is
either the user or a whisper hallucination. No safeguard claims to tell them
apart. This is today's phantom-utterance risk, unchanged.

² The cost of the design. Each refusal is spoken, with a way forward, and none
of them runs anything.

"RAN" means the held `reboot` passed the real `_pre_tool_use` in a release
turn. For an `executor.pending` hold it means `run_pending` ran.

## Resolved ambiguities (flagged for the approver)

1. **A fallback line that would trip B′.** `WarmBrain.ask_stream` yields
   `"That needs confirmation."` when the model said nothing and something is
   held (`claude_backend.py:882`). Its normalised text contains "confirm". So
   under B′, after a silent hold turn, the user's own late "confirm" would be
   refused with SELF. That breaks R1 exactly when the model is terse.
   **Resolution:** line 882 becomes `"That is waiting for you."`, and the
   fixed-lines test covers it. `ClaudeBrain.think`'s `turn.reply` (`:634`)
   is left alone: it is not spoken on the local engine.
2. **A typed confirm must not block the control socket.** `_control` waits
   10 s on `future.result` (`local_engine.py:594-597`). If `_inject` awaited
   `_release(wait=True)` inline, a typed confirm would hold the socket for
   the whole release turn. **Resolution:** `_inject` spawns
   `_typed(text)`, which does `if not await self._consent(text, None): await
   self._answer(text)`. It still returns `"sent"` at once.
3. **"`_answer` clears `_said` on entry".** **Resolution:** it clears it
   right after `async with self._turn_lock:` (`:396`), not before. Otherwise a
   typed turn queued behind the lock would wipe what the running turn is
   still saying.
4. **A clock that sticks at `inf`.** If `_drop_queued_speech` (`:226-233`)
   drains a sentence that `_say` had marked, while the mouth is idle, nothing
   ever resets `_voice_until`. Every later spoken confirm would then be
   refused with SOON. **Resolution:** `_speech_loop` keeps a
   `self._speaking` bool around `_speak_now`. `_drop_queued_speech` sets
   `_voice_until = time.monotonic()` when `not self._speaking`. The loop's
   `finally` sets `_voice_until` **before** `task_done()`, so a `join()`er
   sees the new value.
5. **No echo row is refused by D alone.** A also stops E3, because an onset
   during playback is always too soon. So the "drop D" mutation cannot make
   an *echo* test run the action. **Resolution:** the drop-D mutant must make
   R5 run the action, and must make E3 fail on its assertion that BARGE (not
   SOON) is spoken.
6. **"Echo rows run the action on main".** On main, a *brain* hold cannot be
   released by voice at all: that is #86. So the brain-hold echo tests fail
   on main by their assertions, not by running anything:
   - the refusal line is not spoken;
   - the utterance reaches the model.

   **Resolution:** each echo test runs two subtests.
   - The `executor.pending` subtest fails on main by running the action: the
     scripted model calls `confirm_last` with the transcript, as a real model
     does.
   - Both subtests are shown to run the action at step 5 (naive consent),
     before A, B′ and D are added.
7. **Old-wording asserts.** The spec named `HoldTests` and
   `ReleaseTurnTests`. The actual asserts on the old prompt are at
   `test_local_engine.py:506` (`HoldTests`) and `:761` (`RouterTests`).
   `ReleaseTurnTests` has none. Both of those lines are updated.
8. **Docs.** The spec's risk "it is documented" has no file named for it.
   **Resolution:** README gains one paragraph under the confirmation rules
   (`README.md:864-872`).

## Overlaps and landing order

- **#114 (open): keybind confirm and typed turns play speech into an open
  mic.** `_inject` (`:630`) and the keybind's `_release(wait=False)` still
  spawn `_answer` while `_listen_loop` may be recording. **This plan does not
  fix that.** A still covers consent there, because `_voice_until` is `inf`
  while she plays. Ordinary turns are unchanged. #114 lands **after** #86 and
  builds on `_voice_until` and `_release`.
- **#113 (open): the realtime engine's echo confirm.** It is independent and
  touches `realtime.py` only. There is no conflict.
- **Open branches:**
  - None of them edits `local_engine.py`, `claude_backend.py`,
    `mcp_server.py`, `session.py`, `test_local_engine.py` or
    `test_claude_backend.py`. All four hold docs only on top of `origin/main`,
    and their approved specs were checked.
  - `fix/101-terminal-secrets` plans a `TERMINAL_SECRETS` constant in
    `config.py` next to `DEFAULT_SENSITIVE` (`:192`), plus `tools.py` watch
    paths. #86 adds a field at `:302`, so there is no textual overlap.
  - `fix/97-compose-checks-apps` and `fix/103-cache-eviction` name none of
    #86's files.
  - `feat/82-installed-commands` cites `claude_backend.py:106-123` and
    `mcp_server.py:129` as context only, not edits.
- **Order:** #86 can land in any order relative to #101, #97, #103 and #82.
  Whichever lands second rebases, and no conflict is expected. #114 comes
  after #86.

## Steps

0. **Baseline.** `git fetch origin && git rebase origin/main`, then run both
   suites with `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` exported.
   → Verify by `nix develop --no-write-lock-file -c pytest tests -q` printing
   `988 passed`, and by `nix develop --no-write-lock-file -c python3 -m
   unittest discover -s tests` printing `Ran 988 tests` and `OK`. Both were
   measured at `93c6dac`. Re-run `gh issue view 86` and confirm `OPEN`.
1. **`tests/test_local_engine.py`: add the test fakes, next to `Ears`
   (`:129`).**
   - `OnsetEars(delay)`: a `record_utterance` stand-in. It sleeps `delay`,
     then calls its 5th positional argument (`on_level`) with
     `config.silence_level + 1`, and returns `case.audio`.
   - `EchoBrain(ScriptedBrain)`: a `reply` attribute that its `_turn`
     yields in place of `"ok."`. A `hold_kind` of `"brain"` (the Bash
     `reboot`, as `ScriptedBrain` does) or `"executor"` (an
     `mcp__omarchy__omarchy_cli {"command": "reboot"}` hold parked in
     `executor.pending`). For an utterance it does not know, the executor
     kind calls `mcp__omarchy__confirm_last {"phrase": text}` through
     `_pre_tool_use`. If that is allowed and the phrase `_matches`
     `confirm_words`, it runs `executor.run_pending()`. That is what
     `mcp_server.call_tool` does on main, minus `CONFIRM_DELAY`. It records
     tool names in `allowed`.
   - The class uses a scaled clock: patch `local_engine.ECHO_TAIL_SECONDS`
     to 0.035 and set `spoken_confirm_guard_seconds=0.1`. Then "at mic open"
     is a delay of 0 s, "late" is 0.1 s and R2 is 0.02 s, the same ratios as
     0.35 s, 1.0 s and 1.35 s. The field does not exist yet, so the tests set
     it with `setattr` on the built config. That keeps step 3 red for the
     right reason.

   → Verify by the existing 988 tests still passing (the fakes only).
2. **`tests/test_local_engine.py`: add `SpokenConsentTests`, one test per
   table row, named as in the table.**
   - Each echo test (E1, E1″, E2, E2′, E3, E4) runs `subTest(hold="brain")`
     and `subTest(hold="executor")`.
   - Each test asserts: whether the action ran (`brain.allowed` or
     `executor.pending`), what was spoken (`mouth.spoken`), that a consumed
     utterance is absent from `brain.asked`/`_turn` inputs, and the log line.
   - Also add these, from spec Verification items 6 to 10:
     - `test_r1_executor_hold_released_and_outcome_spoken` (`omarchy_cli
       reboot`);
     - `test_dont_confirm_is_not_consumed`;
     - `test_typed_confirm_releases_without_timing`, which also asserts that
       `_inject` returns `"sent"` before the release turn ends;
     - `test_no_hold_confirm_goes_to_the_model`;
     - `test_r1_after_a_silent_hold_turn`, where the model says nothing, so
       the fallback line is what she said (ambiguity 1);
     - `test_dropped_speech_does_not_stick_the_clock` (ambiguity 4);
     - `test_one_breath_wake_confirm_goes_through_consent` (`_wake_turn`,
       `:337`);
     - `test_no_fixed_line_names_a_confirm_phrase`, which covers
       NOT_CAUGHT, SOON, SELF, BARGE, both prompt templates with `held="x"`,
       and `WarmBrain`'s fallback at `claude_backend.py:882`.
   - R1 asserts that the reboot ran exactly once, that `brain._approved is
     None`, that `ears.captures == 1`, and that `session._tasks` is empty when
     `_turn()` returns. That last one means the release turn was awaited, not
     spawned.

   → Verify by running `pytest tests/test_local_engine.py -q -k
   SpokenConsent` on unchanged code. **Every echo test fails.** Each
   executor subtest fails because `reboot` ran through the model's
   `confirm_last`. Each brain subtest fails on the missing refusal line or
   the utterance reaching the model. R1, R4 and the typed test fail (nothing
   released). No-hold and "don't confirm" pass. Record the list in the PR.
3. **`tests/test_claude_backend.py`: add the gate tests**, using the
   existing `verdict()` helper (`:42-54`):
   - `test_confirm_last_is_denied` (`behavior == "deny"`, the message says the
     engine hears it);
   - `test_hold_message_does_not_ask_for_the_word`: for a held Bash `reboot`,
     the message contains no confirm phrase and not "ask them to confirm";
   - `test_confirm_instruction_does_not_ask_for_the_word`: after
     `_options()`, `executor.confirm_instruction` has no `confirm_last` and no
     phrase.

   Also add, in `tests/test_config.py`,
   `test_spoken_confirm_guard_defaults_to_one_second`. → Verify by all four
   failing on unchanged code.
4. **`src/omarchy_voice/config.py:302`: add `spoken_confirm_guard_seconds:
   float = 1.0`, with the decision 15 comment.** → Verify by the config test
   passing, and by `Config().unknown_keys` still being empty for a
   `[ears] spoken_confirm_guard_seconds = 1.5` file.
5. **`src/omarchy_voice/local_engine.py`: naive consent, to reproduce the
   table's "naive" column.**
   - Add `_release(wait)`, which moves the `_local_confirm` body there;
     `_local_confirm` becomes `return await self._release(wait=False)`.
   - Add `_consent(text, onset)` with the match and the accept paths only.
   - Add the three call sites: `_turn` before `:289`, `_wake_turn` before
     `:337`, and `_inject` through `_typed` (ambiguity 2).
   - Add the onset capture: `self._onset` is reset in `_record` and set in
     `watch` (`:247-250`).

   → Verify by R1, R4, typed, no-hold and "don't confirm" passing, and by
   **every echo test failing with the action RAN** (both subtests). Record
   the output. This is the demonstration's naive column. Do not commit this
   state alone.
6. **`local_engine.py`: add A.**
   - `import math`, and `self._voice_until = 0.0` in `__init__`.
   - `_say` (`:214-224`) sets `math.inf` before `put`.
   - `_speech_loop` (`:197-212`) handles `_speaking` and sets `_voice_until`
     before `task_done` when the queue is empty.
   - `_drop_queued_speech` gets the ambiguity 4 fix.
   - Add the SOON rule for confirm and for cancel.

   → Verify by E1, E2, E3 (now SOON), E4 and R2 passing. E1″, E2′ and R5
   still fail by running the action, as in the table's A column.
7. **`local_engine.py`: add B′.**
   - `self._said: list[str] = []`. `_say` appends. `_answer` clears it after
     taking the lock (ambiguity 3).
   - The SELF rule uses `session._normalize`.
   - The prompt at `:455` changes to the decision 6 lines, and the SOON,
     SELF and BARGE constants are added.

   → Verify by E1″, E2′ and R3 passing, and by E1′ **running** (the residue
   row). R5 still runs.
8. **`local_engine.py`: add D.** BARGE is checked first for a confirm from
   audio, and the barge_in prompt variant is added. → Verify by E3 (asserting
   BARGE) and R5 passing. The whole `SpokenConsentTests` class is green.
9. **`local_engine.py`: the rest of the release path and the screen.**
   - The spoken outcome and the `brain.note` for `executor.pending`
     (decision 8).
   - The cancel line.
   - The log lines (decision 16).
   - The `_settle` notification body (`:192`, decision 6).

   → Verify by `test_r1_executor_hold_released_and_outcome_spoken`,
   `test_r4_user_cancel_after_pause` and a notification assert
   (`feedback.notify` patched), all passing.
10. **`src/omarchy_voice/claude_backend.py`.**
    - Deny `confirm_last` before `:397`.
    - Change the hold message at `:419-421`.
    - Set `self.executor.confirm_instruction` in `_options` right after the
      `servers` dict (`:536-545`).
    - Change the fallback line at `:882` (ambiguity 1).

    → Verify by the step 3 gate tests, `test_r1_after_a_silent_hold_turn`
    and the fixed-lines test passing, and by the executor subtests of the
    echo rows still passing: the model's `confirm_last` is now denied.
11. **Update the old-wording asserts.**
    - `test_local_engine.py:506` asserts "cancel" and "waiting for you" in
      place of "confirm".
    - `test_local_engine.py:761` asserts "waiting for you" in place of
      "needs confirming".

    → Verify by the full file passing with no other existing test edited.
    `git diff tests/` must show only these two lines changed outside the new
    classes.
12. **`README.md:864-872`: one paragraph on the local engine.**
    - The engine hears confirm itself, and the model cannot call
      `confirm_last`.
    - The spoken prompt says "the word on the screen".
    - `spoken_confirm_guard_seconds` and its `tts_command` caveat.
    - With barge_in on, use the key.

    → Verify by reading the rendered section. It contains no claim the tests
    do not back.
13. **Mutation checks** (see Tests). → Verify by each mutant making its named
    test run the action, then restoring the code with `git checkout -p`, not
    a stash.
14. **Full suites and the flake check** (see Tests). → Verify by the counts
    and `OK`.
15. **Commit** as `fix(local): the engine hears "confirm", and nothing she
    said can be it (#86)`, citing this plan's steps. Any deviation updates
    this file in the same commit.

## Tests

Export `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` for every command.
Use fakes only: no microphone, whisper, speakers or live desktop.

```
nix develop --no-write-lock-file -c pytest tests -q
nix develop --no-write-lock-file -c python3 -m unittest discover -s tests
nix flake check --no-write-lock-file
```

Expected: 988 plus the new tests all pass, with the same count under pytest
and under unittest, and `OK`. The flake check passes.

Mutation checks. Apply each one alone to the finished code, run
`SpokenConsentTests`, then restore:

| Mutant | Must fail, by running the action |
| --- | --- |
| drop A (the SOON rule for confirm returns early as "accepted") | `test_e1_echo_at_mic_open_is_too_soon` (both subtests), `test_r2_…` |
| drop A for cancel | `test_e4_cancel_echo_at_mic_open_keeps_hold` (the hold is cancelled) |
| drop B′ (the SELF rule removed) | `test_e2p_reply_go_ahead_echo_late`, `test_e1pp_held_command_names_the_word` |
| drop D (the BARGE rule removed) | `test_r5_barge_in_user_confirm_refused` runs the action. `test_e3_…` fails on BARGE → SOON (ambiguity 5) |
| allow `confirm_last` again | the executor subtest of `test_e2p_…` |
| B′ over the model's text only (skip the engine's own lines) | `test_e1pp_held_command_names_the_word` |

## Rollback

- Revert the implementation commit.
- `spoken_confirm_guard_seconds` then lands in `Config.unknown_keys`, which
  is reported and not fatal (`config.py:521-524`). Remove it from
  `config.toml` to silence the report.
- There is no migration, no state file and no Nix module change.
- The reverted build is today's behaviour: keybind confirm only for brain
  holds, and the model's `confirm_last` for `executor.pending`.

## Deviations while implementing

None of these changes an approved spec decision.

1. **The test clock is stepped, not scaled.** Under a machine load average
   of 17, the scaled real clock let the at-mic-open rows through: E1, E2, E4
   and R2 ran the action in one of three runs. `SpokenConsentTests` now
   patches `local_engine.time` with a clock that `OnsetEars` moves on by its
   delay. The onsets are the table's own numbers, counted from the return of
   the last `pw-cat` with the 0.35 s tail included: 0.35 (at mic open), 1.35
   (late) and 0.55 (R2). They are judged against the shipped 1.0 s default.
   `ECHO_TAIL_SECONDS` is patched to 0, because the onsets carry the tail.
2. **The "allow `confirm_last`" mutant is caught by a new test.** Because
   of decision 1, a consumed echo never reaches the model, so no echo test can
   see the gate. The mutant is caught instead by
   `test_the_model_cannot_release_an_executor_hold`, which ran the action,
   and by `test_confirm_last_is_denied`. `EchoBrain.phrase` models a model
   that claims the user said "confirm".
3. **The gate tests match the approved wording.** The decision 10 message
   names the phrases in order to forbid them. So the tests check that the old
   "ask them to confirm" instruction is gone and that "do not ask them to
   confirm" is present. They do not check for "no phrase".
4. **Log lines and the cancel line land with their paths.** Each is added
   by the step that writes it (5 to 8). Step 9 kept the executor outcome, the
   `brain.note` and the notification body.
5. **The mutants were restored from a byte copy, checked by sha256.** They
   were not restored with `git checkout -p`: the work was not committed yet,
   and a checkout to HEAD would have discarded it.
6. **`test_barge_in_cancel_still_works` was added.** It backs the README's
   claim from decision 4.
7. **An audio turn with no onset counts as too soon, not as typed.**
   `_heard_at()` returns `-inf`. `record_utterance` always reports one when
   it returns audio, so this never happens in normal use.
8. **The commit is `fix(confirm): …`, as the lead asked,** not `fix(local)`.
