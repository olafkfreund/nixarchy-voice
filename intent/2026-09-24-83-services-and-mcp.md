---
status: approved
issue: 83
author: olafkfreund
---

# Intent: know which user services and MCP servers exist, without knowing becoming managing

Closes #83.

## Problem

The user's goal for this project: *"I want to be able to ask for something and
use the specific tools we have installed on the system to make that happen."*
#70 made desktop apps findable (`capabilities.app_index`/`find_apps`,
`capabilities.py:472`, `:556`; tool `find_app`, `tools.py:861`). #82 did the
same for commands on PATH (`path_commands`/`find_commands`,
`capabilities.py:766`, `:784`; tool `find_command`, `tools.py:878`). Both are
read-only (`READ_ONLY_TOOLS`, `tools.py:58-60`). #70 named two more things
the model cannot see: the user's **systemd user services** and the **MCP
servers configured for Claude Code**. #83 was split out to cover them.

Two kinds of question have no answer today:

- **"Is my X running?"** or "is voxtype up?" or "why is the stream deck dead?"
  The model does not know that `voxtype.service`, `streamdeck-ctl.service` or
  `gmessagesd.service` exist. It has no read-only way to ask systemd about
  them either.
- **"What can you reach?"** or "do I have a GitHub MCP?" The model does not
  know which MCP servers the user has configured for Claude Code, and it
  cannot tell those apart from the servers it can call itself.

### What the manifest and tools know today (origin/main `6a9a3f5`)

- **Services: nothing.** No tool lists, reads or manages a unit. The only
  `systemctl` strings in `src/` are advice printed to a human
  (`capabilities.py:1235`, `realtime.py:1655`). `system_query`
  (`tools.py:1334`, topics at `tools.py:1416-1441`) covers disk, memory,
  battery, network, bluetooth, audio, uptime, processes, temperature, time and
  os. It has no services topic.
- **Starting or stopping a unit** is only possible as a shell command. That
  means `run_shell` (refused unless `allow_shell`, `tools.py:3757-3759`) or
  `run_in_terminal` in a visible pane. With the shell off, a command the model
  chose waits for a yes (#112, `tools.py:1902-1905`, `_runs_command` at
  `tools.py:2005`). No confirm rule names `systemctl`
  (`DEFAULT_CONFIRM_RULES`, `config.py:112-131`), so with `allow_shell` on,
  `systemctl --user stop X` runs with no confirmation at all.
- **MCP: only the assistant's own servers.** The Claude Code brain gets
  `omarchy` (in-process), plus `ai-mirror` when `desktop_control` is on
  (`claude_backend.py:550-567`). `strict_mcp_config=True`
  (`claude_backend.py:584`) keeps the user's servers out on purpose. Nothing
  reads the user's MCP configuration, so "what can you reach" has only one
  honest answer today, "these two", and the model is never told even that
  much as a list.

### What exists on this machine

Measured on 2026-09-24, read-only, as the user who owns the daemon:

| Services | Count | Time |
|---|---|---|
| user unit files (`systemctl --user list-unit-files`) | 280 | **0.74 s** |
| of which `.service` | 152 (7 templates) | |
| service files by state | 87 linked-runtime, 23 enabled, 12 generated, 7 bad, 6 transient, 6 static, 5 linked, 2 alias, 2 enabled-runtime, 2 disabled | |
| loaded units (`list-units --all -o json`) | 413 (368 active, 45 inactive) | **0.009 s** |
| running services | 49 | 0.005 s |
| failed units | 0 | |
| services the user defined (`~/.config/systemd/user/*.service`, Home Manager) | **24** | |
| D-Bus `ListUnitFiles` (busctl) | same data | 0.58 s |

Most of the 152 are not things a person would name. They include
`app-*@autostart` instances, portal and D-Bus plumbing, and systemd's own
units. The 24 in `~/.config/systemd/user` are the user's own:
`omarchy-voice`, `voxtype`, `voxtype-model-loader`, `streamdeck-ctl`,
`streamdeck-ctl-deck`, `lan-mouse`, `gmessagesd`, `nixi`, `nixi-watch`,
`bt-agent`, `openclaw-gateway`, `gog-mail-watch`, `gog-dashboard`, and others.
A few more are packaged units a person does name, like `pipewire` or
`hypridle`. The 7 `bad` states are unit files systemd could not parse. They
are worth reporting, not hiding. **Listing unit files is 80 times slower
than listing loaded units**, which matters on a voice channel.

| MCP configuration | Servers | Notes |
|---|---|---|
| `~/.claude.json` `mcpServers` (user scope) | **19** | 12 stdio, 6 http, 1 sse. **4 carry `env`, 4 carry `headers`**, which is where tokens live. File is 412 KB. Parsed in 0.005 s. |
| `~/.claude.json` `projects.*.mcpServers` (local scope) | 11 across 5 of 173 projects | none for this repo |
| `~/.config/Claude/claude_desktop_config.json` | 6 | Claude Desktop, not Claude Code |
| plugin `.mcp.json` under `~/.claude/plugins` | 56 files | includes `.trash/` copies. Which are live depends on enabled plugins. |
| `~/.mcp.json`, repo `.mcp.json`, `/etc/claude-code/managed-mcp.json` | absent | |
| `~/.claude/settings*.json`, `/etc/claude-code/managed-settings.json` | 0 | |

