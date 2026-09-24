---
status: draft
issue: 88
spec: spec/2026-09-24-88-desktop-suffix-in-id.md
---

# Plan: a desktop id that ends in ".desktop" must survive suffix stripping

Closes #88. Line numbers are against `main` at `72f9ab6`. Each one cited here
was checked there. The spec cited `40c80fb`. Since then the `tools.py` sites
have moved by up to 6 lines, and the table below uses the new numbers.

## Approved decisions, carried over from the spec

1. **One helper, `_desktop_id(name) -> str`, in `src/omarchy_voice/tools.py`,
   placed right after `_desktop_entry_exists` (`tools.py:1526-1528`).**
   The helper is exactly this:

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

   It does not end in `.desktop` → returned unchanged, nothing probed. It is
   installed as written → returned unchanged. Otherwise the suffix comes off,
   and that includes "neither form is installed". So a missing app is refused
   with today's message, which names the stripped id.
2. **The helper is used at four sites, and nowhere else:**

   | Site (`tools.py`, `72f9ab6`) | Today | Change |
   | --- | --- | --- |
   | `_pane_hint`, app branch `633-634` | `(target[:-8] if … else target).partition(":")[0]` | `return _desktop_id(target.partition(":")[0])`. The colon split comes first. |
   | `_pane_command`, app branch `659-664` | strip at `660`, regex at `661`, append at `664` | `app = _desktop_id(target)`. The regex and the append stay as they are. |
   | `_resolve_app`, `2149` | `bare = app[:-8] if … else app` | `bare = _desktop_id(app)` |
   | `_tool_launch_app`, `2190-2191` | two-line strip before the regex (`2192`), the existence check (`2199`) and the argv (`2214`) | `app = _desktop_id(app)` replaces both lines |

   The compose hint (`tools.py:3322`, `_desktop_wm_class(hint)` at `3326`) is
   not changed. It now receives the true id, so Telegram's `TelegramDesktop`
   class is found. `_desktop_entry_path`, `_desktop_entry_exists`,
   `desktop_actions` and `_desktop_wm_class` (`1518-1568`) and
   `capabilities.app_index` are not changed either.
3. **The strip in `_validate_launch_app` (`tools.py:2121-2122`) is deleted
   and not replaced.** It only feeds `_DESKTOP_ID_RE` (`tools.py:85`). Any
   valid id is still valid with `.desktop` added, and `.desktop` alone is
   invalid either way. The spec checked 400 prefixes and found no
   counterexample. So these two lines are dead code, and the helper would add a
   probe that cannot change the result.
4. **When both `X` and `X.desktop` are installed, `X.desktop` means the literal
   id `X.desktop`** (the entry file `X.desktop.desktop`). That is the id
   `app_index` and `find_app` publish. This is the only behaviour change for an
   input that works today, and no host here has such a pair.
5. **Out of scope:**
   - Q3: compose refusing an `app` pane whose target is not installed. The
     pane keeps today's shape check and is reported as still opening or failed.
   - Q4: the `find_apps` last-dotted-part alias (`capabilities.py:518`). It is
     now #96, and this fix makes it reachable. See the precondition.

   Nothing outside `tools.py` and the two test files changes. There is no
   config, schema, manifest or packaging change.

**Rejected, and not to be reintroduced while implementing:** fixing only the
three broken sites (that leaves `_resolve_app` correct by luck). Preferring the
filename reading when both exist. A hard-coded Telegram exception or an "ends
in `.desktop.desktop`" test. Normalising arguments once in `Executor.call`
(the pane helpers are also called from `_validate_compose_windows` with raw
dicts). Using the helper in `_validate_launch_app`. Folding in Q3 or Q4.

## Precondition: #96 lands first, or in the same PR

Once this plan lands, `launch_app` accepts `org.telegram.desktop`. Today
`find_apps` scores "desktop" 100 against Telegram, because the last dotted part
of its id is `desktop` (`capabilities.py:518`). `_resolve_app` then rewrites
"desktop" or "the desktop" to `org.telegram.desktop`. Today that launch is
refused by this bug. After this fix, **"open the desktop" would launch
Telegram.** #96 removes that alias.

