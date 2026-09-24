---
status: approved
issue: 110
spec: spec/2026-09-24-110-one-rule-every-launch.md
---

# Plan: one deny rule on an app stops it through launch_app and a compose pane

Closes #110. Branch `fix/110-one-rule-every-launch`, based on main `b73a3f4`.
Every `file:line` below was re-read on `b73a3f4`. `src/`, `tests/` and
`README.md` do not differ from `b73a3f4` on this branch.

## Approved decisions, carried over from the spec

1. **Every app launch is checked as `launch <id>`, on both paths** (spec Q1).
   This is enforced, not only documented. `launch dev.zed.Zed` is the text
   `launch_app` already shows in the transcript (`describe`,
   `tools.py:2068-2069`), so a rule copied from the log is a rule that works.
   It is an *extra* text for the gate. It does not replace any description,
   so the transcript, the #112 labels and the #112 confirm line stay the same.
2. **Both places check it** (spec Q2).
   - **The front gate**, in `Executor._call_locked`, inside the `try` at
     `tools.py:1906-1918`, right after
     `self.policy.check(description, …)` (`:1907`) and **before** the
     `if why:` shell hold (`:1908`), so a deny still refuses before anything
     is held. This is the only place a refusal lands before the workspace
     switch (`:3566-3572`) and the first launch (`:3619`), and the only place
     a confirm match holds the whole composition instead of refusing halfway
     through it. The existing `except Denied` (`:1919-1921`) records
     `DENIED  <description> (<rule>)`. The existing `except
     NeedsConfirmation` (`:1922-1933`) holds the call under its front
     description.
   - **The handler's per-pane check** (`tools.py:3589-3597`), in the same
     `try`, so the existing `except Denied` and the #112
     `except NeedsConfirmation` with `if not self._releasing` apply to it
     unchanged. The deny list has the last word.
3. **The per-pane check keeps the launcher argv** (spec Q3). The argv
   (`" ".join(argv)`, `:3590`) is checked first and `launch <id>` second, so a
   rule on `uwsm-app`, `gtk-launch` or `.desktop` still bites.
4. **Scope: `launch_app` and compose `app` panes only** (spec Q4).
   `hypr_dispatch` exec (`:159-161`), `omarchy_cli launch-or-focus`,
   `run_in_terminal`, the `omarchy launch tui <program>` route and a compose
   `tui` pane are governed by `allow_shell`, not by an app rule. They get no
   `launch <id>` text. The README sentence names the two paths only and does
   not say "however it is launched".
5. **No change for `url` launches or `web` panes** (spec Q5). `launch_app`
   with a `url` runs `xdg-open <url>` (`:2450-2451`) and skips resolution
   (`:1881`). A `web` pane opens `omarchy launch webapp <url>` (`:734`).
   Neither gets a `launch <id>` text.
6. **Approved with (2026-09-24): the text is built from the id that is
   actually launched.** For both paths, the checked text is
   `launch <id>`, where `<id>` is the app with surrounding whitespace
   stripped and a `.desktop` suffix removed by `_desktop_id`
   (`tools.py:1637-1645`, which keeps an installed id such as
   `org.telegram.desktop`), and with any `:action` kept
   (`dev.zed.Zed:new-window` → `launch dev.zed.Zed:new-window`). This mirrors
   what `_tool_launch_app` launches (`:2452-2458`). The raw description is
   still checked as well, so the spelling the model used cannot pass a rule
   that the cleaned id would not match either. This closes the leading-space
   spelling (`" dev.zed.Zed"`, described as `launch  dev.zed.Zed`) and the
   `.desktop` spelling under a `$`-anchored rule, on both paths.
7. **Out of scope, tracked as #129:** the `omarchy launch tui <program>` and
   compose `tui` pane route.
8. **Unchanged:** `describe` and so the transcript and the confirm line;
   the #112 labels, `why` holds and `_releasing`; #97/#70 resolution and its
   ambiguity refusals (`:1881-1900`, `_resolve_app` `:2383-2404`); dry run
   (the front gate runs before it, `:1937`); `Policy.check` (`:207-218`) and
   #109's rule names (`:198-213`); the default lists.

