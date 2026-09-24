---
status: approved
issue: 113
intent: intent/2026-09-24-113-realtime-echo-consent.md
---

# Spec: on the realtime engine, only the confirm key releases a held action

Closes #113. Line numbers are from `origin/main` at fc33ae2 and were checked
there.

## The intent's open questions, answered

The intent was approved without answers to its five questions. Each answer
below is a decision made in this spec. **You can reject any of them at this
gate.** If you reject Q1, Q2 and Q3 need real answers, and the design changes.

1. **Port #86's checks, or stop spoken confirm on realtime? Decision: (b).
   Only the keybind or CLI confirm releases a hold on realtime.** The tool
   stays and the engine refuses every call to it, the same way #86 made the
   brain refuse it on the local engine (`claude_backend.py:404-411`). Reasons:
   - (a) still leaves the model as the only thing that heard the user. A,
     B′ and D narrow the hole. They do not close it. A model that mishears
     or lies after the guard window still releases the hold. The approved
     constraint says "The model's word alone never releases a hold", and (a)
     cannot meet that on realtime. The local engine can meet it with the
     same checks because there the engine hears the words itself.
   - (a) needs a clock that does not exist yet: the onset of the user's
     speech mapped to wall-clock time (intent table, row 2). It needs a
     per-turn reply store. For B′ against the user's words it also needs
     transcription, which is metered and off by default. That is three new
     mechanisms for an engine nobody runs here.
   - (b) is a deletion. The demonstration below shows that it closes the
     echo path however the model behaves.
   - Why not (c), removing `confirm_last` from `GATE_TOOLS`: with no tool,
     the model has nowhere to put "they said yes". It would improvise, for
     example by retrying the gated tool, and the prompt already calls that a
     bug (`realtime.py:246-247`). A refusal that names the key is the one
     moment the model is sure to pass the way forward on to the user. The
     intent's outcome requires exactly that: "a refusal that names the
     keybind". Keeping the tool also keeps the wire schema stable
     (`tests/test_realtime_wire.py:136`).
   - Cost: no hands-free confirm on realtime. Today nobody here uses it
     (intent, "Is realtime still used here?").
2. **Whose words does B′ check?** Not applicable: (b) does not check any
   words. The engine does not read the transcript, and
   `realtime_transcribe_model` stays off by default. Nothing new is metered.
3. **Is `speech_started` arrival time good enough as the onset?** Not
   applicable: (b) has no timing check. `ECHO_TAIL_SECONDS` and the mic gate
   (`realtime.py:52`, `770-786`) stay as they are. They still keep her voice
   from starting turns, but consent no longer depends on them.
4. **Spoken cancel. Decision: `cancel_last` stays model-driven, unchanged.**
   Cancel only drops. The worst case is her echo cancelling the user's held
   action. Nothing runs, the log says `cancel  <held>`, and the user asks
   again. Making cancel key-only would make stopping a reboot harder than
   starting one. #86 made the same choice ("cancel_last stays: it is safe",
   `claude_backend.py:405`). The demonstration shows cancel still works.
5. **Keep the realtime engine at all? Decision: a separate issue.** This
   change neither removes nor deprecates realtime. The recommendation, for
   the lead to file: *"Decide whether the OpenAI realtime engine stays
   supported."* Evidence for that issue: unused here since
   2026-09-12 16:26:58 (21 realtime starts, then 37 local); 22
   `session_expired` errors from the 60-minute cap; it needs its own gate
   wording, tests and prompt, separate from the local engine's; and after
   this change it confirms only by key. Options for that issue: keep it,
   mark it experimental in the README, or remove it.

## Design

All the logic changes are in `src/omarchy_voice/realtime.py`.

1. **`_confirm` never releases.** (`realtime.py:1289-1310`.) Both
   `confirm_last` paths call it: `_on_response_done` (`1190-1199`) and
   `_dispatch` (`1280-1281`). One guard here covers both. The new body:
   - With nothing held, it returns the existing "nothing is waiting" error.
   - Otherwise it logs `reject  confirm_last <phrase>: only the key releases
     <held>` and returns
     `ERROR: <held> is still held. Only the user can release it, with the
     confirm key (bar widget or `omarchy-voice listen confirm`). Tell them to
     press it. Do not call confirm_last again.`
   - The `_matches` check and `executor.run_pending()` go. The `_matches`
     import (`realtime.py:42`) goes too; `_confirm` was its only user in
     this file.
