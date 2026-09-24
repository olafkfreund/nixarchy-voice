---
status: draft
issue: 88
author: olafkfreund
---

# Intent: a desktop id that ends in ".desktop" must survive suffix stripping

Closes #88.

## Problem

Several places in `src/omarchy_voice/tools.py` accept either a desktop id
(`google-chrome`) or its filename (`google-chrome.desktop`) and normalise by
cutting one trailing `.desktop` unconditionally:

```python
app = target[:-8] if target.endswith(".desktop") else target
```

That is only right when the id itself does not end in `.desktop`. Telegram's
does: its file is `org.telegram.desktop.desktop`, its id is
`org.telegram.desktop`. Given the id, every one of these cuts it to
`org.telegram`, which is not installed.

The issue names the compose path. Checking every site shows the plain
`launch_app` path is broken the same way, and that one is the common case:
"open Telegram" resolves by name to `org.telegram.desktop` (#70) and then
`launch_app` refuses it.

### How many ids this affects here

Read-only scan of `app_dirs()` on p620 with `capabilities.app_index()` from
main (fc52245):

- 303 entries indexed, 485 distinct `.desktop` files across all app dirs
  (including hidden ones).
- Exactly **one** id ends in `.desktop`, in both sets: `org.telegram.desktop`.

The pattern is not Telegram-only in principle (`<reverse-dns>.desktop` is a
naming some Flatpak-era apps use), but on this machine it is one app.

### Every site that strips or appends ".desktop"

Found by `grep -n '\.desktop' src/`. Behaviour demonstrated with a fixture app
dir holding only `org.telegram.desktop.desktop` (`StartupWMClass=TelegramDesktop`),
`tools.app_dirs` and `capabilities.app_dirs` patched to it, `shutil.which`
patched to a fake `uwsm-app`, and `Executor._shell` replaced by a recorder.
Nothing was launched.

| Site | Code | Given `org.telegram.desktop` (the id) | Given `org.telegram.desktop.desktop` (the filename) |
| --- | --- | --- | --- |
| `_pane_hint` | `tools.py:633-634` strips one suffix | `org.telegram` — **wrong** | `org.telegram.desktop` — right |
| `_pane_command` | `tools.py:659-664` strips, then appends | `[uwsm-app, org.telegram.desktop]`, i.e. the non-existent entry `org.telegram` — **wrong** | `[uwsm-app, org.telegram.desktop.desktop]` — right |
| `_desktop_wm_class` | `tools.py:1545-1566`, no stripping, looks up `<id>.desktop` | fed `org.telegram` by the compose path (`tools.py:3316-3320`) → `""` — **wrong input** | fed `org.telegram.desktop` → `TelegramDesktop` — right |
| `_desktop_entry_path` / `_desktop_entry_exists` | `tools.py:1516-1526`, appends `.desktop` | `True` — right | `False` (looks for `….desktop.desktop.desktop`) — correct for an id; callers strip first |
| `_validate_launch_app` | `tools.py:2115-2116` strips, then only checks `_DESKTOP_ID_RE` | `None` (passes) — harmless, both shapes match the regex | `None` (passes) — harmless |
| `_resolve_app` | `tools.py:2143-2144` strips into `bare`, checks existence | `bare = org.telegram` does not exist, so it asks `find_apps`, which scores the id only 54 and leaves `args` unchanged — harmless by luck | `bare = org.telegram.desktop` exists, returned unchanged — right |
| `_tool_launch_app` | `tools.py:2184-2185` strips, then `_desktop_entry_exists` at `2193`, appends at `2208` | **refused**: `no desktop entry named 'org.telegram'`; nothing run | `[uwsm-app, org.telegram.desktop.desktop]` — right |
| `capabilities.app_index` | `capabilities.py:451-478`, globs `*.desktop`, id is `entry.stem` | produces `org.telegram.desktop` — right | — |

End to end, by name: `_resolve_app({"app": "Telegram"})` rewrites to
`org.telegram.desktop` (the id `app_index` publishes and `find_app` prints),
and `_tool_launch_app("org.telegram.desktop")` then refuses it with "no
desktop entry named 'org.telegram'". The assistant's own lookup hands it an id
its own launcher rejects.

`launch_app`'s existence check does not rescue it, as the issue expected: the
check runs *after* the strip, on `org.telegram`. It is what turns a silent
wrong launch into an honest refusal, not what makes the id work.

### What already works

- Anything given the full filename `org.telegram.desktop.desktop`: compose,
  class lookup and `launch_app` all resolve. #75's test
  (`tests/test_compose.py:369`) passes that form for this reason.
- `app_index`, `_desktop_entry_exists`, `_desktop_wm_class`: take or produce a
  true id and never strip.
- `_validate_launch_app` and `_resolve_app`: they strip, but only use the
  result for a shape check or an existence probe that falls through
  harmlessly.

### Unrelated oddity seen on the way

`find_apps` (`capabilities.py:518`) treats the last dotted part of an id as an
alias, so for Telegram that alias is `desktop`: `find_apps("desktop")` puts
Telegram at score 100. Not in scope here; noted for the approver.

## Proposed outcome

- A desktop id that ends in `.desktop` works everywhere its filename does:
  `launch_app("org.telegram.desktop")`, "open Telegram" by name, and a
  compose pane with `target: "org.telegram.desktop"` launch Telegram, and the
  pane matches its `TelegramDesktop` window.
- The filename form keeps working, so #75's existing test is unchanged.
- Ids that do not end in `.desktop` behave exactly as now.

## Affected users and systems

- `src/omarchy_voice/tools.py`: `_pane_hint`, `_pane_command`,
  `_tool_launch_app`, and (for consistency only) `_validate_launch_app` and
  `_resolve_app`.
- Anyone who asks for Telegram by name or id, on any host with an id of this
  shape. One app on p620.
- No config, packaging, schema or manifest text changes.

## Constraints

- The installed entries decide what a string means, as the issue proposes:
  strip the suffix only when the remainder is an installed entry, or when the
  full string is not. No hard-coded Telegram special case.
- `_pane_hint` and `_pane_command` are pure today and are also called from
  `_validate_compose_windows` (`tools.py:3249`) before anything launches. A
  filesystem check there is acceptable (the same `app_dirs()` probe
  `launch_app` already does) but must not launch or query the compositor.
- When neither form is installed, behaviour stays as now: `launch_app`
  refuses with its existing message; compose keeps its current shape check.
- Tests use a fixture app dir, never the live desktop.

## Open questions

1. Should this be one shared helper (id from "id or filename", decided by
   `_desktop_entry_exists`) used by all five stripping sites, or only fix the
   three that are broken (`_pane_hint`, `_pane_command`, `_tool_launch_app`)?
2. When both `X` and `X.desktop` are installed (e.g. `foo.desktop` and
   `foo.desktop.desktop`), which does a bare `foo.desktop` mean? Proposed:
   the literal id, since that is what `app_index` and `find_app` publish.
3. Should `_validate_compose_windows` refuse an `app` pane whose target is not
   installed at all, the way `launch_app` does, instead of launching into a
   `uwsm-app` that reports success for a missing entry? That widens the fix;
   it could be a separate issue.
4. Should the `find_apps` "desktop" alias be split into its own issue, or
   ignored as harmless?
