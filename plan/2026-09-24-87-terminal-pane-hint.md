---
status: approved
issue: 87
spec: spec/2026-09-24-87-terminal-pane-hint.md
---

# Plan: a terminal pane takes only the terminal it launched

Closes #87. Line numbers are against `main` at `72f9ab6`, and each one was
checked there. The spec cited `40c80fb`. Everything from `tools.py:3000` on has
moved by about 6 lines since then (the spec's `3316` is `3322` now), and this
plan uses the new numbers. On this branch `src/` and `tests/` are identical to
`main`.

## Approved decisions, carried over from the spec

1. **Tag the launch (Q1).** Every terminal pane is launched as
   `omarchy launch terminal --app-id=org.omarchy.voice-terminal …`, and that id
   is its hint. The flag goes before the command's own words, so
   `omarchy-launch-terminal` hands it to `xdg-terminal-exec` as an option. That
   is the same route the `tui` pane already uses. The terminal's class (`foot`)
   is rejected as the primary hint because it also takes the user's own foot
   and every Omarchy TUI (`initialTitle=foot`). It is used only as the
   fallback in decision 3.
2. **One fixed id, `org.omarchy.voice-terminal`, as the module constant
   `TERMINAL_PANE_ID` (Q2, Q4).** It is `org.omarchy.*`, so Omarchy's
   `terminals.lua:5-8` still gives it `+terminal`. It is not one of the ids
   `system.lua:6-11` floats, so `org.omarchy.terminal` is out. It contains
   `term`, so `_terminal_on_screen` counts it and `TERMINAL_CLASSES` is **not**
   changed. There is no per-launch nonce. If one is ever needed, the upgrade
   path is a fixed-width hex suffix (`…-voice-terminal-1a2b3c4d`).
3. **A read-only pre-check before each terminal pane's wait (Q3).**
   `Executor._terminal_pane_hint()` runs
   `xdg-terminal-exec --print-cmd --app-id=org.omarchy.voice-terminal` through
   `subprocess.run(…, capture_output=True, text=True, timeout=4)`. It takes
   about 17 ms here, so it runs for every terminal pane and is **not cached**,
   because a cache goes stale when the user switches terminals.
   - If the printed command carries the id (a line equal to it, as in kitty's
     two-word `--class ID`, or a line ending in `=` + id, as in `--app-id=ID`
     or `--class=ID`), the hint is the id.
   - If the id was dropped (Alacritty's nixpkgs entry has no
     `X-TerminalArgAppId`), the hint is the lower-cased basename of the first
     printed line, e.g. `alacritty`. This is the **fallback to the terminal's
     program name**.
   - If the check fails (`OSError`, a timeout, a non-zero exit, empty output),
     the hint is `""`. By decision 5 that matches nothing, so the pane is
     **reported, not guessed**, through #75's "Did not appear as asked" path.
   Refusing terminal panes for terminals that cannot be tagged is rejected.
4. **The `tui` hint and the `tui` `--app-id` come from one helper.** A module
   helper `_tui_app_id(target, name)` holds the expression now inlined at
   `tools.py:657`, and both `_pane_command` and `_pane_hint` call it. Today
   they disagree for a name that sanitises to nothing (`"!!!"`): the argv falls
   back to `argv[0]`, and the hint is `""`.
5. **An empty hint matches nothing, everywhere (Q5).** `_await_new_window`
   returns `None` at once when no non-empty hint is left, and the
   `if hints else fresh` arm is deleted. This covers the failed probe, a
   `web_search`/`open_page` URL with no host (`tools.py:3925`, `3954-3955`),
   and the `tui` case (decision 4 closes it at the source, and this closes it
   again). No caller keeps `""` as "any window".
6. **Compose changes in one place.** At `tools.py:3322` the hint is
   `self._terminal_pane_hint() if kind == "terminal" else _pane_hint(…)`.
   Everything after that is unchanged. A terminal whose window does not match
   goes into `unmatched` (with what appeared) or `slow`. It is never moved,
   focused, used as an anchor or counted in "Composed" (#75's guarantee).
7. **Unchanged:** `PANE_TIMEOUT["terminal"]` stays 6 s, the #24 baseline guard
   (`3305-3312`) stays, `_open_web_window` and its callers are not edited, and
   `_ensure_visible_session` (`3575-…`) is out of scope (it waits on tmux
   attaching, not on a window). Omarchy's config, the desktop entries and the
   user's terminal choice are not touched. The launch still goes through
   `omarchy launch terminal`.
8. **Rejected, and not to be reintroduced while implementing:** the default
   terminal's class as the only hint; `--title` instead of `--app-id`; adding
   the id to `TERMINAL_CLASSES`; refusing untaggable terminals; editing
   `Alacritty.desktop` or calling the terminal directly; a per-launch nonce;
   keeping `""` as "any window" for any caller.
9. **No new module, config key or dependency.** All code is in
   `src/omarchy_voice/tools.py`. Tests use fakes only. No test runs the real
   `xdg-terminal-exec`, and nothing is launched on the desktop.

Details the spec left implicit, decided here:

- **The new tests must fail on `main` for the right reason.** Two traps:
  - `StrangerWindowTests`' launch fake keys what maps on `argv[-1]`
    (`tests/test_compose.py:291-292`). A bare terminal pane's `argv[-1]` is
    `terminal` on `main` and `--app-id=org.omarchy.voice-terminal` after the
    change. So the fake also looks up `" ".join(argv[:3])`
    (`"omarchy launch terminal"`) when `argv[-1]` has no entry. #75's `app`
    tests still key on `argv[-1]` and do not change.
  - Step 1 must not import `TERMINAL_PANE_ID` or `_terminal_pane_hint`. On
    `main` that `ImportError` would fail the whole module. The tests use the
    literal `"org.omarchy.voice-terminal"` and fake the probe as an instance
    attribute (`ex._terminal_pane_hint = lambda: …`), the way `setUp` already
    replaces `_shell` and `_wait_tick`. On `main` that attribute is simply
    never called.
- **Clock and entries, reused from #75.** The new compose tests live in a
  `TerminalPaneTests(unittest.TestCase)` next to `StrangerWindowTests` with the
  same `setUp`: `moving_clock()` (`tests/test_compose.py:259-266`),
  `_wait_tick` as a no-op, and `omarchy_voice.tools.app_dirs` and
  `shutil.which` patched (`:296-303`). It is not a subclass, so #75's tests do
  not run twice. The shared `setUp` body can move into a small mixin if that
  keeps it shorter. That choice is left to the implementer.
- **Where the probe is called.** It is called at `3322`, after the launch and
  before the wait, as the spec's Design 4 says. It does not delay the launch.
  `before` is taken at `3313`, so a window that maps during the probe is still
  new. The wait's deadline is computed inside `_await_new_window`, so the probe
  does not shorten it.
- **`_tui_app_id("", name)` returns `""`.** Only `_pane_hint` can call it with
  an empty target, because `_pane_command` returns `None` first (`654-655`).
  Decision 5 then makes that hint match nothing.
- **A wrapped `Exec` line** (e.g. `env X=1 alacritty`) would give the fallback
  hint `env`. That matches nothing, so the pane fails closed and is reported.
  It is not special-cased.
- **Test 7 (counted as visible) is a subTest in `tests/test_terminal.py`**:
  add `org.omarchy.voice-terminal` to the class tuple in
  `test_the_common_terminals_are_recognised` (`VisibilityTests`, `:357-362`).
  That fixture already fakes `_visible_workspaces` and `_query_json`.

## Landing order

**#88 (`fix/88-desktop-suffix-in-id`, spec approved, no code yet) edits the
same two functions.** Its spec rewrites the `app` branch of `_pane_hint`
(`tools.py:633-634`) and of `_pane_command` (`659-664`) to call a new
`_desktop_id` helper placed after `_desktop_entry_exists` (`~1524`). It also
adds or repeats Telegram tests near `tests/test_compose.py:359`. This plan edits
the `tui` and `terminal` branches of the same functions (`628-630`, `635`,
`650-658`) and adds tests in the same region. The hunks are adjacent but
not the same lines, and neither change depends on the other's behaviour. So:

- **Either may land first. The second rebases** onto `origin/main`, keeps both
  sides of every conflict (#88's `_desktop_id(...)` lines in the `app` branches,
  this plan's `terminal`/`tui` lines), and re-runs step 7. Step 8 covers it.
- No other open branch touches `_await_new_window`, `_pane_hint`,
  `_pane_command` or `_tool_compose_windows` as of `72f9ab6`. Re-check with
  `git log origin/main` at step 8.

## Steps

0. **Baseline.** `nix develop -c pytest tests -q` → **930 passed**, 473
   subtests, 1 warning (measured on this branch at `72f9ab6`). Record it.

1. **`tests/test_compose.py`: write the regression tests first and watch them
   fail on `main`.** Add the `argv[:3]` lookup to the launch fake (see the
   details above). Add `TerminalPaneTests` with tests 1, 2, 3, 5 and 8 from
   "Tests" below, faking the probe as an instance attribute and using the
   literal id.
   → verify by `nix develop -c pytest tests/test_compose.py -q`. Exactly
   those five fail. Tests 1, 2, 3 and 5 fail at their "no dispatch names
   `0xdiscord`" (or `0xuserfoot`/`0xherdr`) assertion, meaning Discord or the
   user's foot was adopted as the terminal. Test 8 fails at its first
   `assertIsNone`, because `""` takes Discord. #75's `StrangerWindowTests`
   all still pass.

2. **`src/omarchy_voice/tools.py`: `TERMINAL_PANE_ID` after `TERMINAL_CLASSES`
   (`374-375`).** Add `TERMINAL_PANE_ID = "org.omarchy.voice-terminal"` with a
   comment on the three conditions: `org.omarchy.*` for the terminal tag, not
   an id Omarchy floats, and it contains `term`.
   → verify by `nix develop -c python -c 'from omarchy_voice.tools import TERMINAL_PANE_ID as t, TERMINAL_CLASSES as c; assert any(n in t for n in c)'`.

3. **`tools.py`: `_tui_app_id`, `_pane_hint` (`622-635`), `_pane_command`
   (`638-665`).**
   - New module function `_tui_app_id(target, name) -> str`, just before
     `_pane_hint`: `argv = shlex.split(target) if target else []`. If `argv`
     is empty, return `""`. Otherwise return
     `re.sub(r"[^A-Za-z0-9_.-]", "", name or argv[0]) or argv[0]`, the
     expression now at `657`.
   - `_pane_hint`: the `tui` branch (`628-630`) returns
     `_tui_app_id(target, name)`. The last line (`635`) returns
     `TERMINAL_PANE_ID`, with a comment saying compose asks
     `_terminal_pane_hint` instead (#87).
   - `_pane_command`: the terminal branch (`650-652`) becomes the single
     expression `["omarchy", "launch", "terminal", f"--app-id={TERMINAL_PANE_ID}", *shlex.split(target)]`.
     The `tui` branch (`653-658`) keeps its `if not target: return None`, then
     `argv = shlex.split(target)` and
     `return ["omarchy", "launch", "tui", f"--app-id={_tui_app_id(target, name)}", *argv]`.
   → verify by tests 6 and 9, `test_panes_never_launch_or_focus` (`:60-66`)
   and `test_tui_app_id_is_sanitised` (`:72-75`) passing.
   `test_hints_come_off_the_target` (`:88-92`) is updated in step 6.

4. **`tools.py`: `Executor._terminal_pane_hint(self) -> str`, after
   `_unmatched_new_windows` (`3096-3111`).** It runs
   `subprocess.run(["xdg-terminal-exec", "--print-cmd", f"--app-id={TERMINAL_PANE_ID}"], capture_output=True, text=True, timeout=4)`,
   in the direct style of `tools.py:2275`, catching
   `(OSError, subprocess.SubprocessError)` → `""`. A non-zero `returncode` or
   no non-blank line → `""`. Then `lines = [l.strip() for l in out.stdout.splitlines() if l.strip()]`.
   If any line equals `TERMINAL_PANE_ID` or ends with `"=" + TERMINAL_PANE_ID`,
   return `TERMINAL_PANE_ID`. Otherwise return
   `os.path.basename(lines[0]).lower()`. The docstring says why the fallback
   exists (Alacritty's entry drops the flag), what it costs (another window of
   the same terminal in the same ≤6 s), and that a failed check fails closed.
   → verify by test 4 (all seven cases).

5. **`tools.py`: `_await_new_window` (`3038-3094`) and
   `_tool_compose_windows` (`3322`).**
   - Right after `hints = …` (`3067`): `if not hints: return None`.
   - `3087-3088` becomes
     `matched = [c for c in fresh if any(_window_matches(c, h) for h in hints)]`.
   - Rewrite the comment at `3064-3066` and add a docstring line: an empty
     hint, or an all-empty tuple, matches nothing (#87). It no longer means
     "any classed window, the terminal pane's behaviour".
   - `3322`: `hint = self._terminal_pane_hint() if kind == "terminal" else _pane_hint(kind, str(pane.get("target", "")), str(pane.get("name", "")))`.
     `3323-3326` are unchanged: the `app` tuple still uses `_desktop_wm_class`.
   → verify by `nix develop -c pytest tests/test_compose.py -q`. Tests 1-5
   and 8 pass. `test_an_unhinted_pane_still_takes_a_classed_window`
   (`:115-119`) and the first assertion of `test_hint_tuple_edges`
   (`:342-343`) now fail. They pin the bug, and step 6 replaces them.

6. **Tests that pin the old behaviour, and the probe seam in an existing test.**
   - `tests/test_compose.py:115-119`: rename it to
     `test_an_unhinted_pane_takes_nothing` and expect `None`.
   - `:342-343`: `("", "")` with Discord present → `None`.
   - `:92`: `_pane_hint("terminal", "", "")` → `TERMINAL_PANE_ID`, now
     imported.
   - `ComposeRunTests.test_a_pane_whose_window_never_appears_is_reported_not_claimed`
     (`:221-234`): add
     `mock.patch.object(executor, "_terminal_pane_hint", return_value="org.omarchy.voice-terminal")`,
     so the suite never runs the real `xdg-terminal-exec`.
   - Add test 7 (`tests/test_terminal.py:357-362`) and tests 6 and 9.
   → verify by `nix develop -c pytest tests/test_compose.py tests/test_terminal.py tests/test_web.py tests/test_reach.py tests/test_hypr_events.py -q`,
   all green. `test_reach.py:643` and `test_hypr_events.py:150,169` (hint
   `"foot"`), `test_web.py`'s #24 cases (`BaselineFailureTests`, `:308`) and
   the #75 stranger cases (`StrangerExecutor`, `:362-388`) pass unchanged.

7. **Mutation checks, then the whole suite.** Restore the code after each one.
   - Put back `if hints else fresh` and remove `if not hints: return None` →
     test 8 and the `:115-119`/`:342-343` replacements fail.
   - At `3322`, use `_pane_hint(...)` for terminals too, with `_pane_hint`
     returning `""` for a terminal → tests 1, 2 and 3 fail.
   - Make `_terminal_pane_hint` always return `TERMINAL_PANE_ID` (no fallback)
     → the Alacritty cases of tests 4 and 5 fail.
   - Make it return the program name even when the id is present → test 4's
     foot, kitty and ghostty cases fail.
   - Make it return `"foot"` instead of `""` on a failed check → test 4's
     failure cases fail.
   - Drop the `kitty` two-line match (keep only `endswith("=" + id)`) → test
     4's kitty case fails.
   - Have `_pane_hint("tui")` go back to its own expression → test 9 fails.
   - `nix develop -c pytest tests -q` → 930 plus the new tests, 0 failures.
   - `nix flake check --no-write-lock-file` → passes.

8. **If #88 (or anything touching these functions) merged first:**
   `git fetch origin && git rebase origin/main`. Keep both sides, as in
   "Landing order". Then re-run the last two commands of step 7. Otherwise
   skip this step.

9. **Read-only live check. It opens no terminal and no window.**
   ```
   xdg-terminal-exec --print-cmd --app-id=org.omarchy.voice-terminal
   nix develop -c python -c 'from omarchy_voice.config import Config; from omarchy_voice.tools import Executor; print(Executor(Config())._terminal_pane_hint())'
   ```
   → verify: the first prints `foot` and `--app-id=org.omarchy.voice-terminal`
   (seen at `72f9ab6`), and the second prints `org.omarchy.voice-terminal`.
   The second runs only the `--print-cmd` check. No compose, no launch, and
   the desktop is not driven.

10. **PR.** Push the branch and open a PR that says "Closes #87", links the
    intent, spec and plan, and states the landing order with #88.

## Tests

The windows: `DISCORD` and `EDITOR` from `tests/test_compose.py:253-256`,
`USERFOOT = {"address": "0xuserfoot", "class": "foot", "initialClass": "foot", "title": "~", "initialTitle": "foot", "focusHistoryID": 0, "workspace": {"name": "1"}}`,
`HERDR = {"address": "0xherdr", "class": "org.omarchy.herdr", "initialTitle": "foot", …, "focusHistoryID": 1}`,
`AP = {"address": "0xap", "class": "chrome-apnews.com__-Default", "initialTitle": "apnews.com", …}`,
and `PANE = {"address": "0xpane", "class": "org.omarchy.voice-terminal", …}`.
The compose runs `[terminal "" name "shell", web https://apnews.com name "AP"]`
on workspace `"4"`, with the launch of `https://apnews.com` adding `AP`.

1. `test_a_terminal_pane_does_not_adopt_discord` (intent demo A): the probe
   gives the id, launching the terminal adds `DISCORD`, and the terminal never
   maps. No `_dispatch_lua` call names `0xdiscord`. The output has no
   `"Composed workspace 4 in a columns layout: shell"`, and it contains
   `"Did not appear as asked"`, `discord` and `address:0xdiscord`. AP is
   moved to `"4"`, and `"Composed workspace 4 in a columns layout: AP."`
   appears. **Fails on main**, which emits `window.move … 0xdiscord`.
2. `test_the_users_foot_and_an_omarchy_tui_are_not_taken` (demos B and C):
   the terminal launch adds `USERFOOT` and `HERDR`. Neither address appears in
   any dispatch, and both are named in the output. **Fails on main.**
3. `test_the_tagged_terminal_composes`: the launch adds `PANE` and `DISCORD`
   (Discord focused, `focusHistoryID` 0). `0xpane` is moved to `"4"`, the
   output says `"Composed workspace 4 in a columns layout: shell, AP."`, and
   `0xdiscord` is in no dispatch.
4. `test_the_terminal_check` (subTests, `subprocess.run` patched in
   `omarchy_voice.tools`):
   `foot\n--app-id=org.omarchy.voice-terminal\n` → id;
   `kitty\n--class\norg.omarchy.voice-terminal\n` → id;
   `ghostty\n--class=org.omarchy.voice-terminal\n` → id;
   `/nix/store/x-alacritty/bin/alacritty\n` → `alacritty`;
   `OSError`, `subprocess.TimeoutExpired`, `returncode=1`, and stdout `"\n"`
   → `""`. It also asserts the argv passed to `subprocess.run` is exactly
   `["xdg-terminal-exec", "--print-cmd", "--app-id=org.omarchy.voice-terminal"]`
   (read-only, no `-e`).
5. `test_an_untaggable_terminal_falls_back_to_its_name`: the probe gives
   `alacritty`. The launch adds `{class: "Alacritty", address: "0xalac"}` →
   composed as `shell`. A second compose where the launch adds only `DISCORD`
   → Discord is not moved and is named. The first half also passes on
   `main`, because an empty hint takes any window. The second half **fails on
   main**. It is test 1's guarantee on the fallback path.
6. `test_the_terminal_argv_carries_the_id`: `_pane_command("terminal", "", "")`
   is `["omarchy", "launch", "terminal", "--app-id=org.omarchy.voice-terminal"]`.
   `_pane_command("terminal", "htop -d 5", "")` has the flag at index 3,
   followed by `htop`, `-d` and `5`.
7. `tests/test_terminal.py` `test_the_common_terminals_are_recognised`: a
   `org.omarchy.voice-terminal` subTest → `_terminal_on_screen()` is `True`.
8. `test_an_empty_hint_matches_nothing` (the check-failed path, and Q5):
   `_await_new_window({"0xeditor"}, 5.0, "")` and `(…, ("", ""))` with
   `DISCORD` present → `None`. A compose with the probe returning `""` and
   the launch adding `DISCORD` → Discord is in no dispatch, and it is named
   under "Did not appear as asked".
9. `test_the_tui_hint_is_its_app_id`: for `("btop", "!!!")`, `("btop", "")`
   and `("btop -d 5", "My Mon")`, `_pane_hint("tui", t, n)` equals
   `_pane_command("tui", t, n)[3].removeprefix("--app-id=")`. On `main` the
   first case fails (`""` vs `btop`).

```
nix develop -c pytest tests -q                      # 930 + new, 0 failures
nix develop -c pytest tests/test_compose.py tests/test_terminal.py -q
nix develop -c pytest tests/test_web.py tests/test_reach.py tests/test_hypr_events.py -q
nix flake check --no-write-lock-file                # CI parity
```

Live: only step 9's two read-only commands. Proving the race live would mean
launching a terminal and another app on the desktop, and the fakes cover it.

## Deviations while implementing

Only step 7's predictions changed. The code and every decision above are as
planned.

- **Mutation 2** (`_pane_hint` for terminals, returning `""`) fails tests 3
  and 5 and `test_hints_come_off_the_target`, but not tests 1 and 2. Decision
  5 guards those two separately: with `""` matching nothing, Discord and the
  user's foot are still left alone. The mutation is caught, but by a different
  set of tests.
- **Mutation 3** (no fallback) fails only test 4's `alacritty` subTest.
  Test 5 fakes the probe on the instance, so it never reaches the real
  `_terminal_pane_hint`.
- Test 4 lives in `TerminalPaneTests` and builds its own `Executor`, because
  `setUp` fakes the probe on the shared one. The shared `setUp` moved into a
  `ComposeFakes` mixin, as allowed.

## Rollback

It lands as one squash-merged PR that touches one source file
(`src/omarchy_voice/tools.py`) and two test files. There is no Nix, config,
schema or persisted-state change. `git revert <merge>` restores the untagged
terminal launch and `""` as "any window". Composed terminals then go back to
the class `foot`. No user data or Omarchy config needs to be undone. If #88
landed after this change, revert only this PR's merge. #88's `app`-branch lines
are independent of it and stay.
