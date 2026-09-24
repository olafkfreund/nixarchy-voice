---
status: approved
issue: 88
intent: intent/2026-09-24-88-desktop-suffix-in-id.md
---

# Spec: a desktop id that ends in ".desktop" must survive suffix stripping

Closes #88. Line numbers are against `main` at `40c80fb`.

## The intent's open questions, answered

The intent was approved without answers to its four questions, so each is
decided here with the reasoning shown. Q1, Q2 and Q4 are partly claims, so
they are answered with a demonstration. **Any of these decisions can be
rejected at this gate.**

### Demonstration (fakes only)

A temp app dir holds four entries: `org.telegram.desktop.desktop`
(`StartupWMClass=TelegramDesktop`), `google-chrome.desktop`, and the both-exist
pair `foo.desktop` and `foo.desktop.desktop`. `tools.app_dirs` and
`capabilities.app_dirs` are patched to it. `tools.shutil.which` returns
`/fake/uwsm-app`, and `Executor._shell` is a recorder. Nothing is launched and
the compositor is never queried. Section B runs the proposed rule as a local
function in the script. It is not in the repo.

```
fixture: ['foo.desktop', 'foo.desktop.desktop', 'google-chrome.desktop', 'org.telegram.desktop.desktop']
app_index ids: ['foo', 'foo.desktop', 'google-chrome', 'org.telegram.desktop']

A. today, per site
  org.telegram.desktop          hint='org.telegram'         wm=''                pane='org.telegram.desktop'           launch=REFUSED: no desktop entry named 'org.telegram'
  org.telegram.desktop.desktop  hint='org.telegram.desktop' wm='TelegramDesktop' pane='org.telegram.desktop.desktop'   launch=ran ['/fake/uwsm-app', 'org.telegram.desktop.desktop']
  google-chrome                 hint='google-chrome'        wm='Google-chrome'   pane='google-chrome.desktop'          launch=ran ['/fake/uwsm-app', 'google-chrome.desktop']
  google-chrome.desktop         hint='google-chrome'        wm='Google-chrome'   pane='google-chrome.desktop'          launch=ran ['/fake/uwsm-app', 'google-chrome.desktop']
  foo.desktop                   hint='foo'                  wm=''                pane='foo.desktop'                    launch=ran ['/fake/uwsm-app', 'foo.desktop']
  foo.desktop.desktop           hint='foo.desktop'          wm=''                pane='foo.desktop.desktop'            launch=ran ['/fake/uwsm-app', 'foo.desktop.desktop']
  missing                       hint='missing'              wm=''                pane='missing.desktop'                launch=REFUSED: no desktop entry named 'missing' on th
  missing.desktop               hint='missing'              wm=''                pane='missing.desktop'                launch=REFUSED: no desktop entry named 'missing' on th

B. proposed helper (literal id wins)
  org.telegram.desktop          -> 'org.telegram.desktop' exists=True  wm='TelegramDesktop' argv=[uwsm-app, org.telegram.desktop.desktop]
  org.telegram.desktop.desktop  -> 'org.telegram.desktop' exists=True  wm='TelegramDesktop' argv=[uwsm-app, org.telegram.desktop.desktop]
  google-chrome                 -> 'google-chrome'        exists=True  wm='Google-chrome'   argv=[uwsm-app, google-chrome.desktop]
  google-chrome.desktop         -> 'google-chrome'        exists=True  wm='Google-chrome'   argv=[uwsm-app, google-chrome.desktop]
  foo.desktop                   -> 'foo.desktop'          exists=True  wm=''                argv=[uwsm-app, foo.desktop.desktop]
  foo.desktop.desktop           -> 'foo.desktop'          exists=True  wm=''                argv=[uwsm-app, foo.desktop.desktop]
  missing                       -> 'missing'              exists=False wm=''                argv=[uwsm-app, missing.desktop]
  missing.desktop               -> 'missing'              exists=False wm=''                argv=[uwsm-app, missing.desktop]

C. name resolution today
  'Telegram'              find_apps=[(100, 'org.telegram.desktop')]
                          _resolve_app -> {'app': 'org.telegram.desktop'}
  'org.telegram.desktop'  find_apps=[(54, 'org.telegram.desktop')]
                          _resolve_app -> {'app': 'org.telegram.desktop'}
  'desktop'               find_apps=[(100, 'foo.desktop'), (100, 'org.telegram.desktop')]
                          _resolve_app -> more than one app fits 'desktop': Foo Desktop Edition (foo.desktop), Telegram (org.telegram.desktop). Ask which, or call launch_app with the id.

D. _validate_launch_app's strip: regex(s) vs regex(s[:-8])
  400 prefixes, counterexamples: []
```

A second run uses only Telegram and Chrome in the dir, which is how p620 looks:

```
'desktop'      find_apps=[(100, 'org.telegram.desktop')]  _resolve_app -> {'app': 'org.telegram.desktop'}
'the desktop'  find_apps=[(100, 'org.telegram.desktop')]  _resolve_app -> {'app': 'org.telegram.desktop'}
```

