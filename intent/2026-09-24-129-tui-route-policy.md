---
status: approved
issue: 129
author: olafkfreund
---

# Intent: a program started by the tui route answers to the same policy as any other launch

Closes #129. Split out of #110, whose approved plan
(`plan/2026-09-24-110-one-rule-every-launch.md`, decision 4) leaves this route
out of scope.

## Problem

Two routes start a program in a terminal window with a name the model picks:

- `omarchy_cli` with `launch tui <program>` or `launch-or-focus tui <program>`;
- a `compose_windows` pane `{kind: "tui", target: "<program>"}`.

Neither is checked as a launch, and with the shell off neither is held when
the program is a bare name:

1. **No hold.** #112 (`plan/2026-09-24-112-terminal-needs-shell.md`, step 3)
   frees `launch tui NAME` and `launch or focus tui NAME` when `NAME` matches
   `_BARE_PROGRAM_RE` (`src/omarchy_voice/tools.py:391`), in
   `omarchy_runs_command` (`tools.py:1746-1772`), and a tui pane likewise in
   `_pane_runs_command` (`tools.py:715-720`). That was deliberate: a bare name
   is a program, not a command line. But `bash`, `python3` and `nu` are bare
   names too, and each is free (`omarchy_runs_command(["launch","tui","bash"])`
   is `None`, measured below).
2. **No app check.** #110 adds a `launch <desktop-id>` text (`_launch_text`,
   `tools.py:723-727`) only for `launch_app` and compose `app` panes
   (`_call_locked`, `tools.py:1890-1925`; inner pane check,
   `tools.py:3604-3623`). The tui route is judged only on its own text:
   `omarchy launch tui zeditor` (`describe`, `tools.py:2081-2083`), the compose
   label, and the pane argv `omarchy launch tui --app-id=zeditor zeditor`.

So the rule a user writes for an app decides whether the app is stopped by
*which route* the model took. #110's README sentence says these routes are
"governed by `allow_shell`", but with a bare name `allow_shell` does not
govern them either.

### Measured with fakes

