---
status: approved
issue: 167
intent: intent/2026-09-27-167-routine-on-off-by-voice.md
---

# Spec: turn a routine on or off by saying so

## Decisions on the intent's open questions

The intent was approved on 2026-09-27 ("continue then") with its questions
unanswered, so the recommendations are taken:

1. **Off does not hold. On holds, with a short readback.** Stopping a timer
   cannot do harm, and the routine's steps were approved when it was saved.
   Turning one on restarts unattended runs, so a misheard name must not do it
   silently.
2. **Two new `do` values, `enable` and `disable`.** These match the CLI verbs
   (`omarchy-voice action enable|disable`), so the docs, the menu and the
   voice use one vocabulary. One `do: "schedule"` with a flag would add a
   property to a schema every turn carries, for no gain.
3. **"Until Monday" is out of scope.** Oma turns it off now, and turning it
   back on is a second request.

## Design

### One shared implementation: `actions.set_enabled`

`_set_enabled` moves from `cli.py:636` to `src/omarchy_voice/actions.py` as
`set_enabled(name: str, on: bool) -> Action`. It keeps the text edit that
preserves comments: the same two regexes, the same "no `[schedule]` with a
`when`" refusal. It adds what the CLI version lacks:

- `check_name(name)` and a missing file → `ActionError("no action called …")`,
  as `delete` does (`actions.py:276`);
- `_writable(path)` (`actions.py:247`), so a Home Manager routine gets
  "`<name>` is declared in Home Manager; change it there". Today the CLI
  fails on such a routine with a raw `OSError` from the read-only store.
  That is fixed by the same move;
- it parses the result before writing (as `save` does, `actions.py:268`), writes
  through a `.tmp` and `replace`, and returns the parsed `Action`, so callers
  can say its `when`;
- errors are `ActionError`, not `ValueError`.

`cli.py` `cmd_action`'s `enable`/`disable` branch calls
`act.set_enabled(...)` and then `after()`, as now. The CLI's behaviour is
unchanged, apart from the clearer errors above.

### The `action` tool (`src/omarchy_voice/tools.py`)

- **Schema** (`:1554`): `do` enum gains `"enable", "disable"`. The
  description gains one clause: `enable/disable: a routine's timer on or off`.
  That is about 12 tokens more per turn, on top of about 214.
- **`_validate_action`** (`:5276`): accepts the two values. They need a
  `name`, checked with `check_name`, like `show`/`delete`. The error string
  lists the new values.
- **Policy gate** (`:66-67`, `:2214`): `"enable"` joins `ACTION_WRITES`, so it
  is validated and then always raises `NeedsConfirmation`, exactly like
  save/delete. `"disable"` goes into neither set. It is not a read, so it
  passes through the normal gate (deny/confirm patterns on its description,
  and the #139 wait for her sentence) and runs without a yes.
- **`describe("action")`** (`:2470`): for `enable` it loads the action
  (`actions_mod.load`, inside a `try`; on failure it falls back to the name)
  and returns `turn on routine <name>, runs <when>`. For `disable` it returns
  `turn off routine <name>`. The hold notification and the spoken prompt both
  use this line.
- **`_tool_action`** (`:5294`): a branch next to `delete`:

  ```python
  if do in ("enable", "disable"):
      action = actions_mod.set_enabled(name, do == "enable")
      notes = actions_mod.after_change(self.config)
      said = f"runs {action.when}" if do == "enable" else "it will not run until turned on"
      return Result(True, f"{name} is {'on' if do == 'enable' else 'off'}: {said}"
                    + ("".join(f"\n{n}" for n in notes)))
  ```

  `ActionError` is caught by the existing `except` and reported as the
  failure. `after_change` rewrites the timers and the menu, the same call the
  CLI's `after()` makes. Approvals are not touched: the steps did not change.

### What is said

The tool result is what the model turns into speech, so "morning-repo is off:
it will not run until turned on" and "morning-repo is on: runs Mon..Fri
08:00" reach the user without new speech code.

## Alternatives rejected

- **Leave it to `show` + `save`.** That is the problem the intent describes:
  the model re-sends every step, the recipe is read back in full, and the
  comments are lost (`save` even refuses a commented file,
  `actions.py:264`).
- **Hold both directions.** Pausing a routine would then need a yes to do
  something harmless. The safety hold is for starting unattended runs.
- **Hold neither.** A misheard "turn on the cleanup routine" would quietly
  start a timer. The steps being approved is not the same as the user wanting
  them on a schedule now.
- **Say the computed next run time** (`systemctl show -p NextElapseUSecRealtime`).
  That is another systemd call and a timestamp to format. A `login` routine
  has none, and the `when` string ("Mon..Fri 08:00") is what the user wrote
  and recognises.
- **A new tool** (`routine`). That is one more schema on every turn, for two
  verbs of the existing tool.

## Risks

- **A declared (Home Manager) routine on p620 or razer.** `_writable` refuses
  it with a message naming Home Manager. The timers HM wrote are untouched,
  because `write_timers` skips symlinked units (`actions.py:610,618`).
- **A user's file with an unusual `enabled` layout** (inline table
  `schedule = {when = …}`): the regexes miss it, as the CLI's do today. The
  parse-before-write means a wrong edit is refused, never written. That is
  the same ceiling as now, marked `ponytail:` in the code.
- **Local planner phrasing.** The local engine's planner sees the same schema,
  and nothing routes "turn off …" to it by pattern. If a small local model
  picks `save` instead, that is the current behaviour, not a regression.
- **The schema grows by about 12 tokens a turn.** That is accepted, and it is
  measured in verification.

## Verification

- `tests/test_actions.py`: `set_enabled`:
  - flips `true`↔`false` and keeps comments;
  - adds `enabled` after `when` when it is missing;
  - refuses: no schedule, a missing action, a symlinked (declared) file;
  - never writes an unparsable file.
- `tests/test_actions_cli.py`: the existing `test_enable_keeps_comments` and
  `test_enable_without_a_schedule` pass unchanged. There is a new case for a
  declared routine: exit 1, with the Home Manager message and no traceback.
- `tests/test_actions.py` (tool, through a real `Executor` as the existing
  `run_pending` tests do, with `record_systemctl` standing in for systemd):
  - `disable` runs without a hold, the file says `enabled = false`, and the
    timer is stopped;
  - `enable` raises the hold, and its description is
    `turn on routine r, runs Mon..Fri 08:00`; after `run_pending` the file
    says `enabled = true` and the timer is enabled;
  - `enable` on an action with no schedule fails with the refusal and leaves
    no hold;
  - approvals are unchanged by both.
- The full suite passes (`nix develop -c python -m pytest -q`), and so do
  `nix flake check` and `nix build .#omarchy-voice`.
- The schema size before and after is measured with the same count #157 used
  (`json.dumps` of the `action` entry, divided by 4) and recorded in the PR.
- **Live, on p620** (its `morning-repo` routine):
  1. `omarchy-voice listen say 'turn off my morning repo routine'`: no hold.
     `systemctl --user is-enabled omarchy-voice-routine-morning-repo.timer` →
     `disabled`, the file says `enabled = false`, and she says it is off.
  2. `… say 'turn my morning repo routine back on'`: the hold notification
     reads `turn on routine morning-repo, runs Mon..Fri 08:00`. Then
     `listen confirm` → the timer is `enabled` and its next run is Monday
     08:00.
