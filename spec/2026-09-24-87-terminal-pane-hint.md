---
status: approved
issue: 87
intent: intent/2026-09-24-87-terminal-pane-hint.md
---

# Spec: a terminal pane takes only the terminal it launched

Closes #87 (split out of #75, whose spec deferred it as Q4).
Line numbers are against `main` at `40c80fb`. The branch changes no code.

## The intent's open questions, answered

The intent was approved without answers to its five questions, so each one is
decided here with the reasoning shown. The claims were checked read-only: no
terminal or app was opened, and the desktop was only queried
(`xdg-terminal-exec --print-cmd`, `--help`, `hyprctl clients -j`, and reading
files). **Any of these decisions can be rejected at this gate.**

### Q1: tag the launch or match the default class? Tag it.

Every terminal pane is launched with `--app-id=org.omarchy.voice-terminal`,
and that id is the hint. The class (`foot` here) is rejected as the primary
hint. The intent's demos B and C show it accepts the user's own foot, and any
Omarchy TUI too, because TUIs are born with `initialTitle=foot`. The class
survives only as the fallback in Q3, for terminals that cannot be tagged.

The tag uses the same route the `tui` pane already uses
(`omarchy launch tui --app-id=…`, `tools.py:653-658`). It survives the
terminal route on this machine without being changed. This is a dry run that
prints the command and launches nothing:

```
$ xdg-terminal-exec --print-cmd --dir=/tmp --app-id=org.omarchy.voice-terminal tmux new -A
foot
--app-id=org.omarchy.voice-terminal
--working-directory=/tmp
-e
tmux
new
-A
$ xdg-terminal-exec --print-cmd --dir=/tmp          # untagged, for comparison
foot
--working-directory=/tmp
```

`omarchy-launch-terminal` (omarchy-4.0.4) is
`exec setsid uwsm-app -- xdg-terminal-exec --dir="$(omarchy-cmd-terminal-cwd)" "$@"`.
So `--app-id=…` placed before the command's own words reaches
`xdg-terminal-exec` as an option, exactly as in the dry run.

### Q2: which id? `org.omarchy.voice-terminal`, fixed. `TERMINAL_CLASSES` is not changed.

It meets each of the intent's three conditions. Demonstrated with Omarchy
4.0.4's own patterns and the real `_window_matches` and `_terminal_on_screen`
(scratch script with fakes, not committed):

```
B. _window_matches(window, 'org.omarchy.voice-terminal')
   foot                         False      <- the user's own foot
   org.omarchy.herdr            False      <- an Omarchy TUI, initialTitle=foot
   discord                      False
   org.omarchy.voice-terminal   True
C. _terminal_on_screen with only the tagged pane on visible workspace 4
   True | TERMINAL_CLASSES hit: ['term']
D. Omarchy 4.0.4 class rules vs org.omarchy.voice-terminal
   system.lua:6-11 +floating-window   fullmatch=False search=False
   terminals.lua:5-8 +terminal        fullmatch=True search=True
```

