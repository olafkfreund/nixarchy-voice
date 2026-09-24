---
status: approved
issue: 113
spec: spec/2026-09-24-113-realtime-echo-consent.md
---

# Plan: on the realtime engine, only the confirm key releases a held action

Closes #113. Every file:line below was checked on `origin/main` at fc33ae2.
Branch: `fix/113-realtime-echo-consent`.

## Approved decisions, carried over from the spec

You can implement from this list without opening the intent or the spec.

1. **On realtime, only the confirm key releases a hold.** The confirm key
   means the bar widget, the keybind, or `omarchy-voice listen confirm`. All
   three go through `_local_confirm` (`realtime.py:955-963`), which is not
   changed. Nothing the model reports releases a hold.
2. **`confirm_last` stays in `GATE_TOOLS` and is always refused.** With
   something held, `_confirm` logs
   `reject  confirm_last <phrase>: only the key releases <held>` and returns
   `ERROR: <held> is still held. Only the user can release it, with the
   confirm key (bar widget or `omarchy-voice listen confirm`). Tell them to
   press it. Do not call confirm_last again.` With nothing held, it returns
   the current "nothing is waiting" error. It never calls
   `executor.run_pending()` or `_matches`. The tool stays so that the model
   has somewhere to put "they said yes" and gets a refusal that names the key.
   It also keeps the wire schema unchanged (`tests/test_realtime_wire.py:136`).
   Removing the tool was rejected.
3. **The guard lives in `_confirm` only.** Both `confirm_last` paths call
   it: `_on_response_done` and `_dispatch` (`realtime.py:1280-1281`).
4. **The bookkeeping goes.** `created_hold` goes (`1182`, `1191`, `1205`),
   and so does `_user_turn_since_hold` (set at `446`, `937`, `981`, `1206`,
   read only at `1191`). The `confirm_last` branch of `_on_response_done`
   (`1190-1199`) becomes `output = await self._confirm(str(args.get("heard_phrase", "")))`.
   The `_matches` import (`realtime.py:42`) goes too. Its only user in the
   file is `_confirm` (`1302`).
5. **Spoken cancel stays model-driven and unchanged.** `cancel_last` and
   `_cancel` are not touched. The worst case is her echo dropping a hold.
   Nothing runs, and the user asks again.
6. **The gated tool's reply asks for the key.** Add a module constant
   `HOLD_INSTRUCTION` next to `GATE_TOOLS`: `"is held until the user presses
   the confirm key (the bar widget or `omarchy-voice listen confirm`). You
   cannot release it. Say what is held and ask them to press the confirm key;
   do not ask them to say anything."`. In `RealtimeSession.__init__`, right
   after the executor is built (`realtime.py:421-424`), set
   `self.executor.confirm_instruction = f"This action {HOLD_INSTRUCTION}"`.
   That replaces the default "needs spoken confirmation … confirm out loud"
   (`tools.py:1758-1760`). The pattern is the one at `claude_backend.py:562`.
   `claude_backend.HOLD_INSTRUCTION` (`claude_backend.py:72-75`) is a
   different text for a different engine, so do not import it.
7. **Persona block.** Rewrite `# Confirmations` in `REALTIME_PERSONA`
   (`realtime.py:240-258`). It says: stop and do not route around the gate.
   Say what is held. Ask the user to press the confirm key (bar widget or
   `omarchy-voice listen confirm`). Do not ask them to say "confirm". Do not
   call `confirm_last` because they said yes, because the machine refuses it.
   If they decline or change the subject, call `cancel_last`.
8. **Tool description.** The `confirm_last` description in `GATE_TOOLS`
   (`realtime.py:263-286`) becomes: "Always refused on this engine; only the
   confirm key releases a held action. The refusal tells you what to say to
   the user." The `heard_phrase` parameter stays, so the schema is unchanged.
   The shared `PERSONA` line (`persona.py:26`) stays, because the local
   engine uses it too.
9. **Notification.** In `_settle` (`realtime.py:1269-1276`), the "Waiting for
   confirmation" body becomes `f"{held}\nPress the confirm key."`, as in
   `local_engine.py:214`.
10. **README** (`README.md:865-873`). Replace the four-item realtime list
    (same batch, new turn, phrase match, local confirm) with this: on the
    realtime engine, only the bar widget, the keybind or
    `omarchy-voice listen confirm` releases a hold. A spoken confirm is
    refused, and the model is told to point at the key. A spoken cancel still
    works. The local-engine paragraph at `875` is unchanged.
