---
status: approved
issue: 83
spec: spec/2026-09-24-83-services-and-mcp.md
---

# Plan: know which user services and MCP servers exist, without knowing becoming managing

Every `file:line` is checked against `origin/main` at `50dcf99`. The branch
holds only intent and spec there (`git diff --stat 50dcf99 -- src tests
tools README.md flake.nix` is empty). On that commit,
`nix develop -c pytest tests -q` gives **1085 passed** (re-run for this plan
on 2026-09-25, with `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`).

## Approved decisions, carried over from the spec

1. **User units only.** The rows are the services systemd has loaded
   (`systemctl --user list-units --type=service --all -o json --no-pager`,
   6 ms, keeping only `unit load active sub description`) plus the user's
   own unit files in `$XDG_CONFIG_HOME/systemd/user/*.service` (default
   `~/.config/systemd/user`) that are not already listed. Those rows get the
   state `not loaded` and every row from that directory is marked `own`.
   `list-unit-files` (0.58 s) and system units are not used. Follow-up, filed
   only when a request needs it: "find_service: system units too".
2. **"Is X running" is answered by the finder, live.** Each row carries its
   state from the same read. No cache, anywhere: every call reads again. No
   `services` topic in `system_query` (it takes no argument, and all 85
   services would be about 3.5 KB against `OUTPUT_LIMIT = 4000`,
   `tools.py:262`).
3. **Managing stays out.** No start/stop tool, no persona text steering
   toward `systemctl`. The only commands #83 ever runs are
   `systemctl --user list-units` and `systemctl --user show`. The missing
   confirm rule for `systemctl` state changes (`DEFAULT_CONFIRM_RULES`,
   `config.py:111-131`, names none) is a follow-up issue to file, a named
   rule such as `systemctl-change`, noting that stopping
   `omarchy-voice.service` stops the assistant mid-answer.
4. **"Configured MCP" means Claude Code's user scope and local scope** in
   `$CLAUDE_CONFIG_DIR/.claude.json` when that variable is set, else
   `~/.claude.json`, read once per call (5 ms), no cache. Plugin servers,
   Claude Desktop's and project `.mcp.json` are not read, and the answer
   ends with "plugin-provided servers and Claude Desktop's are not listed".
   Follow-up only if asked: "list plugin-provided MCP servers".