- **Not floated, and not specially handled.** `default/hypr/apps/system.lua:6-11`
  floats `org.omarchy.btop|org.omarchy.terminal|org.omarchy.bash|…|Omarchy|…`.
  The id matches none of them, whether Hyprland matches the whole class (as
  Omarchy's comment in `terminals.lua:4` says) or a substring. The only other
  class rules that match it are `windows.lua:3,6` (`.*`: suppress maximize,
  default opacity), which apply to every window, foot included. The rest of
  `default/hypr/apps/*.lua` names other apps. `~/.config/hypr/*.lua` has one
  class rule, for `dev.csfh.atmos`.
- **Keeps the terminal tag.** `terminals.lua:5-8` tags `org\.omarchy\..*` with
  `+terminal`. Omarchy's terminal copy and paste binding keys on that tag
  (`bindings/clipboard.lua:20-32`), not on the class. The live Omarchy TUIs
  already show the effect: `hyprctl clients -j` gives `org.omarchy.herdr`
  `floating=False tags=['default-opacity*', 'terminal*']`.
- **Counts as a visible terminal.** `_terminal_on_screen` (`tools.py:3551-3567`)
  substring-matches the class against `TERMINAL_CLASSES` (`tools.py:374-375`).
  `term` is in it, so the tagged pane counts for `run_in_terminal` with no
  change to the tuple (demo C).
- Not `org.omarchy.terminal`: that is one of the floated ids.

### Q3: a terminal that ignores `--app-id`? Probe with `--print-cmd` and fall back to the terminal's binary name.

`xdg-terminal-exec` adds the flag only when the chosen entry declares
`X-TerminalArgAppId`. Otherwise it drops it
(`.xdg-terminal-exec-wrapped:1430-1443`, which logs
"terminal entry has no TerminalArgAppId=" and prepends nothing). The entries
installed here:

| Entry | `X-TerminalArgAppId` | Terminal's own flag (`--help`) |
| --- | --- | --- |
| `foot.desktop` (the one in `xdg-terminals.list`) | `--app-id=` | `-a, --app-id=ID` |
| `kitty.desktop` | `--class` (two words) | `--class, --app-id=[kitty]`: "On Wayland set the application id" |
| `com.mitchellh.ghostty.desktop` | `--class=` | config key `class` (GTK application id) |
| `Alacritty.desktop` (nixpkgs) | **none** (no `X-Terminal*` keys at all) | `--class <general>`: "Defines window class/app_id on X11/Wayland" |

So Alacritty supports the id itself, but its entry does not tell
`xdg-terminal-exec` how to pass it. The flag is then dropped silently. A tag
hint would never match, and every Alacritty user's terminal pane would be
reported as "did not appear" while it sat on screen. That is the wrong answer
the intent described.

(Kitty, ghostty and Alacritty were checked by `--help` and by reading their
entries and the script. They could not be dry-run as the selected terminal:
that needs a different `xdg-terminals.list`, which means overriding
`XDG_CONFIG_HOME`, and that was not done.)

**Decision:** before waiting for a terminal pane, run one read-only probe,
`xdg-terminal-exec --print-cmd --app-id=org.omarchy.voice-terminal`. It takes
17 ms here (5 runs: 16.0-17.7 ms), against a 6 s budget, so it runs for each
terminal pane and is not cached. A cache would go stale if the user switches
terminals.

- The printed command carries the id (`--app-id=ID`, `--class=ID`, or
  `--class` followed by `ID`) → the hint is the id.
- The id is missing → the hint is the basename of the printed command's first
  word, e.g. `alacritty`. This is the class match the intent rejected as the
  primary hint. It is kept only for terminals that cannot be tagged. It is
  still far narrower than today's empty hint: Discord no longer matches. The
  exposure that remains, another window of the same terminal mapping in the
  same ≤6 s, is listed under Risks.
- The probe fails (no binary, non-zero exit, timeout, empty output) → the
  hint is `""`, which Q5 makes match nothing. The pane is reported through
  #75's path, naming whatever did appear. It fails closed.

Refusing terminal panes for untaggable terminals was rejected. Alacritty is
one of Omarchy's supported terminals, and refusing would turn a narrow race
into "compose never works with a terminal" for those users.

### Q4: one id for every pane, or one per launch? One fixed id.

A per-launch nonce protects against a single case. A terminal pane takes
longer than its 6 s budget, and then maps while a *later* terminal pane (in
the same compose, or in the next one) is waiting. With a fixed id, that later
pane adopts the late window. The harm is contained. The window is one this
tool launched, for the same kind of pane, and compose had already switched to
the target workspace before launching it (`tools.py:3264-3267`). So the user's
own windows are never touched, which is what #87 is about. The pane that loses
its window is still reported by #75's note.

A nonce has costs. `_pane_command` and `_pane_hint` are pure functions of
`(kind, target, name)`, called separately (`tools.py:3249, 3279, 3316`). A
nonce would have to be generated in compose and threaded into both. It would
also give every composed terminal a different class, which a user's own
Hyprland rule could then match only by regex. **Upgrade path, if it is ever
needed:** append a fixed-width hex suffix (`…-voice-terminal-1a2b3c4d`). Fixed
width keeps the substring match in `_window_matches` from confusing `-1` with
`-12`. `term` still hits `TERMINAL_CLASSES`, and `org\.omarchy\..*` still tags
it.

### Q5: should an empty hint stop meaning "any window"? Yes, everywhere.

After Q1, no caller passes `""` on purpose. The places that can still produce
one by accident are all better off matching nothing:

- the terminal probe failing (Q3);
- `open_page` / `web_search` on a URL with no host (`tools.py:3909, 3938-3939`
  pass `hostname or ""`). Today that adopts whatever maps first;
- a `tui` pane whose `name` sanitises to nothing, e.g. `"!!!"`. `_pane_hint`
  (`tools.py:630-632`) then returns `""`. `_pane_command` (`tools.py:655-658`)
  falls back to `argv[0]` for the `--app-id`, so today the hint and the id
  already disagree, and the pane takes any window. The fix is to derive both
  from one helper (Design 2).

`_await_new_window` returns `None` at once when no non-empty hint is left. The
caller then reports the pane through the #75 path.

## Design

All in `src/omarchy_voice/tools.py`. No new module, config key or dependency.

### 1. `TERMINAL_PANE_ID` next to `TERMINAL_CLASSES` (`374-375`)

`TERMINAL_PANE_ID = "org.omarchy.voice-terminal"`, with a comment on the three
Q2 conditions: `org.omarchy.*` for the tag, not a floated id, contains `term`.

### 2. `_pane_command` (`638-664`) and `_pane_hint` (`622-635`)

- Terminal branch (`650-652`):
  `["omarchy", "launch", "terminal", f"--app-id={TERMINAL_PANE_ID}", *shlex.split(target)]`.
  One expression. The `if target` split goes away, because `shlex.split("")`
  is `[]`.
- `_pane_hint("terminal", …)` returns `TERMINAL_PANE_ID` instead of `""`
  (`635`).
- `tui`: a module helper `_tui_app_id(target, name)` holds the expression now
  inlined at `655-657`. `_pane_command` and `_pane_hint` both call it, so the
  hint is always the id the pane was launched with (Q5, third bullet).

### 3. `Executor._terminal_pane_hint() -> str`, next to `_unmatched_new_windows` (after `3104`)

The Q3 probe: `subprocess.run(["xdg-terminal-exec", "--print-cmd",
f"--app-id={TERMINAL_PANE_ID}"], capture_output=True, text=True, timeout=4)`,
the same direct `subprocess.run` style as the other read-only probes
(`tools.py:2269`). It returns `TERMINAL_PANE_ID` when a printed line is the id
or ends with `=` + id. Otherwise it returns the lower-cased basename of the
first printed line. On `OSError`, a timeout, a non-zero exit or empty output
it returns `""`. The docstring says why the fallback exists (Alacritty's entry)
and what it costs (Risks, first bullet).

### 4. `_tool_compose_windows` (`3255-…`)

At `3316`: `hint = self._terminal_pane_hint() if kind == "terminal" else _pane_hint(…)`.
Everything after that is unchanged. A terminal that does not match lands in
`unmatched` (`3327-3335`) with what appeared instead, or in `slow`. It is
never moved, focused, used as an anchor or counted in "Composed".

### 5. `_await_new_window` (`3032-3088`)

- Right after `hints` is built (`3061`): `if not hints: return None`.
- `3081-3082` loses the `if hints else fresh` arm.
- The comment at `3058-3060` and the docstring say that an empty hint matches
  nothing (#87), instead of "any classed window, the terminal pane's behaviour".

`_open_web_window` (`3728-…`) and its callers do not change. An empty host now
reaches the "did not open a window" path they already have.

## Alternatives rejected

- **The default terminal's class as the only hint** (the issue's first
  option). It takes the user's own foot and every Omarchy TUI (the intent's
  demos B and C). It is kept only as the Q3 fallback.
- **`--title` instead of `--app-id`.** A shell or the command that runs
  retitles the window right away. `_rank_windows` scores `initialTitle`, so it
  would work, but title is weaker than class there (`score 1.0` vs `4.0`), and
  an Omarchy TUI's `initialTitle` is already `foot`. The app-id is also what
  makes the window a terminal to Omarchy.
- **Add the id to `TERMINAL_CLASSES`.** Not needed. `term` already matches.
- **Refuse terminal panes when the tag cannot be applied.** See Q3. It turns
  a narrow race into a feature that never works for Alacritty users.
- **Edit `Alacritty.desktop` (add `X-TerminalArgAppId=--class=`) or call the
  terminal directly.** The intent rules both out: Omarchy's config and the
  user's terminal choice are not touched, and the launch goes through
  `omarchy launch terminal`.
- **A per-launch nonce.** See Q4. It is kept as the upgrade path.
- **Keep `""` as "any window" for some other caller.** None needs it (Q5).
  Keeping it would leave the #75 bug one empty string away.

## Risks

- **Untaggable terminals get the class fallback.** On this machine that means
  Alacritty, whose nixpkgs entry has no `X-TerminalArgAppId`. For those users,
  another window of the same terminal that maps in the same ≤6 s is still
  taken as the pane. That is the user's own `Super+Return`, and it is narrower
  than today's "any window". foot, kitty and ghostty are tagged.
- **ghostty runs `--gtk-single-instance=true`.** If a running instance ignored
  a new `--class`, the flag would pass the probe but never reach the window.
  That was not demonstrated either way (no ghostty was launched). If it
  happens, the pane fails closed: it is reported with the ghostty window named,
  and left where it opened.
- **The probe and the launch see different environments.** The probe runs in
  the daemon's environment. The launch runs under `uwsm-app` in the session's.
  `xdg-terminal-exec` reads `$XDG_CURRENT_DESKTOP`-prefixed lists first
  (`xdg-terminals.list` is the only one here). If the two picked different
  terminals, the hint would be wrong, and again the pane would fail closed and
  be reported.
- **A later Omarchy could change the route.** If `omarchy-launch-terminal`
  stopped using `xdg-terminal-exec`, the probe would describe a route that is
  no longer used. Omarchy is pinned by the flake, so the bump that changes this
  is a visible diff, and the failure mode is the #75 report, not a stolen
  window.
- **Composed terminals change class, from `foot` to
  `org.omarchy.voice-terminal`.** A user rule written against the class `foot`
  no longer applies to them. There is none on this machine. Omarchy's rules
  key on the tag.
- **The pane's argv changes.** A user deny pattern is matched against
  `omarchy launch terminal --app-id=org.omarchy.voice-terminal …`
  (`tools.py:3284-3287`). A pattern like `\blaunch terminal\b` still matches.
  One anchored as `launch terminal$` would stop matching a bare terminal pane.
- **Empty hints now fail closed** (Q5). An `open_page` on a host-less URL now
  reports "did not open a window" instead of reading whatever mapped first.
  That is the intended change.
- **Out of scope, unchanged:** `_ensure_visible_session` (`3569-3592`) also
  launches `omarchy launch terminal tmux`, but it waits on tmux attaching, not
  on a window, so it is not exposed to this bug.
- **Hosts:** the code does not depend on the host. The terminal census is from
  p620. Another host whose chosen terminal lacks the entry key gets the
  fallback, and one where the probe fails gets the report.
- **Merge overlap:** the hunks are `374-376`, `622-664`, `3032-3088`, a new
  method after `3104`, and one line at `3316`. Other open branches that touch
  `tools.py` could conflict textually around `_await_new_window` or compose.
  Nothing here changes a signature they call.

## Verification

Unit tests with fakes only. The seams are `_query_rows`, `_query_json`,
`_shell`, `_dispatch_lua`, `_wait_tick`, `time.monotonic` (the existing
`moving_clock`), and `subprocess.run` / `_terminal_pane_hint` for the probe.
Nothing is launched, and no test runs the real `xdg-terminal-exec`. In
`tests/test_compose.py`, next to `StrangerWindowTests`:

1. **The intent's demo A as a regression test, which fails on `main`.**
   Compose `[terminal "" name "shell", web https://apnews.com name "AP"]` on
   workspace 4, with the probe faked to return `TERMINAL_PANE_ID`. Launching
   the terminal adds `DISCORD`, and the terminal never maps. Launching the web
   pane adds the AP window. Assertions:
   - no `window.move` or `focus` dispatch names `0xdiscord`;
   - the output does not say "Composed … shell";
   - it contains "Did not appear as asked", `discord` and `address:0xdiscord`;
   - AP is still moved to 4 and focused.

   On `main` this test fails at the first assertion. The intent's run shows
   `window.move … 0xdiscord`.
2. **Demo B/C: the user's own foot and an Omarchy TUI are not taken.** The
   same compose, with `USERFOOT` and a `org.omarchy.herdr`/`initialTitle=foot`
   row appearing instead. Neither is moved. Both are named.
3. **The tagged terminal composes.** A row with `class=org.omarchy.voice-terminal`
   appears → moved to the workspace, counted in "Composed … shell".
4. **The probe's four outcomes**, faking `subprocess.run`:
   `foot\n--app-id=org.omarchy.voice-terminal\n` → the id;
   `kitty\n--class\norg.omarchy.voice-terminal\n` → the id;
   `/nix/store/…/bin/alacritty\n` → `alacritty`;
   `OSError`, timeout, exit 1 and empty stdout → `""`.
5. **The Alacritty fallback end to end:** probe → `alacritty`, a row with
   class `Alacritty` appears → composed. `DISCORD` alone → not taken.
6. **The argv:** `_pane_command("terminal", "", "")` and
   `_pane_command("terminal", "htop -d 5", "")` carry
   `--app-id=org.omarchy.voice-terminal` right after `launch terminal`, before
   the command's words. `test_panes_never_launch_or_focus` (`:60-66`) still
   passes.
7. **Counted as visible:** `_terminal_on_screen` with only a
   `class=org.omarchy.voice-terminal` row on a visible workspace → `True`
   (demo C above).
8. **Q5:** `_await_new_window(before, 5.0, "")` and `("", "")` with `DISCORD`
   appearing → `None`. These replace `test_an_unhinted_pane_still_takes_a_classed_window`
   (`:115-119`), which pins the bug, and the first assertion of
   `test_hint_tuple_edges` (`:337-345`). `test_hints_come_off_the_target`
   (`:92`) expects `TERMINAL_PANE_ID` for a terminal.
9. **The tui hint is its app-id:** `_pane_hint("tui", "btop", "!!!")` equals
   the id inside `_pane_command("tui", "btop", "!!!")`.
10. Existing tests unchanged and passing, in particular `ComposeRunTests`
    (`:217-234`, which fakes `_await_new_window` and gets the probe faked too),
    the `"foot"`-hinted `test_reach.py:643` and `test_hypr_events.py:150,169`,
    and the `test_web.py` #24/#75 cases.

Then `nix flake check --no-write-lock-file`, which runs the whole suite.

**Read-only live check, launching nothing:**

```
$ xdg-terminal-exec --print-cmd --app-id=org.omarchy.voice-terminal
foot
--app-id=org.omarchy.voice-terminal
$ PYTHONPATH=src python3 -c 'from omarchy_voice.config import Config; from omarchy_voice.tools import Executor; print(Executor(Config())._terminal_pane_hint())'
org.omarchy.voice-terminal
```

The second command runs only the `--print-cmd` probe. No window is opened,
and the desktop is not driven.
