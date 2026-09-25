---
status: approved
issue: 145
spec: spec/2026-09-25-145-tui-typing-hold.md
---

# Plan: with the shell off, Return typed into a tui window waits for a yes

Closes #145. Branch `fix/145-tui-typing-hold`, based on main `806804a`
(#129 merged). `git diff 806804a HEAD --stat -- src tests README.md` is
empty. Every `file:line` below was re-read on `806804a`. Where the spec's
number was off, the corrected one is used and marked "(spec: …)".

## Approved decisions, carried over from the spec

1. **A tui window is recognised by the app id it was launched with** (spec
   Q1, Design §1). A new constant goes beside `TERMINAL_CLASSES`
   (`tools.py:388`):

   ```python
   # The ids Omarchy launches tuis and its own terminals under: the same two
   # prefixes its `+terminal` tag matches (default/hypr/apps/terminals.lua) (#145).
   TUI_CLASS_PREFIXES = ("org.omarchy.", "tui.")
   ```

   `Executor._is_terminal_window` (`tools.py:2152-2158`) reads both the
   window's `class` and its `initialClass`, lower-cased. The window counts as
   a terminal if either one contains a `TERMINAL_CLASSES` word (today's
   test) or starts with a `TUI_CLASS_PREFIXES` entry. Either match is enough,
   so the change can only add holds. `initialClass` is included because it
   is the id the window was launched with. foot lets a program change its app
   id later (OSC 176), and a window that renames itself must still count.
   These are the same two prefixes Omarchy's `+terminal` tag uses. In
   omarchy 4.0.4, every `org.omarchy.*` and `TUI.*` id is given to a
   terminal.
2. **Compose launches a tui pane under `org.omarchy.voice.<name>`** (spec
   Q1, Design §2). A new constant goes beside `TERMINAL_PANE_ID`
   (`tools.py:417`):

   ```python
   # A compose tui pane's app id is this plus its hint: `org.omarchy.` so the
   # shell-off gate and Omarchy's terminal tag know it, `voice.` so it is none of
   # the ids Omarchy floats (#145).
   TUI_PANE_PREFIX = "org.omarchy.voice."
   ```

   `_pane_command`'s tui branch (`tools.py:795-799`; spec: `792-796`)
   passes `--app-id={TUI_PANE_PREFIX}{_tui_app_id(target, name)}`. The id is
   not bare `org.omarchy.<name>`, because Omarchy floats `org.omarchy.btop`,
   `org.omarchy.terminal` and `org.omarchy.bash`
   (`default/hypr/apps/system.lua:7`), and a compose pane must tile. The
   model picks only the suffix, and it cannot pick an Omarchy `launch tui`
   id, since `--app-id=` is an argument and #112 already holds that launch.
3. **The hint stays the bare name** (spec Design §2). `_tui_app_id`
   (`tools.py:705-714`) and `_pane_hint` (`tools.py:717-731`) do not change.
   The hint `vim` still finds `org.omarchy.voice.vim` by substring of its
   class (`_rank_windows`, `tools.py:642-693`, score 2 at `:682-683`; spec: `671-687`). When
   the terminal drops `--app-id`, as Alacritty does, the hint can still match
   by title. #87's invariant changes from "the hint is the app id" to "the
   app id is the prefix plus the hint". It is still one expression.
4. **Every tui window counts, and only for the submit keys** (spec Q2).
   There is no allowlist of "safe" tuis. With the shell off, btop, lazygit,
   htop and `org.omarchy.about` hold a typed Return, `ctrl+m`/`j`/`o`, and a
   newline in `type_text`. Everything else typed into them stays free:
   arrows, letters, `q`, and text without a newline. The submit-key rules at
   `tools.py:2177-2198` (`SUBMIT_KEYS`, `CTRL_SUBMIT_KEYS` at `:411-412`) do
   not change.
5. **No process-tree check** (spec Q3). There is no `/proc` walk. The id
   check reuses the `hyprctl clients` query the gate already makes.
6. **The shell-on path does not change** (spec Design §1). `_is_terminal_window`
   is called only from `_runs_command` (`tools.py:2180,2196`), and
   `_runs_command` is asked only with `allow_shell = false`.
7. **An unresolvable window still counts as a terminal**
   (`tools.py:2154-2156`, unchanged).
8. **No new wait. The release does not change** (spec Q4). #139's wait in
   `_pre_tool_use` already covers `type_text` and `send_shortcut`. More
   calls become holds through the existing `NeedsConfirmation` path in
   `_call_locked` (`tools.py:2006`). `run_pending` (`tools.py:2107-2127`;
   spec: `2106-2127`) calls the handler directly, so a released Return is
   not held again. `_releasing` (`tools.py:1978,2121,2127,3863`) does not
   change. Only the argv text that compose's per-pane re-check sees changes.
9. **The launch list stays as it is** (spec Q4). `_COMMAND_PROGRAMS`
   (`tools.py:398`) and #129's launch-time rule do not change. With the
   shell off, `launch tui vim` and a compose tui `vim` pane still run
   unheld. The key hold is what covers a vim window that is already open.
10. **`TERMINAL_CLASSES` and `_terminal_on_screen` do not change**
    (`tools.py:388`, `:4134-4146`). A vim window is not a tmux client.
11. **#87's terminal pane does not change.** `TERMINAL_PANE_ID =
    "org.omarchy.voice-terminal"` (`tools.py:417`), its argv (`:793`) and
    `_terminal_pane_hint` (`:3635-3661`) stay as they are.
12. **README** (spec Design §3). The existing bullet "**Held when the shell
    is off**" (`README.md:783-795`) says that "a terminal window" includes any
    tui window opened by `omarchy launch tui` or by a compose tui pane. It
    also says that Return into btop or lazygit waits for a yes with the shell
    off. It adds one sentence: a compose tui pane now launches as
    `--app-id=org.omarchy.voice.<name>`, so a deny or confirm pattern
    written against `--app-id=<name>` must follow it.
13. **The verdict table.** Each row must be true with fakes after the
    change. H = HELD, R = RAN. The four submit calls are `type_text ":!id\n"`,
    `send_shortcut Return`, `send_shortcut ctrl+m`, and `hypr_dispatch
    send_shortcut {key: Return}`.

    | Window (`class` / `initialClass`) | off: 4 submit calls | off: `type_text ":!id"` | on: all 5 | main, off, submit |
    | --- | --- | --- | --- | --- |
    | `org.omarchy.vim` (omarchy `launch tui vim`) | H | R | R | R |
    | `org.omarchy.voice.vim` (compose tui, new id) | H | R | R | R |
    | `org.omarchy.htop` | H | R | R | R |
    | `TUI.float` | H | R | R | R |
    | `firefox` / `org.omarchy.vim` (renamed) | H | R | R | R |
    | `Alacritty` (plain terminal) | H | R | R | H |
    | `org.omarchy.voice-terminal` (#87 pane) | H | R | R | H |
    | `firefox` (GUI app) | R | R | R | R |
    | unresolvable (`address:0xgone`) | H | R | R | H |

    The first five rows fail on main. The rest pin today's behaviour.

## Steps

0. **Baseline.** Run
   `export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` first. Then run
   `nix develop -c python3 -m unittest discover -s tests` and
   `nix develop -c python3 -m pytest -q tests`. Also run
   `git diff 806804a --stat -- src tests README.md`.
   → verify: both runners report **1170** tests OK (lead's figure for main
   `806804a`), and the diff is empty.

1. **Measure the spec's residual gap: a `Terminal=true` entry started with
   `launch_app`.** Do this before writing anything. It is read-only, uses
   fakes, and launches nothing. Scratch goes in
   `scratchpad/plan-145/`, and none of it is committed.
   1. Fixture: a temp app dir that contains `tuifix.desktop` with
      `Type=Application`, `Terminal=true` and `Exec=vim`. Patch
      `omarchy_voice.tools.app_dirs` and `omarchy_voice.capabilities.app_dirs`
      to that dir, as `FakeDesktop.setUp` does. Patch `shutil.which` so that
      `uwsm-app` resolves. With `Fake(False)` (`tests/test_shell_off.py:49`),
      call `launch_app {"app": "tuifix"}` and record the argv.
      → expect `["…/uwsm-app", "tuifix.desktop"]`: RAN and not held, with no
      `--app-id` and no `-T`.
   2. Read the uwsm source that the argv reaches (uwsm 0.26.7,
      `share/uwsm/modules/uwsm/main.py:3506-3547`). For `Terminal=true`, uwsm
      runs the default terminal entry's `Exec`. It adds an app id only from
      `Terminal.opts`, which come from uwsm's own command-line options, and
      step 1.1 passes none. → expect: the window gets the terminal's own
      default class.
   3. Run `xdg-terminal-exec --print-cmd vim`. It prints a command and runs
      nothing. `argv[0]`'s basename is the default terminal. Take that
      terminal's default class: `Alacritty`, `foot`, `kitty`,
      `com.mitchellh.ghostty` or `org.wezfurlong.wezterm`.
   4. With a fake window of that class as `class` and `initialClass`, run
      all four submit calls with the shell off.
      → expect HELD ×4. That class hits `TERMINAL_CLASSES`, so this already
      holds on main.

   → verify: 1.1 to 1.4 all hold as expected. **If the window would not be
   held**, for example because uwsm passes an app id, the default terminal's
   class matches neither `TERMINAL_CLASSES` nor a prefix, or 1.1 shows a
   different launcher, **stop and report to the lead with the evidence. Do
   not widen the rule in this change.** If it holds, add a row for it to
   step 2's table (`launch_app` Terminal=true → default terminal class:
   H ×4), and record the measured argv and class in the PR.

2. **`tests/test_shell_off.py`: new tests, written before any code
   change.** Use the existing fakes: `Fake` (`:49-72`), `FakeDesktop`
   (`:75-102`), and `press`/`type_` (`:121-125`). Add one extra window per
   row, with its `class` and `initialClass` set, at address `0x3` and not
   focused. Target it as `address:0x3`, because the renamed row has to
   resolve by address and not by class. Add:
   - `TUI_TABLE`: decision 13's nine rows × shell off/on × the five calls,
     each as a subTest through `Executor.call` (`self.outcome`). The
     `hypr_dispatch` call is `{"dispatcher": "send_shortcut", "args": {"mods":
     "", "key": "Return", "window": "address:0x3"}}`.
   - `test_a_compose_tui_pane_launches_under_the_voice_prefix`:
     `_pane_command("tui", "vim", "notes")[3] ==
     "--app-id=org.omarchy.voice.notes"`, and `_pane_hint("tui", "vim",
     "notes") == "notes"`.
   - `test_the_compose_tui_id_is_not_one_omarchy_floats`: for the names
     `btop`, `terminal`, `bash`, `about` and `screensaver`, the pane's app id
     is not `org.omarchy.<name>`. That set is Omarchy 4.0.4's float and
     fullscreen list (`system.lua:7,26,36`), pinned as a literal in the test.
     The assertion is that the id starts with `TUI_PANE_PREFIX`.
   - `test_the_hint_still_finds_the_pane`: `_window_matches({"class":
     "org.omarchy.voice.notes"}, hint)` is true. `_window_matches({"class":
     "Alacritty", "title": "notes"}, hint)` is true, for the terminal that
     drops the id. Here `hint = _pane_hint("tui", "vim", "notes")`.
   - `test_the_tui_launch_itself_is_not_held`: with the shell off,
     `omarchy launch tui vim` and a compose with one tui pane `vim` named
     `notes` both RAN. The compose argv contains
     `--app-id=org.omarchy.voice.notes`.
   - `test_a_released_return_into_a_tui_is_pressed_once`: with the shell off
     and window `org.omarchy.vim`, `send_shortcut Return` is HELD.
     `run_pending()` is ok, exactly one key press reaches the fake, and
     `pending is None` afterwards.
   - `test_the_terminal_pane_id_is_unchanged`: `TERMINAL_PANE_ID ==
     "org.omarchy.voice-terminal"`, and `_pane_command("terminal", "", "")[3]`
     is `--app-id=org.omarchy.voice-terminal`.

   Import `TUI_PANE_PREFIX` inside the tests that use it, so that the file
   still imports on main and the other tests report.
   → verify: run `python3 -m unittest tests.test_shell_off` on the unchanged
   code. These FAIL: the first five `TUI_TABLE` rows (off, submit calls
   only), the voice-prefix test, the not-floated test, and the tui-launch
   test's argv assertion. Every other new subTest PASSES. Record the
   failing count in the PR.

3. **`src/omarchy_voice/tools.py:388` and `:2152-2158`: the recognition
   rule** (decisions 1, 6 and 7). Add `TUI_CLASS_PREFIXES` after
   `TERMINAL_CLASSES`. Rewrite `_is_terminal_window` to build `ids` from
   `class` and `initialClass`, lower-cased and with empty ones dropped. It
   returns `any(term in i for i in ids for term in TERMINAL_CLASSES) or
   any(i.startswith(TUI_CLASS_PREFIXES) for i in ids)`. The `None` → `True`
   branch is kept. Update the docstring to name #145.
   → verify: all of `TUI_TABLE` passes except the `org.omarchy.voice.vim`
   row, which passes too because its fake window already has that class.
   The compose tests from step 2 still fail.

4. **`src/omarchy_voice/tools.py:417` and `:799`: the compose id**
   (decisions 2 and 3). Add `TUI_PANE_PREFIX` after `TERMINAL_PANE_ID`. The
   tui branch's argv uses `f"--app-id={TUI_PANE_PREFIX}{_tui_app_id(target,
   name)}"`. In `_tui_app_id`'s docstring (`:706`), change "and so also its
   hint" to "after `TUI_PANE_PREFIX`, and so also its hint (#145)".
   → verify: every step 2 test passes.

5. **Existing tests that pin the old compose id** (spec Risks):
   - `tests/test_compose.py:75-77`: expect
     `--app-id=org.omarchy.voice.mynamerm-rf`.
   - `tests/test_compose.py:87-92`: rename the test to
     `test_the_tui_app_id_is_the_prefix_plus_its_hint`, and compare against
     `…[3].removeprefix("--app-id=" + TUI_PANE_PREFIX)`.
   - `tests/test_shell_off.py:327`: the deny pattern becomes
     `r"--app-id=org\.omarchy\.voice\.notes"`, so that it still matches only
     the pane argv.

   → verify: `python3 -m unittest tests.test_compose tests.test_shell_off`
   passes. `git diff 806804a -- tests/test_compose.py` touches only those
   two tests.

6. **`README.md:783-795`: decision 12's sentences, added to the "Held when
   the shell is off" bullet.**
   → verify: `grep -n "org.omarchy.voice.<name>" README.md` finds the
   bullet, and `grep -n "btop or lazygit" README.md` finds the friction
   note.

7. **Full run and flake.** Run both runners, then
   `nix flake check --no-write-lock-file`.
   → verify: 1170 plus the new tests pass in both runners, with the same
   count in each. The flake check is green.

8. **Mutation checks.** One per decision. Apply each mutation, run
   `python3 -m unittest tests.test_shell_off tests.test_compose`, and revert.
   The named test must fail. For decisions with no code to mutate, the
   check is a grep, as noted.

   | # | Decision | Mutation | Test that must fail |
   | --- | --- | --- | --- |
   | M1 | 1 | drop `"org.omarchy."` from `TUI_CLASS_PREFIXES` | `TUI_TABLE` `org.omarchy.vim`, `org.omarchy.htop` and `org.omarchy.voice.vim` rows |
   | M2 | 1 | drop `"tui."` from `TUI_CLASS_PREFIXES` | `TUI_TABLE` `TUI.float` row |
   | M3 | 1 | read `class` only, not `initialClass` | `TUI_TABLE` renamed row |
   | M4 | 2 | the tui argv uses the bare `_tui_app_id` (main's) | the voice-prefix and tui-launch argv tests |
   | M5 | 2 | set `TUI_PANE_PREFIX = "org.omarchy."` | the not-floated test |
   | M6 | 3 | `_pane_hint` for tui returns the full prefixed id | the hint test (`_pane_hint(…) == "notes"`, and the title match) |
   | M7 | 4 | exempt a "safe" tui: `org.omarchy.htop` is not a terminal | `TUI_TABLE` `org.omarchy.htop` row |
   | M8 | 5 | (grep) `git diff 806804a -- src` contains no `/proc` and no `psutil` | — |
   | M9 | 6 | call `_runs_command` with the shell on too (drop the `allow_shell` condition from `why` in `_call_locked`) | `TUI_TABLE` shell-on column |
   | M10 | 7 | a failed resolve returns `False` | `TUI_TABLE` unresolvable row |
   | M11 | 8 | `run_pending` re-enters `_call_locked` instead of `handler(**args)` | the released-Return test |
   | M12 | 9 | add `vim` to `_COMMAND_PROGRAMS` | the tui-launch-not-held test |
   | M13 | 10 | (grep) `git diff 806804a -- src` touches neither `TERMINAL_CLASSES =` nor `_terminal_on_screen` | — |
   | M14 | 11 | `TERMINAL_PANE_ID = TUI_PANE_PREFIX + "terminal"` | the pane-id-unchanged test, and `test_compose.py:83` |
   | M15 | 12 | (grep) step 6's two greps | — |
   | M16 | 13 | put the rule back as main's (M1+M2+M3 together) | the first five `TUI_TABLE` rows |

   → verify: all 13 code mutations are caught, all 3 grep checks hold, and
   `git diff` after each revert shows only the intended change.

## Tests

- Before running anything: `export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`.
- `nix develop -c python3 -m unittest discover -s tests`: expect `OK`, with
  1170 plus the new tests.
- `nix develop -c python3 -m pytest -q tests`: expect the same count
  passing.
- `nix flake check --no-write-lock-file`: expect it to be green.
- Fakes only. Nothing sends real keys, and nothing runs real hyprctl,
  omarchy, uwsm or a terminal. The only real command is step 1.3's
  `xdg-terminal-exec --print-cmd`, which prints and runs nothing.
- Existing tests that change: `tests/test_compose.py:75-77`, `:87-92`, and
  `tests/test_shell_off.py:327`. No others: the existing `TABLE` windows are
  `Alacritty` and `firefox`, and `tests/test_keys.py` already runs with
  `allow_shell=True` (#112).

## Rollback

`git revert` the implementation commit. There is no config key, schema,
persisted state or NixOS module change. Compose panes opened under the new
id keep it until they are closed, which is harmless. For one machine,
without a revert, set `hands.allow_shell = true`. That skips the gate
entirely, as on p620.

## Landing order and overlap

This change lands **second**: after #144 and before #143.

- **#144** (`fix/144-claude-json-deny`) edits `config.py`
  (`DEFAULT_DENY_RULES`), the README's deny table and paragraph, and
  `tests/test_config.py`/`test_policy.py`. It does not touch `tools.py`.
  **Overlap: `README.md` only**, in a different section (the deny table,
  not the "Held when the shell is off" bullet). After #144 lands, rebase,
  re-read `README.md:783-795` for the new line numbers, and rerun step 7.
- **#143** (`fix/143-stale-wtype-check`), landing after this one, edits
  `README.md:61,219,426`, and makes comment-only edits to `tools.py`
  (`:369`, `:2997`, `:3978-3989`), plus `keys.py`, `cli.py` and
  `capabilities.py`. **Overlap: `README.md` and `tools.py`, with no shared
  hunks.** Its `tools.py:369` comment sits 19 lines above this plan's
  `:388` insertion, so the #143 rebase may need a context fix there.
- Neither touches `_is_terminal_window`, `_pane_command`, `tests/test_shell_off.py`
  or `tests/test_compose.py`.
