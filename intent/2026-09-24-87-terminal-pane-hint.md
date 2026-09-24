---
status: draft
issue: 87
author: olafkfreund
---

# Intent: a terminal pane must only take the terminal it launched

Closes #87.

Split out of #75. Its spec deferred this as Q4
(`spec/2026-09-24-75-unmatched-new-window.md:85-93`): "A follow-up issue
should be opened for it."

## Problem

`compose_windows` launches a pane, then waits for its window with
`Executor._await_new_window` (`src/omarchy_voice/tools.py:3032-3088`). Since
#75, a candidate must match the pane's hint, and no other window is returned.
The one exception is an empty hint. A terminal pane always has an empty hint.

`_pane_hint` (`tools.py:622-635`) ends with:

```python
return ""  # a terminal has no distinguishing mark worth guessing at
```

`_await_new_window` drops empty hints (`tools.py:3061`). With no hints left,
every new window that has a class is taken (`tools.py:3081-3082`):

```python
matched = [c for c in fresh
           if any(_window_matches(c, h) for h in hints)] if hints else fresh
```

Its own comment says so: an empty hint is "any classed window, the terminal
pane's behaviour" (`tools.py:3058-3060`), and
`tests/test_compose.py:115-119` (`test_an_unhinted_pane_still_takes_a_classed_window`)
pins it. So if anything else maps during the terminal's 6 s budget
(`PANE_TIMEOUT`, `tools.py:239`), `compose_windows` treats that window as the
terminal. It is counted as opened (`3337`), moved to the target workspace
(`3338-3340`), focused as the preselect anchor for the next pane
(`3291-3293`), handed to `_equalize_columns` (`3343`), focused at the end
(`3345-3347`), and named in "Composed ...". This is the bug #75 fixed for
`web`, `app` and `tui` panes. It survives here only because no hint was ever
chosen for terminals.

### How a terminal pane is launched on this machine

Traced read-only. No terminal was opened.

1. `_pane_command("terminal", target, …)` returns
   `["omarchy", "launch", "terminal", *shlex.split(target)]`
   (`tools.py:650-652`).
2. `omarchy` (omarchy-4.0.4) resolves `launch terminal` to
   `omarchy-launch-terminal` and `exec`s it with the remaining arguments
   unchanged. Only help flags are intercepted (`bin/omarchy`, `exec
   "$binary_path" "${remaining[@]}"`).
3. `omarchy-launch-terminal` has one line of code:
   `exec setsid uwsm-app -- xdg-terminal-exec --dir="$(omarchy-cmd-terminal-cwd)" "$@"`.
4. `xdg-terminal-exec` 0.14.3 reads `~/.config/xdg-terminals.list`, which
   lists `foot.desktop` only. `xdg-terminal-exec --print-id --print-path`
   (prints, launches nothing) gives
   `foot.desktop` → `~/.local/share/applications/foot.desktop` (`Exec=foot`,
   `StartupWMClass=foot`).

So the terminal is **foot**. `hyprctl clients -j` shows one plain foot window
now: `class=foot`, `initialClass=foot`, `initialTitle=foot`. It also shows two
TUIs launched through `omarchy launch tui`, with `class=org.omarchy.herdr`
and **`initialTitle=foot`**.

### Can the launched terminal be marked? Yes, on this route

- `xdg-terminal-exec(1)` documents `--app-id=app_id` ("set app-id (Wayland)")
  and `--title=title`, applied "if supported by terminal and its entry".
- `foot --help` lists `-a,--app-id=ID` and `-T,--title=TITLE`.
- The dry run below prints the command without executing it. The flag passes
  through the same route `omarchy-launch-terminal` uses:

  ```
  $ xdg-terminal-exec --print-cmd --dir=/tmp --app-id=org.omarchy.voice-pane btop
  foot
  --app-id=org.omarchy.voice-pane
  --working-directory=/tmp
  -e
  btop
  ```

- The repo already does this for `tui` panes:
  `omarchy launch tui --app-id=<id>` (`tools.py:653-658`).

Omarchy's own window rules affect which tag can be used:

- `default/hypr/apps/terminals.lua` gives `+terminal` to
  `org\.omarchy\..*`, so an `org.omarchy.*` id keeps terminal theming.
- `default/hypr/apps/system.lua:6-11` floats `org.omarchy.terminal`,
  `org.omarchy.bash` and `org.omarchy.btop` (the class is matched in full).
  A composed pane must not use one of those ids.
- `_terminal_on_screen` (`tools.py:3551-3567`) decides "a terminal is
  visible" by substring against `TERMINAL_CLASSES` (`tools.py:374`: `foot`,
  `term`, …). An id without `foot` or `term` in it would not count as a
  terminal there.

### Demonstrated with fakes

A scratch script, not committed, patches `Executor._query_rows`, `_query_json`,
`_shell`, `_dispatch_lua` and `_wait_tick`, and uses a clock that advances
1 s per look. The real `_tool_compose_windows`, `_pane_hint` and
`_await_new_window` run unchanged. The panes are
`[terminal "", web https://apnews.com]` on workspace 4. In each case the
terminal never maps, and something else does:

