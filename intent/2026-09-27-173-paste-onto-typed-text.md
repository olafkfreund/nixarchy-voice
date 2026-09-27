---
status: draft
issue: 173
author: olafkfreund
---

# Intent: a command Oma runs is never mixed with text already on the line

## Problem

`run_in_terminal` (#159):
1. checks that the pane's program is an idle shell (`tools.py:4452`);
2. pastes the command with bracketed paste;
3. presses Enter (`:4466-4468`).

It never looks at the shell's **input line**. Whatever is already typed
there becomes part of the command.

Seen on p620 on 2026-09-27, during the #172 live test:
- `yes` was already on the input line of Oma's own tmux shell, put there by
  something outside Oma (her log has no input action);
- the paste turned the command into `yes( repo=…`;
- bash refused it with `syntax error near unexpected token` (exit 2).

That time it failed harmlessly. It doesn't always have to:
- a line ending in `sudo ` or `rm -rf ` joins the start of Oma's command;
- a half-typed command of the user's joins its end;
- anything Oma runs then executes with text it never showed and the user
  never confirmed. A held command's readback describes only Oma's text.

The line gets text from anyone who types into that terminal: a person, a
stray key while it has focus, or dictation (Voxtype types into the focused
window).

## Proposed outcome

- What runs is exactly the command Oma was asked for and, if it was held,
  exactly what the user confirmed. Nothing typed before it joins it.
- If there is text on the line, it is not silently thrown away either.
  What happens to it is decided in the spec (see the open questions), and
  Oma says what she did.
- A command in an empty line behaves as today.

## Affected users and systems

- `src/omarchy_voice/tools.py`, `run_in_terminal`'s paste path, and its tests
  (`tests/test_terminal.py`).
- Every engine and the MCP server (shared tool); both Oma's own `Oma` session
  and a user's drawn session given as `target`.
- bash, zsh, sh, dash, ksh (the shells #159's exit marker supports).

## Constraints

- No change to what is held or how it is released. The policy gate stays in
  charge.
- It works through tmux, as the paste does. No new dependency.
- It never loses text a person typed in their own terminal without saying
  so.
- It must be reliable. A check that sometimes misreads the line and blocks
  every command would be worse than today.

## Open questions

1. **Text on the line in Oma's own `Oma` session.** Recommended: clear it
   (the shell's kill-line, `C-u` after `C-e`, or `C-a C-k`), run the
   command, and say "there was text on the line; I cleared it". That session
   is Oma's, and what is on its line is almost always stray input.
2. **Text on the line in a user's own session (`target`).** Recommended:
   refuse, and say "there's unfinished text on that line; clear it or pick
   another pane". Clearing someone's half-typed command is not Oma's call.
3. **How to tell the line is empty.** To be settled in the spec by
   measurement. Candidates:
   - compare the cursor line with the prompt the shell drew when idle;
   - or skip detection: clear unconditionally in `Oma`, refuse only where
     detection is reliable.
