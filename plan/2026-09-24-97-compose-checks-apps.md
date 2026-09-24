---
status: draft
issue: 97
spec: spec/2026-09-24-97-compose-checks-apps.md
---

# Plan: an app pane in compose_windows is resolved and checked before anything runs

Closes #97. Branch `fix/97-compose-checks-apps`, rebased on origin/main
93c6dac (988 tests pass there). Every `file:line` below was re-read against
93c6dac; where the spec's numbers drifted, the number here is the right one.

## Approved decisions, carried over from the spec

1. **App panes are resolved in `_call_locked`, before `describe`,** with the
   existing `_resolve_app` (`tools.py:2170-2191`), in a `compose_windows`
   branch next to the `launch_app` one (`tools.py:1757-1763`). Each pane that
   is a dict with `kind == "app"` has its `target` passed through
   `_resolve_app({"app": target})`; a clear match replaces `target` with the
   resolved id. Other fields and other kinds are untouched, and the caller's
   list and dicts are not mutated (build new ones). Because this is before
   `describe`, the gate, the held `pending` args, the dry-run validator and
   the handler all see resolved ids, as for `launch_app` (#70).
2. **No second resolver, no new matcher, thresholds unchanged.** Reuse
   `_resolve_app` → `capabilities.find_apps` / `clear_match`
   (`capabilities.py:521`, `563`), `_desktop_id` (`tools.py:1552-1561`),
   `_desktop_entry_exists` (`tools.py:1547-1549`). A weak spelling ("zedd")
   is not launched; an installed id always wins over a name (`tools.py:2180`).
3. **`_resolve_app` gains one keyword,** `retry="call launch_app with the id"`,
   so the choice text ends "Ask which, or {retry}." `launch_app`'s text stays
   byte-identical; compose passes `retry="give the pane the id"`.
4. **An ambiguous app refuses the whole composition** before `describe`:
   `Result(False, "pane N: " + <choice text>)`, listing every candidate id
   (e.g. "Discord (discord), Discord Canary (discord-canary)"). Nothing is
   gated, held, recorded as `RUN`, or launched.
5. **A missing entry refuses the whole composition in
   `_validate_compose_windows`** (`tools.py:3288-3319`, the pane loop is
   `3309-3318`), which the handler runs first (`tools.py:3323`), before the
   workspace switch (`tools.py:3327-3333`), and which dry-run also runs
   (`tools.py:1784-1793`). So the missing pane costs 0 fake seconds, not 12
   of the 32 s budget, and no workspace moves. For an `app` pane:
   `app = _desktop_id(target)`; if `_APP_NAME_RE` (`tools.py:89`) matches and
   `_desktop_entry_exists(app)` is false, return `"pane N: " +
   _no_desktop_entry(app)`.
6. **One shared function for the missing-entry wording.** The text now inline
   in `_tool_launch_app` (`tools.py:2228-2233`) moves to a module function
   `_no_desktop_entry(app) -> str`, used by `_tool_launch_app` and
   `_validate_compose_windows`, so the two cannot drift. `launch_app`'s output
   is byte-identical.
7. **Names with spaces are accepted.** "VS Code" resolves to `code` (initials)
   because `_resolve_app` accepts anything `_APP_NAME_RE` does; a spaced name
   that does not resolve gets the missing-entry refusal, not "app needs a
   desktop id". Command lines ("chromium --incognito", "rm -rf /") still fail
   `_APP_NAME_RE` and keep today's shape refusal.
8. **The outer `describe` code is not changed** (`tools.py:1936-1943`), and
   neither are `_tool_compose_windows`, `_pane_command` (`tools.py:660`),
   `_pane_hint` (`tools.py:643`) or the inner per-pane gate
   (`tools.py:3348-3353`). A deny rule on a resolved id (`dev\.zed\.Zed`)
   refuses a "zed" pane: "pane 1 (…) is not allowed by policy".
9. **Schema text** (`tools.py:1137`, `1142`): an app target is "an installed
   app's name or desktop id (find_app lists them)".
10. **Q5, the gate-spelling gap (`^launch dev\.zed\.Zed` blocks launch_app but
    not a compose pane), is out of scope:** split to issue #110 (open). The
    PR links it.

### Spec ambiguities, and how this plan resolves them

- **A. The outer label of an unnamed app pane.** Decision 8 keeps the
  `describe` code, but decision 1 rewrites `target` before `describe`, so an
  app pane *without* a `name` is now labelled with the resolved id
  (`dev.zed.Zed`) where main shows `zed`. Resolution: accept it. It is what
  "describe sees resolved ids" means, it matches `launch_app` (whose describe
  shows the id), and a rule on the id now matches the outer gate as well as
  the inner one. Panes with a `name` are labelled as today. The dry-run test
  (T6) pins it. The approver can reject this; the alternative is carrying the
  original target alongside for the label, which the spec did not ask for.
- **B. Order inside the pane loop.** The existence check runs **before** the
  `_pane_command` shape check, so "VS Codez" gets the missing-entry text
  (decision 7) and a host with no launcher still reports the missing entry
  first. The shape check and its message follow unchanged.
- **C. "0 fake seconds".** Measured as the fake clock's call count: the
  refused call reads `time.monotonic` zero times (the deadline read at
  `tools.py:3340` is never reached), against dozens on main.

## Steps

0. **Baseline.** `git switch fix/97-compose-checks-apps && git rebase
   origin/main`; `git diff origin/main --stat -- src tests` is empty.
   `export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`, then
   `nix develop -c pytest tests -q` → 988 passed, and
   `nix develop -c python3 -m unittest discover -s tests` → OK. Record both.
1. **`tests/test_compose.py` `ComposeFakes` (`289-324`): fixture only.**
   (a) the `launch` fake also appends `argv` to `self.launched`;
   (b) patch `omarchy_voice.capabilities.app_dirs` as well as
   `omarchy_voice.tools.app_dirs` (spec risk: `find_apps` would otherwise scan
   the test machine), and `os.environ` `XDG_CURRENT_DESKTOP=Hyprland` as
   `tests/test_find_apps.py:64` does;
   (c) keep the started `moving_clock()` mock as `self.clock`;
   (d) `entry(app_id, wm_class="", name="x")` writes `Name={name}`.
   → verify by both runners still 988 / OK on the unchanged code.
2. **`StrangerWindowTests` (`326-442`): add the entries it composes.**
   `compose()` (`335-341`) calls `self.entry("vlc")` before composing; the
   Discord test (`372`) adds `self.entry("spotify")`. → verify the class
   passes on main unchanged (it must: entries only make launches real).
3. **Pinning test, passes on main:** a new class `ComposeAppPaneTests
   (ComposeFakes, unittest.TestCase)` with
   `test_launch_app_texts_are_unchanged`: `launch_app "nosuch-app"` returns
   exactly today's missing-entry string (copied from `tools.py:2230-2233`),
   and `launch_app "Discord"` with `discord`/`discord-canary` returns text
   ending "Ask which, or call launch_app with the id." → verify it passes on
   main.
4. **Must-FAIL-on-main tests, same class.** Fixture entries per the spec:
   `dev.zed.Zed` (Name=Zed), `code` (Name=Visual Studio Code), `discord`
   (Name=Discord), `discord-canary` (Name=Discord Canary), `vlc`. Every
   compose is two panes on workspace 4, pane 2 `vlc`.
   - **T1 `test_a_name_opens_the_entry_it_means`:** pane 1 `zed`. `launched`
     contains `[…gtk-launch, "dev.zed.Zed.desktop"]`, not `zed.desktop`;
     transcript has `RESOLVE 'zed' → dev.zed.Zed`; the caller's `panes` list
     still says `zed` (not mutated).
   - **T2 `test_a_missing_app_refuses_the_composition_up_front`:** pane 1
     `nosuch-app`. `ok=False`; output starts `pane 1: no desktop entry named
     'nosuch-app'`; `launched == []`; `lua == []` (no workspace switch);
     `self.clock.call_count == 0`.
   - **T3 `test_an_ambiguous_app_lists_both_ids`:** pane 1 `Discord`.
     `ok=False`; output contains `(discord)`, `(discord-canary)` and
     "give the pane the id"; `launched == []`; no `RUN` line in transcript.
   - **T4 `test_a_spaced_name_resolves`:** pane 1 `VS Code` → `code.desktop`
     launched; and `chromium --incognito` still gets "is not usable as a app
     target" (this half passes on main).
   - **T5 `test_a_deny_rule_on_the_resolved_id_refuses_the_pane`:**
     `Config(deny_patterns=[r"dev\.zed\.Zed"])` (build the executor in the
     test, re-applying the fakes), pane 1 `zed` → `ok=False`, "refused" or
     "not allowed by policy", `launched == []`.
   - **T6 `test_dry_run_refuses_missing_and_resolves_names`:**
     `Config(dry_run=True)`: `nosuch-app` → `ok=False`, "pane 1: no desktop
     entry"; an unnamed `zed` pane → `ok=True`, output contains
     `dev.zed.Zed` (ambiguity A); `launched == []` in both.
   → verify by `nix develop -c pytest tests/test_compose.py -q -k
   ComposeAppPane`: T1, T2, T3, T4, T5, T6 FAIL on main, the pin passes.
   Record the failure lines in the PR.
5. **`src/omarchy_voice/tools.py`: `_no_desktop_entry(app)`** as a module
   function after `_desktop_id` (after `1561`), returning the exact text of
   `2230-2233`; `_tool_launch_app` returns `Result(False,
   _no_desktop_entry(app))`. → verify by the step-3 pin and
   `tests/test_find_apps.py` passing.
6. **`tools.py` `_resolve_app` (`2170`): keyword `retry`** defaulting to
   `"call launch_app with the id"`; the choice text at `2188-2189` becomes
   `f"… Ask which, or {retry}."`. → verify by the pin (byte-identical) and
   `test_find_apps.py` `LaunchByNameTests`.
7. **`tools.py` `_call_locked` (`1757-1763`): the `compose_windows` branch**
   (decisions 1, 3, 4). Only when `args.get("panes")` is a list; each app
   pane dict is copied with the resolved `target`; an ambiguous one returns
   `Result(False, f"pane {n}: {resolved.output}")` at once.
   → verify by T1, T3, T4, T5 passing.
8. **`tools.py` `_validate_compose_windows` pane loop (`3309-3318`):** after
   the kind check and before the `_pane_command` check (ambiguity B), the
   existence check of decision 5. → verify by T2 and T6 passing.
9. **`tools.py` schema (`1137`, `1142`):** decision 9's wording. → verify by
   `grep -n "find_app lists them" src/omarchy_voice/tools.py` (two hits).
10. **Full verification** (see Tests). → both runners green, flake check green.
11. **Mutation checks**, each reverted after: see Tests. → each named test
    fails under its mutation.

## Tests

All with `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`, fakes only,
`tests/_isolated.py` imported first (already at `test_compose.py:16`),
nothing launched, the fake clock in every compose.

- `nix develop -c pytest tests -q` → 988 + 7 new passed, 0 failed.
- `nix develop -c python3 -m unittest discover -s tests` → OK, same count.
- `nix flake check --no-write-lock-file` → passes (its check runs
  `pytest tests -q`, `flake.nix:145`).
- Mutations (run `pytest tests/test_compose.py -q`, then `git checkout`
  the file):
  - delete the `compose_windows` branch in `_call_locked` → T1, T3, T4, T5,
    T6 fail;
  - delete the existence check in the validator → T2, T6 fail;
  - move resolution from `_call_locked` into `_tool_compose_windows` → T6
    fails (dry-run never reaches the handler), and T3's "no RUN" fails;
  - drop the `pane N: ` prefix → T2, T3 fail;
  - compose passes no `retry` → T3 fails;
  - `_no_desktop_entry` wording edited in one place only is impossible by
    construction; edit its text → the step-3 pin fails.

## Overlaps with other open branches

Both are at "spec approved", docs only so far; neither has touched code.

- **`fix/101-terminal-secrets`** edits `tools.py` at the `read_terminal`
  schema (`917-933`) and the terminal functions (`3524-3800`). No hunk is
  shared with this plan (`1137-1142`, `1561`, `1757`, `2170-2233`,
  `3309-3318`); a rebase only shifts lines. No shared test file.
- **`feat/82-installed-commands`** edits `tools.py` at `READ_ONLY_TOOLS`
  (`58-60`), a schema after `find_app` (`797-812`), `describe` near `1880`,
  and adds `_tool_find_command`, most likely next to `_tool_find_app`
  (`2193-2206`), which sits between this plan's `_resolve_app` (step 6) and
  `_tool_launch_app` (step 5) hunks: expect a context conflict there, trivial
  to resolve. Its tests are in `tests/test_find_apps.py`, which this plan
  does not edit.

**Landing order: #97, then #101, then #82.** #97 is the smallest change to
existing functions and its tests pin the `launch_app` texts that #82's
neighbouring hunk must keep; #101 is disjoint; #82 rebases last over both and
resolves the one adjacent-context conflict.

## Rollback

`git revert` the implementation commit: `tools.py` and `test_compose.py`
return to main exactly (no config, schema version, manifest or packaging
change; pure Python, same on every host). If only the refusal proves wrong,
reverting step 8 alone restores main's launch-and-wait for missing apps while
keeping name resolution.
