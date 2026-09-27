---
status: approved
issue: 168
spec: spec/2026-09-27-168-show-hold-before-readback.md
---

# Plan: what you are confirming is on screen while she reads it out

## Approved decisions (from the spec, complete)

1. **Shown when the hold starts.** `Executor.__init__` (`tools.py:2079`) gains
   `on_hold: Callable[[str], None] | None = None`, stored as `self.on_hold`
   with a no-op default, like `on_action` (`:2092`). It is called with the
   description:
   - in `Executor._hold` (`tools.py:2245`), after `self.pending` is set and
     recorded;
   - in the Claude backend's permission callback (`claude_backend.py:437-440`),
     after `self.pending = description`, as `self.executor.on_hold(description)`.
2. **The engine** passes `on_hold=self._show_hold` (`local_engine.py:241`).
   `_show_hold(held)`:
   - sets the bar with `feedback.state("confirm", held)`;
   - posts the card: `feedback.notify(summary, body, urgency="critical",
     replace=self._hold_note)`, storing the returned ID in `self._hold_note`;
   - sets `self._shown_hold = held`.

   `_settle()` (`:316`):
   - hold == `_shown_hold` → bar state only, no new card;
   - a different hold → `_show_hold` (it replaces the card, same ID);
   - no hold → `feedback.close(self._hold_note)` if set, then clears
     `_hold_note` and `_shown_hold`, and sets listening or idle as now.

   `HELD_PROMPT` is unchanged.
3. **`Feedback.notify`** (`feedback.py:248`):
   - gains `replace: int | None = None`;
   - adds `-p`, and `-r <id>` when `replace` is given;
   - returns `int(stdout)`, or `None` on no or unparsable output (and returns
     `None` when notify is off).

   Existing callers ignore the return value.
4. **`Feedback.close(note_id)`** runs `busctl --user call
   org.freedesktop.Notifications /org/freedesktop/Notifications
   org.freedesktop.Notifications CloseNotification u <id>`. It ignores
   failures and does nothing when `note_id` is None or notify is off.
5. **Critical urgency** for the hold card only, so it lasts as long as the
   hold. Every other notification keeps its urgency. The app name stays `OMA`,
   so under DND the card is hidden, as today. There is no DND bypass.
