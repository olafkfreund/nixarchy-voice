---
status: draft
issue: 167
author: olafkfreund
---

# Intent: turn a routine on or off by saying so

## Problem

A routine is an action with a `[schedule]`. Its timer runs only while
`enabled = true`. Today there are two ways to flip that:

- `omarchy-voice action enable|disable <name>` on the CLI (`cli.py`,
  `_set_enabled`, which edits the one line and keeps the user's comments);
- editing the file by hand.

Oma has neither. The `action` tool's `do` is `list | show | run | save | delete`
(`src/omarchy_voice/tools.py:1554`). So "turn off my morning routine" or
"pause the repo check until Monday" has no tool to call. The only route the
model can find is `show` then `save` again with `schedule.enabled` flipped.
That route:

- resends every step, and the model can get one wrong while copying it;
- reads the whole recipe back and waits for a yes, just to pause a timer;
- rewrites the file through `render_toml`, which loses the comments that
  `_set_enabled` keeps.

This came up with p620's `morning-repo` routine (Mon..Fri 08:00). Pausing it
for a day off takes a terminal, although asking is the main way people use
Oma.

## Proposed outcome

- "Turn off / pause / stop my morning routine" and "turn it back on" work by
  voice, on every engine and over MCP (they share `TOOL_SCHEMAS`).
- Afterwards the file, the systemd timer and the menu agree, exactly as after
  `omarchy-voice action disable`.
- Oma says what changed, with the next run time when it is turned on.
- A routine declared in Home Manager is not changed. Oma says it is declared
  in Nix and where to change it (the file is a read-only store link).

## Affected users and systems

- `src/omarchy_voice/tools.py`: the `action` tool schema, its validator and
  handler, and `describe`.
- `src/omarchy_voice/cli.py`: `_set_enabled` becomes shared rather than
  CLI-only (it probably moves to `actions.py`).
- Tests: `tests/test_actions.py` and `tests/test_actions_cli.py`.
- Anyone with routines. p620 has one today.

## Constraints

- The same code path as the CLI, so the file, the timer and the menu cannot
  drift between the two.
- Comments in the user's file survive, as they do with `_set_enabled` today.
- The schema stays small. Every turn carries it, and it was trimmed to about
  214 tokens in #157.
- Nothing about what the routine *does* changes, so no step is re-approved and
  no approval is dropped.
- Deny rules and holds are unchanged for every other `do`.

## Open questions

1. **Should turning a routine on wait for a yes?** Recommended:
   - **off: no hold.** Stopping something is safe, and a routine's steps were
     approved when it was saved.
   - **on: hold, with a short readback**: "turn on morning-repo, runs
     Mon..Fri 08:00". It starts unattended runs again, and a misheard name
     should not do that.

   The alternative is no hold either way, since the steps are already
   approved.
2. **How it is spelled in the schema.** Recommended: two new `do` values,
   `enable` and `disable`, which match the CLI verbs. The alternative is one
   `do: "schedule"` with an `enabled` flag.
3. **"Until Monday."** Recommended: out of scope. Oma can turn it off now,
   and turning it back on is a second request. A timed pause would need a
   second timer, which is its own issue if wanted.
