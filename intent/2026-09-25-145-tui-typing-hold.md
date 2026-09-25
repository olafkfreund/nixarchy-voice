---
status: draft
issue: 145
author: olafkfreund
---

# Intent: with the shell off, Return typed into a tui window is held like Return typed into a terminal

Closes #145. Split out of #129's approved spec
(`spec/2026-09-24-129-tui-route-policy.md`, "Residual, larger, not in #129's
scope"), against #112's keyboard gate
(`plan/2026-09-24-112-terminal-needs-shell.md`).

## Problem

With `allow_shell` off, #112 holds a call that submits a line to a terminal:
`type_text` whose text has `\n` or `\r`, and `send_shortcut` (or
`hypr_dispatch` `send_shortcut`/`send_key_state`) of Return, KP_Enter,
ISO_Enter, Linefeed, or ctrl+m/j/o (`Executor._runs_command`,
`src/omarchy_voice/tools.py:2160`). "Terminal" is decided by
`_is_terminal_window` (`tools.py:2152`): the window's class must contain one
of `TERMINAL_CLASSES` (`tools.py:388`: foot, alacritty, kitty, ghostty,
wezterm, term, console). #87's terminal pane passes because its id,
`TERMINAL_PANE_ID = "org.omarchy.voice-terminal"` (`tools.py:417`), contains
"term".

A tui window does not pass. Both tui routes launch the program in the default
terminal under an app id named after the program:

- `omarchy launch tui vim` runs `omarchy-launch-tui`, which, with no
  `--app-id`, sets `APP_ID="org.omarchy.$(basename $1)"` and execs
  `xdg-terminal-exec --app-id=$APP_ID -e vim`. `launch-or-focus tui` uses the
  same id. On this host `xdg-terminal-exec --print-cmd` gives
  `foot --app-id=org.omarchy.vim -e vim`, so the class is `org.omarchy.vim`.
- A compose pane `{kind: "tui", target: "vim"}` launches
  `omarchy launch tui --app-id=vim vim` (`_pane_command`, `tools.py:799`;
  `_tui_app_id`, `tools.py:705`), so the class is `vim`.

Neither contains a terminal word. #129 holds the tui launch itself when the
program takes commands (`_takes_commands`, `tools.py:406`), but vim, less,
man and htop are not on that list, by design: they are not shells. They open
free, and then typing into them is free too. Several reach a shell from
inside:

| Program | Way to a shell | Source |
| --- | --- | --- |
| vim / nvim | `:!{cmd}`, `:terminal` | nvim `various.txt:281` ("Execute {cmd} with 'shell'") |
| less | `! shell-command`; `v` opens `$VISUAL` | `man less` line 470; both off only under `LESSSECURE=1` (line 2195) |
| man, git log/diff | whatever pager they use; `$PAGER=less` here | env; `MANPAGER` here is `bat`, not less |
| ranger | `!` / `:shell` | `man ranger` line 404 |
| mc | a subshell (not installed here, not verified) | — |
| htop | none found: `s` attaches strace, it does not run a shell | `man htop` line 155 |

So with the shell off a model can `launch tui vim` and then
`type_text ":!anything\n"` into its window without a yes. The deny rules see
`type ':!anything\n'`, so a literal on the deny list is caught, but nothing
else is.

### Measured with fakes

`tests/test_shell_off.py`'s `Fake` executor and `FakeDesktop` patches, with
the fake client list extended by the classes below; `Executor.call` as the
tests call it; "RAN" means a command reached the faked `_shell`.
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`, main at 806804a (#129
merged). Nothing launched, nothing typed.

| Window class | `_is_terminal_window` | `type ":!id\n"` off / on | Return off / on | ctrl+m off / on |
| --- | --- | --- | --- | --- |
| `org.omarchy.vim` (`launch tui vim`) | False | **RAN** / RAN | **RAN** / RAN | **RAN** / RAN |
| `vim` (compose tui pane) | False | **RAN** / RAN | **RAN** / RAN | **RAN** / RAN |
| `org.omarchy.less` | False | **RAN** / RAN | **RAN** / RAN | **RAN** / RAN |
| `org.omarchy.htop` | False | RAN / RAN | RAN / RAN | RAN / RAN |
| `Alacritty` | True | HELD / RAN | HELD / RAN | HELD / RAN |
| `org.omarchy.voice-terminal` | True | HELD / RAN | HELD / RAN | HELD / RAN |
| `firefox` | False | RAN / RAN | RAN / RAN | RAN / RAN |

The launches themselves, shell off: `launch tui vim`, `launch-or-focus tui
less` and a compose tui pane `vim` all RAN, as #129 intends.

The class check costs about 4 µs per call over a faked client list; the real
one is the `hyprctl clients` query it already makes.

## Proposed outcome

With the shell off, a line submitted to a window opened by the tui route
(either route, any program) waits for a yes, as it does for a terminal. With
the shell on, nothing changes. Typing without a newline, and keys that do not
submit, stay free. A GUI window (firefox) stays free.

## Affected users and systems

- `src/omarchy_voice/tools.py`: `_is_terminal_window` and whatever it learns
  to recognise; possibly `_tui_app_id` / the `launch tui` ids.
- `type_text`, `send_shortcut`, `hypr_dispatch` with the shell off; both
  backends and the MCP server share this gate.
- A user who runs vim in a tui pane with the shell off will be asked more
  often.
- README's shell-off section; `tests/test_shell_off.py`.
- Hosts: all; the default terminal (foot here) decides whether `--app-id` is
  kept.

## Constraints

- Must not loosen anything that is held today; unknown windows stay "yes".
- Must hold both id shapes: `org.omarchy.<prog>` (omarchy's default) and the
  bare `<prog>` a compose pane uses.
- Must not depend on the model naming the window a particular way; the model
  picks the window selector and the pane name.
- Must not launch, type or read anything to decide, beyond the compositor
  queries the gate already makes, unless the approver accepts the cost.
- No change with the shell on.

## Open questions

1. **How to recognise a tui window.** By the app id it was launched with (the
   #87 pattern: give every tui window a recognisable id, or match
   `org.omarchy.*` plus the compose id), or by the window's process tree
   (the client's `pid` has a child on a pty). The id is cheap but any
   `org.omarchy.*` window matches (also Omarchy's own GUI ids?), and a
   renamed or pre-existing window may not. The process tree catches a
   terminal of any class but depends on the terminal: a foot server or
   footclient window shares one pid across windows.
2. **Does every tui window count as a terminal for key holds?** Including
   htop, btop or a music player where Return does not reach a shell. Simpler
   and safer to say yes; the cost is more yes prompts.
3. **Latency and cost of a process-tree check.** A `/proc` walk per held-key
   call, on top of the `hyprctl clients` query; not measured here.
4. **Interaction with #139 and #129.** #139 (tools wait for speech) changes
   when a held call is announced; a new hold must not add a second wait.
   #129's `_COMMAND_PROGRAMS` already holds shells at launch: should this
   issue instead extend that list to vim/less (hold the launch), or keep
   launches free and hold only the keys?