A fixture app dir holding only `dev.zed.Zed.desktop` (`Exec=zeditor %U`),
patched into `tools.app_dirs` and `capabilities.app_dirs`; `shutil.which`
faked to find only `gtk-launch`; `Policy.check` wrapped to record every text;
`Config(dry_run=True)` with `DEFAULT_DENY` plus the rule under test;
`Executor._call_locked` called directly. The compose inner texts are the
`_pane_command` / `_launch_text` strings `_tool_compose_windows` would check,
put through the same `Policy.check` (dry run returns before that loop).
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`, `tests/_isolated.py`,
main at 50dcf99. Nothing launched.

What the gate sees:

| Call | Texts `Policy.check` sees |
| --- | --- |
| `omarchy_cli "launch tui zeditor"` | `omarchy launch tui zeditor` |
| `omarchy_cli "launch-or-focus tui zeditor"` | `omarchy launch or focus tui zeditor` |
| compose tui pane `zeditor` | outer `compose 2 windows … : zeditor, https://example.com`; inner `omarchy launch tui --app-id=zeditor zeditor` |
| `launch_app "zeditor"` / `"dev.zed.Zed"` | `launch dev.zed.Zed` (name resolved first, #70) |
| compose app pane `dev.zed.Zed` | outer compose line and `launch dev.zed.Zed`; inner `/run/current-system/sw/bin/gtk-launch dev.zed.Zed.desktop` and `launch dev.zed.Zed` |

Outcome, same for `allow_shell` on and off unless marked:

| Call | no extra rule | `\bzeditor\b` | `^launch dev\.zed\.Zed` |
| --- | --- | --- | --- |
| `launch tui zeditor` | ran | denied | **ran** |
| `launch-or-focus tui zeditor` | ran | denied | **ran** |
| compose tui `zeditor` | ran | denied | **ran** |
| `launch_app zeditor` | ran | **ran** | denied |
| `launch_app dev.zed.Zed` | ran | **ran** | denied |
| compose app `dev.zed.Zed` | ran | **ran** | denied |
| `launch tui 'zeditor .'` (argument) | ran; shell off: held | denied | ran; shell off: held |

So the gap runs both ways. An id rule misses the tui route (the issue). A
binary-name rule misses `launch_app`, because the binary appears only in the
entry's `Exec=`, which no gate text carries. No single rule stops Zed on
every route today.

Built-in rules on the tui route, any shell setting:

| Call | Result | Why |
| --- | --- | --- |
| `launch tui passwd` | denied, `passwd` | the word is in the text |
| `launch tui sudo`, compose tui `sudo` | denied, `sudo` (outer and inner) | the word is in the text |
| `launch tui rm` | ran | `rm-rf` needs `rm -r`/`-f`; a bare `rm` is not a match |
| `launch tui bash` / `python3` / `nu` | ran, no hold with the shell off | bare name, so #112 frees it |

A word-shaped built-in (`passwd`, `sudo`, `ssh`, `mkfs`) already catches the
tui route, because the program name is in the text. What it catches is the
name alone, not what the program does once it is open.

## Proposed outcome

- A program started through `launch tui`, `launch-or-focus tui` or a compose
  tui pane is checked against the same deny and confirm rules as the same
  program started by `launch_app` or an `app` pane, so one rule the user
  writes stops it on every route.
- With the shell off, a tui program that gives a shell or interpreter prompt
  is not free merely because its name is bare (subject to open question 2).
- `launch tui btop`, `launch or focus tui lazygit` and a compose `btop` pane
  still run with no hold when no rule names them (#112's table rows stay).
- The README sentence from #110 matches what the gate actually does.

## Affected users and systems

- `src/omarchy_voice/tools.py`: `_call_locked` (the `launches` texts),
  `omarchy_runs_command`, `_pane_runs_command`, the inner pane check in
  `_tool_compose_windows`, possibly `_launch_text` and a binary-to-id lookup
  near `_resolve_app` / `capabilities`.
- `tests/test_shell_off.py`, `tests/test_policy.py`, `tests/test_compose.py`.
- `README.md` Safety section (the #110 sentence).
- Anyone with `allow_shell = false` or a per-app deny rule, on any host. No
  config keys, rule names (#109) or packaging change.

## Constraints

- Deny before confirm across every text, as #110 made it
  (`_call_locked` `tools.py:1919-1925`, inner `tools.py:3610-3616`): a confirm
  match on one text must not hold a call another text denies.
- The inner per-pane check must stay no weaker than the outer one, and a
  confirm match there passes only inside a confirmed release (`_releasing`,
  #112, `tools.py:3622`); any new tui text must go through both loops.
- No new built-in rule names, and no renamed ones (#109: names are an interface).
- A lookup from a binary to a desktop id, if any, is a filesystem read of
  `app_dirs()`, never a launch or a compositor query, because
  `_validate_compose_windows` also runs in dry run.
- #112's promise that a bare `btop`/`lazygit` runs without a yes holds unless
  the approver decides otherwise under question 2.
- Tests use fakes and fixture app dirs only.

## Open questions

1. **Binary, desktop id, or both?** Check the tui program as `launch <name>`
   (so `^launch zeditor` works), as the desktop id whose `Exec=` runs it
   (`launch dev.zed.Zed`, needs an `Exec` lookup and can match several
   entries), or both? And the reverse: should `launch_app dev.zed.Zed` also
   be checked as its `Exec` binary, so `\bzeditor\b` stops it? Without that,
   the gap closes one way only.
2. **Hold a bare name with the shell off?** Options: (a) as today, free; (b)
   hold a short list of shells and interpreters (`bash`, `sh`, `zsh`, `fish`,
   `nu`, `python3`, …) as "runs a command"; (c) hold every tui launch with the
   shell off, dropping #112's free rows. (b) is a list to keep; (c) costs a yes
   for `btop`.
3. **Built-in deny rules as a tui program.** Word rules already match. Is a
   bare `launch tui rm` (runs, does nothing harmful without arguments)
   acceptable, or should the tui program be matched as if it were a command
   line, e.g. `rm` alone? Should `ssh` as a tui (denied today by the `ssh`
   rule) stay denied, given it is interactive and the user types the host?
4. **#112 `_releasing` and #110's deny-first pass.** A new tui text joins
   `launches`, so the deny pass covers it outer and inner. But a confirm match
   on it outside a release refuses the pane ("not allowed by policy") rather
   than holding. Is that the wanted wording for a tui pane, or should the
   outer description carry the text so the hold happens up front, as #112 did
   for the pane label?
5. **README.** Rewrite #110's sentence here, or leave it until the design
   settles?