11. **Unchanged:** `_local_confirm` / `_local_cancel` (`955-971`), `_cancel`,
    `ECHO_TAIL_SECONDS` and the mic gate (`52`, `770-786`), `barge_in`,
    `local_engine.py`, `claude_backend.py`, `mcp_server.py`, `config.py`,
    `persona.py`, `tools.py`. `realtime_transcribe_model` stays off by
    default. Nothing new is metered.
12. **Out of scope:** whether realtime stays supported at all. That is
    issue #121 and is not decided here. This change neither removes nor
    deprecates the engine.

## Landing order

The code change touches only `src/omarchy_voice/realtime.py`,
`tests/test_realtime.py` and `README.md:865-873`. It is independent of
#120, #114, #112 and #109 and can land before or after any of them. If one
of them lands first and moves lines, rebase and re-check the line numbers
with the `grep` in step 0. The logic does not depend on them.

## Steps

Every command runs from the repo root with
`export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`. Only fakes are
used: `FakeSocket` and `RealtimeSessionTests` from `tests/test_realtime.py`,
and `Config(dry_run=True, notify=False)`. There is no OpenAI connection,
microphone or speaker.

0. **Baseline.** `git switch fix/113-realtime-echo-consent && git rebase origin/main`.
   Then `nix develop -c python3 -m pytest -q tests` and
   `nix develop -c env PYTHONPATH=src python3 -m unittest discover -s tests`.
   → verify both pass. At fc33ae2 both give **1075** tests (pytest adds
   "709 subtests passed"). Record the numbers. Also run
   `grep -n "_user_turn_since_hold\|created_hold\|_matches" src/omarchy_voice/realtime.py`
   → verify you get exactly `42, 446, 937, 981, 1182, 1191, 1205, 1206, 1302`.
   A new hit is a new reader. Stop and re-plan if there is one.

1. **`tests/test_realtime.py`: add the red test first.** Add a helper and
   `test_her_echo_cannot_release_a_hold` to `RealtimeSessionTests`. The
   sequence is:
   - `_on_response_done` with one `omarchy_cli {"command": "reboot"}` call,
     so the hold is created the real way;
   - `_on_event({"type": "response.output_audio_transcript.done", "transcript": "Reboot is held. Confirm?"})`;
   - `_on_event({"type": "input_audio_buffer.speech_started"})`, which is her
     echo as a VAD turn;
   - `_on_response_done` with `confirm_last {"heard_phrase": "Confirm?"}`.

   Assert that `executor.pending` is not `None` and that the last
   `function_call_output` contains `"confirm key"`.
   → verify it **FAILS on main** with `AssertionError: unexpectedly None`,
   because the hold was released. This was checked at fc33ae2 with the same
   sequence minus the transcript event.

2. **`realtime.py` `_confirm` (`1289-1310`): never release** (decisions 2
   and 3). Rewrite the docstring to say the model's word never releases on
   this engine and only `_local_confirm` does.
   → verify step 1's test passes.

3. **`realtime.py`: remove the bookkeeping** (decision 4). Delete the
   `created_hold` lines, the whole `if created_hold or not
   self._user_turn_since_hold` branch, the four `_user_turn_since_hold`
   writes and the `_matches` import. Keep the surrounding `_tool_rounds` /
   `_rate_limit_retries` resets at `938-939` (`_inject`) and `982-984` (`speech_started`).
   → verify that the step 0 `grep` returns nothing and that
   `nix develop -c python3 -m pyflakes src/omarchy_voice/realtime.py`
   (or `python3 -m py_compile`) is clean.

4. **`realtime.py`: `HOLD_INSTRUCTION`, `__init__`, persona, tool
   description** (decisions 6-8).
   → verify with `python3 -c` that
   `RealtimeSession(Config()).executor.confirm_instruction` contains
   "confirm key" and not "out loud". Also check that
   `json.dumps(realtime.to_realtime_tools())` still has the same
   `confirm_last` `parameters` as before.

5. **`realtime.py` `_settle` (`1274`): the notification names the key**
   (decision 9). → verify by the test in step 7.