### Plan-level resolution, flagged for this gate

**R1. One helper, `_launch_text(app)`, instead of the spec's
`_pane_launch(kind, target)`.** Decision 6 makes `launch_app` and the pane
build the same text from the same cleanup, so one function serves both. The
callers test `kind == "app"` themselves (compose) or `not url` (launch_app).
If R1 is struck, wrap it as `_pane_launch(kind, target)` returning
`_launch_text(target) if kind == "app" else None`; nothing else changes.

**R2. An `:action` on a compose pane.** `_DESKTOP_ID_RE` (`tools.py:87`) has
no `:`, so `_validate_compose_windows` already refuses an app pane with an
action. The action-kept tests are therefore `launch_app` tests only.

## Steps

0. **Baseline.** `export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`.
   Run `nix develop -c python3 -m unittest discover -s tests` and
   `nix develop -c python3 -m pytest -q tests`, and
   `git diff b73a3f4 --stat -- src tests README.md`.
   → verify: both runners report **1126** tests OK (measured on `b73a3f4`),
   and the diff is empty.
1. **`tests/test_compose.py`: a new class `OneRuleEveryLaunchTests(
   ComposeFakes, unittest.TestCase)` after `ComposeAppPaneTests` (`:460-555`),
   written before any code change.** Fakes only, from `ComposeFakes`
   (`:289-334`): entries in a temp dir, `shutil.which` → `gtk-launch`,
   `_shell` records argv into `self.launched`, `_dispatch_lua` into
   `self.lua`. The file already imports `_isolated` first (`:16`). setUp
   adds `dev.zed.Zed` (name Zed), `vlc` (name VLC) and `com.ssh.Client`, and
   writes `dev.zed.Zed.desktop` with `Actions=new-window;` and a
   `[Desktop Action new-window]` section. `ZED = r"^launch dev\.zed\.Zed"`.
   The tests are listed under **Tests** below.
   → verify: run `python3 -m unittest tests.test_compose` on the unchanged
   code. Every test marked **(red on main)** fails, and every test marked
   **(guard)** passes. Record the failing count in the PR.
2. **`src/omarchy_voice/tools.py`: add `_launch_text`** beside
   `_pane_command` (before `:723`):

   ```python
   def _launch_text(app: str) -> str:
       """What an app launch is checked as, on every path: the id that is
       actually launched, `.desktop` and whitespace off, `:action` kept (#110)."""
       app, colon, action = app.strip().partition(":")
       return f"launch {_desktop_id(app.strip())}{colon}{action.strip()}"
   ```

   → verify: a helper test in the new class: `" dev.zed.Zed "`,
   `"dev.zed.Zed.desktop"`, `"dev.zed.Zed"` → `launch dev.zed.Zed`;
   `" dev.zed.Zed:new-window"` → `launch dev.zed.Zed:new-window`; an
   installed `org.telegram.desktop` keeps its suffix. Step 1's end-to-end
   tests still fail.
3. **`tools.py:1881-1900` and `:1906-1908`: the front gate** (decisions 1, 2,
   5, 6). Collect `launches: list[str]`:
   - `launch_app` without a `url`: `[_launch_text(str(args.get("app", "")))]`,
     after resolution (`:1887`);
   - `compose_windows`: `_launch_text(str(pane["target"]))` for every dict
     pane with `kind == "app"`, after resolution (`:1900`);
   - otherwise empty.

   Inside the `try`, right after `self.policy.check(description, …)` at
   `:1907` and before `if why:`, add
   `for text in launches: self.policy.check(text)`. The `except` branches
   are unchanged.
   → verify: every launch_app and front-gate compose test of step 1 passes.
4. **`tools.py:3589-3590`: the per-pane check** (decisions 2, 3, 6). After
   `self.policy.check(" ".join(argv))`, in the same `try`, add
   `if kind == "app": self.policy.check(_launch_text(str(pane.get("target", ""))))`.
   → verify: the handler-level tests of step 1 pass, and so does the whole
   new class.
