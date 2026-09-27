---
status: approved
issue: 172
author: olafkfreund
---

# Intent: dev-setup works the first time a new user runs it

## Problem

`dev-setup` is the example the README and the project page point developers
to first (`omarchy-voice action new dev-setup --from dev-setup`, README.md:54
and :717). It opens a workspace, resumes Claude in the user's repository in a
herdr pane, and tells it how to UI-test.

It was run live on p620 on 2026-09-27, as a copy aimed at a scratch
repository so that it would not resume the session running the test. The
core chain works:
- `herdr agent start … -- --continue` returns only once Claude is idle;
- `herdr agent prompt` lands;
- the resumed Claude reports that it has the ai-mirror and Chrome MCPs.

So the part of the original #157 request about driving testing on a desktop
or in the browser when prompted already works, with nothing watching.

But a new user's first run fails, in ways a returning user never sees:

1. **No earlier interactive Claude session in the repository.**
   `claude --continue` prints "No conversation found to continue" and exits,
   so the step fails. A `claude -p` run doesn't count. That is the normal
   state of a repository someone has just started to use.
2. **A folder Claude has not been told to trust.** Claude waits on its
   folder-trust dialog in a pane nobody is looking at, and
   `herdr agent start` fails with `agent_not_ready` ("blocked during
   startup"). Oma reported that correctly, but the user is left to find the
   pane and answer it.
3. **"An empty Hyprland workspace" is not what happens.** Step 1 focuses
   workspace 4, then `omarchy launch terminal-herdr` reuses an existing
   herdr window wherever it is (workspace 15 on p620), and focus follows it
   there. The comment at the top of the file promises something the steps
   don't do.
4. **The prompt names `razer`,** the author's laptop
   (`src/omarchy_voice/examples/dev-setup.toml:24`). A new user has no host
   of that name, so Claude is told to drive a desktop that doesn't exist.

p620's own `dev-setup` has none of 1 and 2 (its repository has sessions and
is trusted), which is why they were not seen until now.

## Proposed outcome

- Copied and pointed at a repository, the example either works on the first
  run or stops with a message that says exactly what to do. It never leaves
  a Claude waiting on a dialog nobody sees.
- A first run in a repository with no earlier session starts a fresh Claude
  there instead of failing. A later run carries on where the last stopped,
  as today.
- The file's comments describe what the steps actually do, and nothing in it
  is specific to the author's machines.
- The README and project page describe the same behaviour.

## Affected users and systems

- `src/omarchy_voice/examples/dev-setup.toml`, the README's dev-setup
  section (:744-747), and `docs/index.html` if it describes the steps.
- Anyone who copies the example. p620's own copy is the user's file and is
  not changed unless the user asks.
- Tests that load the shipped examples (`tests/test_actions*.py`).

## Constraints

- The example stays an action: tool steps through the Executor's gate,
  holds unchanged. There's no new tool and no new privilege, and nothing
  answers Claude's trust dialog for the user (trusting a folder is the
  user's decision).
- It must not resume a session in a different directory than the one it
  opens. `--continue` is per directory today, and that stays true.
- It works whether or not the user has herdr's other workspaces open, and
  on a machine with one monitor or three.
- No dependency beyond what the example already uses (herdr, jq, claude).

## Open questions

1. **A first run with no earlier session.** Recommended: fall back to a fresh
   `claude` when there's nothing to continue, in the same pane, and tell it
   that it is starting fresh. The alternative is to fail with a message
   ("run claude once in <repo> first"). That is simpler, but it breaks the
   example for exactly the user it is for.
2. **An untrusted folder.** Recommended: detect it before starting the agent
   and stop with one clear line ("open <repo> in Claude once and trust it,
   then say dev setup again"), with the herdr pane left focused on the
   dialog so the user can answer it there. The alternative is doing nothing
   beyond Oma's current failure report.
3. **The workspace.** Recommended: drop the "empty workspace" promise and
   step 1, and say "focuses herdr" instead. herdr is one window by design,
   and a workspace number is personal. The alternative is moving the herdr
   window to the workspace, which fights the user's own window rules.
4. **The host in the prompt.** Recommended: "drive the other desktop through
   the ai-mirror MCP", with a comment on where to name your own host.
