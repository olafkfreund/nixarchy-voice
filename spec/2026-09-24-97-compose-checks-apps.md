---
status: draft
issue: 97
intent: intent/2026-09-24-97-compose-checks-apps.md
---

# Spec: an app pane in compose_windows is resolved and checked before anything runs

Closes #97. Line numbers are against origin/main 93c6dac.

## The intent's open questions, answered

The intent was approved without answers to its five questions. Each is
decided below, with the reason and a fake-run demonstration. **Each can be
rejected at this gate**; a rejection changes the Design section and nothing
else is written until this spec is approved.

### How the answers were demonstrated

A scratch script (not committed) used the `ComposeFakes` pattern from
`tests/test_compose.py:289-321`: a fixture app dir holding `dev.zed.Zed`
(Name=Zed), `code` (Visual Studio Code), `discord`, `discord-canary`
(Discord Canary) and `vlc`, patched into **both** `tools.app_dirs` and
`capabilities.app_dirs`; `shutil.which` a fake `gtk-launch`; `_shell` a
recorder; `time.monotonic` a clock that moves one fake second per read;
`tests/_isolated.py` imported first (#99);
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`. Nothing was launched.
Every run is a two-pane compose on workspace 4, pane 1 under test, pane 2
`vlc`. "Proposed" is a subclass of `Executor` that does exactly what the
Design below says: resolve app panes with `_resolve_app` before `describe`,
and check the entry in `_validate_compose_windows`.

| Pane 1 target | main (93c6dac): launched at fake second | Proposed |
| --- | --- | --- |
| `zed` | `zed.desktop` @0, `vlc.desktop` @12; Zed never opens | `RESOLVE 'zed' → dev.zed.Zed`; `dev.zed.Zed.desktop` @1, `vlc.desktop` @13 |
| `nosuch-app` | `nosuch-app.desktop` @1, `vlc` @13, ok=True | nothing launched, ok=False: "pane 1: no desktop entry named 'nosuch-app' …" |
| `Discord` | `Discord.desktop` @1, `vlc` @13, ok=True | nothing launched, ok=False: "pane 1: more than one app fits 'Discord': Discord (discord), Discord Canary (discord-canary). Ask which, or give the pane the id." |
| `VS Code` / `vs code` | nothing, ok=False: "'VS Code' is not usable as a app target" | `RESOLVE 'VS Code' → code`; `code.desktop` @1, `vlc` @13 |
| `zedd` (weak spelling) | `zedd.desktop` @1, `vlc` @13 | nothing launched: "no desktop entry named 'zedd' …" |
| `zed`, deny `dev\.zed\.Zed` | launches `zed.desktop`: the rule misses | "pane 1 (First) is not allowed by policy"; nothing launched |
| `zed`, deny `^launch dev\.zed\.Zed` | launches | **still launches** `dev.zed.Zed.desktop`, while `launch_app "zed"` under the same rule is refused (Q5) |

### Q1. Missing app: refuse the pane or the whole composition?

**The whole composition, before anything moves.** The check goes in
`_validate_compose_windows`, which `_tool_compose_windows` runs first
(`tools.py:3323`), before the workspace switch (`3329-3333`), and which
dry-run also runs (`tools.py:1784-1793`). Reasons:

- Nothing to undo: no workspace switch, no half layout, zero fake seconds
  (table row 2), against 12 s of the 32 s budget today.
- A refused pane cannot be fixed later: compose builds a fresh layout, so a
  retry with the corrected pane re-launches every other pane too. Refusing
  the call lets the model fix one pane and get the full workspace in one retry.
- It is how a malformed pane is refused today (`tools.py:3314-3318`); the
  message names the pane, as the intent's constraint requires.

This departs from the issue's wording ("refuse the pane"); the outcome the
intent asks for (that pane launches nothing and waits for nothing, and the
result names it) holds.

### Q2. Ambiguous name?

**Same as Q1: refuse the whole call**, with launch_app's choice wording
prefixed `pane N:` and ending "Ask which, or give the pane the id." The
model asks the user once and retries with ids. The refusal happens before
`describe`, so it is not even gated or recorded as a RUN (row 3).

### Q3. Should the outer `describe` show resolved ids?

**No, not in this issue.** Resolution already happens before *both* gates,
so the inner per-pane check (`tools.py:3348-3353`) judges
`<launcher> dev.zed.Zed.desktop` and a deny rule on the id refuses the pane
(row 6). Changing what the outer description says changes which existing
rules match compose at all, which is the Q5 question; it belongs there. The
labels stay as today (`tools.py:1936-1943`).

### Q4. Names with spaces ("VS Code")?

**Yes.** `_resolve_app` accepts anything `_APP_NAME_RE` (`tools.py:89`)
accepts, so "VS Code" resolves to `code` by initials (score 95) before the
shape check sees it (row 4). A spaced name that does not resolve gets the
"no desktop entry named 'VS Codez' … find_app …" refusal rather than
"app needs a desktop id", because the existence check covers every
name-shaped target. Command lines ("chromium --incognito", "rm -rf /") still
fail `_APP_NAME_RE` and keep the shape refusal.

### Q5. The gate-spelling gap?

**Separate issue; out of scope here.** Row 7 shows it: a rule
`^launch dev\.zed\.Zed` blocks `launch_app` but not a compose pane, whose
inner description is `/run/current-system/sw/bin/gtk-launch
dev.zed.Zed.desktop`. It predates #97, applies to every installed id (not only
resolved names), and fixing it means choosing one spelling for the policy
surface of two tools, which is a policy decision with its own tests. After
this spec, a rule written on the bare id (`dev\.zed\.Zed`) matches both. The
follow-up issue will be opened when the plan is written, and is linked from
the PR.

## Design

Three small changes in `src/omarchy_voice/tools.py`, all reusing what
`launch_app` already uses (#70/#88/#96): `_resolve_app`, which calls
`capabilities.find_apps` + `capabilities.clear_match`
(`capabilities.py:521`, `563-573`); `_desktop_id` (`tools.py:1552-1561`);
`_desktop_entry_exists` (`tools.py:1547-1549`). No second resolver, no new
matcher, thresholds unchanged.

1. **Resolve app panes before `describe`.** In `_call_locked`
   (`tools.py:1757-1763`), next to the `launch_app` branch, add a
   `compose_windows` branch: for each pane that is a dict with
   `kind == "app"`, call `self._resolve_app({"app": target})`. A `Result`
   (ambiguous) returns at once as `pane N: <text>`; otherwise the pane's
   `target` is replaced with the resolved id (other pane fields and other
   kinds untouched, the caller's list not mutated). Because it is before
   `describe`, the held `pending` args, the dry-run validator and the handler
   all see resolved ids, exactly as for `launch_app`.
   `_resolve_app` (`tools.py:2170-2191`) gains one keyword,
   `retry="call launch_app with the id"`, so the choice text can end "give
   the pane the id" for compose. Its logic is unchanged: installed ids,
   `:action` suffixes and #88's `org.telegram.desktop` pass through as now.
2. **Refuse a missing entry in `_validate_compose_windows`**
   (`tools.py:3308-3318`). For an `app` pane, `app = _desktop_id(target)`;
   if `_APP_NAME_RE` matches `app` and `_desktop_entry_exists(app)` is false,
   return `pane N: ` + the launch_app missing-entry text. That text moves
   from `_tool_launch_app` (`tools.py:2231-2234`) into one module function
   (`_no_desktop_entry(app)`) used by both, so the wording cannot drift.
   Everything else (`_pane_command`'s shape check and its message) is as now.
   The check is a filesystem probe of `app_dirs()` only, so it is safe in
   dry-run.
3. **Schema text** (`tools.py:1137`, `1142`): an app target is "an installed
   app's name or desktop id (`find_app` lists them)", not only an id, so the
   model knows a name is accepted and a miss is refused.

`_tool_compose_windows`, `_pane_command`, `_pane_hint`, `describe` and the
inner per-pane gate are not changed.

## Alternatives rejected

- **Resolve inside `_validate_compose_windows` or `_pane_command`.** Both run
  after the outer gate, and `_pane_command` is called twice per pane
  (validate, then the launch loop at `tools.py:3345`), so it would resolve and
  record `RESOLVE` twice, and `_pane_hint` would still see the unresolved
  name. `_call_locked` is where #70 put it for the same reason.
- **Refuse only the bad pane and compose the rest** (the issue's wording).
  See Q1: nothing can be added to the layout afterwards, and the model's
  retry duplicates the panes that did open.
- **Check existence in the launch loop.** Too late: the workspace has already
  switched, and dry-run would not see it.
- **A compose-specific, looser matcher** (e.g. accept `found[0]` at 70+).
  The intent forbids it; "zedd" must stay a lookup, not a launch.
- **Change `describe` or the inner argv spelling now.** Q3/Q5.

## Risks

- **Existing tests that compose uninstalled apps.** `StrangerWindowTests`
  (`tests/test_compose.py:326-341`) composes `spotify` and `vlc`, neither of
  which has a fixture entry; after this change both are refused. The tests
  must add `self.entry("spotify")`/`self.entry("vlc")`. That is the fix
  working, not a regression, but the plan must list it.
- **`ComposeFakes` patches only `tools.app_dirs`** (`test_compose.py:313`).
  `find_apps` reads `capabilities.app_dirs`, so without a second patch the
  resolver would scan the machine running the tests. The plan adds that patch.
- **A name that equals a different installed id's name.** Resolution only
  runs when the literal is not an installed id (`tools.py:2180`), so an
  installed id always wins, as in `launch_app`.
- **`:action` panes** (`google-chrome:new-window`) are refused by the shape
  check today (`_DESKTOP_ID_RE` has no colon) and still are; resolution may
  rewrite the name part first, which changes nothing visible. Supporting
  actions in compose is not part of #97.
- **Per-call cost:** one `app_index()` scan (~36 ms for 303 entries,
  `capabilities.py:440`) per name-shaped, non-installed app pane; installed
  ids skip it.
- **Hosts:** pure Python, same on every host; no config, manifest or
  packaging change.

## Verification

New tests on `ComposeFakes` in `tests/test_compose.py`, fixture entries only,
fake clock, `_isolated` imported first, nothing launched:

1. `"zed"` with `dev.zed.Zed` installed: the recorder sees
   `gtk-launch dev.zed.Zed.desktop`, the transcript has
   `RESOLVE 'zed' → dev.zed.Zed`, and the pane's hint is `dev.zed.Zed`.
2. A missing app: `ok=False`, "pane 1: no desktop entry named 'nosuch-app'",
   the recorder is empty, no dispatch was sent (no workspace switch), and the
   fake clock read count shows no wait (well under one pane timeout).
3. Ambiguous `"Discord"` (`discord` + `discord-canary`): `ok=False`, text
   names both ids and "give the pane the id"; nothing launched; no `RUN` in
   the transcript.
4. `"VS Code"` with `code` installed resolves and launches `code.desktop`;
   `"chromium --incognito"` still gets the shape refusal.
5. A deny rule `dev\.zed\.Zed` refuses a `"zed"` pane: "pane 1 (…) is not
   allowed by policy", nothing launched.
6. Dry-run (`Config(dry_run=True)`): a missing app is refused and a name
   resolves, with nothing launched.
7. Unchanged: the #88 Telegram cases and the ordinary-id/PWA cases in
   `StrangerWindowTests` still pass (with `vlc`/`spotify` entries added);
   `launch_app`'s own ambiguous and missing-entry texts are byte-identical.

Then the full suite with
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent python -m unittest discover
-s tests`, and `nix flake check --no-write-lock-file`.