6. **Layout,** `LocalEngine._hold_card(held) -> (summary, body)`:
   - summary: `Say "<confirm_words[0]>" or press the confirm key`, or
     `Press the confirm key` when `barge_in` is on;
   - body: `held` split before each `; N. ` into one line per step (only a
     save's `describe` produces that);
   - more than 3 lines → the first 2 plus
     `+N more — hover the voice indicator to read all of it`;
   - a wrapped long line can still be clipped by the server. That ceiling is
     marked `ponytail:`, and the upgrade path is a dedicated view.

   The bar state keeps the full text.
*Deviation (step 5, live on razer, 2026-09-27), replacing parts of 2, 4 and 6:*

- **Replaced, not closed.** nixarchy's shell ignores a sender's close.
  Probed on razer: `notify-send -p` gave id 81 and busctl's
  `CloseNotification u 81` returned 0, but the toast stayed up. Its `closed`
  handler only drops `liveRefs` (`Service.qml:165-168`), and a critical toast
  never expires. The spec had misread `:56` as honouring a close.
  - So `_settle` with no hold replaces the card
    (`notify("No longer waiting", <held>, replace=id)`, low urgency). That
    card expires by itself on every server and says the hold is over.
  - `Feedback.close` is removed, as dead code.
- **The head goes in the summary.** The first live card's body line was
  "save action demo-focus: 1. dispatch focus workspace='8'". It wrapped and
  hid step 3, the failure the intent is about. So a save's head ("save
  action x") follows the answer in the summary
  (`Say "confirm" or press the confirm key: save action x`), and the body is
  only the steps. Holds without numbered steps are unchanged.

7. **Out of scope:** a dedicated window or menu page, moving `_settle()`
   before the spoken prompt, the MCP server's holds, and a card left behind if
   the daemon dies mid-hold (accepted).

## Steps

1. **`src/omarchy_voice/feedback.py`:** decisions 3 and 4.

   **`tests/test_feedback.py`,** with `subprocess.run` patched:
   - `notify` passes `-p` and returns 42 from stdout `"42\n"`;
   - `replace=7` adds `-r 7`;
   - empty stdout → `None`;
   - `close(7)` runs the exact `busctl` argv;
   - `close(None)` and notify off run nothing.

   → verify: `pytest -q tests/test_feedback.py`. Commit
   `feat(feedback): notify returns its id, can replace, and can close (#168)`.

2. **`src/omarchy_voice/tools.py` and `claude_backend.py`:** decision 1.

   **Tests:**
   - `tests/test_policy.py` `ExecutorTests` (`:118`, next to the "already
     waiting" test at `:146`): an `Executor(on_hold=seen.append)` with a confirm pattern → one call to
     `seen`, with the description. A second gated call while one is held
     (refused) adds nothing;
   - `tests/test_claude_backend.py`: next to the HOLD log test (`:559`), the
     permission callback's hold calls `executor.on_hold` once with the
     description.

   → verify: those tests pass. Commit
   `feat(tools): on_hold, called the moment a call is held (#168)`.

3. **`src/omarchy_voice/local_engine.py`:** decisions 2 and 6. Wire
   `on_hold`, add `_show_hold`, `_hold_card`, `_hold_note` and `_shown_hold`,
   and rework `_settle`.

   **`tests/test_local_engine.py` `HoldTests`** (`:693`), patching
   `session.feedback.notify` (returning an ID) and `session.feedback.close`,
   as `:1063` does:
   - `test_the_card_is_up_before_she_speaks`: a scripted brain whose tool call
     holds and then keeps talking → the card was posted before the first
     `_say` of the held prompt;
   - `test_one_card_per_hold`: after the turn, exactly one notify for that
     hold;
   - `test_confirm_closes_the_card` and `test_cancel_closes_the_card`:
     `close` is called with the ID;
   - `test_a_reheld_step_replaces_the_card`: an action held at step 1,
     confirmed, then held at step 3 → the second notify has `replace=<id>`;
   - `_hold_card`: a 3-step save → 3 body lines with the summary text; a
     5-step one → 2 lines plus `+3 more — hover the voice indicator to read
     all of it`; a plain hold → 1 line; `barge_in` → `Press the confirm key`.

   → verify: `pytest -q tests/test_local_engine.py`. Commit
   `fix(engine): show a hold when it starts, one card, closed on yes or cancel (#168)`.

4. **Whole suite and builds.**
   → verify: `nix develop -c python -m pytest -q` (all pass), `nix flake
   check`, and `nix build .#omarchy-voice`.

5. **Live on razer** (announced on the bus, and only while no other agent has
   claimed razer), running the branch through the user-space shim and
   restarting omarchy-voice:
   1. replay the demo's "ask" beat (`demo/shots.toml`, beat `ask`) with a
      `grim -o eDP-1` still every 2 s;
   2. the card is on screen within about 1 s of the `HOLD` line in
      `session.log`, before the readback's `say` line;
   3. the summary reads `Say "confirm" or press the confirm key`, and the 3
      steps are on 3 lines;
   4. `omarchy-voice listen confirm` mid-readback → the card is gone at the
      next still, and the action is saved;
   5. a 5-step recipe → `+3 more — hover the voice indicator…`, and hovering
      the indicator shows all 5 (a screenshot of the tooltip);
   6. clean up: delete the demo actions, `restore` as `demo.py` does, and put
      the deployed omarchy-voice back.

6. **PR** "fix(engine): show a hold on screen before it is read out (#168)",
   linking the intent, spec and plan, with the stills from step 5.

## Tests

```
nix develop -c python -m pytest -q tests/test_feedback.py tests/test_claude_backend.py \
  tests/test_policy.py tests/test_local_engine.py
nix develop -c python -m pytest -q          # whole suite
nix flake check && nix build .#omarchy-voice
```

## Rollback

Revert the three code commits. Nothing is persisted. The only change a user
can see is the card's timing, layout and lifetime, so a revert restores
today's end-of-turn notification.