2. **Delete the same-batch and new-turn bookkeeping.** It only existed to
   decide whether `confirm_last` could run. The `confirm_last` branch of
   `_on_response_done` (`1190-1199`) becomes one line, `output = await
   self._confirm(...)`. `created_hold` goes (`1182`, `1191`, `1205`), and so does
   `_user_turn_since_hold`, which is set at `446`, `937`, `981` and `1206`.
3. **What the model is told.**
   - Add a module constant `HOLD_INSTRUCTION` next to `GATE_TOOLS`: "is held
     until the user presses the confirm key (the bar widget or
     `omarchy-voice listen confirm`). You cannot release it. Say what is held
     and ask them to press the confirm key; do not ask them to say anything."
     `RealtimeSession.__init__` (`realtime.py:419-422`) sets
     `self.executor.confirm_instruction = f"This action {HOLD_INSTRUCTION}"`.
     This replaces the default "needs spoken confirmation … ask the user to
     confirm out loud" (`tools.py:1758-1760`), which is the text the model
     gets back from the gated tool. This follows the pattern at
     `claude_backend.py:562`.
   - Rewrite the "# Confirmations" block of `REALTIME_PERSONA`
     (`realtime.py:241-258`). It now says: stop and do not route around the
     gate. Say what is held. Ask the user to press the confirm key (bar
     widget or `omarchy-voice listen confirm`). Do not ask them to say
     "confirm", and do not call `confirm_last` because they said yes; the
     machine refuses it. If they decline or change the subject, call
     `cancel_last`.
   - Change the `confirm_last` description in `GATE_TOOLS` (`264-286`) to:
     "Always refused on this engine; only the confirm key releases a held
     action. The refusal tells you what to say to the user." The
     `heard_phrase` parameter stays, so the schema is unchanged.
   - The shared `PERSONA` line "when a tool tells you an action needs spoken
     confirmation" (`persona.py:26`) stays. The local engine uses it too, and
     it still reads correctly as "ask when a tool holds something".
4. **The notification names the key.** In `_settle` (`1269-1277`), the
   "Waiting for confirmation" body becomes `f"{held}\nPress the confirm
   key."`, as `local_engine.py:214` does when `barge_in` is on.
5. **README** (`README.md:865-873`). Replace the realtime list (same batch,
   new turn, phrase match, local confirm) with: on the realtime engine, only
   the bar widget, the keybind or `omarchy-voice listen confirm` releases a
   hold. A spoken confirm is refused, and the model is told to point at the
   key. A spoken cancel still works. The local-engine paragraph after it is
   unchanged.

Unchanged: `_local_confirm` / `_local_cancel` (`955-971`), `_cancel`,
`local_engine.py`, `claude_backend.py`, `mcp_server.py`, `config.py`, the mic
gate, and `barge_in`.

### Demonstration (fakes only)

This used the `FakeSocket` from `tests/test_realtime.py`, the real
`RealtimeSession`, the real dry-run `Executor`, and the real `_on_event` and
`_on_response_done`. There was no network and no audio, with
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`. "After" is a scratch copy
of `src/` with items 1 and 3 applied. The script: the model holds `omarchy_cli
reboot`. Her reply transcript is "Reboot is held. Confirm?". Then
`speech_started` arrives, which is her echo as a VAD turn, and the model
calls `confirm_last("Confirm?")`. After that, `_local_confirm()`. In a fresh
session: a hold, then `speech_started`, then `cancel_last`.

```
== BEFORE
hold result: ERROR: This action needs spoken confirmation. Stop here and ask the user to confirm out loud; ...
echo confirm_last: [dry-run] would run: omarchy reboot
  pending after echo: None
