---
status: approved
issue: 112
spec: spec/2026-09-24-112-terminal-needs-shell.md
---

# Plan: with the shell off, every route to a command waits for a yes

Closes #112. Branch `fix/112-terminal-needs-shell`, rebased on origin/main
`fc33ae2`. Every `file:line` below was re-read on `fc33ae2`.

## Approved decisions, carried over from the spec

1. **Scope: every route that runs a command line the model chose** (spec Q1).
   With `hands.allow_shell = false` (`config.py:443`, the default) these are
   held. With it on, each one behaves exactly as on main:
   - `run_in_terminal`, always.
   - `omarchy_cli` with a `launch` or `restart` command that passes on
     arguments (decision 4).
   - `compose_windows` with a `tui` pane or a `terminal` pane that carries a
     command (decision 5).
   - A submit key sent to a terminal window through `send_shortcut`,
     `type_text` or `hypr_dispatch` (decision 6).
2. **Hold, never refuse** (spec Q2). Every held route goes through the
   existing single slot and the existing release,
   `Executor.run_pending` (`tools.py:1883-1900`). That release is already
   called by realtime (`realtime.py:961,1308`), the local engine
   (`local_engine.py:738`), the tty (`cli.py:159`) and MCP `confirm_last`
   (`mcp_server.py:164`, with #86's delay and phrase check). The Claude Code
   brain runs our tools in-process on the same Executor
   (`claude_backend.py:548-562`) and cannot release a hold itself
   (`claude_backend.py:404-411`). Nothing new is refused.
3. **One gate, in `Executor._call_locked`** (spec Design §1). It sits inside
   the existing `try` at `tools.py:1849-1864`, right after
   `self.policy.check(...)`, and raises `NeedsConfirmation` when
   `not self.config.allow_shell and name not in READ_ONLY_TOOLS` and
   `self._runs_command(name, args)` returns a reason. The existing
   `except NeedsConfirmation` branch does the rest: the one-slot refusal,
   `pending`, `pending_since`, the `HOLD` record and `confirm_instruction`.
   The `HOLD` line gets the reason appended,
   `(<reason>; allow_shell is off)`, so `omarchy-voice log` says why. Because
   of where it sits:
   - deny rules refuse before anything is held;
   - dry runs hold the way confirm patterns do (the gate is before the
     dry-run branch at `tools.py:1868`);
   - `run_pending` does not re-enter `_call_locked`, so a released call is not
     held again.
4. **`omarchy_cli`: the rule is based on the route's shape** (spec Q4). It is
   a module-level `omarchy_runs_command(argv) -> str | None`, placed beside
   `_misused_launch_browser` (`tools.py:1715`), applied to
   `normalise_omarchy(command)[0]` (`tools.py:1682`). It looks at the route
   and the argument count, never at the command text:
   1. The family comes from `argv[0]`, with any `omarchy-` prefix removed and
      hyphens split, so `launch-or-focus` is caught even without
      `_HYPHENATED_ROUTES` (`tools.py:1675`). If the family is not in
      `_SPAWNING_FAMILIES = {"launch", "restart"}` (a new constant beside
      `_HYPHENATED_ROUTES`), the call runs. Examples:
      `theme set 'Tokyo Night'` and the toggles.
   2. If `shutil.which("omarchy-" + "-".join(words))` resolves, the whole
      argv is a route name, nothing is passed on, and the call runs. Examples:
      `launch terminal` (the #71 router, `router.py:158`), `launch about`,
      and a bare `launch editor`.
   3. The call also runs if it has one of these shapes, where
      `NAME` matches `_BARE_PROGRAM_RE = ^[A-Za-z0-9][A-Za-z0-9_.+-]*$`:
      - `launch tui NAME`
      - `launch or focus tui NAME`
      - `launch webapp <http(s) URL>`
      - `launch or focus webapp NAME <http(s) URL>`
   4. Anything else in the two families is held. Examples: `launch tui`
      given arguments, `launch terminal -e …`,
      `launch floating terminal with presentation …`,
      `launch or focus <pattern> <cmd>`, `launch editor '+!cmd'`,
      `restart app …`, and a webapp given extra flags.
   5. If `which` fails, for example because omarchy is not on PATH, the call
      is held unless step 3 matches. It fails closed.
5. **`compose_windows`** (spec Q2, Design §2–3).
   - A new helper, `_pane_runs_command(kind, target)`, goes beside
     `_pane_command` (`tools.py:701`). It is true for a `terminal` pane
     with a non-empty target, and for a `tui` pane whose target is not a
     bare `NAME`.
   - The whole composition is one hold, and one yes releases all of it.
   - `describe` (`tools.py:2022-2029`) labels each pane that carries a command
     as `name (kind: target)`, with the target not cut at 32 characters. The
     line the user says yes to therefore shows what will run, and the deny
     and confirm patterns see it at the front gate.
   - The per-pane check (`tools.py:3460-3465`) catches only `Denied`, and
     lets `NeedsConfirmation` through. Without that, the release would refuse
     the pane the user had just approved. A deny match still refuses the
     pane.
6. **Keyboard: hold the key that submits a line, not the typing** (spec Q3).
   With the shell off, a call is held when its target window is a terminal
   and it does any of the following:
   - `send_shortcut` with a key in `SUBMIT_KEYS = {Return, KP_Enter,
     ISO_Enter, Linefeed}`;
   - `send_shortcut` with CTRL plus a key in `CTRL_SUBMIT_KEYS = {m, j, o}`;
   - `type_text` whose text contains `\r` or `\n`;
   - `hypr_dispatch` with the dispatcher `send_shortcut` or `send_key_state`
     (matched as `dispatcher.rsplit(".", 1)[-1]`, as `_check_dispatch_args`
     does at `tools.py:1570`) and those same keys, or given the `message`
     form, whose key cannot be read.

   Keys and modifiers are normalised with `normalise_key` and `normalise_mods`
   (`keys.py:161,190`) before the check. A key that does not normalise is not
   a submit key, so the gate does not hold it, and the existing validator
   refuses it.

   "Terminal" means the window's lowercased class contains a name from
   `TERMINAL_CLASSES` (`tools.py:378`). That is the same substring test that
   `_terminal_on_screen` uses (`tools.py:3745-3747`). The window is resolved
   with `_resolve_window(window or "activewindow")` (`tools.py:3062`). A
   window that cannot be resolved counts as a terminal.

   Not held: typing without a newline, `ctrl+c`, and Return into a window
   that is not a terminal.
7. **A yes covers exactly one call, once** (spec Q5, #76). This is already
   enforced by `run_pending`, which clears the slot. A second gated call
   while one is held is refused with "another action is already waiting"
   (`tools.py:1855-1860`). The same call made again after a release is held
   again. There is no yes that lasts for the rest of the turn.
8. **The `doctor` line** (spec Q6). `cli.shell_status` (`cli.py:245-257`)
   returns one line:
   - off: `  shell: off — run_shell is not offered; any other command (terminal, launcher, compose pane, Return in a terminal) waits for your yes; N deny rules, M confirm rules`
   - on: `  shell: on — commands run without asking, except N deny rules and M confirm rules`

   The docstring's "`allow_shell` is the whole answer" is replaced with this
   rule.
9. **README.**
   - `README.md:257`: replace "so `allow_shell` means what it says" with
     "With `allow_shell` off, `run_shell` is not offered, and every other way
     to run a command waits for your yes: `run_in_terminal`, an omarchy
     launcher given a command, a compose `terminal`/`tui` pane with a
     command, and Return in a terminal."
   - `README.md:832-834`: keep "Blocked as process execution". Add a sibling
     bullet, **"Held when the shell is off"**, that lists the routes from the
     table in decision 10, says a yes is spent on that one call, and says
     that terminals inside apps (VS Code, Zed, vterm) are not seen.
   - `README.md:597`: in the `run_in_terminal` section, add one sentence:
     with the shell off, each command is held for a yes.
   - `README.md:851`: after "Off by default: the shell tool", add "with it
     off, commands elsewhere wait for a yes".
10. **The route table.** Each row must be true with fakes after the change.

    | Call | shell off | shell on |
    | --- | --- | --- |
    | `run_in_terminal` `python3 -c 'import os; os.system(…)'`, `bash -c 'echo pwned > ~/f'`, `curl -o x … && sh x`, `find ~/tmpdir -name '*.log' -delete` | HELD | RAN |
    | `run_in_terminal "rm -rf ~/x"` | REFUSED (deny) | REFUSED (deny) |
    | `omarchy_cli` `launch tui bash -c '…'`, `launch floating terminal with presentation '…'`, `launch or focus zzz 'bash -c …'`, `launch terminal -e bash -c '…'`, `restart app bash -c '…'`, `launch-or-focus tui python3 -c '…'`, `launch editor '+!touch /tmp/x'` | HELD | RAN |
    | `omarchy_cli` `launch terminal`, `launch tui btop`, `launch or focus tui lazygit`, `launch-or-focus webapp x https://x.com/`, `launch webapp https://…`, `launch about`, `theme set 'Tokyo Night'` | RAN | RAN |
    | `compose_windows` with a tui `bash -c …` pane and a terminal `-e python3 …` pane | HELD | RAN |
    | `compose_windows` with a tui `btop` pane, an empty terminal pane and a web pane | RAN | RAN |
    | `type_text "python3 -c 'print(1)'"` → Alacritty; `send_shortcut ctrl+c` → Alacritty; `send_shortcut Return` → firefox; `type_text "hello\r"` → firefox | RAN | RAN |
    | `type_text "ls\r"` → Alacritty; `send_shortcut Return`, `enter` (active window), `ctrl+m` → Alacritty; `hypr_dispatch send_shortcut {Return, 0x1}` | HELD | RAN |
    | `launch_app "bash -c …"`, `hypr_dispatch exec_cmd` | REFUSED (#22, unchanged) | RAN (unchanged) |

11. **Unchanged** (spec Design §5):
    - `tools_for` (`tools.py:1482`), the tool schemas, and the persona;
    - the `launch_app` and `hypr_dispatch` exec refusals;
    - `watch_terminal`, `read_terminal` and `list_terminals`;
    - ai-mirror input;
    - the internal `omarchy launch terminal tmux` (`tools.py:3765`);
    - the NixOS module. p620 has `allow_shell = true` and sees no change.

### Plan-level decision, not in the spec: approved 2026-09-24 (P1 kept)

**P1. With the shell off, validate the arguments before holding.** The spec
places the gate before any argument check. As a result, a call the tool would
refuse anyway is held first, and the user's yes is spent on a refusal. This
also loses the guidance that test `test_terminal.py:197-205` protects: a
heredoc sent to `run_in_terminal` gets the `printf` advice from
`_validate_run_in_terminal` (`tools.py:3778-3790`). With the gate as written,
it gets `confirm_instruction` instead.

The fix is inside the new branch only. When `_runs_command` gives a reason,
run the tool's `_validate_<name>(**args)`, if there is one, before raising.
This is the same argument-only validator the dry run already calls at
`tools.py:1868-1876`. An error is returned as the refusal. Deny rules still
come first. The handler-time checks (pane not on screen, pane busy) stay at
release, as they already are for confirm holds.

If P1 is struck, drop step 4 and change `test_terminal.py:193-205` so that
it runs with `allow_shell=True`.

## Steps

0. **Baseline.** Run
   `export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` first. Then run
   `nix develop -c python3 -m unittest discover -s tests` and
   `nix develop -c python3 -m pytest -q tests`. Also run
   `git diff origin/main --stat -- src tests README.md`.
   → verify: both runners report **1075** tests OK (measured on `fc33ae2`),
   and the diff is empty.
1. **`tests/test_shell_off.py` (new): the end-to-end tests through
   `Executor.call`, written before any code change.** Every test uses fakes:
   - an `Executor` subclass whose `_shell` records the argv;
   - `_query_json` / `_query_rows` returning Alacritty `0x1` (focused) and
     firefox `0x2`;
   - `shutil.which` patched to a fixed set of `omarchy-*` names (and tmux);
   - tmux faked the way `test_terminal.FakeTmux` fakes it;
   - `_kb_layout` stubbed;
   - `import tests._isolated` first.

   Each row of the table in decision 10 is a subTest in both columns. The
   tests also cover:
   - deny before hold: `run_in_terminal "rm -rf ~/x"` refused and
     `pending is None`;
   - `hypr_dispatch send_key_state` with Return, the `message` form, and an
     unresolvable window (all held);
   - `HOLD … (…; allow_shell is off)` in `transcript`;
   - the router: `Executor.call(*route("open a terminal"))` runs unheld;
   - release (Q5): after a hold, `run_pending` sends exactly the held argv,
     the same call is held again, and a second gated call while one is held
     gets "another action is already waiting";
   - MCP `confirm_last`, following `test_mcp.py`'s pattern: it releases a
     held `run_in_terminal` after `CONFIRM_DELAY` with a confirm phrase;
   - dry run with the shell off: a held route holds and does not print
     `[dry-run]`.

   → verify: run it on the unchanged code. Every shell-off HELD row and the
   `HOLD` reason test FAIL. Every shell-on row, the deny row, the RAN
   exceptions, the router test and the #22 refusals PASS. Record the
   failing count in the PR.
2. **`src/omarchy_voice/tools.py`: add the constants and the helpers.**
   - Add `_BARE_PROGRAM_RE`, `SUBMIT_KEYS` and `CTRL_SUBMIT_KEYS` beside
     `TERMINAL_CLASSES` (`:378`).
   - Add `_SPAWNING_FAMILIES` beside `_HYPHENATED_ROUTES` (`:1675`).
   - Add `omarchy_runs_command(argv)` beside `_misused_launch_browser`
     (`:1715`), following decision 4.
   - Add `_pane_runs_command(kind, target)` beside `_pane_command` (`:701`),
     following decision 5.
   - Add `Executor._runs_command(name, args) -> str | None` beside
     `describe` (`:1926`), following decisions 1, 4, 5 and 6. The reasons
     are short, for example "runs a command in a terminal", "an omarchy
     launcher given a command", "a compose pane runs a command" and "presses
     Return in a terminal". Add a private `_is_terminal_window(target)` that
     uses `_resolve_window` and the substring test, returning True on a
     failed resolve.

   Add unit tests for the helpers to `tests/test_shell_off.py`:
   `omarchy_runs_command` over every omarchy row, plus `which → None`
   holding `launch terminal`; `_pane_runs_command`; and `_runs_command` for
   each keyboard row.
   → verify: the helper tests pass. The step 1 end-to-end tests still fail,
   because nothing calls the helpers yet.
3. **`tools.py:1848-1864`: the gate.** Compute the reason once as
   `why = None if self.config.allow_shell or name in READ_ONLY_TOOLS else
   self._runs_command(name, args)`. After `self.policy.check(...)`, inside
   the `try`: `if why: raise NeedsConfirmation(why)`. The `except` branch
   records `HOLD    {description}`, followed by
   `f" ({why}; allow_shell is off)"` when `why` is set. It is otherwise
   unchanged.
   → verify: all of step 1 passes, and the full suite is run. Expect the
   failures listed in steps 6 to 8, and nothing else.
4. **(P1) `tools.py`, in the same gate: validate before holding.** When
   `why` is set, look up `_validate_{name}` and call it with `**args`,
   catching `TypeError` exactly as at `:1869-1876`. If it returns an error,
   return `Result(False, error)`, record nothing, and hold nothing. This
   runs after `policy.check`, so deny is still first.
   Add tests to `tests/test_shell_off.py` for the shell-off case:
   - a heredoc sent to `run_in_terminal` returns the `printf` advice with
     `pending is None`;
   - `omarchy_cli "launch tui <placeholder>"` returns the placeholder error
     and is not held.

   → verify: those two tests pass. Temporarily comment out the validation
   and they fail.
5. **`tools.py:2022-2029` and `:3460-3465`: compose.** In `describe`, a
   pane for which `_pane_runs_command` is true is labelled
   `f"{name or kind} ({kind}: {target})"`, with the target not cut. Other
   panes keep today's label. In `_tool_compose_windows`, change
   `except (Denied, NeedsConfirmation)` to `except Denied`.

   > **Deviation found while implementing (2026-09-24).** `except Denied`
   > alone does not let the pane through: `NeedsConfirmation` is then
   > uncaught, and it escapes `run_pending` as an exception (seen in the
   > "compose release with a confirm match" test below). So the change is
   > `except Denied` (refuse, unchanged) plus a separate
   > `except NeedsConfirmation: pass`. The behaviour is what decision 5
   > asks for, and the step 11 mutation ("revert the `except` to catch
   > `NeedsConfirmation`") still applies as written.

   Add tests to `tests/test_shell_off.py`:
   - `describe` of a tui pane named `notes` with target
     `bash -c 'echo pwned > ~/f'` contains `bash -c 'echo pwned > ~/f'`;
   - with `confirm_patterns=[r"\bpwned\b"]` and the shell on, the compose is
     held at the front gate, and `run_pending` launches both panes;
   - with `deny_patterns` matching only the pane argv's `--app-id`, the pane
     is refused at release.

   → verify: those three tests pass. `tests/test_compose.py` is unchanged
   and passes.
6. **`tests/test_terminal.py:38-40`:** `FakeTmux.__init__` gains
   `allow_shell=True` and passes `Config(allow_shell=allow_shell)`. The
   existing `RunningTests` (`:156-208`) and `run_long` (`:314`) test how the
   handler behaves on today's shell-on path. Their shell-off counterparts
   are in `test_shell_off.py`.
   → verify: `python3 -m unittest tests.test_terminal` passes.
7. **`tests/test_keys.py:138`:** `SendShortcutTests.setUp` uses
   `Executor(Config(allow_shell=True))`. Its window is `foot`
   (`ORDINARY_WINDOW`, `:25`), which is a terminal, so the Enter tests at
   `:154`, `:161`, `:185` and `:199` would otherwise be held. `:205`
   (dry-run `Zorp`) is left on its default config, because a key that does
   not normalise is not held.
   → verify: `python3 -m unittest tests.test_keys` passes.
8. **`src/omarchy_voice/cli.py:245-257`:** `shell_status` returns the two
   lines from decision 8, with the docstring rewritten. Update
   **`tests/test_backend_choice.py:340-360`** so that the assertions check
   `"shell: off"`, `"waits for your yes"` and the counts (off), and
   `"shell: on"` (on). Keep `len(lines) == 1`.
   → verify: `python3 -m unittest tests.test_backend_choice` passes, and
   `nix develop -c omarchy-voice doctor 2>/dev/null | grep 'shell:'` shows
   the off line on a default config.
9. **`README.md`: the four edits in decision 9.**
   → verify: `grep -n "Held when the shell is off" README.md` finds a line,
   and every route in the decision 10 table is named in that bullet.
10. **Full run and flake.** Run both runners, then
    `nix flake check --no-write-lock-file`.
    → verify: 1075 plus the new tests pass in both runners, and the flake
    check is green.
11. **Mutation checks.** Apply each mutation, run
    `python3 -m unittest tests.test_shell_off`, and revert. The named test
    must fail each time:

    | Mutation | Test that must fail |
    | --- | --- |
    | delete `if why: raise …` | every shell-off HELD row |
    | drop the `allow_shell` condition from `why` | every shell-on row |
    | move the raise above `policy.check` | the deny-before-hold test |
    | make a failed resolve count as not a terminal | the unresolvable-window test |
    | remove the `which` step (decision 4.2) | the `launch terminal` and router rows |
    | treat any `NAME` pattern as free, dropping the arg-count check | `launch tui bash -c …` held |
    | drop `restart` from `_SPAWNING_FAMILIES` | `restart app …` held |
    | drop `\r` from the `type_text` check | `type_text "ls\r"` held |
    | drop `m` from `CTRL_SUBMIT_KEYS` | `ctrl+m` held |
    | drop `send_key_state` from the dispatch rule | the `send_key_state` row |
    | revert the compose `except` to catch `NeedsConfirmation` | the compose release with a confirm match |
    | revert `describe` for compose | the describe-contains-command test |
    | (P1) remove the pre-hold validation | the heredoc guidance test |

    → verify: 13 of 13 mutations are caught, and `git diff` after reverting
    shows only the intended change.

## Tests

- Before running anything: `export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`.
- `nix develop -c python3 -m unittest discover -s tests`: expect `OK`, with
  1075 plus the new tests.
- `nix develop -c python3 -m pytest -q tests`: expect the same count
  passing.
- `nix flake check --no-write-lock-file`: expect it to be green.
- Fakes only. Nothing sends real keys, and nothing runs real tmux, hyprctl
  or omarchy.
- Existing tests that change: `tests/test_terminal.py` (FakeTmux config),
  `tests/test_keys.py` (SendShortcutTests config) and
  `tests/test_backend_choice.py:340-360` (doctor text).
- These were checked and need no change, because they call only routes that
  are not held or that are refused anyway: `test_compose.py` (app panes,
  empty terminal panes, `tui btop` only in helper tests), `test_policy.py`
  (the `hypr_dispatch exec_cmd` refusal, with `pending is None` at `:151`;
  `type_text "-something"` at `:198`; omarchy `reboot` and `update`),
  `test_mcp.py`, `test_realtime.py`, `test_claude_backend.py`,
  `test_local_engine.py` (omarchy `reboot`/`update`), and
  `test_input_guard.py`, which calls `_tool_type_text` directly and so
  bypasses the gate.

## Rollback

`git revert` the implementation commit. There is no config key, schema,
persisted state or NixOS module change, so nothing else needs undoing. For
one machine, without a revert, set `hands.allow_shell = true`. That restores
today's behaviour exactly, as on p620.

## Landing order and overlap

The requested order is to land after #120 and #114. There is **no real
overlap**:

- #120 touches `local_engine.py` (`:564-584`) and
  `tests/test_local_engine.py`.
- #114 touches `local_engine.py`, `listen_local.py`, `feedback.py`,
  `config.py:363,430` and `tests/test_local_engine.py`.
- This plan touches `tools.py`, `cli.py`, `README.md`,
  `tests/test_terminal.py`, `tests/test_keys.py`,
  `tests/test_backend_choice.py` and the new `tests/test_shell_off.py`.
  It only reads `config.py` (`allow_shell` at `:443`, which neither of the
  others touches) and does not edit `local_engine.py` or its tests.

It can land independently. If it lands after them, rebase and rerun step 10.