Only names and counts were read. No command, URL, env value or header was
printed.

### What the log shows

`~/.local/state/omarchy-voice/session.log` has 3,046 lines. No heard or
typed request so far asked whether a service was running or what MCP servers
exist. The demand comes from the stated goal and from #70's inventory, not
from a failed request. The approver should weigh that.

## Proposed outcome

- Asked "is voxtype running?", the assistant answers from systemd: whether
  the unit exists, and whether it is active, failed or inactive. It does not
  guess, and it does not start anything to find out.
- Asked "what services do I have for X?", it finds the user's own services by
  name and by their `Description=`, the way `find_app` finds apps. A unit
  that does not exist is reported as not existing.
- Asked "what MCP servers do I have?" or "do I have one for GitHub?", it
  answers with configured server names and scope (user, project, plugin). It
  keeps two things apart: what the **user's Claude Code** has configured, and
  what **this assistant** can call right now.
- Finding never becomes managing. The lookup starts, stops, restarts, enables
  and connects to nothing. Anything that changes a unit's state goes through
  an existing gated path, and it is visible or confirmed.
- No secret leaves a config file through this lookup: no MCP `env`,
  `headers`, URL credentials or command arguments.
- A lookup takes one read-only round trip at voice speed (well under a
  second).

## Affected users and systems

- Every engine, since they share one `Executor`: voice (local and realtime),
  typed `say`, and the MCP server (`mcp_server.py:111` `build_server`).
- `capabilities.py`, next to `app_index`/`path_commands`; `tools.py`, for
  `READ_ONLY_TOOLS`, schemas and dispatch; `persona.py`, for steering.
- `claude_backend.py` only as the source of truth for "what this assistant
  can reach". `strict_mcp_config` is not up for change here.
- The user's systemd user manager (read via `systemctl --user` or D-Bus). The
  daemon already runs inside it as `omarchy-voice.service`.
- `~/.claude.json`, which holds secrets and, in `projects.*`, the history of
  173 working directories. Only the MCP server names in it are relevant.
- #82 (sibling; shares the finder shape), #100 (deny rules on reads; secret
  paths), #101 (sensitive content in what a tool returns), #112 (shell-off
  confirmation).

## Constraints

- **Read-only, like #70 and #82.** The tool takes a query and returns text.
  It executes no unit, no MCP server command and no `--help` probe, and it
  opens no network connection to an MCP URL to check it is alive.
- **Never print MCP `env`, `headers`, URLs with credentials, or command
  arguments.** Names, transport and scope at most. `~/.claude.json` must not
  reach the model whole, not even through the `Read` built-in
  (`claude_backend.py:118-124`). #100's deny paths cover
  `~/.claude/.credentials.json` (`config.py:176`) but not `~/.claude.json`.
- **Local only.** Nothing about installed units or configured servers leaves
  the machine except the answer the model is given.
- **The cached prompt prefix stays stable** (#69). 152 services or 19 server
  names do not go into the system prompt. Unit state changes by the second.
- **Stale is worse than slow** (#70). State ("is it running") is read live,
  never from a cache. The list of units may be cached only if a newly added
  unit is found by the next start.
- **#94, #100 and #112 hold.** No built-in `Bash` comes back, deny rules apply
  to reads, and a command the model chose still waits for a yes with the
  shell off.
- **Voice latency.** The 0.74 s `list-unit-files` is not paid per question if
  the 0.009 s `list-units` answers it.

## Open questions

1. **Which services are findable?** The options are all 152 service files,
   only the loaded 413 units, or the user's own 24
   (`~/.config/systemd/user`) plus loaded services with a `Description=`.
   Should system-level units (`systemctl` without `--user`, like `docker` or
   `sshd`) be in scope, or only user units as the issue says?
2. **Does "is X running" belong in this finder or in `system_query`?** A
   finder that also reports live state is two jobs. A `services` topic in
   `system_query` would be a fixed argv with no model-chosen arguments, but
   it cannot take a unit name without putting an argument slot back.
3. **Should managing a unit become a gated tool, or stay a shell command?**
   Today it is `run_shell`/`run_in_terminal` only. With `allow_shell` on,
   `systemctl --user stop` has no confirm rule. Is that a gap to close here, a
   separate issue, or intended?
4. **Which MCP sources count as "configured"?** `~/.claude.json` user and
   project scope are clear. Plugin servers (56 files, some in `.trash/`)
   depend on Claude Code's enabled-plugin state. Claude Desktop's 6 belong to
   a different client. Include, exclude, or label?
5. **Should the answer say that the user's MCP servers are not reachable by
   this assistant?** `strict_mcp_config` means "you have a GitHub MCP" and "I
   can use it" are different. Is saying so enough, or does the user expect
   #83 to lead to reaching them? If so, that is a separate decision and a
   separate issue.
6. **Is there enough demand?** The log has no request of either kind yet.
   Is the goal statement enough reason to build both halves now, or should
   services (more likely to be asked by voice) go first and MCP wait?