5. **`README.md:904-906`** (Safety, the deny/confirm lists paragraph): add one
   sentence after it: "An installed app is checked as `launch <desktop-id>`
   (the id as launched, without `.desktop`) whether `launch_app` opens it or
   a `compose_windows` `app` pane does, so `^launch dev\.zed\.Zed` in
   `deny_patterns` stops both. Other routes to a program (a terminal, a `tui`
   pane, `omarchy_cli`) are governed by `allow_shell`, not by this rule."
   → verify: `grep -n 'launch <desktop-id>' README.md` finds one line.
6. **Full run and flake.** Both runners, then
   `nix flake check --no-write-lock-file`.
   → verify: 1126 plus the new tests pass in both runners with the same
   count, and the flake check is green.
7. **Mutation checks.** Apply each mutation below, run
   `python3 -m unittest tests.test_compose tests.test_shell_off`, revert.
   → verify: every row is caught, and `git diff` after reverting shows only
   the intended change.

## Tests

New, in `OneRuleEveryLaunchTests` (step 1). "Refused" means `result.ok` is
false, `self.launched == []`, `self.lua == []` (no workspace switch), and one
`DENIED` line in the transcript.

- **(red on main)** `launch_app` blocked by `ZED`:
  - by id with a leading space, `" dev.zed.Zed"`;
  - by `.desktop` suffix under `^launch dev\.zed\.Zed$`,
    `"dev.zed.Zed.desktop"`;
  - with a leading space and an action, `" dev.zed.Zed:new-window"`, under
    `^launch dev\.zed\.Zed:new-window$`.
