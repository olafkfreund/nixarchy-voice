---
status: draft
issue: 144
author: olafkfreund
---

# Intent: ~/.claude.json is a secret path, so the deny list must refuse a read of it

Closes #144.

## Problem

#100 gave the deny list a set of named secret-path rules (`config.py:162-177`,
the `secret-*` entries of `DEFAULT_DENY_RULES`). Claude Code's own login is one
of them: `secret-claude-login`, `/\.claude/\.credentials\.json\b`
(`config.py:175`). Claude Code's *configuration* file, `~/.claude.json`, is
not. It holds the MCP server table, user scope under `mcpServers` and local
scope under `projects.<path>.mcpServers`, and a server entry can carry tokens
in its `env` (stdio servers) and `headers` (http/sse servers, typically
`Authorization`). So a shell or terminal action that reads it passes the gate.

Found while re-verifying #83's spec. #83's own reader never touches those
fields (below); this is about every other route.

### What the file holds on this machine (key counts only, no values read out)

Counted with a scratch script that loads the JSON and prints only counts and
key names. Mode is `0600`.

| Scope | Servers | with non-empty `env` | with non-empty `headers` | with `url` |
| --- | --- | --- | --- | --- |
| user (`mcpServers`) | 19 | 4 | 4 | 7 |
| local (`projects.*.mcpServers`, 5 projects) | 11 | 4 | 0 | 2 |

So 12 server entries can carry a secret here. The top level also has an
`oauthAccount` object (account identity, not a token) and
`customApiKeyResponses`. Whether any given `env` value is a real token was not
checked, deliberately; the shape is enough.

### What the gate does today (origin/main 806804a)

`Policy.check` (`tools.py:209`) against default `Config()`, with the
descriptions built by the real `Executor.describe` and
`claude_backend.describe_tool`, `read=True` for the readers as the engines
pass it. Paths are fakes (`/home/u/...`); nothing ran.

| Route | Description the gate sees | Verdict |
| --- | --- | --- |
| `run_shell` | `cat ~/.claude.json` | PASS |
| `run_in_terminal` | `run in terminal: cat ~/.claude.json` | PASS |
| `run_in_terminal` | `run in terminal: jq .mcpServers /home/u/.claude.json` | PASS |
| Claude Code `Bash` | `cat /home/u/.claude.json` | PASS |
| Claude Code `Read` | `read /home/u/.claude.json` | PASS |
| Claude Code `Read` | `read /home/u/.claude.json.backup` | PASS |
| Claude Code `Read` | `read /home/u/.claude/backups/.claude.json.backup.1` | PASS |
| Claude Code `Read` | `read /home/u/.claude/.credentials.json` | DENY `secret-claude-login` |

Claude Code writes backups of the file (`~/.claude.json.backup` and
`~/.claude/backups/` both exist here), with the same contents, so they are
the same secret.

### Other agents' credential and config files on this machine

Existence only; no file was opened except for the count above and a count of
the `env` keys in `~/.claude/settings.json` (0 here, but that block is where
Claude Code takes environment variables, tokens included).

| Path | Exists | Covered today |
| --- | --- | --- |
| `~/.claude/.credentials.json` | yes | yes, `secret-claude-login` |
| `~/.claude.json`, `~/.claude.json.backup`, `~/.claude/backups/` | yes | **no** |
| `~/.claude/settings.json`, `settings.local.json` (`env` block) | yes | no |
| `~/.codex/auth.json` (Codex login) | yes | no |
| `~/.codex/config.toml` (MCP servers, `env`) | yes | no |
| `~/.gemini/oauth_creds.json` (Gemini login) | yes | no |
| `~/.gemini/settings.json` (MCP servers) | yes | no |
| `~/.config/github-copilot/apps.json` (Copilot token) | yes | no |
| `~/.config/Claude/claude_desktop_config.json` (Desktop MCP servers) | yes | no |
| `~/.config/opencode/opencode.json`, `~/.local/share/opencode/auth.json` | yes | no |
| `~/.cursor/mcp.json`, `~/.mcp.json` | no | no |

### #83's `mcp_servers()` is not affected

