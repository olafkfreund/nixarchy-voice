---
status: draft
issue: 110
intent: intent/2026-09-24-110-one-rule-every-launch.md
---

# Spec: a deny rule on an app must stop that app however it is launched

Closes #110. Line numbers are from main `b73a3f4` (re-verified). `tools.py`,
`README.md` and `tests/test_compose.py` did not change between `6a9a3f5`, where
this spec was first written, and `b73a3f4`.

## Evidence

Measured with the `ComposeAppPaneTests` fakes (`tests/test_compose.py:460`),
which use `gtk-launch` as the launcher, through `Executor.call`, with the
default lists plus one rule. The script is `scratchpad/spec110.py` and is not
committed. `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`. The compose call
is `[Zed (app: zed), VLC (app: vlc)]` on workspace 4.

| Rule added               | `launch_app zed` | compose                                                                   |
| ------------------------ | ---------------- | ------------------------------------------------------------------------- |
| deny `^launch dev\.zed\.Zed`    | refused          | **both panes launched**                                                   |
| deny `launch dev\.zed\.Zed`     | refused          | refused by the per-pane check, only because `gtk-launch dev.zed.Zed.desktop` contains the text |
| deny `dev\.zed\.Zed`            | refused          | refused at the front gate                                                 |
| confirm `^launch dev\.zed\.Zed` | held             | **both panes launched**                                                   |
| confirm `launch dev\.zed\.Zed`  | held             | **refused**, not held: the per-pane check is not a release (`:3596`)      |
| confirm `dev\.zed\.Zed`         | held             | held at the front gate                                                    |

The intent measured the second row on p620, where the launcher is `uwsm-app`,
and the rule missed. So whether the unanchored rule catches a pane depends on
which launcher `shutil.which` finds first (`tools.py:747`).

### Every route, re-measured on `b73a3f4`

What `Policy.check` receives for Zed on each route that can start it, with deny
`^launch dev\.zed\.Zed` added. A spy on `Policy.check`, the same fakes, nothing
launched (`scratchpad/routes110.py`, not committed). "Shell off/on" is
`allow_shell`. The `omarchy_cli` holds were checked with the real
`shutil.which`, because the fakes make every `which` succeed.