**Stop at step 1 if #96 is not merged**, unless the approver chooses to land
both in one PR. In that case #96's approved plan steps are done first on this
branch, and the PR closes both issues.

## Steps

0. **Baseline.** Rebase onto `origin/main` and run the suite in the dev shell
   → verify by `nix develop --no-write-lock-file -c python3 -m unittest
   discover -s tests` printing `Ran 930 tests` and `OK`. Outside the dev shell,
   20 key and layout tests fail because libxkbcommon is missing. That is the
   environment, not a regression, and `test_environment` says so.
1. **Precondition (#96).** Run `gh issue view 96 --json state` and
   `git log origin/main --grep '#96'`. Then check on a fixture dir that holds
   only `org.telegram.desktop.desktop` and `google-chrome.desktop`:
   `capabilities.clear_match(capabilities.find_apps("the desktop"))` must be
   `None` on the base this branch sits on → verify by that `None`. If it
   returns Telegram and the approver has not chosen one PR: **stop**.
2. **`tests/test_find_apps.py`: add the failing tests first** (list under
   Tests) → verify by running them on the unchanged code. Every test marked
   *fails on main* fails, and every other test passes.
3. **`tests/test_compose.py`: add the compose tests** (list under Tests) →
   verify the same way: the ones marked *fails on main* fail.
4. **`src/omarchy_voice/tools.py`: add `_desktop_id` after `_desktop_entry_exists`
   (`1526-1528`), exactly as in decision 1** → verify by the `_desktop_id`
   table test passing. The site tests still fail.
5. **`tools.py:2190-2191` (`_tool_launch_app`): replace the strip with
   `app = _desktop_id(app)`** → verify by the launch tests passing.
6. **`tools.py:2149` (`_resolve_app`): `bare = _desktop_id(app)`** → verify by
   `org.telegram.desktop` returning the args unchanged with `find_apps` patched
   to fail.
7. **`tools.py:633-634` and `660` (`_pane_hint`, `_pane_command`): use the
   helper as in decision 2** → verify by the compose tests passing.
8. **`tools.py:2121-2122` (`_validate_launch_app`): delete the two strip
   lines** → verify by the validator tests passing, and by
   `test_policy.test_launch_app_rejects_a_command_line` still passing.
9. **Mutation check** (below) → verify by each mutant failing at least one new
   test, then restore.
10. **Full suite and flake check** → verify by `Ran 9xx tests … OK` (930 plus
    the new ones) and `nix flake check --no-write-lock-file` passing.
11. **Commit** the code and tests together, citing this plan. If anything
    deviates from these steps, update this file in the same commit.

## Overlap with #87

Branch `fix/87-terminal-pane-hint` (spec approved, not yet planned or
implemented) also edits `_pane_hint` (`622-635`) and `_pane_command`
(`638-664`), and the compose call site at `3322`. #87 changes the `terminal`
and `tui` branches, and this plan changes the `app` branch. The hunks are
separate, but they are close enough that the one that lands second should
expect a textual conflict. Resolve it by keeping both changes. Neither change
alters the other's behaviour.

## Tests

All tests use a fixture dir. `tools.app_dirs` and `capabilities.app_dirs` are
patched to it, as in `tests/test_find_apps.py:59` and
`tests/test_compose.py:297`. `tools.shutil.which` returns a fake launcher, and
`Executor._shell` is a recorder. Nothing is launched, and the compositor is
never queried.

The fixture for the new `tests/test_find_apps.py` class
(`DesktopSuffixTests`, with its own temp dir so that `ENTRIES` and the
existing `MatchTests` are not changed) holds these entries:

- `org.telegram.desktop.desktop`: `Name=Telegram`, `StartupWMClass=TelegramDesktop`
- `google-chrome.desktop`: `Name=Google Chrome`, the ordinary id
- `foo.desktop` (`Name=Foo`) and `foo.desktop.desktop` (`Name=Foo Desktop Edition`), the both-exist pair

In `tests/test_find_apps.py`:

- **`_desktop_id` table** (*fails on main*: the name does not exist yet):
  `org.telegram.desktop` → `org.telegram.desktop`;
  `org.telegram.desktop.desktop` → `org.telegram.desktop`;
  `google-chrome` → `google-chrome`; `google-chrome.desktop` → `google-chrome`;
  `foo.desktop` → `foo.desktop`; `foo.desktop.desktop` → `foo.desktop`;
  `missing` → `missing`; `missing.desktop` → `missing`.
- **`launch_app` through `Executor.call`**:
  - `org.telegram.desktop` runs `[launcher, "org.telegram.desktop.desktop"]`
    (*fails on main*: refused, "no desktop entry named 'org.telegram'").
  - `org.telegram.desktop.desktop` runs the same argv (passes on main).
  - `google-chrome` and `google-chrome.desktop` both run
    `[launcher, "google-chrome.desktop"]` (pass on main).
  - `foo.desktop` runs `[launcher, "foo.desktop.desktop"]` (*fails on main*:
    it runs `foo.desktop`).
  - `missing.desktop` is refused, and the message names `'missing'` (passes
    on main).
- **Name resolution**:
  - "Telegram" → `RESOLVE 'Telegram' → org.telegram.desktop` in the
    transcript, and the argv ends in `org.telegram.desktop.desktop`
    (*fails on main*: the resolve works but the launch is refused).
  - `_resolve_app({"app": "org.telegram.desktop"})` returns the args
    unchanged, with `capabilities.find_apps` patched to raise (*fails on main*:
    it calls `find_apps`).
- **Validator**: `_validate_launch_app` returns `None` for
  `org.telegram.desktop`, `org.telegram.desktop.desktop`, `google-chrome` and
  `google-chrome.desktop`. It still refuses `bash -c 'echo hi'` when
  `allow_shell` is off. (All pass on main. They guard decision 3.)

In `tests/test_compose.py`, in the class that holds
`test_telegram_composes_on_its_declared_class` (`:359`):

- The same test with target `org.telegram.desktop`. The launched argv ends in
  `org.telegram.desktop.desktop`, the `TelegramDesktop` window is moved to
  workspace 4, and Discord is not touched (*fails on main*: the pane launches
  `org.telegram.desktop` and the hint is `org.telegram`). The existing
  filename-form test stays unchanged and still passes.
- The ordinary id: target `google-chrome` with `StartupWMClass=Google-chrome`
  composes onto its window (passes on main).
- `_pane_hint("app", "foo.desktop", "")` is `"foo.desktop"` when both
  entries exist (*fails on main*: `"foo"`), and
  `_pane_command("app", "foo.desktop", "")` ends in `"foo.desktop.desktop"`.

### Mutation check

Each mutant is applied alone. Each one must fail at least one new test, and
then it is reverted:

1. The helper always strips (`return name[:-8] if name.endswith(".desktop") else name`).
   The Telegram launch, compose and `foo.desktop` tests must fail.
2. The helper never strips (`return name`). The `google-chrome.desktop` and
   `missing.desktop` rows must fail.
3. The old strip is restored in `_pane_hint` only. The Telegram compose test
   must fail.
4. The old strip is restored in `_resolve_app` only. The "`find_apps` not
   called" test must fail.

### Commands

```
nix develop --no-write-lock-file -c python3 -m unittest tests.test_find_apps tests.test_compose -v
nix develop --no-write-lock-file -c python3 -m unittest discover -s tests   # 930 + new, OK
nix flake check --no-write-lock-file                                        # passes
```

## Rollback

Revert the implementation commit. It touches only `tools.py`,
`tests/test_find_apps.py` and `tests/test_compose.py`, so there is no config,
state or packaging to undo. After a revert, `org.telegram.desktop` is refused
again, which is today's behaviour. If #96 landed in the same PR, revert only
the #88 commit, because #96 is safe on its own.