`capabilities.mcp_servers()` (`capabilities.py:949`) opens the file with
`Path.read_text()` in Python, inside the `system_query` topic `mcp`
(`_mcp_report`, `tools.py:1568`). The gate never sees the path: what it
checks for that call is `look up system mcp` (`Executor.describe`,
`tools.py:2291`), which the issue's pattern does not match (dry run above:
PASS both before and with the candidate). It keeps only server names, scope
and transport, never `env`, `headers`, `url`, `command` or `args` values.
#83's `find_services` (`capabilities.py:900`) reads systemd, not this file.
Claude Code itself reading its own config as the backend process is also not
a tool call and not gated.

### The issue's candidate pattern, `/\.claude\.json\b`

Dry run of `re.search` against the cases above plus a few mentions: it
matches `cat ~/.claude.json`, `jq ... /home/u/.claude.json`, both backup
paths, and `open ~/.claude.json in the editor`. It does not match
`look up system mcp`, `look up omarchy command 'claude'`,
`grep claude.json README.md`, `read /home/u/notes/claude.json-notes.md`,
`read /tmp/x.claude.json`, or any of the other agents' files. Like every
secret-path rule it is a heuristic: `cd ~ && cat .claude.json` (no slash
before the name) gets past it, as a symlink gets past the others.

## Proposed outcome

- A shell, terminal or Claude Code action whose description names
  `~/.claude.json` (or its backups) is refused by a named built-in deny rule,
  on every engine.
- The refusal names the rule, so `deny_patterns_remove` (#109) turns it off
  by that name, like every other default.
- "What MCP servers do I have" (#83) still answers with the names.
- A lookup or command that only mentions "claude" still runs.

## Affected users and systems

- `src/omarchy_voice/config.py`: `DEFAULT_DENY_RULES`, and the list of rule
  names wherever it is documented.
- `src/omarchy_voice/tools.py` `Policy` and `claude_backend.describe_tool`:
  unchanged, but every route through them is affected.
- Anyone with `allow_shell` on, or using the Claude Code backend's `Read` /
  `Bash`; anyone with a `deny_patterns_remove` list (names must stay stable).
- Tests: `tests/test_policy.py` and the gate cases in `verify_gate.py`.
- README's deny-rule list.

## Constraints

- Must not break #83's `mcp_servers()` or its `system_query mcp` answer.
- Must be one named entry (or a few), removable by name through
  `deny_patterns_remove`; existing rule names must not change.
- A path rule, not a word rule: "claude" or "mcp" on its own must still run
  (the #100 principle).
- Must not print or log any value from the real `~/.claude.json` in tests,
  evidence or error messages. Fakes only; the live desktop is read-only.
- Tests: a read of the path is refused on each route above, and a lookup that
  merely mentions "claude" still runs (the issue's stated test).

## Open questions

1. **Name and pattern.** `secret-claude-config` with `/\.claude\.json\b`, as
   the issue suggests? It already covers `.claude.json.backup` and
   `~/.claude/backups/.claude.json.backup.*`. Should `~/.claude/settings.json`
   and `settings.local.json` (their `env` block can hold tokens) go in the same
   rule, a separate one, or stay out (they are also where a user edits
   permissions, so refusing a read has a cost)?
2. **Other agents.** Cover the other agents' files found above (Codex
   `auth.json`/`config.toml`, Gemini `oauth_creds.json`/`settings.json`,
   Copilot `apps.json`, Claude Desktop config, opencode `auth.json`) in this
   issue, one rule per agent so each is removable, or leave them to a
   follow-up issue and keep #144 to `~/.claude.json`?
3. **False positives.** Accept that any command naming the path is refused,
   even a harmless one (`ls -l ~/.claude.json`, `stat`, opening it in an
   editor to fix a setting)? Every secret-path rule from #100 already behaves
   this way. And accept the false negative with no slash
   (`cat .claude.json` from `~`)?
4. **#83 interaction.** Confirm that `mcp_servers()` staying outside the gate
   (a Python read behind `look up system mcp`) is the intended design, and
   that the new rule is not meant to reach it. Also: #83's `find_services`
   is untouched; does anything else in #83's design read the file through a
   gated route?