```
hint for a terminal pane: '' | argv: ['omarchy', 'launch', 'terminal']
== A. Discord maps while the terminal pane launches; the terminal never does
   launch: omarchy launch terminal
   launch: omarchy launch webapp https://apnews.com
   dispatch: hl.dsp.window.move({ window = "address:0xdiscord", workspace = "4" })
   dispatch: hl.dsp.focus({ window = "address:0xdiscord" })
   dispatch: hl.dsp.window.move({ window = "address:0xap", workspace = "4" })
   dispatch: hl.dsp.focus({ window = "address:0xdiscord" })
   -> True 'Composed workspace 4 in a columns layout: shell, AP.'
== B. the user opens their own foot (Super+Return) meanwhile
   launch: omarchy launch terminal
   launch: omarchy launch webapp https://apnews.com
   dispatch: hl.dsp.window.move({ window = "address:0xuserfoot", workspace = "4" })
   dispatch: hl.dsp.focus({ window = "address:0xuserfoot" })
   dispatch: hl.dsp.window.move({ window = "address:0xap", workspace = "4" })
   dispatch: hl.dsp.focus({ window = "address:0xuserfoot" })
   -> True 'Composed workspace 4 in a columns layout: shell, AP.'
== C. would a class hint 'foot' tell them apart?
   _window_matches('foot', 'foot') -> True
   _window_matches('org.omarchy.herdr', 'foot') -> True
   _window_matches('discord', 'foot') -> False
== D. a tagged app-id hint
   _window_matches('foot', 'org.omarchy.voice-pane') -> False
   _window_matches('org.omarchy.herdr', 'org.omarchy.voice-pane') -> False
   _window_matches('discord', 'org.omarchy.voice-pane') -> False
   _window_matches('org.omarchy.voice-pane', 'org.omarchy.voice-pane') -> True
```

In (A), the user's Discord is pulled off workspace 1, tiled, focused and
reported as "shell". The result is `ok=True`, with no "did not appear" note.
(B) shows the limit of the first option in the issue, the default terminal's
class. The hint `foot` accepts the user's own foot, and C shows it also
accepts any Omarchy TUI, because those are born with `initialTitle=foot`.
Only a mark on the launched window (D) tells it apart from every other window.

### Evidence from the session log

`~/.local/state/omarchy-voice/session.log` covers 2026-09-11 11:21 to
2026-09-24 12:57, 2988 lines. It has **no `compose_windows` action**. It has
6 bare `omarchy launch terminal` actions. Those went through the `omarchy`
tool, which does not wait for a window, so this bug does not affect them.
There is **no evidence from use**. The case rests on the code and the fakes,
as #75's did.

## Proposed outcome

- A terminal pane in `compose_windows` is placed only if the window is the
  terminal that pane launched. Any other new window, including another foot
  or an Omarchy TUI, is left where it is. The summary names it, the way
  #75's "Did not appear as asked" note already does for other panes.
- A terminal that did not appear is reported that way, never as composed.
- A composed terminal still looks and behaves like a terminal. It tiles,
  keeps Omarchy's terminal tag, and counts as a visible terminal for
  `run_in_terminal`.

## Affected users and systems

- `src/omarchy_voice/tools.py`: `_pane_hint`, `_pane_command` (terminal
  branch), and possibly `_await_new_window`'s empty-hint rule and
  `TERMINAL_CLASSES`.
- `tests/test_compose.py:115-119` and `:337-345`, which pin "empty hint takes
  any classed window" today.
- The Omarchy launch route (`omarchy-launch-terminal` → `xdg-terminal-exec`
  → the terminal named in `xdg-terminals.list`). It is read, not changed.
- Anyone who composes a workspace with a terminal pane, on any engine,
  through the shared `Executor`.

## Constraints

- **#75's guarantee stays.** No unmatched window is moved, focused, used as
  an anchor or reported as composed. The `_unmatched_new_windows` report path
  is reused, not duplicated.
- **The #24 baseline guard stays** (a failed `before` query refuses to
  launch).
- **No longer waits.** `PANE_TIMEOUT["terminal"]` stays at 6 s.
- **Omarchy's config is not edited**, and the user's terminal choice is not
  overridden. Whatever mark is used must travel through `omarchy launch
  terminal` → `xdg-terminal-exec` as it is.
- **No id Omarchy floats.** `org.omarchy.terminal`, `org.omarchy.bash` and
  `org.omarchy.btop` are out (`system.lua:6-11`).
- **Proven with fakes.** No terminal is launched on the live desktop to prove
  it.

## Open questions

1. **Tag the launch or match the default class?** The issue lists both. The
   class (`foot` here) is read from config and needs no launch change. It
   still takes the user's own foot and any Omarchy TUI (demo B, C). A tag
   (`--app-id`) is unique to the pane and uses the route the `tui` pane
   already uses. Recommendation: tag.
2. **Which id?** It must be `org.omarchy.*` to keep the terminal tag, must
   not be one of the three floated ids, and should contain `term` or `foot`
   so `_terminal_on_screen` counts it (e.g. `org.omarchy.voice-terminal`).
   Should it instead be one fixed id plus a change to `TERMINAL_CLASSES`?
3. **What if the user's terminal ignores `--app-id`?** `xdg-terminal-exec`
   "silently discards" options the entry does not support. The tagged pane
   would then never match, and would be reported as "did not appear" while
   it sits on screen. That fails closed, but it is wrong. Should we check
   with `xdg-terminal-exec --print-cmd` that the flag survives, and fall back
   to the class hint (or refuse terminal panes) when it does not?
4. **One id for every terminal pane, or one per launch?** Panes launch in
   order, each with its own `before`, so one fixed id works within a single
   compose. It can still take a terminal from an earlier compose that mapped
   late. Is that worth a nonce?
5. **Should `_await_new_window` still accept an empty hint as "any window"?**
   Once terminals have a hint, no caller passes `""` on purpose. Should an
   empty hint then return None, so the case cannot come back?
