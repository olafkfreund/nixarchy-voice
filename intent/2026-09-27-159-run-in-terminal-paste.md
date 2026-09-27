---
status: approved
issue: 159
author: olafkfreund
---

# Intent: run_in_terminal runs what it was given, where the user can see it, and says whether it worked

## Problem

Live-testing #158 on p620, the `dev-setup` action asked `run_in_terminal` to
run one herdr command line. Three things went wrong, all in
`src/omarchy_voice/tools.py`, and all older than #158:

1. **The command that ran was not the command that was sent.** It is sent as
   keystrokes, `tmux send-keys -t <pane> -- <command> Enter` (line 4321). The
   shell's line editor handles each key, and on p620 it auto-closes brackets and
   quotes as they are typed:

   ```
   sent:     p=$(herdr workspace create … | jq -r …) && … "Continue where we left off …"
   typed as: p=$(herdr )workspace create … "Continue "where we left off …""
   bash: syntax error near unexpected token `)'
   ```

   Any spoken request whose command has `$( )`, quotes or brackets is exposed,
   not only actions. Here it failed safely on a syntax error. A different
   rewrite could produce a command that is valid and does something else.

2. **A failed command is reported as success.** The result was
   `ran '<command>' in <pane>` with `ok=True`: success means "the pane is idle
   again", not "the command exited 0". The model, or an action's runner, then
   carries on and tells the user it worked. `dev-setup` reported `3. ran` and
   exited 0.

3. **The pane is not necessarily one the user can see.** `_ensure_visible_session`
   (line 4168) accepts the first idle pane whose tmux session has *a* client,
   provided *some* terminal window is on screen. Those are two independent
   facts. On p620 the terminal on screen was herdr's, and the command went to
   `Synechron-Development:1.1`, a work session with credentials loaded into its
   environment, which the user was not looking at. The tool's own description
   promises it "only runs in panes that are on screen".

## Proposed outcome

- The exact characters sent are the characters the shell receives, whatever
  line editor, prompt or auto-pairing the user has.
- A command that exits non-zero comes back as a failure with its exit status,
  so the model and the action runner stop and say so.
- With no target given, a command runs only in a pane that is actually drawn
  in a terminal window on a visible workspace. If none is, it opens one, as it
  already does when nothing is attached, rather than guessing.
- After the fix, the `dev-setup` example from #158 runs end to end on p620:
  live check 6, still owed on #158.

## Affected users and systems

- `run_in_terminal`, and whatever shares its send path: `compose_windows` tmux
  panes, and `send_shortcut` Return into a terminal (#112). To be confirmed in
  the spec.
- Every voice or MCP request that runs a command, on the Claude and local
  engines alike. Any action with a `run_in_terminal` step.
- p620 (where it was found) and razer. Any user whose shell has auto-pairing:
  ble.sh, zsh-autopair, fish, some starship setups.

## Constraints

- The confirm gate and `allow_shell` behave exactly as now. This changes how a
  command is delivered, not whether it is allowed.
- Still no newlines in a command (the existing refusal at line 4196 stays).
- No focus, no key events to the desktop. It stays inside tmux, as now.
- Must not print secrets. Reading the exit status must not require capturing
  more of the pane than is read today (#101's guard still applies).
- A command still running after the wait keeps working through `watch_terminal`.

## Open questions

*Answered at approval (2026-09-27): all three recommendations taken —
bracketed paste, a visible exit marker, and point 3 fixed here.*

1. **How to deliver the text.** Recommended: tmux bracketed paste,
   `load-buffer -` then `paste-buffer -p -d -t <pane>`, then `Enter`. readline,
   ble.sh and zle take a bracketed paste literally. The alternative,
   `send-keys -l`, still goes through the line editor key by key, so it is
   rejected.
2. **How to learn the exit status.** Recommended: append a marker the pane
   prints, e.g. `; printf '\n\x1eOMA_EXIT=%s\x1e\n' $?`. This is visible in
   the pane, so the user sees it too. The alternative, `tmux wait-for` plus a
   status file, is invisible but writes a file per command. Does a visible
   marker bother you in your terminal?
3. **Point 3's scope.** Fix it here (match the tmux client's terminal window to
   a window on a visible workspace), or split it into its own issue? It is the
   one with the security edge, since it typed into a work session. Recommended:
   here, because the tool's promise is already "on screen".