6. **`tests/test_realtime.py`: rewrite the tests that assert the old
   behaviour.** Replace, don't delete without a replacement:
   - `test_a_real_confirmation_phrase_releases_it` (`126-131`) and
     `test_confirmation_phrase_inside_a_sentence_counts` (`133-138`) assert a
     spoken release. Merge them with
     `test_wrong_phrase_does_not_release_the_gate` (`117-124`) and
     `test_dont_confirm_does_not_release_the_gate` (`140-145`) into one test,
     `test_no_spoken_phrase_releases_a_hold`. Use subTests over
     `["confirm", "yes do it please", "go ahead", "don't confirm", "ok", ""]`.
     Each one returns `ERROR:` containing "confirm key", and `pending` is
     still set.
   - `test_same_batch_confirm_last_does_not_release_the_gate` (`182-197`):
     it is still held. The asserted text changes from `"new user turn"` to
     `"confirm key"`.
   - `test_typed_turn_is_injected_as_a_user_message`: drop the
     `_user_turn_since_hold` assertion (`345`). Otherwise it now fails with
     `AttributeError`.

   → verify these pass. Keep `test_confirming_nothing_is_an_error`
   (`147-149`), `test_cancel_drops_the_held_action` (`151-157`),
   `test_local_confirm_releases_without_a_model_phrase` (`347-351`) and
   `test_gate_tools_are_realtime_only` (`64-68`) byte-for-byte. They must
   still pass unedited.

7. **`tests/test_realtime.py`: new tests.**
   - `test_the_key_releases_exactly_once_after_an_echo`: step 1's sequence,
     then `_local_confirm()`. Its output has no `ERROR:` and contains
     `[dry-run]`. `pending` is `None`. `"CONFIRM omarchy reboot"` appears
     once in `executor.transcript`. A second `_local_confirm()` returns
     `"nothing to confirm"`.
   - `test_cancel_still_drops_a_hold_after_an_echo`: hold, `speech_started`,
     then `_on_response_done` with `cancel_last`. The output contains
     "Cancelled" and `pending` is `None`.
   - `test_the_hold_reply_asks_for_the_key`: `_dispatch("omarchy_cli",
     {"command": "reboot"})` contains "confirm key" and not "out loud".
   - `test_persona_does_not_invite_a_spoken_confirm`: the `# Confirmations`
     block of `REALTIME_PERSONA` contains "confirm key". It does not tell the
     model to call `confirm_last` when the user agrees, so check that there is
     no `heard_phrase` and no "call `confirm_last`" in it.
   - `test_waiting_notification_names_the_key`: patch
     `self.session.feedback.notify`, hold, call `_settle()`, and assert that
     the body ends with `"Press the confirm key."`.

   → verify that all pass.

8. **`README.md:865-873`: replace the list** (decision 10). → verify that
   `grep -n "new user turn\|same\*\* response" README.md` returns nothing.

9. **Full run.** Run `nix develop -c python3 -m pytest -q tests` and
   `nix develop -c env PYTHONPATH=src python3 -m unittest discover -s tests`.
   → verify both are green in both runners. The expected count is baseline
   − 3 + 6 = **1078**: steps 6 and 7 remove 4 tests, add 1 merged test and 6
   new ones, including step 1's. Then run
   `nix flake check --no-write-lock-file` → verify it passes (its
   `checks` run `pytest tests -q`, `flake.nix:145`).

## Mutation checks

Apply each mutation by hand, run `nix develop -c python3 -m pytest -q
tests/test_realtime.py`, and revert. Each must turn at least the named test
red.

| Mutation | Must fail |
| --- | --- |
| In `_confirm`, call `run_pending()` again when `_matches(...)` is true | `test_her_echo_cannot_release_a_hold`, `test_no_spoken_phrase_releases_a_hold` |
| Drop the `confirm_instruction` assignment in `__init__` | `test_the_hold_reply_asks_for_the_key` |
| Restore the old step 3 ("call `confirm_last` with `heard_phrase`…") in the persona | `test_persona_does_not_invite_a_spoken_confirm` |
| `_settle` notifies with just `held` | `test_waiting_notification_names_the_key` |
| `_local_confirm` returns early without `run_pending` | `test_the_key_releases_exactly_once_after_an_echo`, `test_local_confirm_releases_without_a_model_phrase` |
| `_cancel` returns without `drop_pending` | `test_cancel_still_drops_a_hold_after_an_echo`, `test_cancel_drops_the_held_action` |

## Tests

```
export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent
nix develop -c python3 -m pytest -q tests                          # 1078 passed
nix develop -c env PYTHONPATH=src python3 -m unittest discover -s tests   # Ran 1078, OK
nix flake check --no-write-lock-file                               # passes
```

## Rollback

The change is one code commit, with no config, schema or state migration.
Undo it with `git revert <sha>`. That restores the phrase-matched spoken
confirm on realtime, including the echo hole. No user data or config key
depends on this change.