5. **The MCP answer says the assistant is not connected to them.** It starts
   "Configured for your Claude Code. This assistant is not connected to
   these." With `config.desktop_control` on it adds "(ai-mirror desktop
   control is this assistant's own)" (`claude_backend.py:566-567`).
   `strict_mcp_config=True` (`claude_backend.py:584`) and
   `setting_sources=[]` (`:580`) are not touched. Nothing proposes
   connecting the user's servers.
6. **Both halves now; the MCP half is tiny.** Services get one tool. MCP gets
   no tool and no argument: one callable `system_query` topic, `mcp`.
7. **`capabilities.find_services(query, limit=8)`**, next to `find_commands`
   (`capabilities.py:800`), reusing `_words` (`:484`) and `_stems` (`:795`)
   and `_run` (`:82`, returns `""` on failure). Not `find_apps`' tiers.
   Matching: query stems against the name (`.service` dropped, split on
   `-` `_` `@` `.`) and the description. An exact name (`voxtype` or
   `voxtype.service`) comes first. Ties: `own` first, then the shorter name.
   Template units (`foo@.service`) are dropped. `app-*@autostart` and
   D-Bus/portal units are not filtered, only outscored.
8. **From a unit file, only the `Description=` line is read.**
   `Environment=`, `EnvironmentFile=` and `ExecStart=` are never parsed or
   kept.
9. **Exact-name fallback.** If nothing matched and the query matches
   `^[A-Za-z0-9@._:-]{1,128}$` with no leading `-`, run
   `systemctl --user show -p LoadState,ActiveState,UnitFileState,Description
   -- <name>.service` (9 ms). `not-found` → "no user service named X".
   Otherwise the unit is reported as existing (for example a disabled
   packaged unit). The query is only ever an argv element after `--`, never
   a shell string.
10. **Detail for an exact top hit** is `systemctl --user show -p
    LoadState,ActiveState,SubState,UnitFileState,Result,ActiveEnterTimestamp
    -- <unit>`, `<unit>` taken from the index, not the query. **The property
    list is fixed**: `show` without `-p` prints `Environment=` and
    `ExecStart=`.
11. **A read-only tool, `find_service`.** Schema after `find_command`
    (`tools.py:884-900`), with the spec's description: *"The user's
    background services (systemd user units) for a name or a purpose —
    \"voxtype\", \"stream deck\", \"sync\". Returns each one's state now:
    running, failed, inactive, not loaded. Starts and stops nothing."* Only
    argument `query` (string, required, `additionalProperties: false`). It
    joins `READ_ONLY_TOOLS` (`tools.py:58-60`), so the confirm gate and
    #112's hold (`tools.py:1916`) skip it, dry run runs it, and deny rules
    still apply (#100). `_is_read` (`claude_backend.py:113-117`) and
    `tools_for` (`mcp_server.py:129`) pick it up with no second list.
    `describe` (`tools.py:2104-2105` is `find_command`'s) gains
    `find services: '<query>'`.
12. **What `find_service` prints.** Rows like
    `  voxtype.service — Voxtype speech-to-text daemon: active (running)`.
    A failed row says `FAILED`. A load state other than `loaded` is shown.
    The exact-hit detail follows the rows. Nothing found: "no user service
    matches '<q>'". Output capped at `OUTPUT_LIMIT`; no path, ever.
    `systemctl` failing reads "could not ask systemd", never "not found".
13. **`capabilities.mcp_servers() -> list[tuple[str, str, str]] | None`**,
    returning `(scope, name, transport)` and nothing else. Scope is `user`
    for top-level `mcpServers`, `local: <basename of the project dir>` for
    `projects.*.mcpServers`. Transport is `type` if it is `stdio`, `http`,
    `sse` or `ws`, else `stdio` when `command` is present, else `?`. `env`,
    `headers`, `url`, `command` and `args` are never read into a variable
    (only `"command" in server` is tested). A key not matching
    `^[\w.@:-]{1,64}$` becomes `(unnamed)`. Missing file → `[]`; bad JSON →
    `None`, and the exception text is never echoed.
14. **The `mcp` topic.** `"mcp"` joins the enum (`tools.py:1352-1354`), the
    schema description (`:1342-1345`) gains "which MCP servers are
    configured", and `SYSTEM_QUERIES["mcp"]` is a callable like `battery`
    and `media` (`tools.py:1492-1493`, dispatched at `:4514-4515`). Text:
    the decision-5 line, one row per server (`  github (http) — user`), the
    decision-4 "not listed" line. None configured: "no MCP servers are
    configured for Claude Code".
15. **Manifest: one static sentence** under "Applications installed here"
    (`capabilities.py:1119-1125`): *"For the user's background services,
    call find_service before saying whether one exists or is running. For
    configured MCP servers, system_query mcp."* No unit or server name goes
    into the prompt. `_cache_key` (`capabilities.py:1130`) hashes this file's
    content (#103), so the key moves once at deploy; nothing new joins it.
16. **`tools/verify_find.py` gains a service set**, `SERVICE_REQUESTS`:
    `voxtype`, `is voxtype running`, `stream deck`, `lan mouse`, `pipewire`,
    `messages`, `mail watch`, `notarealunit`. Each prints the top 5 and the
    time taken, starting nothing. Plus one `system_query mcp` call (see
    ambiguity D for what it prints).
17. **Out of scope:** the `~/.claude.json` deny rule, which is **issue #144
    (OPEN), separate and not a prerequisite** (#83's reader never returns a
    value and its descriptions hold no path, so it is safe with or without
    that rule, and the rule will not break it); the `systemctl` confirm rule
    (decision 3); system units; plugin servers; `withhold_secrets`
    (`tools.py:435`) is not applied, because nothing that could hold a
    secret is ever read into a result.

## Spec ambiguities, resolved here (the approver should confirm)

- **A. Where the fake sits (flagged: stronger than the spec).** The spec
  fakes `systemctl` by patching `capabilities._run`. **Resolution:** the
  tests patch `subprocess.run` one layer down, so the real `_run` runs and
  its argv is checked. The fake allows only `["systemctl", "--user", verb,
  …]` with `verb` in `{"list-units", "show"}` and returns fixture output;
  anything else, including `start`, `stop`, `restart`, `is-active`,
  `list-unit-files` and a missing `--user`, raises `AssertionError`.
  `AssertionError` is not caught by `_run` (it catches `OSError` and
  `SubprocessError`), so a forbidden call fails the test instead of reading
  as "could not ask systemd". `is-active` is refused too: the spec never
  uses it, so the allowlist is exactly the spec's two verbs.
- **B. Two fixed property lists.** The spec's test says "exactly the fixed
  property list", but decisions 9 and 10 use two. **Resolution:** two
  module constants, `_SHOW_EXISTS` and `_SHOW_DETAIL`; every `show` argv must
  be `["systemctl", "--user", "show", "-p", <one of the two>, "--", <x>.service]`.
- **C. The systemd-failed return shape.** Decision 12 needs to tell "found
  nothing" from "could not ask". **Resolution:** `find_services` returns
  `(rows, systemd_ok)`. When `systemd_ok` is false, own-file rows are still
  shown under the line "could not ask systemd; from your unit files only".
- **D. What `verify_find.py` prints for MCP.** Spec §5 says "the row count
  and the scopes"; a `local:` scope carries a project basename.
  **Resolution:** it prints the count per scope kind (`user: N, local: M`)
  and no name at all, so a run log lists nothing from `~/.claude.json`.
- **E. `$CLAUDE_CONFIG_DIR` in tests.** `tests/_isolated.py` redirects
  `HOME` and `XDG_*` but not `CLAUDE_CONFIG_DIR`. **Resolution:** the
  variable is read from `os.environ` at call time, and every MCP test sets
  or removes it with `mock.patch.dict`, so a developer's own value never
  reaches the real file.
- **F. README.** The spec does not name it. **Resolution:** add
  `find_service` to the "Never held" list (`README.md:752-754`), as #82 did.

## Overlaps with other open branches, and the landing order

`refactor/77-engine-duplication` (spec approved, `fce93d2`) cites
`tools.py:1808-1818`, `2064-2176` and `3737-3749`.
`feat/91-atspi-app-content` (spec approved, `aab6d90`) cites
`tools.py:2814`. #83 edits `tools.py:58-60`, `884-900`, `1342-1354`,
`1492-1495`, `2104-2105` and adds a handler after `_tool_find_command`
(`:2439-2462`). #77's `2064-2176` range contains `describe` (`:2104`), so a
rebase may conflict on context there, but no line has two meanings.

**Landing order: #77, then #83, then #91.** #83 is 2nd. It rebases onto #77
and re-checks every line number above before its PR; #91 rebases onto #83.

## Steps

Every Python command runs with
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`. No real `systemctl` is
run by any test, and no real `~/.claude.json` is read.

0. **Baseline.** `git switch feat/83-services-and-mcp && git rebase
   origin/main` (after #77 lands, onto that), then both runners.
   → verify by: 1085 passed and 1085 OK on `50dcf99` (or the new count after
   #77), and `git diff --stat origin/main -- src tests tools` is empty.
1. **`tests/test_find_services.py` (new): the tests, written first.** It
   imports `_isolated` before any `omarchy_voice` import (#99), so `HOME`
   and `XDG_CONFIG_HOME` are throwaway. Canaries: `FAKE-ENV-SECRET-83`,
   `FAKE-HEADER-SECRET-83`, `FAKE-URL-PW-83`, `FAKE-ARG-TOKEN-83`.
   - **Fixture unit dir** `~/.config/systemd/user/` under the isolated
     HOME: `mine.service` (`Description=Stream Deck controller`,
     `Environment=API_KEY=FAKE-ENV-SECRET-83`,
     `ExecStart=/bin/x --token FAKE-ARG-TOKEN-83`), `tpl@.service`, and
     `voxtype.service` (own and loaded).
   - **Fixture `~/.claude.json`** under the isolated HOME: user-scope
     `github` (`type: http`, `headers` with the header canary, `url`
     `https://u:FAKE-URL-PW-83@h/x`) and `local-tool` (`command`, `env` with
     the env canary, `args` with the arg canary); `projects["/tmp/some/deep/proj"]`
     with one server; a key `"Bearer FAKE-HEADER-SECRET-83"` (not a name).
     A second copy under a `CLAUDE_CONFIG_DIR` temp dir with a different
     server, and a bad-JSON file whose bad byte sits next to a canary.
   - **The systemd guard** (ambiguity A), set up in `setUp` for every class:
     `subprocess.run` → the allowlisting fake, which **records every argv**
     and returns fixture JSON for `list-units` (running `voxtype`, failed
     `broken`, `bad-setting` load state `bad`, inactive `pipewire`) and
     `key=value` text for `show` (echoing `Environment=…FAKE-ENV-SECRET-83`
     if `-p` is missing; `LoadState=not-found` for `notarealunit`).
     `subprocess.Popen`, `os.system`, `os.fork`, `os.posix_spawn*`, every
     `os.exec*` and `os.spawn*` raise, as in
     `tests/test_find_commands.py:144-157`.
   Tests:
   - **Guard self-test.** Calling `subprocess.run` with `systemctl --user
     start|stop|restart|is-active x` raises; `list-units` and `show` return.
   - **Read-only.** After each tool, topic, fallback and detail call, every
     recorded argv has verb `list-units` or `show`, starts
     `["systemctl", "--user"]`, and every `show` argv has `-p` with exactly
     `_SHOW_EXISTS` or `_SHOW_DETAIL` and `--` before the unit
     (ambiguity B). Queries `--help` and `x;rm` record no `show` call.
   - **Secret-free.** No canary appears in any `Result.output`, in
     `describe()`, or in any exception text, for `find_service`,
     `system_query mcp`, the `CLAUDE_CONFIG_DIR` variant, bad JSON and a
     missing file. The isolated HOME path and `/tmp/some/deep` are absent.
   - **Finding.** "stream deck" finds `mine.service` as `not loaded`, own.
     `voxtype` puts `voxtype.service` first with its detail. `broken` says
     `FAILED`. The `bad` load state is shown. `tpl@` is absent.
     `notarealunit` reads "no user service named". Two calls with the
     fixture state changed in between show the new state (no cache). With
     the fake returning `""` for `list-units`, the answer says "could not
     ask systemd" and still lists `mine.service`, never "no user service".
   - **MCP.** `system_query mcp` lists `github (http) — user`,
     `local-tool (stdio) — user` and a row ending `— local: proj`; says
     "not connected" and "not listed"; the bad key shows as `(unnamed)`.
     With `CLAUDE_CONFIG_DIR` set, only the variant's server is listed. With
     `desktop_control=True` the ai-mirror note appears. Bad JSON → "could
     not read"; missing file → "no MCP servers are configured".
   - **Wiring**, modelled on `tests/test_find_commands.py:280-308`:
     `find_service` is in `READ_ONLY_TOOLS` and `tools_for(Config())`;
     `_is_read("mcp__omarchy__find_service")` is true; `"mcp"` is in the
     `system_query` enum; `Executor(Config()).call("find_service",
     {"query": "ssh"})` is denied; in dry run a query reaches the handler.
   - **Manifest.** `capabilities.manifest(refresh=True)`, with `CACHE_DIR`
     and `_run` patched as `tests/test_find_commands.py:311-320` does,
     contains "find_service" and "system_query mcp".
   → verify by: **on `50dcf99` the new file fails** (`AttributeError` for
   `find_services`/`mcp_servers`, `mcp` not a topic, `find_service` unknown)
   except the guard self-test, and every other test still passes.
2. **`src/omarchy_voice/capabilities.py`: `find_services` and helpers**,
   after `close_commands` (`:831-833`), before `live_state` (`:836`):
   `_SHOW_EXISTS`, `_SHOW_DETAIL`, `_UNIT_NAME_RE`, `_own_units()`
   (Description= line only), `user_services() -> (rows, systemd_ok)`,
   `find_services(query, limit=8) -> (rows, systemd_ok)`,
   `service_detail(unit)` and `service_exists(name)`. No new import (`json`,
   `re`, `os` are at `:24-27`). → verify by: the guard, read-only,
   secret-free and finding tests pass.
3. **`src/omarchy_voice/capabilities.py`: `mcp_servers()`**, after step 2's
   code. → verify by: the `mcp_servers` unit assertions pass (tuples only,
   `(unnamed)`, basename, `[]`/`None`).
4. **`src/omarchy_voice/capabilities.py:1119-1125`: the manifest sentence**
   (decision 15). → verify by: the manifest test passes, and
   `tests/test_find_commands.py` `ManifestTests` still passes.
5. **`src/omarchy_voice/tools.py`: the tool and the topic.**
   - `find_service` schema after `find_command`'s (`:884-900`).
   - `"find_service"` into `READ_ONLY_TOOLS` (`:58-60`).
   - `describe` branch after `find_command`'s (`:2104-2105`).
   - `_tool_find_service(query)` after `_tool_find_command`
     (`:2439-2462`), per decision 12, descriptions cut at 120 characters.
   - `"mcp"` into the enum (`:1352-1354`) and the description
     (`:1342-1345`); `_mcp_report(executor)` and
     `SYSTEM_QUERIES["mcp"] = _mcp_report` after `:1495`.
   → verify by: the wiring and MCP tests pass.
6. **`tests/test_policy.py:674`: add `"find_service"` to `READ_FAKES`**, and
   add `("find_service", {"query": q}) for q in ("reboot", "shutdown")` next
   to `:703`. → verify by: `ReadsAreNotActions` passes; "reboot" is not held.
7. **`tools/verify_find.py`: `SERVICE_REQUESTS` and the MCP count**
   (decision 16, ambiguity D). It exits 1 if `notarealunit` matches or any
   warm service query takes 50 ms or more. → verify by: a run on p620
   starts nothing (`systemctl --user list-units --state=active` count is the
   same before and after), prints `voxtype` first for `voxtype`, `lan-mouse`
   as `not loaded`, and no MCP server name.
8. **`README.md:752-754`: add `find_service`** to "Never held" (ambiguity F).
   → verify by: reading it.
9. **Mutation checks, one per decision.** Apply each, run
   `pytest tests/test_find_services.py tests/test_policy.py -q`, confirm at
   least one failure, revert. Record the results in the PR.

   | # | Decision | Mutation | Caught by |
   |---|---|---|---|
   | M1 | 1 | `list-units` → `list-unit-files` | guard raises |
   | M2 | 2 | `functools.cache` on `user_services` | no-cache test |
   | M3 | 3 | add `_run(["systemctl","--user","restart",unit])` in `service_detail` | guard raises |
   | M4 | 4 | ignore `CLAUDE_CONFIG_DIR` | variant test |
   | M5 | 5 | drop the "not connected" line | MCP text test |
   | M6 | 6 | remove `"mcp"` from the enum | wiring test |
   | M7 | 7 | drop the template filter | `tpl@` absent test |
   | M8 | 8 | keep the whole unit file as the description | canary test |
   | M9 | 9 | drop the `--` before the unit (spec M6) | argv test |
   | M10 | 9 | drop the name regex check | `--help`/`x;rm` test |
   | M11 | 10 | drop `-p` from the detail `show` (spec M1) | canary + argv tests |
   | M12 | 11 | remove `find_service` from `READ_ONLY_TOOLS` (spec M5) | wiring + policy tests |
   | M13 | 12 | systemd failure reads "no user service matches" | failure test |
   | M14 | 13 | return `json.dumps(server)` as the name (spec M2) | canary test |
   | M15 | 13 | full project path in place of the basename (spec M4) | `/tmp/some/deep` test |
   | M16 | 13 | echo `str(exc)` on bad JSON | canary test |
   | M17 | 14 | drop the "not listed" line | MCP text test |
   | M18 | 15 | remove the manifest sentence | manifest test |

   Decision 16 (a live script) and 17 (out of scope) have no unit test;
   16 is checked by step 7's run. Spec M3 is M8.
   → verify by: all 18 caught.
10. **The full gates.** Run the Tests section. → verify by: every result
    matches.

## Tests

| Command | Expected |
|---|---|
| `nix develop -c pytest tests -q` | 1085 (or the post-#77 count) plus the new tests pass, no new warnings |
| `nix develop -c python3 -m unittest discover -s tests` | the same count, OK |
| `nix flake check --no-write-lock-file` | passes |
| `nix develop -c python3 tools/verify_find.py` on p620 | exits 0; each service query under 50 ms; no MCP name printed |
| by hand, by the user (calls the real brain): `omarchy-voice -n say --no-confirm "is voxtype running"` | answers from a `find_service` call |
| by hand, by the user: `omarchy-voice -n say --no-confirm "do I have a GitHub MCP"` | answers from `system_query mcp` and says the assistant is not connected to it |

All of these read only. None starts, stops or edits a unit, and none prints
an MCP secret.

## Rollback

`git revert` the implementation commit, then
`systemctl --user restart omarchy-voice`. The change adds one tool, one
topic, a few functions and one manifest sentence. Nothing is written to disk
(no cache, no config key), so there is nothing to migrate; the manifest key
moves back on its own because it hashes `capabilities.py` (#103).


## Approved with (2026-09-25)

The owner approved this plan together with one addition, done in the same PR: `tests/_isolated.py` also resets `CLAUDE_CONFIG_DIR` (to a path under the throwaway HOME, or unsets it), so no test can read the real Claude config on a machine where it is set. The explicit per-test setting in this plan stays. A test must show that a set `CLAUDE_CONFIG_DIR` in the parent environment does not reach a test.