### Q1: one shared helper, or fix only the three broken sites? One helper, used at four sites. The fifth strip is deleted.

The rule is added once, next to `_desktop_entry_exists`. `_pane_hint`,
`_pane_command`, `_resolve_app` and `_tool_launch_app` call it. If only the
three broken sites were fixed, `_resolve_app` would keep its own strip. That
strip is harmless today only because `find_apps` scores the id 54 (C, second
row). A later scoring change could quietly break it. With the helper,
`_resolve_app` sees that `org.telegram.desktop` is installed and returns before
it calls `find_apps`.

`_validate_launch_app` (`tools.py:2115-2116`) is the exception. Its strip
feeds only `_DESKTOP_ID_RE`, and D shows that the regex gives the same answer
with or without the suffix. This follows from the regex: any id that is valid
stays valid with `.desktop` added, and `.desktop` alone is invalid either way.
So the two lines are dead code and are deleted. Replacing them with the helper
would add a filesystem probe that cannot change the result.

### Q2: when both `X` and `X.desktop` are installed, what does `X.desktop` mean? The literal id.

The intent's proposal stands. B, row `foo.desktop`: the helper returns
`foo.desktop`, which is the id that `app_index` publishes for
`foo.desktop.desktop` (see `app_index ids` above). That is also what
`find_app` prints and what `_resolve_app` hands on. The assistant always gets
ids from those places. So an id it was handed must mean that id, never another
entry whose filename happens to match. The only other way to name `foo` is
`foo`. A: today `foo.desktop` launches `foo.desktop` (the entry `foo`). This is
the one behaviour change for an input that works now. It needs two entries
that are one suffix apart, and no host here has such a pair (the intent's scan
found one id ending in `.desktop` on p620, and no `org.telegram` beside it).

### Q3: should compose refuse an `app` pane whose target is not installed? No, not in this issue.

The intent's constraint already says what happens when neither form exists:
compose keeps its shape check. A (`missing`) shows the pane gets
`[uwsm-app, missing.desktop]`. The pane then waits out its budget, no window
matches, and compose lists it as still opening or failed. That is an honest
report, not a wrong window, because #75 removed the unmatched-window guess.
Refusing up front would be better, but it is a new refusal message in
`_validate_compose_windows` (`tools.py:3249`). It needs its own tests and has
nothing to do with the suffix. **Recommendation: a separate issue**, which the
lead files if wanted.

### Q4: the `find_apps` "desktop" alias. Out of scope, but this fix makes it reachable. A separate issue is recommended.

`find_apps` (`capabilities.py:518`) adds the last dotted part of every id to
the set of names it matches against. For Telegram that part is `desktop`. The
second run shows the result: with Telegram installed, "desktop" and "the
desktop" are a clear match (100) and `_resolve_app` rewrites them to
`org.telegram.desktop`. **Today that is hidden by this bug**, because
`launch_app` then refuses `org.telegram`. Once this spec lands, "open the
desktop" would really launch Telegram.

It stays out of scope for three reasons:

- It is a scoring rule in a different module (`capabilities.py`).
- The right fix is a judgment call: which last parts are too generic
  (`desktop`, `app`, `client`?). That needs the #70 corpus re-measured, and has
  nothing to do with suffix handling.
- The exposure is narrow. It needs the model to pass the bare word "desktop"
  as an app name to `launch_app`. That is also true of every other generic id
  tail today.

**Recommendation: the lead files a separate issue**, and it should land close
to this one. It is listed under Risks.

## Design

One helper in `src/omarchy_voice/tools.py`, placed after
`_desktop_entry_exists` (`tools.py:1524-1526`):

```python
def _desktop_id(name: str) -> str:
    """The desktop id for an id or its filename (#88).

    org.telegram.desktop is an id that already ends in ".desktop", so the
    suffix comes off only when the literal is not an installed id.
    """
    if name.endswith(".desktop") and not _desktop_entry_exists(name):
        return name[:-8]
    return name
```

The helper covers every case:

- The literal is installed: it is returned unchanged (Q2).
- It is not installed and ends in `.desktop`: it is read as a filename and the
  suffix comes off. This also covers the case where neither form is installed,
  so a missing app gives exactly today's result and `launch_app` refuses with
  today's message (A and B, `missing.desktop`).
- It does not end in `.desktop`: nothing is probed and nothing changes.

Every site, checked against `origin/main` at `40c80fb`:

| Site | Today | Change |
| --- | --- | --- |
| `_pane_hint`, `tools.py:633-634` | `target[:-8] if …` then `.partition(":")[0]` | `_desktop_id(target.partition(":")[0])`. The colon split moves first, which is the correct order. Colon targets never get here because `_pane_command` rejects them first. |
| `_pane_command`, `tools.py:659-664` | strip at 660, append at 664 | `app = _desktop_id(target)`. The regex check and the append stay as they are. |
| compose hint use, `tools.py:3316-3320` | `_desktop_wm_class(hint)` | No change. It now receives the true id, so Telegram's `TelegramDesktop` is found (B). |
| `_validate_launch_app`, `tools.py:2115-2116` | strip, then regex | Delete both lines (Q1, D). |
| `_resolve_app`, `tools.py:2143` | `bare = app[:-8] if …` | `bare = _desktop_id(app)` |
| `_tool_launch_app`, `tools.py:2184-2185` | strip before the regex, the existence check at 2193 and the append at 2208 | `app = _desktop_id(app)` |
| `_desktop_entry_path` / `_desktop_entry_exists` / `_desktop_wm_class` / `desktop_actions`, `tools.py:1516-1566` | take a true id | No change. |
| `capabilities.app_index`, `capabilities.py:437-478` | id is `entry.stem` | No change. |

