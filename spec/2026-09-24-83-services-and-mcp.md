---
status: approved
issue: 83
intent: intent/2026-09-24-83-services-and-mcp.md
---

# Spec: know which user services and MCP servers exist, without knowing becoming managing

Line numbers are against `origin/main` at `50dcf99` (re-verified 2026-09-25,
after #79, #80, #110, #111, #114, #121 and #138). The intent's line numbers
were taken at `6a9a3f5`, and some have moved. Measurements were taken on p620
on 2026-09-24 by two scratch scripts, which are not committed. Both were
read-only and ran with `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`
(`systemctl --user` uses the manager's private socket, not the bus). The MCP
script printed names, transport keys and counts, and no values.

| Read | Result | Time |
|---|---|---|
| `systemctl --user list-units --type=service --all -o json` | 85 services (55 active, 30 inactive). Keys: `unit load active sub description` | **0.006 s** |
| `systemctl --user list-unit-files --type=service -o json` | 152 files. Keys: `unit_file state preset` (no description) | 0.58 s |
| `~/.config/systemd/user/*.service` | 24. **3 are not loaded**, so `list-units` alone misses them | a directory listing |
| `systemctl --user show -p <fixed list> voxtype.service` | `Description`, `LoadState=loaded`, `ActiveState=active`, `SubState=running`, `UnitFileState=enabled`, `ActiveEnterTimestamp`, `Result=success` | 0.009 s |
| `show -p LoadState` on a unit that does not exist | `LoadState=not-found` (while `list-units` prints `[]` and exits 0) | 0.009 s |
| `~/.claude.json` parse | 19 user-scope servers (stdio, http, sse). Keys seen: `args command env headers type url`. 11 local-scope servers in 5 projects | 0.005 s |
| Enabled plugins (`~/.claude/settings.json` `enabledPlugins`, 10) that ship an `.mcp.json` | 1 plugin, 1 server | 0.001 s |

## Decisions on the intent's open questions

The intent was approved with a bare "approve all", which leaves its six
questions open. This spec decides each one. **Each decision can be rejected
at this gate.**

1. **Which services are findable: user units only. The loaded services plus
   the user's own unit files. Not all 152 unit files. System units are
   deferred.**
   `list-units --type=service --all` covers every service that systemd has
   loaded, with its live state and `Description=`, in 6 ms. Three of the
   user's 24 own units are not loaded (`aikit-sync`, `lan-mouse`,
   `app-com.mitchellh.ghostty`). Listing `~/.config/systemd/user` adds them
   for the cost of a directory listing. `list-unit-files` costs 0.58–0.74 s,
   which is 100 times more. The only thing it adds is packaged units that are
   neither loaded nor the user's own, such as a disabled packaged service.
   Nobody asks about those by purpose. They are still answered by exact name
   (Design 1, "exact name fallback"), so "not found" stays true. System units
   (`docker`, `sshd`) are out, as the issue says. Reading them is just as
   read-only, but no request has asked for one (intent, "What the log
   shows"). **Follow-up, filed only when a request needs it:** "find_service:
   system units too".

2. **"Is X running" goes in the finder, not in `system_query`.** It is not a
   second job: `list-units` returns the state in the same 6 ms read that
   finds the unit, so each row carries it. `system_query` topics take no
   argument (schema at `tools.py:1340-1360`). A `services` topic would have to return
   all 85 services every time, about 3.5 KB against `OUTPUT_LIMIT = 4000`
   (`tools.py:262`), or it would need an argument slot. State is read live on
   every call, with no cache, which is what "stale is worse than slow" asks
   for.

3. **Managing a unit stays out of #83. It stays a shell command, and a
   named follow-up closes the gap.** No `start_service` tool. With the shell
   off, a model-chosen `systemctl` command already waits for a yes (#112,
   `tools.py:1914-1917`). With `allow_shell` on, `systemctl --user stop X`
   runs unconfirmed, because no rule in `DEFAULT_CONFIRM_RULES`
   (`config.py:111-131`) names it. That gap is on main today and has nothing
   to do with finding, so it gets its own review. The same reasoning was
   used for #82's Q1. **Follow-up issue to file:** "Confirm rule for
   `systemctl` state changes (start, stop, restart, reload, enable, disable,
   mask, unmask, kill, isolate, daemon-reload, edit, set-property)". It
   would be a new named rule (#109 names), such as `systemctl-change`. The
   issue should note that stopping or restarting `omarchy-voice.service`
   stops the assistant mid-answer. #83 adds no persona text that steers
   toward running `systemctl`.

4. **"Configured" means Claude Code's user scope and local scope in
   `~/.claude.json`. Plugin servers and Claude Desktop are not listed, and
   the answer says so.** User and local scope are what `claude mcp list`
   manages, and they are one 5 ms JSON parse. Plugin servers depend on
   Claude Code's own enabled-plugin and install-path bookkeeping
   (`enabledPlugins` plus `installed_plugins.json`). That format is internal,
   and the 56 files include `.trash/` copies, so a naive scan would
   overcount and a correct one would track a private format. On p620 the
   correct answer is 1 server. Claude Desktop's 6 belong to a different
   client. The answer ends with "plugin-provided servers and Claude Desktop's
   are not listed", so "no GitHub MCP" is never claimed from an incomplete
   read. Project-scope `.mcp.json` is skipped too: the daemon has no current
   project, and p620 has none. `$CLAUDE_CONFIG_DIR/.claude.json` is read
   instead of `~/.claude.json` when that variable is set, because that is
   where Claude Code keeps it. **Follow-up, only if asked:** "list
   plugin-provided MCP servers".

5. **Yes, the answer says so in one line. Reaching the user's servers is
   not proposed.** The MCP answer starts with "Configured for your Claude
   Code. This assistant is not connected to these." When `desktop_control`
   is on, it adds "(ai-mirror desktop control is this assistant's own)",
   because `claude_backend.py:566-567` adds it apart from the user's list.
   `strict_mcp_config=True` (`claude_backend.py:584`) and
   `setting_sources=[]` are deliberate (#94). Loading the user's 19 servers,
   four of which carry tokens, into an open-microphone brain is a security
   decision, not a finder feature. No follow-up is filed unless the user
   asks for it.

6. **Build both halves now, and keep the MCP half tiny.** The demand comes
   from the stated goal, not from the log. So the smallest form that meets
   the outcome is built. Services get one tool (Design 2). MCP adds **no new
   tool and no argument**: one callable `system_query` topic, `mcp`
   (Design 3), which is about 30 lines. Splitting the halves across two
   issues would cost more review than the MCP half costs to build.

## Design

### 1. `capabilities.py`: `find_services(query, limit=8)`

It goes next to `find_commands` (`capabilities.py:800`). It reuses `_words`
(`:484`) and `_stems` (`:795`). It does not reuse `find_apps`' tiers, for the reason
#82's spec gives: they score names, not purposes.

- **Rows, read live on every call. No cache.**
  - `_run(["systemctl", "--user", "list-units", "--type=service", "--all",
    "-o", "json", "--no-pager"])` (`_run` at `capabilities.py:82`, which
    returns `""` on failure). Only `unit`, `load`, `active`, `sub` and
    `description` are kept.
  - `$XDG_CONFIG_HOME/systemd/user/*.service` (default
    `~/.config/systemd/user`), for units that are not already in the list.
    From each file, **only the `Description=` line is read**. `Environment=`,
    `EnvironmentFile=` and `ExecStart=` are never parsed or kept, since
    they are where a unit's secrets live. These rows get the state
    `not loaded`. Each row is marked `own` if it is in that directory.
  - Template units (`foo@.service`) are left out: they are not something
    that can be running.
- **Matching.** Stems are compared against the name, with `.service`
  dropped and split on `-` `_` `@` `.`, and against the description. An
  exact name (`voxtype` or `voxtype.service`) comes first. When scores tie,
  an `own` unit ranks first, then the shorter name. `app-*@autostart`
  instances and D-Bus or portal plumbing are not filtered out. They lose on
  score because their descriptions do not match what people ask for.
- **Exact-name fallback.** If nothing matched, and the query matches
  `^[A-Za-z0-9@._:-]{1,128}$` with no leading `-`, run
  `systemctl --user show -p LoadState,ActiveState,UnitFileState,Description
  -- <name>.service`, one read of 9 ms. If the result is `not-found`, the
  answer is "no user service named X". Otherwise the unit is reported as
  existing, for example a disabled packaged unit. The query is only ever an
  argv element after `--`, and never goes through a shell.
- **Detail for the top hit when it is an exact name:**
  `systemctl --user show -p
  LoadState,ActiveState,SubState,UnitFileState,Result,ActiveEnterTimestamp
  -- <unit>`, where `<unit>` comes from the index and not from the query.
  **This property list is fixed.** `show` without `-p` would print
  `Environment=` and `ExecStart=`, which can hold secrets. This is what
  answers "why is the stream deck dead": `Result=exit-code` and since when.

### 2. `tools.py`: a read-only tool, `find_service`

- Schema, after `find_command` (`tools.py:884-900`):
  `{"name": "find_service", "description": "The user's background services
  (systemd user units) for a name or a purpose — \"voxtype\", \"stream deck\",
  \"sync\". Returns each one's state now: running, failed, inactive, not
  loaded. Starts and stops nothing.", "input_schema": {query: string,
  required, additionalProperties: false}}`.
- `READ_ONLY_TOOLS` (`tools.py:58-60`) gains `"find_service"`. The confirm
  gate never holds it, dry run runs it, deny rules apply (#100), and #112's
  hold skips it (`tools.py:1916`). `claude_backend._is_read`
  (`claude_backend.py:113-117`) and the MCP server's list (`tools_for`,
  `mcp_server.py:129`) pick it up with no second list, as with `find_command`.
- `describe` (`tools.py:2102-2105`): `find services: '<query>'`.
- `_tool_find_service(query)` returns plain text in rows like
  `  voxtype.service — Voxtype speech-to-text daemon: active (running)`.
  Failed rows say `FAILED`. A load state other than `loaded` is shown, so
  the 7 `bad` units are reported and not hidden. The exact-hit detail
  follows the rows. When nothing is found: "no user service matches '<q>'".
  Output is capped at `OUTPUT_LIMIT`, and no path is ever included.
  `systemctl` failing (no user manager) reads "could not ask systemd". It
  never reads "not found".

### 3. `tools.py`: `system_query` topic `mcp`

- `"mcp"` joins the enum (`tools.py:1352-1354`, which now ends in `media`, #79).
  `SYSTEM_QUERIES["mcp"]` is a callable, the way `battery` and `media` are
  (assigned at `tools.py:1492-1493`, dispatched at `tools.py:4514-4515`). The schema's description gains "which MCP servers
  are configured".
- `capabilities.mcp_servers() -> list[tuple[str, str, str]]` returns
  `(scope, name, transport)` and nothing else:
  - It reads `$CLAUDE_CONFIG_DIR/.claude.json`, or else `~/.claude.json`,
    once per call, since a parse takes 5 ms. There is no cache, so a newly
    added server shows up on the next call.
  - The scope is `user` for top-level `mcpServers`, and `local: <basename of
    the project dir>` for `projects.*.mcpServers`. Only the basename is
    used, so the 173-directory history does not reach the model.
  - The transport is `type` if it is one of `stdio`, `http`, `sse` or `ws`,
    else `stdio` when `command` is present, else `?`.
  - **Only these three strings are ever extracted.** `env`, `headers`,
    `url`, `command` and `args` are never read into a variable. A key that
    does not match `^[\w.@:-]{1,64}$` becomes `(unnamed)`, so a secret
    pasted as a key cannot leak through the name.
  - A missing file returns `[]`. Bad JSON returns `None`, and the exception
    text is not echoed, since `JSONDecodeError` carries a position and the
    raw document is never formatted into the answer.
- The `_mcp` handler text: the "configured for your Claude Code, not
  connected to this assistant" line (Decision 5), then one row per server
  (`  github (http) — user`), then "plugin-provided servers and Claude
  Desktop's are not listed". With none configured, it says "no MCP servers
  are configured for Claude Code".

### 4. `capabilities.py` manifest: one static sentence

Under "Applications installed here" (`capabilities.py:1119-1125`), which is
static text. It adds nothing that varies by call or by host, so the prompt
prefix stays stable (#69). The key does move **once**, at the deploy:
`_cache_key` (`capabilities.py:1130`) hashes this file's content (#103), and
since #111 also `_versions()` and the installed coding agents. Nothing in #83
joins that key: *"For the user's background
services, call find_service before saying whether one exists or is running.
For configured MCP servers, system_query mcp."* No unit or server name goes
into the prompt.

### 5. `tools/verify_find.py`: a service set

`SERVICE_REQUESTS`: `voxtype`, `is voxtype running`, `stream deck`, `lan
mouse` (own but not loaded), `pipewire` (packaged), `messages`, `mail
watch`, and `notarealunit`. Each prints the top 5 and the time taken, and
nothing is started. Plus one `system_query mcp` call that prints only the
row count and the scopes, so a run log does not list the server names.

## Alternatives rejected

- **`list-unit-files` as the index.** It takes 0.58–0.74 s per question, has
  no descriptions and no state, and would still need a second call for
  state. The exact-name fallback covers what it adds.
- **Caching the unit list.** 6 ms live is cheaper than invalidation, and
  state must be live anyway.
- **D-Bus (`ListUnits`) instead of `systemctl`.** It returns the same data
  and adds a bus dependency to the tests. `systemctl -o json` works without
  the session bus.
- **A `services` topic in `system_query`.** See Decision 2: it must either
  dump every service or grow an argument.
- **A separate `find_mcp` tool.** It would add a second schema and a query
  argument for a list of about 30 names that the model can scan itself.
- **Scanning plugin `.mcp.json` files.** See Decision 4: it would
  overcount `.trash/`, and resolving it correctly means tracking Claude
  Code's private bookkeeping.
- **Returning MCP URLs or commands "without the secret parts".** Redacting
  is a denylist over formats nobody controls (query tokens, `user:pw@`,
  `--api-key=` arguments). Returning names only is an allowlist.
- **A `manage_service` tool behind a confirm.** See Decision 3.
- **Adding the `~/.claude.json` deny rule here.** See Risks.

## Risks

- **`~/.claude.json` is not a deny path.** #100's secret rules cover
  `~/.claude/.credentials.json` (`secret-claude-login`, `config.py:175`) but not `~/.claude.json`,
  which holds MCP `env` and `headers` tokens for 4 or more servers, and
  also not `~/.config/Claude/claude_desktop_config.json`. **This spec does
  not fix that.** Its reader never returns a value, and its tool
  descriptions contain no path, so it is safe with or without a rule, and a
  later rule will not break it. The exposure is the `Read` built-in
  (`claude_backend.py:130`) and `run_in_terminal cat ~/.claude.json`. That
  exposure exists on main today and is a policy change with its own
  review. **Recommended follow-up issue:** "Deny reads of `~/.claude.json`
  and Claude Desktop's config (MCP tokens)", adding
  `secret-claude-config: r"/\.claude\.json\b"`, which also matches
  `.claude.json.backup*`, plus a `claude_desktop_config.json` rule.
- **Managing is still unconfirmed with `allow_shell` on** until Decision
  3's follow-up lands. #83 does not make this worse and does not steer
  toward it.
- **Deny rules apply to the query.** On a default install,
  `find_service "ssh-agent"` is refused by `\bssh\b`, as `find_app` and
  `find_command` already are (#82 Risks).
- **Unit descriptions and server names are untrusted text** in a tool
  result. They are the same class as #101's content. They are shown, not
  obeyed. #101's `withhold_secrets` (`tools.py:435`) filters pane text by
  line and is not applied here: nothing that could hold a secret (`Environment=`,
  `ExecStart=`, MCP `env`/`headers`/`url`/`args`) is ever read into the result.
- **The user's `~/.claude.json` format can change.** A reshaped file gives
  `[]` or "could not read", never a crash and never a leak, because only
  keys are walked.
- **No user manager** (a test box or a container): `_run` returns `""` and
  the answer is "could not ask systemd". Only the user's own unit files are
  still found, as `not loaded`.
- **The daemon's own unit.** `find_service omarchy-voice` reports itself as
  running. That is correct.
- **Hosts:** the same code runs on p620 and razer. razer's unit set differs,
  and nothing is hardcoded.

## Verification

Tests go in `tests/test_find_services.py`, which imports `_isolated` first
(#99), so `HOME` and `XDG_CONFIG_HOME` are throwaway directories. Every run
exports `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`.

- **Fixtures carry fake secrets.** The canaries are
  `FAKE-ENV-SECRET-83`, `FAKE-HEADER-SECRET-83`, `FAKE-URL-PW-83` and
  `FAKE-ARG-TOKEN-83`.
  - A fixture unit dir with `mine.service`: `Description=Stream Deck
    controller`, `Environment=API_KEY=FAKE-ENV-SECRET-83` and
    `ExecStart=/bin/x --token FAKE-ARG-TOKEN-83`. It also holds a
    template `tpl@.service`.
  - A fixture `~/.claude.json` with user-scope `github` (http, `headers`
    with the header canary, `url` `https://u:FAKE-URL-PW-83@h/x`) and
    `local-tool` (stdio, `env` with the env canary, `args` with the arg
    canary). It has a project entry `/tmp/some/deep/proj` with one server,
    and a key that is itself a canary-shaped non-name. It also has a
    `$CLAUDE_CONFIG_DIR` variant.
  - `systemctl` is faked by patching `capabilities._run` with fixture JSON:
    a running, a failed, a `bad-setting` and an inactive service. The fake
    **records every argv**.
- **Assertions:**
  - No canary string appears in any `Result.output`, in `describe()`, or in
    any exception text, for the tool, the topic, or bad-JSON and
    missing-file cases.
  - `find_service` finds `mine.service` by "stream deck" as `not loaded`.
    An exact name comes first with its detail. A failed unit says `FAILED`.
    A `bad-setting` load state is shown. The template is absent.
    `notarealunit` reads "no user service", from the fallback returning
    `not-found`.
  - **Every `show` argv has `-p` with exactly the fixed property list, and
    `--` before the unit.** A query of `--help` or `x;rm` never reaches
    argv.
  - Only `list-units` and `show` are ever called. `subprocess.Popen` and
    `os.system` are patched to raise. There is no `start`, `stop` or
    `list-unit-files`.
  - `system_query mcp` lists `github (http) — user`, `local-tool (stdio) —
    user` and `… — local: proj`. It contains "not connected" and "not
    listed". `/tmp/some/deep` is absent. The bad key shows as `(unnamed)`.
  - Wiring: `find_service` is in `READ_ONLY_TOOLS` and `tools_for(Config())`.
    It is added to `READ_FAKES` (`tests/test_policy.py:674`), so a query
    of "reboot" runs and is not held. "ssh" is denied.
- **The tests fail on main first.** They are run against `origin/main`
  before the implementation, and fail with `find_services` / `mcp_servers`
  missing and `mcp` not a topic.
- **Mutation checks,** each of which must turn a test red, and each then
  reverted:
  1. drop `-p` from the detail `show`, so the fake echoes `Environment=`
     with the canary;
  2. return `json.dumps(server)` in place of the name;
  3. read the whole unit file in place of the `Description=` line;
  4. use the full project path in place of the basename;
  5. remove `find_service` from `READ_ONLY_TOOLS`;
  6. drop the `--` before the unit.
- `nix develop -c pytest tests -q` and
  `nix develop -c python3 -m unittest discover -s tests` both pass.
- `nix flake check --no-write-lock-file` passes.
- **By hand on p620, in dry run:** `omarchy-voice -n say --no-confirm "is
  voxtype running"` answers from a `find_service` call.
  `omarchy-voice -n say --no-confirm "do I have a GitHub MCP"` answers from
  `system_query mcp` and says the assistant is not connected to it.
  `python3 tools/verify_find.py` shows each service query under 50 ms.
