---
status: draft
issue: 174
author: olafkfreund
---

# Intent: running dev-setup again is harmless, and a stopped run leaves nothing behind

## Problem

#172 made `dev-setup`'s first run work. Its live test on p620 (2026-09-27)
showed two things about later runs:

1. **Running it while the previous run's Claude is still open fails.**
   - herdr answers `agent_name_taken`, because the example always names its
     agent `dev` (`src/omarchy_voice/examples/dev-setup.toml`).
   - Oma explained it well ("already set up; no need to rerun"), but the
     step still counts as failed, and nothing brings the running session
     forward.
   - Saying "dev setup" to get back to your work is exactly what a user will
     do, several times a day.
2. **Every stopped run leaves a herdr workspace behind.**
   - Seen with the trust stop, the name-taken stop and the busy-pane stop.
   - The step creates the workspace first, then stops, so empty `dev`
     workspaces pile up in herdr.

## Proposed outcome

- Saying "dev setup" when its Claude is already running brings that
  session's herdr workspace to the front and says so. It succeeds, it
  doesn't fail, and it doesn't start a second Claude.
- A run that stops for any reason leaves no new herdr workspace behind.
- A first run and a resume behave exactly as #172 made them.

## Affected users and systems

- `src/omarchy_voice/examples/dev-setup.toml` and its tests
  (`tests/test_actions_cli.py`, `TestDevSetupExample`), and the README's
  dev-setup paragraph if the behaviour it describes changes.
- Anyone who copies the example. p620's own copy is the user's, and is not
  changed unless asked.

## Constraints

- Still one action, one line to the shell, in a subshell, with nothing that
  answers Claude's trust dialog. Everything #172 settled stays.
- It uses herdr's CLI and error codes only, and does not read herdr's or
  Claude's files.
- It must not close or disturb a herdr workspace it did not create in this
  run.

## Open questions

1. **Finding the running session.** Recommended: when `agent start` answers
   `agent_name_taken`, take the workspace from herdr's own error message,
   which names `workspace_id` and `pane_id`. Focus it, and close the one
   this run just created. The alternative is a lookup before creating
   anything (`herdr agent list` or the like), if herdr offers one. The spec
   should measure what herdr provides.
2. **Should the running session get the prompt again?** Recommended: no.
   Bring it forward, and don't interrupt a Claude that may be mid-task.