| Route                                              | Text the gate sees                                              | Shell off              | Shell on        |
| -------------------------------------------------- | --------------------------------------------------------------- | ---------------------- | --------------- |
| `launch_app` app=`dev.zed.Zed`                     | `launch dev.zed.Zed`                                            | refused                | refused         |
| `launch_app` app=`zed` (resolved, #70)             | `launch dev.zed.Zed`                                            | refused                | refused         |
| `launch_app` app=`dev.zed.Zed.desktop`             | `launch dev.zed.Zed.desktop`                                    | refused (prefix only)  | refused         |
| `launch_app` app=`" dev.zed.Zed"`                  | `launch  dev.zed.Zed` (two spaces)                              | **launched**           | **launched**    |
| compose app pane, named `Zed`, target `zed`        | front: `compose … Zed (app: dev.zed.Zed)`; pane: `<launcher> dev.zed.Zed.desktop` | passes | passes |
| compose app pane, unnamed                          | front: `compose … dev.zed.Zed`; pane: as above                  | passes                 | passes          |
| `omarchy_cli` `launch-or-focus zed [cmd]`          | `omarchy launch or focus zed …`                                 | held (#112)            | passes          |
| `omarchy_cli` `launch tui zeditor`; compose `tui` pane `zeditor` | `omarchy launch tui zeditor`; `compose … zeditor` | passes, no hold        | passes          |
| `hypr_dispatch` `exec_cmd`                         | `dispatch exec_cmd '<command>'`                                 | refused (`:159-161`)   | passes          |
| `run_in_terminal` `zeditor`                        | `run in terminal: zeditor`                                      | held (#112)            | passes          |

Two rows are gaps in `launch_app` itself, not in compose. `_resolve_app`
(`:2383-2404`) returns the caller's args unchanged when the stripped text
already names an entry, and `describe` (`:2068-2069`) does not strip, while
`_tool_launch_app` (`:2446-2476`) strips and applies `_desktop_id`. So a
leading space escapes an anchored rule, and the `.desktop` filename form is
caught only because the rule has no `$`. See Risks.

## Decisions on the intent's open questions

The intent was approved with "approve all" and its questions unanswered, so
each one is decided here and can be rejected at this gate.

1. **Every app launch is checked as `launch <resolved-id>`, whatever the
   path.** It is not only documented. A rule shape that is documented but not
   enforced is the problem this issue reports. `launch dev.zed.Zed` is the text
   `launch_app` already shows in the transcript (`describe`, `tools.py:2068-2069`),
   so a rule copied from the log is the rule that works. For a pane this is an
   extra text that the gate checks. It does not replace the compose
   description, so the transcript line and the #112 confirmation stay as they are.
2. **Both places.** Each app pane's `launch <id>` goes through the gate on its
   own, so anchored rules work. The first check is in `_call_locked`, right
   after the composition's description is checked (`tools.py:1907`). That is
   the only place a refusal lands before the workspace switch and the first
   launch (`:3566-3572`, `:3619`), and the only place a confirm match becomes a
   hold of the whole composition rather than a refusal halfway through it. The
   handler's per-pane check (`:3587-3597`) also checks the same text, because
   the deny list has the last word (a constraint in the intent). A pane that
   was confirmed passes it through the `_releasing` path that #112 added.
3. **Yes, the per-pane check keeps the launcher argv.** A rule on `uwsm-app`,
   `gtk-launch` or `.desktop` bites today, and the intent says existing rules
   must keep matching. The argv is checked first and `launch <id>` second.
4. **Scope is `launch_app` and compose `app` panes only**, as the issue says.
   `hypr_dispatch` exec is refused as process execution only while
   `allow_shell` is off (`tools.py:159-161`). `omarchy_cli launch-or-focus
   <pattern> [cmd]` takes a window pattern and a command, not a desktop id, so
   there is no resolved id to describe it by. With the shell off it is held
   for a yes (#112), which is not a refusal. Neither route, nor
   `run_in_terminal`, has a desktop id to check. They are governed by
   `allow_shell`, not by an app rule (the route table above). A bare-program
   `tui` route (`omarchy launch tui zeditor`, or a compose `tui` pane
   `zeditor`) is not held even with the shell off, and it starts the app's
   binary with no id check. It needs its own design and belongs in a separate
   issue if wanted. The README sentence below names these two paths only, so
   it does not promise more.
5. **No `url` / `web` pane change.** `launch_app` with a `url` does not start
   the named app: it runs `xdg-open <url>` (`:2450-2451`), and `app` is only
   in the description. A `web` pane opens a webapp window
   (`omarchy launch webapp <url>`, `:734`), not an installed app by id, so the
   two are not the same app launched by two paths. Its description already
   shows the URL (`name (web: url)`), so a rule on the host catches it. The
   `url` case also skips resolution (`:1881`). That is the #70 decision and is
   left alone here.

## Design

Only the stdlib is used and no new helper type is added. There are two edits
in `src/omarchy_voice/tools.py` and one sentence in the README.

1. **The pane's text, in one place.** Add a module function next to
   `_pane_command` (`tools.py:723`):

   ```python
   def _pane_launch(kind: str, target: str) -> str | None:
       """What an app pane is checked as: launch_app's own text (#110)."""
       return f"launch {target.strip()}" if kind == "app" else None
   ```

   By then the target is the resolved id (`_call_locked`, `:1888-1899`), or the
   caller's text unchanged when that already names an entry (`_resolve_app`
   leaves it alone, `:2383-2404`), which can carry whitespace or the
   `.desktop` filename form. With a clean id this is the exact text
   `describe("launch_app", {"app": id})` produces. `_pane_launch` strips; it
   does not apply `_desktop_id`, which `_pane_command` does (`:744`). See Risks.

2. **The front gate** (`_call_locked`, inside the `try` at `:1906-1918`,
   right after `self.policy.check(description, …)`). For `compose_windows`,
   `policy.check(text)` runs on every pane's `_pane_launch`. It runs before
   the `why` shell hold, so deny still refuses first. On `Denied` the existing
   handler records `DENIED  <compose description> (<rule>)`. The description
   already names the pane's id (label `Zed (app: dev.zed.Zed)`, or the bare id
   when there is no name, `:2147-2152`). The rule is named by #109's message.
   On `NeedsConfirmation` the existing handler holds the whole composition
   under its front description (`:1929-1933`). So a confirm rule on an app
   holds a composition that contains it, and nothing launches before the yes.

3. **The per-pane check** (`:3589-3597`) runs `policy.check` on `" ".join(argv)`
   and then on `_pane_launch(kind, target)` when it is not None. Both are
   inside the same `try`, so the existing `Denied` and `NeedsConfirmation`
   branches, including `if not self._releasing`, apply to both unchanged.

4. **README** (Safety, after the deny/confirm lists paragraph near
   `README.md:905`), one sentence: an installed app is checked as
   `launch <desktop-id>` whether `launch_app` opens it or a `compose_windows`
   `app` pane does, so `^launch dev\.zed\.Zed` in `deny_patterns` stops both.

Unchanged: `describe` and so the transcript, the #112 labels, #97/#70
resolution and ambiguity refusals, dry-run (the front gate runs before it at
`:1937`), and `Policy.check`.

## Alternatives rejected

- **Put `launch <id>` into each pane's label in `describe`** (for example
  `Zed (app: launch dev.zed.Zed)`). Rejected: an anchored `^launch …` still
  cannot match the middle of the compose line, and it changes the text #112
  just settled on.
- **Only change the per-pane check in the handler.** Rejected: rows 1 and 5
  above. A deny there fires after the workspace switch and after earlier panes
  launched, and a confirm match becomes a refusal because the handler is not a
  release.
- **Only document an unanchored rule shape.** Rejected by Q1. Row 2 shows the
  unanchored shape works on one launcher and not the other.
- **Make `Policy.check` accept a list of texts.** Rejected: it adds an
  interface change to the class every tool goes through, for two call sites.
- **Check the launcher argv at the front too.** Not needed for this outcome,
  and the argv is host-dependent. The handler keeps checking it (Q3).

## Risks

- **A pane that used to open is now refused or held.** Only with a user rule
  that already refuses or holds `launch_app` of the same app. That is the
  point of the change. The default lists contain nothing that matches
  `launch <id>` for an ordinary id, and `launch_app` already goes through the
  same text with the same defaults.
- **A confirm hold now shows the compose line, not `launch <id>`.** The user
  confirms the composition that contains the app, which is what runs.
  Accepted.
- **Double checking of a pane's text on release.** The front gate and the
  handler both check it. On release the handler's `NeedsConfirmation` passes
  through `_releasing`, and a deny refuses in both places. This is not a
  behaviour risk.
- Hosts: all. No host-specific code. The fix removes the launcher dependence
  for this rule shape.
- **Open, found in re-verification: `launch_app` is not canonical either.**
  `launch_app` with app `" dev.zed.Zed"` is described as `launch  dev.zed.Zed`
  and launches Zed past `^launch dev\.zed\.Zed` on `main` today (route table).
  The design above leaves `describe` unchanged, so the outcome "one rule stops
  both paths" still fails on this spelling, while the pane, which strips, is
  caught. The `.desktop` filename form (`launch dev.zed.Zed.desktop`) is caught
  by both paths only because the rule has no `$`; `^launch dev\.zed\.Zed$`
  misses it on both. Not decided here, because it changes `describe`: the
  approver should decide whether the gate text for both paths is built from
  `_desktop_id(app.strip())`, the id that is actually launched.
- **Residual routes, by the Q4 decision.** A bare-program `tui` route
  (`omarchy launch tui zeditor`, a compose `tui` pane) starts the app's binary
  with no hold and no id check, even with the shell off. `omarchy_cli
  launch-or-focus`, `run_in_terminal` and `hypr_dispatch` exec are held or
  refused with the shell off and pass unmatched with it on. An app rule is not
  a boundary against these; the README sentence must not say "however it is
  launched".
- **#109 and #112 interplay, checked.** The front denial records the compose
  line plus #109's message, which names a built-in rule (`blocked by deny rule
  \`<name>\``) or shows a user rule's pattern. No default deny or confirm rule
  matches `launch dev.zed.Zed`. On release, the handler's `NeedsConfirmation`
  passes only under `_releasing`, and `run_pending` does not re-run the front
  gate, so a held composition runs as confirmed and a deny still refuses there.

