---
status: draft
issue: 70
spec: spec/2026-09-24-70-find-what-is-installed.md
---

# Plan: the assistant must be able to find what is installed, by the name a person uses

## Approved decisions, carried over from the spec

1. **Scope: launchable apps only.** That is every visible desktop entry and its
   `Actions=`. PATH commands (with tldr summaries) and user units / MCP
   servers become two follow-up issues.
2. **Lexical matching on local metadata, standard library only.** It uses
   `Name`, `GenericName`, `Keywords`, `Comment`, the id, the id's last dotted
   part and the `Exec` basename, with `difflib` for typos. There is no new
   dependency and no model. Measured at 20 of 24 real requests with the right
   top hit, 15-30 ms per query.
3. **Scan on every lookup, no cache.** A scan measured 36 ms for 303 entries.
   A `ponytail:` comment names the upgrade (cache on the application
   directories' mtimes) if a trace ever shows the scan.
4. **Tiers:** 100 for the whole query equal to the name, id, the id's last
   dotted part or the command; 95 for initials (`vs code`); 90 minus extra
   words for every word in the name; 70 for GenericName or Keywords; 40 for
   Comment; up to 60 for a `difflib` ratio of 0.8 or more. Ties go to
   `preferred-*`, then by name. Filler words are dropped.
5. **A clear match is exactly one row at 100 with no other row within 15
   points.** "zed" is clear; "code" (100 / 89) and "discord" (100 / 100) are
   choices.
6. **`launch_app` resolves names in `_call_locked`, before `describe()` and
   the policy check**, so the gate sees the resolved id. A clear match
   rewrites `args["app"]`, keeps any `:action`, and records one line. A choice
   returns the candidates before policy, and nothing runs. Nothing matched means
   the path is unchanged.
7. **A `find_app(query)` tool**, read-only and shaped like `omarchy_help`,
   added to `READ_ONLY_TOOLS`. It reaches MCP and realtime through the shared
   schema list.
8. **The manifest stops listing apps.** `installed_apps()` and its
   `limit=28` are deleted, and `{apps}` becomes one fixed line. This also fixes
   the manifest cache ignoring the application directories.
9. **The 24 requests stay**, as fixture-based tests and as
   `tools/verify_find.py` against the real machine.

Two details the spec left implicit, decided here so the existing tests keep
their meaning. Both only narrow when resolution happens:

- **Resolution only looks at name-shaped input**, meaning letters, digits,
  spaces, `.`, `_`, `+`, `-`, and an optional `:action`. `bash -c 'echo hi'`
  has quotes, so it never reaches the matcher, and
  `test_launch_app_rejects_a_command_line` still gets the "desktop id"
  refusal.
- **A choice needs a top score of at least 70.** Weak Comment-only or typo
  hits fall through to today's refusal instead of producing a list of noise.

## Steps

0. **Baseline.** `nix develop -c pytest tests -q` → record the pass count
   (792 on `main`).

1. **`src/omarchy_voice/capabilities.py`: `app_index()` and `find_apps()`.**
   - Add `STOP_WORDS` and a `_words(s)` helper
     (`re.findall(r"[a-z0-9]+", s.lower())`).
   - `app_index() -> list[dict]`: for each `app_dirs()` root and each sorted
     `*.desktop` file, skip an id already seen. Read the text, split off
     everything from the first `\n[` after the `[Desktop Entry]` group so an
     action group's `Name=` is never read, and take the first `Name`,
     `GenericName`, `Keywords`, `Comment`, `Exec`, `NoDisplay`, `Hidden`,
     `OnlyShowIn`, `NotShowIn` and `Actions`. Skip the entry if `NoDisplay` or
     `Hidden` is true, `Name` is missing, `OnlyShowIn` does not intersect
     `$XDG_CURRENT_DESKTOP` (colon-separated, default `Hyprland`), or
     `NotShowIn` does. The row holds `id`, `name`, `generic`, `keywords`,
     `comment`, `command` (the basename of the first `Exec` token) and
     `actions`. `OSError` on read skips the entry.
   - `find_apps(query, limit=8) -> list[tuple[int, dict]]`: the tiers from
     decision 4. The initials tier: a query word of two or more letters equals
     the first letters of consecutive name words, and the remaining query words
     are name words. Results are sorted by score descending, then
     `preferred-*` first, then name.
   - `clear_match(results) -> dict | None`: decision 5.
   - Delete `installed_apps()` (:425-452).
   → verify by step 6.

2. **`capabilities.py`: the manifest (:726-728, :773).** Replace
   `## Applications installed here\n\n{apps}` with the one fixed line from the
   spec (§3), and drop `apps=` from `TEMPLATE.format`. → verify: a new test
   renders `manifest(refresh=True)` with the external calls stubbed. The
   manifest contains `find_app` and no line from a fixture desktop entry.

3. **`src/omarchy_voice/tools.py`: `find_app`.**
   - Add the schema after `omarchy_help` (:744), with the spec's wording.
   - Add `"find_app"` to `READ_ONLY_TOOLS` (:57).
   - `describe`: `find apps: <query>`.
   - `_tool_find_app(query)`: up to 8 rows,
     `Name (id) — GenericName [actions: …]`. With none, it returns
     `Result(True, "nothing installed matches …; it is not installed under
     that name")`.
   → verify by step 6.

4. **`tools.py`: `launch_app` resolution.**
   - In `_call_locked`, before `description = self.describe(name, args)`
     (:1641): `if name == "launch_app" and not args.get("url"):` call
     `resolved = self._resolve_app(args)`. If that returns a `Result`, return
     it (a choice). Otherwise `args = resolved`.
   - `_resolve_app(args)`: split off `:action`. If the id half already exists
     (`_desktop_entry_exists`, also stripping `.desktop`) or is not
     name-shaped, return `args` unchanged. Otherwise call `find_apps`, then:
     a clear match rewrites `app` to `id[:action]` and records
     `resolved '<said>' → <id>`; a top score of 70 or more returns
     `Result(False, "more than one app fits '<said>': A (a), B (b). Ask which,
     or call launch_app with the id.")`; anything else returns `args`
     unchanged.
   - `launch_app`'s description (:764-773) and its `app` parameter
     description: "An installed app, by the name a person uses or its
     desktop id".
   - The "no desktop entry" refusal (:2066-2070) also names `find_app`.
   → verify by step 6.

5. **`persona.py` (:129-130).** No change to the rule. Check that its "Launch
   apps with launch_app or omarchy_cli" still reads right, and change nothing
   if it does.

6. **Tests.**
   - `tests/test_find_apps.py` (new). The fixture directory is written in
     `setUp`, and `capabilities.app_dirs` and `tools.app_dirs` are **both**
     patched to `[fixture]`, because `app_dirs()` always includes the user's
     `DATA_HOME` (`config.py:45`). The fixture entries: `dev.zed.Zed`
     (`Name=Zed`, `Exec=zeditor %U`), `org.gnome.Nautilus` (`Name=Files`,
     `Keywords=folder;manager;`), `preferred-file-manager`
     (`Name=File Manager`), `code` (`Name=Visual Studio Code`), `claude-code`
     (`Name=Claude Code`), `discord` and `omarchy-Discord` (both
     `Name=Discord`), `preferred-web-browser` (`Name=Web Browser`),
     `yad-icon-browser` (`Name=Icon Browser`), `hidden` (`NoDisplay=true`),
     `gnome-only` (`OnlyShowIn=GNOME;`), `not-here` (`NotShowIn=Hyprland;`),
     and `with-action` (an action group whose `Name=Decoy`).
     - `app_index`: hidden, gnome-only and not-here are absent; `with-action`'s
       name is not `Decoy`; a duplicate id in a second directory is ignored.
     - `find_apps` plus `clear_match`: "zed", "zedd" and "open zed" are
       clearly `dev.zed.Zed`; "the file manager" is clearly
       `preferred-file-manager`; "vs code" is clearly `code`; "code" is not
       clear; "discord" is not clear; "browser" has `preferred-web-browser`
       first; "flurble" gives `[]`.
     - `Executor.call("launch_app", {"app": "zed"})` with `_shell` faked:
       the launcher is called with `dev.zed.Zed.desktop`, and the transcript
       has `resolved 'zed' → dev.zed.Zed`.
     - A policy deny rule for `dev\.zed\.Zed`: `launch_app("zed")` is
       **refused**, which proves the gate saw the resolved id.
     - `launch_app("code")` returns the choice naming both ids, and `_shell`
       is never called.
     - `launch_app("bash -c 'echo hi'")` still says "desktop id".
     - `find_app("manager")` lists Files; "flurble" returns `ok` with "not
       installed".
     - `"find_app" in READ_ONLY_TOOLS`, and the MCP tool list
       (`mcp_server._to_mcp_tools` over the schemas) includes it.
     - The manifest test from step 2.
   - The existing tests must pass unchanged: `test_policy.py`
     (`DesktopActionTests`, the command-line refusal) and `test_realtime.py:105`
     (`launch firefox` in the log).
   - Mutation check: drop the 15-point margin from `clear_match` →
     "code is not clear" fails. Then restore it.
   → verify: `nix develop -c pytest tests -q` equals the baseline plus the new
   tests, with no new failures.

7. **`tools/verify_find.py` (new).** It runs the spec's 24 requests through
   `find_apps` on the real machine and prints each request's top rows, whether
   it is a clear match, and the time. It never launches anything. The
   docstring says so, in the style of `verify_matching.py`. → verify: it runs
   here and reproduces the spec's table (20 of 24, plus the three lexical
   fixes: "vs code", "code" as a choice, "browser").