- **(guard)** `launch_app` blocked by `ZED` by id `"dev.zed.Zed"` and by name
  `"zed"` (resolved, #70). Passes on main; must keep passing.
- **(red on main)** compose `[zed, VLC]` on workspace 4 refused by `ZED` at
  the front: named pane (`Zed`, target `zed`), unnamed pane (target `zed`),
  target `" dev.zed.Zed"`, and target `"dev.zed.Zed.desktop"`. The
  `DENIED` line contains `dev.zed.Zed`, and the output contains
  `blocked by deny rule /^launch dev\.zed\.Zed/`.
- **(red on main)** `test_the_same_rule_refuses_launch_app_and_the_pane`: one
  Config, `launch_app zed` and the compose both refused.
- **(red on main)** per pane: `_tool_compose_windows` called directly (no
  front gate) with an already resolved `dev.zed.Zed` pane under `ZED`
  returns `pane 1 (…) is not allowed by policy` and `launched == []`.
- **(red on main)** confirm `ZED`: the compose returns `confirm_instruction`,
  `executor.pending[0] == "compose_windows"`, nothing launched; then
  `run_pending()` launches both panes (the per-pane `NeedsConfirmation`
  passes only under `_releasing`). And `_tool_compose_windows` called
  directly under confirm `ZED` (not releasing) refuses the pane.
- **(guard)** an action is kept: under `^launch dev\.zed\.Zed$`,
  `launch_app "dev.zed.Zed:new-window"` is not refused (no `DENIED`, and
  `launched == [[GTK, "dev.zed.Zed.desktop:new-window"]]`).
- **(guard)** the raw description is still checked: deny `^launch {2}`
  refuses `launch_app " dev.zed.Zed"`.
- **(guard)** url and web unchanged: under `^launch firefox$`,
  `launch_app {"app": "firefox", "url": "https://x.com/"}` runs `xdg-open`;
  under `^launch https?://`, a compose with a `web` pane
  `https://x.com/` launches it.
- **(guard)** the launcher argv still bites (decision 3): deny `gtk-launch`
  refuses the pane in the handler with `not allowed by policy`.
- **(guard)** built-in rules and #109 names: with `Config()` defaults,
  `launch_app zed` and the compose run. `launch_app com.ssh.Client` is
  refused with ``blocked by deny rule `ssh` `` and, with
  `deny_patterns_remove=["ssh"]`, runs.
- **(red on main)** #112 shell-off: with `allow_shell=False` and `ZED`, a
  compose of an app pane `zed` and a tui pane `bash -c 'echo x'` is
  **refused**, `pending is None` (deny before hold). **(guard)** without
  `ZED`, the same compose is held with `(… ; allow_shell is off)` in the
  `HOLD` line, and `run_pending` launches both panes.

Existing tests: `ComposeAppPaneTests` (`:460-555`), `tests/test_shell_off.py`
and `tests/test_policy.py` (`:32`, the `ssh` name) are unchanged and pass.

Commands, after `export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`:

- `nix develop -c python3 -m unittest discover -s tests`: `OK`, 1126 plus new.
- `nix develop -c python3 -m pytest -q tests`: the same count passing.
- `nix flake check --no-write-lock-file`: green.
- Fakes only. Nothing launches, and nothing writes the real log (#99).

### Mutations (step 7), one per decision

| Decision | Mutation | Test that must fail |
| --- | --- | --- |
| 1 | drop the `launch_app` entry from `launches` | launch_app with a leading space |
| 2 (front) | delete the `for text in launches` loop | compose refused at the front (`launched`, `lua` asserts) |
| 2 (order) | move the loop below `if why: raise …` | #112 shell-off deny-before-hold |
| 2 (handler) | delete the handler's `_launch_text` check | per pane, `_tool_compose_windows` direct |
| 2 (`_releasing`) | move the handler check outside the `try` | confirm `ZED`: `run_pending` launches both |
| 3 | delete the `" ".join(argv)` check | the launcher argv still bites |
| 4 | apply `_launch_text` to every pane kind | web pane under `^launch https?://` |
| 5 | drop the `not url` condition for `launch_app` | url launch under `^launch firefox$` |
| 6 (strip) | drop both `.strip()` calls on the id | leading-space tests, both paths |
| 6 (`.desktop`) | drop `_desktop_id` | `.desktop` tests, both paths |
| 6 (action) | drop `{colon}{action…}` | the action-kept guard |
| 6 (raw) | replace `policy.check(description)` by the launch texts for `launch_app` | the raw-description guard |

That is 12 of 12. Decisions 7 and 8 are "no change" and are held by the
existing suite plus the guards above.

### Deviation found while implementing (2026-09-24)

The `6 (.desktop)` row says "`.desktop` tests, both paths", but the compose
`.desktop` case above runs under `ZED`, which has no `$` and so matches
`launch dev.zed.Zed.desktop` with or without `_desktop_id`. Dropping
`_desktop_id` was caught on the `launch_app` path only. Added one test,
`test_compose_with_the_desktop_suffix_is_refused_under_an_anchored_rule`
(**red on main**): a compose with target `"dev.zed.Zed.desktop"` is refused
at the front under `^launch dev\.zed\.Zed$`. No code change; the row now
holds on both paths as written.

## Rollback

`git revert` the implementation commit. There is no config key, schema,
persisted state or NixOS module change. Without a revert, a user who is
surprised by a refused pane removes or loosens their own `^launch …` rule;
the defaults contain nothing that matches `launch <id>` for an ordinary id.

## Landing order and overlap

Order: #111, **#110**, #121, #79, #80. This lands second.

- **#111** (`fix/111-manifest-key-cost`): rebase on it when it merges, rerun
  step 6.
- **#121** (realtime engine removal, spec approved on
  `docs/121-realtime-engine-future`): its spec edits README in many places,
  including Safety at `README.md:884` (the realtime confirm-key paragraph,
  `:884-889`). This plan inserts one sentence after `:904-906`, about 15
  lines lower in the same section. They are separate hunks, but #121's
  rebase onto this may need a hand merge if it rewrites the section. Its
  spec does not edit `src/omarchy_voice/tools.py`; the gate, `_launch_text`
  and the compose handler are not realtime code, so there is no `tools.py`
  overlap.
- #79 and #80 land after this and rebase on it.
