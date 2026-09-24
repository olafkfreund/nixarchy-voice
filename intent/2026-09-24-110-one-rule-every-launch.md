---
status: draft
issue: 110
author: olafkfreund
---

# Intent: a deny rule on an app must stop that app however it is launched

Closes #110. Split out of #97 (Q5 of `spec/2026-09-24-97-compose-checks-apps.md`).
Related: #70 (resolve before the gate), #100 (the gate matches descriptions),
#112 (merged: the compose description now shows every pane's target).

## Problem

The policy gate matches deny and confirm rules against a text description of
the call, not against what the call does (`Policy.check`,
`src/omarchy_voice/tools.py:206-218` on `main` 6a9a3f5). The same app gets a
different description depending on which tool opens it, so a rule written
against one spelling misses the other.

The two paths that open an installed app:

- `launch_app` is resolved to a desktop id before the gate (`:1881-1887`, #70)
  and described as `launch <id>` (`describe`, `:2068-2069`).
- A `compose_windows` pane of kind `app` is resolved the same way (`:1888-1898`,
  #97), then checked twice: once as part of the whole composition's
  description (`:2142-2156`, which #112 changed to show every pane's target),
  and again per pane inside the handler against the launcher argv
  (`:3587-3597`), built by `_pane_command` (`:744-748`) as
  `<uwsm-app or gtk-launch> <id>.desktop`.

Measured on p620 with `Executor.describe`, `_pane_command` and a real
`Policy` whose deny list is the defaults plus `^launch dev\.zed\.Zed`:

| Path                               | Text the gate sees                                                                          | Result  |
| ---------------------------------- | ------------------------------------------------------------------------------------------- | ------- |
| `launch_app` app=`dev.zed.Zed`     | `launch dev.zed.Zed`                                                                        | DENIED  |
| compose, pane named "Zed"          | `compose 2 windows on workspace next (columns): Zed (app: dev.zed.Zed), BBC (web: https://bbc.com)` | passes  |
| compose, pane with no name         | `compose 1 windows on workspace next (columns): dev.zed.Zed`                                | passes  |
| the per-pane check                 | `/run/current-system/sw/bin/uwsm-app dev.zed.Zed.desktop`                                   | passes  |

How other spellings of a rule fare against those four texts:

| Rule                     | Catches                                  |
| ------------------------ | ---------------------------------------- |
| `^launch dev\.zed\.Zed`  | `launch_app` only                        |
| `launch dev\.zed\.Zed`   | `launch_app` only                        |
| `dev\.zed\.Zed`          | all four                                 |
| `zed`                    | all four                                 |

So the rule a user would write after reading `launch dev.zed.Zed` in the
transcript (`DENIED  launch dev.zed.Zed ...`) stops that call and lets the same
app open as a compose pane. A rule on the bare id or name happens to catch
both, but nothing tells the user that the shape they copied from the log is
the one that leaks. The same gap applies to confirm rules: a confirm rule
anchored on `launch <id>` holds `launch_app` and not the pane.

The per-pane text is also host-dependent: it carries the launcher's absolute
path, and which launcher (`uwsm-app`, else `gtk-launch`) is whichever
`shutil.which` finds first.

## Proposed outcome

- One rule written against the resolved app, in the form the transcript shows
  for `launch_app`, refuses that app whether it is asked for by `launch_app`
  or as a `compose_windows` app pane, before anything is launched.
- The same holds for confirm rules: the pane is held (or refused) wherever
  `launch_app` would be held.
- A test proves it: a deny rule on a resolved id refuses both paths.
- What the user reads in the transcript for a composed app pane still tells
  them which app was opened.

## Affected users and systems

- Anyone who writes `deny_patterns` / `confirm_patterns` in
  `~/.config/omarchy-voice/config.toml` to keep a particular app from being
  opened by voice or by an MCP client.
- `src/omarchy_voice/tools.py`: `Executor.describe`, `_call_locked`,
  `_tool_compose_windows`'s per-pane check, `_pane_command`.
- The README's policy section (around `README.md:832-925`), which tells users
  how rules are matched.
- Voice, typed and MCP callers alike: all go through `Executor.call`.

## Constraints

- Must not weaken #112: the compose confirmation keeps showing every pane's
  whole target, and a pane passes a confirm match only on the confirmed
  release.
- Must not weaken #97 or #70: names are resolved to ids before any gate, and
  an ambiguous pane still refuses the whole composition before anything runs.
- The deny list keeps the last word: the per-pane "defence in depth" check
  must not become weaker than today.
- Existing rules that match today (by name, by bare id, by the launcher argv)
  must still match.
- Nothing may launch in a check or test; tests must not write the real log
  (#99).

## Open questions

1. Is the target "every app launch is described as `launch <resolved-id>`
   at the gate, whatever the path" (the issue's direction), or is it enough
   that one documented rule shape catches both, with the README saying so?
2. Where does the pane's `launch <id>` get checked: in the composition's front
   description (one line per pane? the whole description?), in the per-pane
   check, or both? Anchored rules (`^launch …`) only work if the pane's text
   is checked on its own.
3. Should the per-pane check keep matching the launcher argv as well, so a
   rule on `uwsm-app` or `.desktop` still bites?
4. Scope: do other routes that can open the same app belong here, e.g.
   `omarchy_cli` `launch or focus …` or a `hypr_dispatch` exec (the latter is
   already blocked as process execution)? Or only `launch_app` and compose
   app panes, as the issue says?
5. Does `launch_app` with a `url` (`launch <id> <url>`) need the same
   treatment for a compose `web` pane (`omarchy launch webapp <url>`), or is
   that a separate issue?
