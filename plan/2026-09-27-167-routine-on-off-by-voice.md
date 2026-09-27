---
status: approved
issue: 167
spec: spec/2026-09-27-167-routine-on-off-by-voice.md
---

# Plan: turn a routine on or off by saying so

## Approved decisions (from the spec, complete)

1. **Two new `do` values on the `action` tool: `enable` and `disable`.** They
   match the CLI verbs. "Until Monday" (a timed pause) is out of scope.
2. **Holds:**
   - `enable` joins `ACTION_WRITES` (`tools.py:67`), so it is validated and
     then always raises `NeedsConfirmation`, like save and delete;
   - `disable` is in neither `ACTION_READS` nor `ACTION_WRITES`. It goes
     through the normal gate (deny/confirm patterns on its description, and
     the #139 wait) and runs without a yes.
3. **One implementation, `actions.set_enabled(name, on) -> Action`,** moved
   from `cli.py:636` `_set_enabled`:
   - the same two regexes (flip `enabled = true|false`, else add
     `enabled = …` after `when = …`), so comments survive;
   - the refusal keeps its text: `no [schedule] with a \`when\` to turn on;
     add one first`;
   - new: `check_name`, "no action called …" for a missing file, `_writable`
     (a Home Manager symlink → "`<name>` is declared in Home Manager; change
     it there"), a parse of the new text before writing, a write through
     `.tmp` + `replace`, and `ActionError` for every refusal;
   - returns the parsed `Action`;
   - an inline-table `schedule = {…}` is not handled, as today. Parse-before-
     write refuses a bad edit. This is marked `ponytail:`.
4. **The CLI** (`cli.py:816`) calls `act.set_enabled`, then `after()`, as now.
   Declared routines now give the Home Manager message instead of a raw
   `OSError`.
5. **Schema** (`tools.py:1554`): the enum gains the two values, and the
   description gains `enable/disable: a routine's timer on or off`. That is
   about 12 tokens more per turn; before and after are recorded in the PR.
6. **`describe("action")`** (`tools.py:2470`):
   - `enable` → `turn on routine <name>, runs <when>`, loading the action in
     a `try` and falling back to `turn on routine <name>`;
   - `disable` → `turn off routine <name>`.
7. **`_tool_action`** (`tools.py:5294`) gains a branch next to `delete`:
   `set_enabled`, then `after_change(self.config)`. The result is
   `"<name> is on: runs <when>"` or
   `"<name> is off: it will not run until turned on"`, followed by any notes
   from `after_change`. Approvals are not touched. The next run time is the
   `when` string, not a value computed from systemd.

## Steps

1. **`src/omarchy_voice/actions.py`:** add `set_enabled` after `delete`
   (decision 3).

   **`src/omarchy_voice/cli.py`:** delete `_set_enabled`, call
   `act.set_enabled(args.name, sub == "enable")` (decision 4).

   **`tests/test_actions.py`,** new class `TestSetEnabled(Clean)`:
   - flips both ways and keeps a comment line;
   - adds `enabled` after `when` when it is missing;
   - refuses: no schedule (message contains `no [schedule]`), a missing
     action, a symlinked file;
   - a file whose edit would not parse is left byte-identical.

   **`tests/test_actions_cli.py`:** the existing `test_enable_keeps_comments`
   and `test_enable_without_a_schedule` pass unchanged. New
   `test_enable_a_declared_routine`: the file is a symlink to a read-only temp
   file → exit 1, `declared in Home Manager` in the output, and no traceback.

   → verify: `nix develop -c python -m pytest -q tests/test_actions.py
   tests/test_actions_cli.py` passes. Commit
   `refactor(actions): set_enabled shared by the CLI and the tool (#167)`.

2. **`src/omarchy_voice/tools.py`:** decisions 1, 2, 5, 6 and 7.
   - the schema enum and description;
   - `ACTION_WRITES` gains `"enable"`;
   - `_validate_action` accepts both values and needs a `name`; its error
     string lists all seven;
   - `describe` gets the two cases, and `_tool_action` gets the branch.

   **`tests/test_actions.py`** `TestTool`, with a real `Executor` and
   `record_systemctl`:
   - `test_disable_runs_without_a_hold`: a routine with `enabled = true` →
     `ex.call("action", {"do": "disable", …})` is ok, `ex.pending` is None,
     the file says `enabled = false`, and the result says `is off`;
   - `test_enable_holds_with_its_schedule`: the call is held,
     `ex.describe(*ex.pending)` is `turn on routine r, runs Mon..Fri 08:00`,
     the file is unchanged; `run_pending()` is ok, the file says
     `enabled = true`, and `record_systemctl` saw the timer enabled;
   - `test_enable_without_schedule_is_refused_unheld`: fails with
     `no [schedule]`, and `ex.pending` is None;
   - approvals are equal before and after both calls.

   → verify: the tests above pass. Commit
   `feat(actions): turn a routine on or off by voice (#167)`.

3. **Schema size** (decision 5): measure with
   `len(json.dumps(<action schema>)) / 4` on `main` and on the branch.
   → verify: the difference is ≤ 20 tokens, recorded for the PR body.

4. **Whole suite and builds.**
   → verify: `nix develop -c python -m pytest -q` (1318 + new tests, all
   pass), `nix flake check`, and `nix build .#omarchy-voice`.

5. **Live on p620** (its user `morning-repo` routine, Mon..Fri 08:00), after
   deploying the branch with the user-space shim used in #158, announced on
   the bus:
   1. `omarchy-voice listen say 'turn off my morning repo routine'` → no hold;
      `systemctl --user is-enabled omarchy-voice-routine-morning-repo.timer` →
      `disabled`; the file says `enabled = false`; she says it is off.
   2. `… say 'turn my morning repo routine back on'` → the hold notification
      reads `turn on routine morning-repo, runs Mon..Fri 08:00`;
      `listen confirm` → `enabled`, and `systemctl --user list-timers` shows
      Monday 08:00.
   3. Leave it **enabled**, as it was found.

6. **PR** "feat(actions): turn a routine on or off by voice (#167)", linking
   the intent, spec and plan, and giving the schema size and the live results.

## Tests

```
nix develop -c python -m pytest -q tests/test_actions.py tests/test_actions_cli.py
nix develop -c python -m pytest -q          # whole suite
nix flake check && nix build .#omarchy-voice
```

## Rollback

Revert the two code commits. `set_enabled` is used only by the CLI branch and
the new tool branch, and no file format changes, so user files written by
either version read the same in the other.