cancel_last: Cancelled: omarchy reboot. It was not run. | pending: None
== AFTER
hold result: ERROR: This action is held until the user presses the confirm key (the bar widget or `omarchy-voice listen con...
echo confirm_last: ERROR: omarchy reboot is still held. Only the user can release it, with the confirm key (bar widget or `omarch...
  pending after echo: omarchy reboot
keybind: [dry-run] would run: omarchy reboot | pending: None
cancel_last: Cancelled: omarchy reboot. It was not run. | pending: None
```

After the change, the echo no longer releases the hold. The keybind still
does, and cancel still drops it. The existing `test_realtime*.py` suite run
against the patched copy: 95 tests ran and 3 failed. The 3 are exactly the
tests that assert the old behaviour (see Verification).

## Alternatives rejected

- **(a) Port #86's A, B′ and D.** Rejected for the reasons under Q1: the
  model stays the ear, it needs three new mechanisms, and B′ on the user's
  words needs metered transcription.
- **(c) Drop `confirm_last` from `GATE_TOOLS`.** Rejected under Q1: it loses
  the refusal that names the key and invites a route around the gate.
- **Engine-side speech recognition of the user's reply on realtime.** That is
  a second ear next to the model, and it is really the local engine. Out of
  scope. Q5's separate issue can weigh it.
- **Only raise `ECHO_TAIL_SECONDS` or force `barge_in` off.** The mic gate
  is a heuristic based on bytes written, not sound played (intent,
  "What keeps her voice out"). The model's word would still release the
  hold.
- **Key-only cancel as well.** Rejected under Q4.

## Risks

- **Loss of hands-free confirm on realtime.** This is intended. It affects
  only users who set `engine = "openai"`, and none do on this machine. Every
  host that runs realtime gets it; it is not host-specific.
- **The model may still say "say confirm"** from habit or from old
  conversation history. The refusal, the tool result on hold and the
  notification all name the key, so the user is told three ways. A spoken
  "confirm" is harmless now, because nothing listens for it.
- **Echo cancels a hold.** This is unchanged and accepted (Q4). The user
  re-asks, and nothing runs.
- **Tests that asserted spoken release** must be rewritten, not deleted
  without a replacement. See Verification.
- **Deleting `_user_turn_since_hold`** could hide another reader of it.
  `grep` on origin/main shows only the four writes listed and the one read
  at `1191`. The implementer re-checks this.

## Verification

Tests change in `tests/test_realtime.py`. Fakes only.

- Replace `test_wrong_phrase_does_not_release_the_gate`,
  `test_a_real_confirmation_phrase_releases_it`,
  `test_confirmation_phrase_inside_a_sentence_counts` and
  `test_dont_confirm_does_not_release_the_gate` (`117-145`) with one test.
  Every phrase, including "confirm", "yes do it please" and "go ahead",
  returns `ERROR:` naming the confirm key, and `executor.pending` is still
  set.
- `test_same_batch_confirm_last_does_not_release_the_gate` (`182-197`):
  still held. The assertion changes from "new user turn" to "confirm key".
- New `test_her_echo_cannot_release_a_hold`, the demonstration above as a
  test. Via `_on_response_done`: a hold, a reply transcript "…Confirm?",
  `speech_started`, then `confirm_last("Confirm?")`. The hold is still
  pending, and the output names the key.
- New: the gated tool's result tells the model to ask for the key and does
  not say "out loud". The persona's Confirmations block does not tell the
  model to call `confirm_last` when the user agrees.
- `test_typed_turn_is_injected_as_a_user_message`: drop the
  `_user_turn_since_hold` assertion (`345`).
- Kept as they are: `test_local_confirm_releases_without_a_model_phrase`
  (`347-351`), `test_cancel_drops_the_held_action` (`151-157`),
  `test_confirming_nothing_is_an_error`, and `test_gate_tools_are_realtime_only`.
- Run:
  `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent PYTHONPATH=src python3 -m unittest discover -s tests`
  — all pass. Then `nix flake check --no-write-lock-file` — passes.
- No real OpenAI connection, microphone or speakers at any point.