`_pane_hint` and `_pane_command` gain a filesystem probe. It runs only for
`app` panes whose target ends in `.desktop`, and it is the same `app_dirs()`
`is_file` check that `launch_app` already makes. It does not launch anything or
query the compositor, as the intent's constraint requires. `_pane_hint` is
defined before the helper in the file. That is fine, because Python looks the
name up when the function is called.

Nothing changes outside `tools.py`. There is no config, schema, manifest or
packaging change.

## Alternatives rejected

- **Fix only `_pane_hint`, `_pane_command` and `_tool_launch_app`.** This
  leaves `_resolve_app` correct only by luck (Q1).
- **Prefer the filename reading when both exist** (`foo.desktop` → `foo`).
  That contradicts the ids `app_index` and `find_app` publish (Q2).
- **A hard-coded exception for `org.telegram.desktop`, or an "ends in
  `.desktop.desktop`" test.** The intent rules out a special case. A
  string-only rule also cannot tell `org.telegram.desktop` (an id) from
  `google-chrome.desktop` (a filename). Only the installed entries can.
- **Normalise ids once at the tool boundary**, for example in `Executor.call`.
  The pane helpers are also called from `_validate_compose_windows` with raw
  pane dicts. One helper called at each site is fewer moving parts than
  rewriting arguments before dispatch.
- **Use the helper in `_validate_launch_app` too.** It would add a probe that
  cannot change the result (D). Deleting the strip is smaller.
- **Fold in Q3 or Q4.** Both are separate changes with their own tests (above).

## Risks

- **Q4 becomes reachable.** Once Telegram's id launches, "desktop" or "the
  desktop" given to `launch_app` as a name resolves to Telegram and opens it.
  Today the same path ends in a refusal. The follow-up issue removes this. Until
  then it happens only on a host with Telegram installed (p620), and only if the
  model passes that bare word as an app name.
- **Both-exist behaviour change (Q2).** `X.desktop` switches from entry `X` to
  entry `X.desktop` when both are installed. No host here has such a pair.
- **Extra filesystem probes.** There is one more `is_file` per `app_dirs()`
  entry for a `.desktop`-suffixed target at each call site. That is
  microseconds, and `launch_app` already does the same.
- **Anyone relying on the filename workaround** (#75's test passes
  `org.telegram.desktop.desktop`) is unaffected: B, row two.
- The desktop is not touched. Tests use a fixture dir, so no host is at risk
  during verification.

## Verification

Unit tests over a temp app dir, with `tools.app_dirs` and
`capabilities.app_dirs` patched as in `tests/test_compose.py:297` and
`tests/test_find_apps.py:59`, `shutil.which` faked, and `_shell` recorded.
Nothing is launched. The fixture holds `org.telegram.desktop.desktop`
(`StartupWMClass=TelegramDesktop`), an ordinary `google-chrome.desktop`, and the
both-exist pair `foo.desktop` + `foo.desktop.desktop`.

- **`_desktop_id`**: the eight inputs in B map exactly as shown.
- **`launch_app`**:
  - `org.telegram.desktop` and `org.telegram.desktop.desktop` both run
    `[launcher, "org.telegram.desktop.desktop"]`.
  - `google-chrome` and `google-chrome.desktop` both run
    `[launcher, "google-chrome.desktop"]`.
  - `foo.desktop` runs `foo.desktop.desktop`.
  - `missing.desktop` is refused with today's message naming `'missing'`.
- **Name resolution**: `_resolve_app({"app": "Telegram"})` → then
  `launch_app` runs Telegram's entry, from end to end through `Executor.call`.
  `_resolve_app({"app": "org.telegram.desktop"})` returns the args unchanged,
  and `find_apps` is not called (patched to fail if it is).
- **Compose**: #75's `test_telegram_composes_on_its_declared_class`
  (`tests/test_compose.py:359`) is repeated with target `org.telegram.desktop`.
  The launched argv ends in `org.telegram.desktop.desktop`, and the
  `TelegramDesktop` window is moved to workspace 4. The existing filename-form
  test stays unchanged and still passes. The same test is repeated with an
  ordinary id (`google-chrome`), and `_pane_hint("app", "foo.desktop", "")` is
  checked to equal `"foo.desktop"`.
- **Validator**: `_validate_launch_app` returns `None` for
  `org.telegram.desktop`, `org.telegram.desktop.desktop` and `google-chrome`.
  It still refuses a command line when `allow_shell` is off.
- The full suite: `python -m pytest -q` passes.
- `nix flake check --no-write-lock-file` passes.
