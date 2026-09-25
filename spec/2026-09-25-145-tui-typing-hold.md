---
status: draft
issue: 145
intent: intent/2026-09-25-145-tui-typing-hold.md
---

# Spec: With the shell off, Return typed into a tui window waits for a yes

Closes #145. Line numbers are from main `806804a` (#129 merged).

## Decisions on the intent's open questions

The intent was approved without answers to its four questions. Each is decided
below, with the reason, and each can be rejected at this gate. Rejecting one
changes the Design section and nothing else.

Evidence comes from fakes (`scratchpad/spec-145/proto.py`, not committed):
`tests/test_shell_off.py`'s `Fake` with one extra window of the class under
test (as both `class` and `initialClass`), asked `Executor._runs_command`
directly. The proposed rule is a patched `_is_terminal_window`.
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`. Nothing was launched or
typed. The omarchy facts are read from the installed omarchy 4.0.4 scripts and
Hyprland config (`share/omarchy/bin`, `share/omarchy/default/hypr`).

### Q1. How to recognise a tui window? **By the app id it was launched with: a class (or initial class) that starts with `org.omarchy.` or `TUI.`, and compose tui panes launched under `org.omarchy.voice.<name>`.**

Omarchy already answers this question for itself.
`default/hypr/apps/terminals.lua` tags as `+terminal` every window whose class
is

```
(Alacritty|kitty|com.mitchellh.ghostty|foot|org\.codeberg\.dnkl\.foot|wezterm|org\.omarchy\..*|TUI\..*)
```

with the comment "Omarchy launches TUIs and its own terminal windows under
dedicated app-ids (org.omarchy.btop, org.omarchy.terminal, TUI.float, ...), so
match those too." The clipboard bindings lean on that tag as "one definition of
what counts as a terminal". Taking the same two prefixes means our gate and
Omarchy agree on what a terminal is.

The intent asked whether Omarchy also uses `org.omarchy.*` for GUI windows.
In 4.0.4 it does not. Every `org.omarchy.*` id in `share/omarchy/bin` is passed
to a terminal: `omarchy-launch-tui` (the default `org.omarchy.$(basename $1)`),
`org.omarchy.about` (`launch-or-focus-tui`), `org.omarchy.terminal`
(`xdg-terminal-exec --app-id=…`), `org.omarchy.agent` (`launch-tui`), and
`org.omarchy.screensaver` (`foot --app-id=…`, `alacritty --class=…`). The
`TUI.` ids come from `omarchy-tui-install` desktop entries
(`xdg-terminal-exec --app-id=TUI.tile -e …`). If a later Omarchy gave a GUI
window such an id, Return into it would be held: the safe side.

The compose route gives the bare class `vim` (`_pane_command`,
`tools.py:792-796`), which no rule can tell from a GUI app named `vim`. So
compose changes, not the matcher: a tui pane is launched with
`--app-id=org.omarchy.voice.<name>`. Not bare `org.omarchy.<name>`, because
Omarchy floats `org.omarchy.btop`, `org.omarchy.terminal` and
`org.omarchy.bash` (`default/hypr/apps/system.lua:7`), and a compose pane must
tile. This is #87's reason for `TERMINAL_PANE_ID =
"org.omarchy.voice-terminal"` (`tools.py:417`). The `org.omarchy.` prefix also
gets the pane Omarchy's terminal tag and styling, as #87's pane has.

`initialClass` is checked as well as `class`. It is the id the window was
launched with, the thing this rule trusts; foot lets a program change its
app id later (OSC 176), and a window that renamed itself away from a terminal
id must still count. Either one matching is enough, so this only adds holds.

The model cannot steer around it. It chooses the pane `name`, but that is only
the suffix after the fixed prefix. It cannot pick a tui launch's id at all
without arguments, and `launch tui --app-id=x vim` has arguments, so #112
already holds that launch (`omarchy_runs_command`, `tools.py:1851-1868`).

With fakes, shell off, main against proposed (H = held, R = runs):

| Window class | main | proposed |
| --- | --- | --- |
| `org.omarchy.vim` (`launch tui vim`) | R | H |
| `org.omarchy.voice.vim` (compose, proposed) | R | H |
| `vim` (compose, today) | R | R, which is why compose changes |
| `org.omarchy.htop` | R | H |
| `TUI.float` | R | H |
| `Alacritty`, `org.omarchy.voice-terminal` | H | H |
| `firefox` | R | R |

Each H is for all four submitting calls: `type_text` with `\n`,
`send_shortcut` Return, `send_shortcut` ctrl+m, and `hypr_dispatch`
`send_shortcut` Return. `type_text` with no newline is R in every row.

### Q2. Does every tui window count as a terminal? **Yes, every one, for the submit keys only.**

The rule sees an app id, not a program's abilities. Telling vim (`:!cmd`) from
htop (no shell found) would need a list of "safe" tuis. That is an allowlist
of programs, re-audited every time one grows a shell escape, and a wrong entry
fails open. btop, lazygit, htop and a music player therefore hold typed Return,
ctrl+m/j/o and a newline in `type_text`, with the shell off. Everything else
typed into them stays free, including arrows, letters, `q`, and text without a
newline. The cost is more yes prompts, only with the shell off. p620 runs with
the shell on and sees no change.

### Q3. Latency and cost of a process-tree check. **Not built, so not paid.**

The app id is reliable for both tui routes by construction: omarchy's route
sets it, and compose's route will. A process tree would add a `/proc` walk per
held-key call and still be wrong for a foot server or footclient window, whose
windows share one pid (the intent's point). The id check itself measured
2.3 µs a call over the fake client list, against 2.5 µs for main's check. It
reuses the `hyprctl clients` query the gate already makes, so it adds nothing
real. A process-tree check is worth revisiting only if a route is found that
opens a tui under an id we cannot set (see Risks).

### Q4. #139, #112's `_releasing`, and #129's launch list. **No new wait. No change to the release. The launch list stays as it is.**

- **#139** (merged, `6634598`) makes a changing call wait in
  `_pre_tool_use` until her line has played. That is before `_decide`, and so
  before the Executor. `type_text` and `send_shortcut` are already not
  `_is_read`, so they already wait once. This change only turns more of them
  into holds inside `Executor._call_locked`, through the existing
  `NeedsConfirmation` path. There is one wait and one announcement, as for any
  hold.
- **#112's `_releasing`**: `run_pending` (`tools.py:2106-2127`) calls the
  handler directly and does not go back through `_call_locked`, so a released
  Return is not held again. `_releasing` is read only by compose's per-pane
  re-check (`tools.py:3863`). The new app id changes the pane argv text that
  check matches (`--app-id=org.omarchy.voice.notes`), and nothing else.
- **Widen #129's `_COMMAND_PROGRAMS` to vim, less, man, ranger?** No.
  Holding the launch does not close the hole: a vim already open, opened by
  the user, or opened through a route the list does not see is typed into
  just the same. The key hold covers all of these. Adding the launch hold as
  well would put a yes in front of every "open vim", and it would not make
  anything safer. #129's list stays what its comment says, programs whose
  whole job is to take commands.

## Design

### 1. `_is_terminal_window` learns Omarchy's tui ids (`tools.py:2152-2158`)

```python
# The ids Omarchy launches tuis and its own terminals under: the same two
# prefixes its `+terminal` tag matches (default/hypr/apps/terminals.lua) (#145).
TUI_CLASS_PREFIXES = ("org.omarchy.", "tui.")
```

beside `TERMINAL_CLASSES` (`tools.py:388`). `_is_terminal_window` checks the
window's `class` and `initialClass`, lower-cased. Either one counts as a
terminal if it contains a `TERMINAL_CLASSES` word, as today, or starts with a
`TUI_CLASS_PREFIXES` entry. An unresolvable window still counts as a terminal.

The only callers are the keyboard rules in `_runs_command`
(`tools.py:2180,2196`), which are asked only with the shell off. So the shell-on
path does not change. `_terminal_on_screen` (`tools.py:4146`), which decides
where `run_in_terminal` may type, is not changed: a vim window is not a tmux
client.

### 2. Compose launches tui panes under `org.omarchy.voice.<name>`

```python
# A compose tui pane's app id is this plus its hint: `org.omarchy.` so the
# shell-off gate and Omarchy's terminal tag know it, `voice.` so it is none of
# the ids Omarchy floats (#145).
TUI_PANE_PREFIX = "org.omarchy.voice."
```

`_pane_command` (`tools.py:792-796`) passes
`--app-id={TUI_PANE_PREFIX}{_tui_app_id(target, name)}`. `_tui_app_id` and
`_pane_hint` stay as they are, so the hint remains the bare name (`vim`). The
hint finds the window by substring of its class (`_rank_windows`,
`tools.py:671-687`, score 2). When the terminal drops `--app-id`, as Alacritty
does, it can still match by title, which a prefixed hint would lose. #87's
invariant changes from "the hint is the app id" to "the app id is the prefix
plus the hint". It is still one expression, so the two cannot disagree.

### 3. README

In the shell-off section, add "Return typed into a terminal" now includes any
tui window opened by `omarchy launch tui` or a compose tui pane. Add that
Return into btop or lazygit also waits for a yes with the shell off.

### 4. Unchanged

- `_COMMAND_PROGRAMS` and #129's launch-time rule.
- The shell-on path, `run_pending`, `_releasing`, #139's wait, `describe`.
- `TERMINAL_CLASSES` and `_terminal_on_screen`.
- Omarchy's own `launch tui` ids, which we do not set.

## Alternatives rejected

- **Match `org.omarchy.*` only, and leave compose's bare id.** A compose
  `vim` pane stays open to `:!cmd\n`, one of the two routes the intent names.
- **Remember the addresses of tui windows we launched.** The state is lost
  on restart, misses tuis the user opened, and needs the address after the
  launch, which compose has but `omarchy_cli` does not.
- **Match the bare compose id against the program list.** The class `vim`
  says nothing a GUI app could not also say, and the name is the model's.
- **Process tree (a child on a pty).** Q3: slower, and wrong for a foot server.
- **Hold only tuis with a known shell escape.** Q2: an allowlist of programs
  that fails open.
- **Hold the launch of vim/less (#129's list).** Q4: it does not cover a
  window that is already open, and it adds a yes to every "open vim".
- **Compose id `org.omarchy.<name>`.** Omarchy floats `btop` and `bash` under
  that id, so a compose layout would break.

## Risks

- **Tuis under ids we do not see.** A `Terminal=true` desktop entry started
  through `launch_app` (`uwsm-app`, `tools.py:2662-2666`) should get the
  terminal's own class (foot) and so already hold. That is not measured here,
  and the plan should check it with a fake and the entry's `Exec=`. A terminal
  inside an app (VS Code, emacs vterm) is not seen, as #112 already states.
- **Compose panes open before the upgrade** keep the class `vim` until they
  are closed.
- **Friction.** With the shell off, Return in btop, lazygit or
  `org.omarchy.about` needs a yes (Q2).
- **Kitty and ghostty** take the id as `--class`. For ghostty, a dotted id is
  the valid GTK application-id shape, so the prefix should help rather than
  hurt. Not verified on a host.
- **Existing tests** that pin the compose id change:
  `test_compose.py:75-77` (`--app-id=mynamerm-rf` becomes
  `--app-id=org.omarchy.voice.mynamerm-rf`), `test_compose.py:87-92` (the
  hint is the app id's suffix), and `test_shell_off.py:327` (the deny pattern
  `--app-id=notes` must follow the new argv). A user deny or confirm pattern
  written against `--app-id=<name>` would need to follow it too. This is
  unlikely, and the README note covers it.
- **Hosts.** p620 (shell on) is unaffected. razer and default installs get the
  new holds.

## Verification

All with fakes, `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`. Nothing is
launched or typed.

**Verdict table** in `tests/test_shell_off.py`, through `Executor.call` as the
existing `TABLE` does. Window kind × shell off/on × call. Calls:
`type_text ":!id\n"`, `type_text ":!id"`, `send_shortcut Return`,
`send_shortcut ctrl+m`, `hypr_dispatch send_shortcut {key: Return}`.

| Window (`class` / `initialClass`) | off: type `\n` / Return / ctrl+m / dispatch | off: type no newline | on: all |
| --- | --- | --- | --- |
| `org.omarchy.vim` | HELD ×4 | RAN | RAN |
| `org.omarchy.voice.vim` | HELD ×4 | RAN | RAN |
| `org.omarchy.htop` | HELD ×4 | RAN | RAN |
| `TUI.float` | HELD ×4 | RAN | RAN |
| `firefox` / `org.omarchy.vim` (renamed) | HELD ×4 | RAN | RAN |
| `Alacritty`, `org.omarchy.voice-terminal` | HELD ×4 | RAN | RAN |
| `firefox` | RAN | RAN | RAN |
| unresolvable window | HELD ×4 | RAN | RAN |

The first five rows fail on main; the rest pin today's behaviour. Also:

- a compose tui pane `vim` named `notes` launches
  `omarchy launch tui --app-id=org.omarchy.voice.notes vim`, and its hint is
  still `notes` (fails on main);
- `launch tui vim` and a compose tui `vim` still run with the shell off (the
  launch is not held; #129 unchanged);
- held Return released by `run_pending` presses the key once and is not held
  again.

**Mutation checks.** Each must make at least one named test fail:

- M1: drop `org.omarchy.` from `TUI_CLASS_PREFIXES`.
- M2: drop `tui.` from `TUI_CLASS_PREFIXES`.
- M3: check `class` only, not `initialClass`.
- M4: compose passes the bare `_tui_app_id` (today's argv).
- M5: prefix compose ids with `org.omarchy.` alone (fails the no-float
  assertion that the id is not in Omarchy's float list).
- M6: an unresolvable window counts as not a terminal.
- M7: apply the tui rule with the shell on as well.

**Suites.** `nix develop -c python3 -m pytest -q` is green. The only edited
existing tests are the three named in Risks.