## Verification

New tests in `tests/test_compose.py`, `ComposeAppPaneTests`. Each must fail
on `main` first:

- `test_an_anchored_deny_on_launch_refuses_the_pane`: deny
  `^launch dev\.zed\.Zed`. The compose of `[zed, vlc]` is refused,
  `self.launched == []` and `self.lua == []` (no workspace switch), and the
  transcript has one `DENIED` line that contains `dev.zed.Zed`. On `main`, both
  panes launch.
- `test_the_same_rule_refuses_launch_app_and_the_pane`: one Config, both
  calls refused. This is the intent's "a test proves it".
- `test_an_anchored_confirm_on_launch_holds_the_composition`: confirm
  `^launch dev\.zed\.Zed`. The compose returns the confirm instruction,
  `executor.pending[0] == "compose_windows"`, and nothing is launched. Then
  `run_pending()` launches both panes. On `main`, both launch without a hold.
- `test_an_unnamed_pane_is_checked_by_id`: the same deny with an unnamed pane
  is refused.
- `test_a_rule_on_the_launcher_still_bites`: deny `gtk-launch` refuses the
  pane in the handler with `not allowed by policy` (Q3; passes on main and
  must keep passing).

Mutation checks. Each one must turn a new test red:

- Drop the front loop: the anchored deny test fails because of the launched
  and lua asserts.
- Drop the handler's `_pane_launch` check: no front-gate test fails, so a
  handler-level test calls `_tool_compose_windows` directly with a pane that
  is already resolved, under deny `^launch dev\.zed\.Zed`, and expects
  `not allowed by policy` with `launched == []`.
- Make `_pane_launch` return the name instead of the id: the unnamed-pane
  test and the anchored tests fail.

Runs, all green, with `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`:

- `nix develop -c pytest tests -q`
- `nix develop -c python3 -m unittest discover -s tests`
- `nix flake check --no-write-lock-file`

No test launches anything (the fakes record argv) or writes the real log
(#99).
