---
status: approved
issue: 97
author: olafkfreund
---

# Intent: an app pane in compose_windows is checked and resolved the way launch_app is

Closes #97.

## Problem

`launch_app` does two things before it runs anything that `compose_windows`
does not do for an `app` pane:

1. **Resolves a name to a desktop id** (#70). `_call_locked`
   (`src/omarchy_voice/tools.py:1757-1763`) calls `_resolve_app`
   (`tools.py:2170-2191`) *before* `describe` and the policy gate, so "zed"
   becomes `dev.zed.Zed` when `capabilities.clear_match`
   (`capabilities.py:563-573`) finds exactly one app, and two close matches
   come back as a choice with nothing run.
2. **Refuses an id with no installed entry.** `_tool_launch_app`
   (`tools.py:2228-2234`) checks `_desktop_entry_exists` and answers "no
   desktop entry named … find_app looks up what is installed …". The comment
   above it says why: `uwsm-app` returns success for a missing entry.

An `app` pane gets neither. `_validate_compose_windows` (`tools.py:3288-3319`)
only asks `_pane_command` (`tools.py:660-685`) whether the target is
*shaped* like a desktop id (`_DESKTOP_ID_RE`, `tools.py:87`). `_pane_command`
normalises with `_desktop_id` (#88, `tools.py:1552-1561`) and builds
`[launcher, "<id>.desktop"]` without looking the id up; `_pane_hint`
(`tools.py:653-654`) does the same for the window hint. `_tool_compose_windows`
then launches (`tools.py:3375`), waits up to `PANE_TIMEOUT["app"]` = 10 s
(`tools.py:243`, `3381`, `3385-3386`) for a window, and reports the pane as
"still opening or did not appear".

### Demonstrated with fakes

`tests/test_compose.py`'s `ComposeFakes` on main (93c6dac): fixture app dir
patched into `tools.app_dirs` and `capabilities.app_dirs`, `shutil.which` a
fake `gtk-launch`, `_shell` a recorder, and a clock that advances one fake
second per read. Every run is a two-pane compose, first pane under test,
second pane `vlc` (installed, maps). Nothing launched; run with
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` and `tests/_isolated.py`.

| First pane target | Launched | Fake seconds before pane 2 launches | Result |
| --- | --- | --- | --- |
| `nosuch-app` (not installed) | `gtk-launch nosuch-app.desktop` | 12 (the 10 s app timeout plus clock reads around it) | `ok=True`: "Composed … VLC. Still opening or did not appear: First" |
| `zed` (`dev.zed.Zed.desktop` installed, maps as `dev.zed.Zed`) | `gtk-launch zed.desktop` | 12 | same as above; Zed never opened |
| `Discord` (`discord` and `discord-canary` installed) | `gtk-launch Discord.desktop` | 12 | same as above; no choice offered |

Under the same fakes, `launch_app`:

- `"zed"` → transcript `RESOLVE 'zed' → dev.zed.Zed`, gate sees
  `launch dev.zed.Zed`, runs `gtk-launch dev.zed.Zed.desktop`.
- `"Discord"` → refused, "more than one app fits 'Discord': Discord
  (discord), Discord Canary (discord-canary). Ask which, or call launch_app
  with the id." Nothing run.
- `"disc"` (weak match) → falls through to "no desktop entry named 'disc' …
  find_app …". Nothing run.

So today a pane with a missing entry, a name, or an ambiguous name costs 10 s
of the 32 s `COMPOSE_BUDGET` (`tools.py:246`) and ends in the same vague
line. The model is not told *why*, so it cannot correct itself with
`find_app` or an id, and on a real desktop the missing launch reports success
too. A name with a space ("VS Code") fails the shape check instead, and that
refuses the *whole* composition with "app needs a desktop id".

### The policy gate and compose panes

Compose panes do pass through the gate, twice:

- Outer: `_call_locked` checks `describe("compose_windows")`
  (`tools.py:1936-1943`), i.e. `compose 2 windows on workspace current
  (columns): First, VLC` — labels only, never ids.
- Inner, per pane: `tools.py:3348-3353` checks the pane's argv joined, e.g.
  `/run/current-system/sw/bin/gtk-launch zed.desktop`. Denied or
  NeedsConfirmation refuses the whole call.

The inner check sees whatever `_pane_command` built. If a name is resolved
before `_pane_command`, the inner gate judges `… dev.zed.Zed.desktop`, which
is the property #70 wanted for `launch_app`. Resolving after the check would
lose it.

Note the spellings differ: `launch_app` is judged as `launch dev.zed.Zed`,
a pane as `<launcher path> dev.zed.Zed.desktop`. A deny rule written against
one may not match the other. Not caused by this issue; noted for the approver.

### Session log

`~/.local/state/omarchy-voice/session.log` (read-only, 2026-09-11 to
2026-09-24) has **no** `compose_windows` calls, so there is no record of
what the model passes as app targets there. The only app launch in it is
`omarchy launch spotify`. The schema's own example target is `"spotify"`
(`tools.py:1142`), a bare word that is a name and an id at once, which is the
case that works today only when the two coincide.

## Proposed outcome

- An `app` pane whose target is a name ("zed") that `clear_match` resolves
  opens that app, and the inner policy check judges the resolved id.
- An `app` pane whose target is ambiguous gets launch_app's "more than one app
  fits …" wording, and that pane launches nothing.
- An `app` pane whose target, resolved or not, has no installed entry gets
  launch_app's "no desktop entry named … find_app …" wording, and that pane
  launches nothing and waits for nothing.
- Installed ids, `:action` suffixes and the #88 `org.telegram.desktop` case
  behave as now. Other pane kinds are untouched.

## Affected users and systems

- `src/omarchy_voice/tools.py`: `_validate_compose_windows`,
  `_tool_compose_windows`, and whatever part of `_resolve_app` and the
  `launch_app` refusal text gets shared.
- `tests/test_compose.py` (new cases on `ComposeFakes`).
- Anyone who composes a workspace with app panes, on any host. No config,
  packaging or manifest changes.

## Constraints

- Resolution must happen before the inner per-pane policy check, so the gate
  judges the id that will run, not the word said (#70).
- Same matcher and thresholds as `launch_app` (`find_apps` + `clear_match`);
  no second, looser resolver for compose.
- The existence check is a filesystem probe of `app_dirs()`, as
  `launch_app`'s is. It must not launch or query the compositor, because
  `_validate_compose_windows` also runs alone in dry-run (`tools.py:1784-1793`).
- A missing or ambiguous pane must not leave half a workspace behind without
  saying so; the result must name the pane and the reason.
- Tests use a fixture app dir and the fake clock, never the live desktop.

## Open questions

1. **Refuse the pane or the whole composition?** Checking in
   `_validate_compose_windows` refuses the call before anything moves (no
   workspace switch, no partial layout), and matches how a bad shape is
   refused today. Checking per pane in `_tool_compose_windows` composes the
   rest and reports the bad one, as the issue says ("refuse the pane"). Which?
2. An ambiguous name: same choice as 1, or always refuse the whole call so the
   model asks the user once and retries with ids?
3. Should the outer `describe` for compose show resolved ids instead of labels,
   so a deny rule on an id catches it at the outer gate too? Today only the
   inner argv check could.
4. Should a name with spaces ("VS Code") be accepted as an app target, as
   `launch_app` accepts it via `_APP_NAME_RE` (`tools.py:89`), rather than
   refused by the shape check?
5. The spelling gap between `launch dev.zed.Zed` and `<launcher>
   dev.zed.Zed.desktop` at the gate: in scope, separate issue, or leave?