8. **Whole check.** `nix flake check --no-write-lock-file` → it passes.

9. **Live dry runs.** `nix run .#omarchy-voice -- -n say --no-confirm "open
   zed"`, then "open obsidian", then "open the file manager". Expected: one
   `launch_app` call each, naming a real id (or, for "obsidian", which has two
   entries on this machine, a choice spoken back). No `ToolSearch`, no guessed
   `omarchy launch …` route. Record the times against the intent's
   7.6-9.7 s.

10. **Follow-ups and PR.** Open two issues: "use an installed command-line tool
    by name (PATH index with tldr summaries)" and "know which services and MCP
    servers exist (user units, MCP config)", each citing this PR. Push, and
    open a PR that closes #70 and links the intent, spec and plan.

## Tests

```
nix develop -c pytest tests -q                              # baseline + new, 0 new failures
nix develop -c pytest tests/test_find_apps.py tests/test_policy.py tests/test_realtime.py -q
python3 tools/verify_find.py                                # real machine, launches nothing
nix flake check --no-write-lock-file                        # CI parity
```

Plus the dry runs in step 9.

## Rollback

It is a single squash-merged PR, with no Nix, config or schema change and no
persisted state. `git revert <merge>` restores the 28-entry list,
`installed_apps()` and id-only `launch_app`. The cached manifest regenerates
on its own, since its key includes `capabilities.py`'s mtime. A session that
called `find_app` before the revert simply stops seeing the tool.
