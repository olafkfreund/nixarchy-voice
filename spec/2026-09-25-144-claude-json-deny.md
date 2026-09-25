---
status: draft
issue: 144
intent: intent/2026-09-25-144-claude-json-deny.md
---

# Spec: ~/.claude.json and the other agents' credential files are secret paths

Closes #144. Branch `fix/144-claude-json-deny`, based on main 806804a.
Every `file:line` below was checked against that commit.

## Decisions on the intent's open questions

The intent was approved without answers to its four questions. Each one is
decided here and shown working on fakes: `Policy.check` with default `Config()`
plus the candidate rules, and descriptions built by the real
`Executor.describe` and `claude_backend.describe_tool`. Nothing ran, and no
real credential file was opened. The only look at the machine was existence
checks, file-name shapes (digits folded to `<N>`), and a count of `env` keys.
**You can reject any of these decisions at this gate.** Each is separate, so
rejecting one does not reopen the others.

| # | Question | Decision |
|---|----------|----------|
| 1a | Name and pattern for `~/.claude.json` | **`secret-claude-config`**, `(^\|[\s/"'=])\.claude\.json\b`. One rule covers the file and every backup name found here. |
| 1b | `~/.claude/settings.json`, `settings.local.json` | **Stay out.** Both have 0 `env` keys here, and a path rule would also refuse every project's `.claude/settings.json`. |
| 2 | The other agents' files | **In scope, one named rule per agent:** `secret-codex`, `secret-gemini`, `secret-copilot`, `secret-claude-desktop`, `secret-opencode`. |
| 3 | False positives and the relative-path gap | **Accept the false positives** (`ls`, `stat`, `edit` of a named path are refused), as every #100 rule does. **Close the relative gap for `.claude.json` only.** Accept it for the other agents. |
| 4 | #83 interaction | **Confirmed unaffected.** `mcp_servers()` is a Python read behind `look up system mcp`, which no new rule matches. Nothing else in #83 reads the file through a gated route. |

### Why each decision

**Q1a: one rule, `secret-claude-config`, with a leading boundary instead of a
slash.** The backups found here are:

- `~/.claude.json.backup` and `~/.claude.json.bak`;
- `~/.claude.json.bak-<N>`, `~/.claude.json.bak2-<N>` and
  `~/.claude.json.bak-<N>-07-22-ollama` (made by hand or by other tools);
- five `~/.claude/backups/.claude.json.backup.<N>` (Claude Code's own).

They all start `.claude.json` followed by a `.` or `-`, so `\b` after `json`
covers every one, and any future suffix of the same shape. They hold the
same data as the file, so they are the same secret. Two separate rules would
let a user remove the file's protection and keep the backups' protection,
which is not a meaningful choice. The name follows `secret-claude-login`: the
login is `.credentials.json`, the config is `.claude.json`.

The issue's `/\.claude\.json\b` needs a `/` before the name. This spec
widens that to the boundary class `secret-dotenv` already uses: start of
text, whitespace, `/`, a quote or `=`. `.claude.json` is a distinctive
name. Unlike `auth.json` it is not a word that shows up in ordinary text, so
the wider class costs nothing that could be found. It also closes
`cd ~ && cat .claude.json` (Q3). `$CLAUDE_CONFIG_DIR/.claude.json`, which
`mcp_servers()` also honours (`capabilities.py:956-957`), is matched because a
`/` precedes the name.

**Q1b: `~/.claude/settings.json` and `settings.local.json` stay out, on
evidence.** Their `env` block is where a token could live. It has 0 keys in
both files here (top-level keys counted, no values read). The files do hold
what a user edits by hand: hooks, plugins, the status line and permissions.
Refusing a read of them has a cost that is paid every time and protects
nothing on this machine. The only precise pattern is
`/\.claude/settings(\.local)?\.json`, and it also matches
`<any repo>/.claude/settings.json`, the project settings that every repo with
Claude Code has. Excluding the project files needs the home directory in the
pattern, and the defaults are written without one. A user who puts a token
in `env` can add `/\.claude/settings` to `deny_patterns`. The README says so.
Adding it later is a one-line change.

**Q2: the other agents, one rule each.** Each file below holds a login token,
or an MCP server table whose entries can carry `env` / `headers`. That is
the same secret as #144's, so leaving them for later would leave the same
hole open under other names, with no tracker behind it. One rule per agent,
not one rule for all, because #109 made names the unit of removal. A user
who runs Codex through voice on purpose removes `secret-codex` and keeps the
rest. Grouping follows the agent, not the file. A user deciding whether to
trust voice with Codex decides for its login and its config together.

| Name | Pattern | Covers (found here) | Why this shape |
|------|---------|---------------------|----------------|
| `secret-claude-config` | `(^\|[\s/"'=])\.claude\.json\b` | `~/.claude.json`, all backups above | Q1a. |
| `secret-codex` | `/\.codex/(auth\.json\|config\.toml)\b` | `auth.json`, `auth.json.chatgpt-bak`, `config.toml`, `config.toml.bak`, `config.toml.before-claude-mcp-<N>-<N>` | Two files, not the directory. `~/.codex` also holds `AGENTS.md` and `skills/`, which are worth reading. |
| `secret-gemini` | `/\.gemini/(oauth_creds\|gemini-credentials\|settings)\.json\b` | `oauth_creds.json`, `gemini-credentials.json`, `settings.json`, `settings.json.orig` | Named files, not the directory, for the same reason (`GEMINI.md`, `commands/`). `google_accounts.json` is account identity, not a token, like `oauthAccount`, so it stays out. |
| `secret-copilot` | `/\.config/github-copilot(/\|\b)` | `apps.json` (token), `auth.db` and its `-wal`/`-shm` | The whole directory: nothing else in it (`versions.json`) is worth a voice read. Like `secret-gnupg`. |
| `secret-claude-desktop` | `/\.config/claude(/\|\b)` | `~/.config/Claude/`: `claude_desktop_config.json` and its `.backup`/`.hm-backup`, `config.json`, `buddy-tokens.json`, `Cookies`, `Local Storage`. Also `~/.config/claude/config.json` | The whole directory. It is an Electron profile, and its cookies are a claude.ai session. Matching is `re.IGNORECASE`, so the lowercase `~/.config/claude/` found here is covered too, deliberately: it is another Claude config file. |
| `secret-opencode` | `/(\.local/share/opencode/(mcp-)?auth\|\.config/opencode/opencode)\.jsonc?\b` | `~/.local/share/opencode/auth.json`, `mcp-auth.json`, `~/.config/opencode/opencode.json` | Named files. `opencode.jsonc` is the other spelling opencode accepts. A project's own `opencode.json` is not matched. |

Left out, on evidence or on purpose:

- `~/.cursor/mcp.json` and `~/.mcp.json` do not exist here. A rule for a file
  nobody has is speculative.
- Codex `sessions/` and `history.jsonl`, and Gemini `history/`, hold
  conversation history. That is private but not a credential, and a
  different question from #144's. Not in scope.
- `~/.gemini/settings.nix` is a source file for the settings.
- The `antigravity*` directories under `~/.gemini` were not examined. No
  file in them was opened, and a rule for contents nobody has seen would be a
  guess.

**Q3: the false positives are accepted, and the relative gap is closed for
one rule.** Every #100 rule refuses any description that names the path,
whatever the verb. The new rules do the same: `ls -l ~/.claude.json`,
`stat`, a Claude Code `Edit` of the file, and `Grep` over
`~/.config/github-copilot` are refused (shown below). Telling a harmless verb
from a reading one is not possible on a description: `ls` and `cat` differ
by one word, and `cat` spelled another way is still `cat`. The refusal names
the rule, so the user sees why and can remove it by name. Editing Claude
Code's MCP table by voice is also a write into a credential store. Refusing
that is fail-safe, not a false positive worth engineering around.

The relative gap: `cd ~ && cat .claude.json` is closed by Q1a's boundary
class. For the other agents the file names are generic (`auth.json`,
`config.toml`, `settings.json`), so catching `cd ~/.codex && cat auth.json`
would take a word rule. That rule would refuse every project's `auth.json`
or `settings.json`, which is the thing #100's "paths, not words" forbids. It
is accepted as a gap, in the same place as the symlink gap. The existing
`secret-claude-login` has the same gap (`cat .claude/.credentials.json`
from `~`). Changing an existing rule's pattern is outside this issue. It is
noted as a possible follow-up, not filed.

**Q4: #83 is unaffected, by design.** `mcp_servers()`
(`capabilities.py:949`) is `Path.read_text()` inside this process, reached
through `system_query` topic `mcp`. The gate sees only
`look up system mcp` (`tools.py:2291`), which none of the six patterns
matches (PASS before and after, below). It keeps only name, scope and
transport. That is intended: the gate stops a *model* from putting a secret
into its context. Our own code reading a known file for known fields is not a
tool call, and it hands nothing secret to the model. #83's `find_services`
(`capabilities.py:900`) reads systemd. No other #83 code touches the file.
If a model asks `run_shell` or `Read` for `~/.claude.json` instead of using
`system_query mcp`, that is exactly what the new rule refuses, and the
refusal leaves `system_query mcp` as the route that works.

### Demonstration (fakes)

`Policy.check` on default rules (**before**) and default rules plus the six
(**after**). Paths are under `/home/u`. For `read <path>`, `read=True` as the
engines pass it.

| Description | Before | After |
|-------------|--------|-------|
| `read` `~/.claude.json`, `.claude.json.backup`, `.bak`, `.bak2-<N>`, `.bak-<N>-ollama`, `~/.claude/backups/.claude.json.backup.<N>` | PASS (6) | DENY `secret-claude-config` (6) |
| `read` `~/.codex/auth.json`, `auth.json.chatgpt-bak`, `config.toml`, `config.toml.bak` | PASS (4) | DENY `secret-codex` (4) |
| `read` `~/.gemini/oauth_creds.json`, `gemini-credentials.json`, `settings.json`, `settings.json.orig` | PASS (4) | DENY `secret-gemini` (4) |
| `read` `~/.config/github-copilot/apps.json`, `auth.db` | PASS (2) | DENY `secret-copilot` (2) |
| `read` `~/.config/Claude/claude_desktop_config.json`, `Cookies`, `buddy-tokens.json`, `~/.config/claude/config.json` | PASS (4) | DENY `secret-claude-desktop` (4) |
| `read` `~/.local/share/opencode/auth.json`, `mcp-auth.json`, `~/.config/opencode/opencode.json` | PASS (3) | DENY `secret-opencode` (3) |
| `run_shell` `cat ~/.claude.json` | PASS | DENY `secret-claude-config` |
| `run_in_terminal` `jq .mcpServers ~/.claude.json` (described as `run in terminal: …`) | PASS | DENY `secret-claude-config` |
| `run_shell` `cd ~ && cat .claude.json` | PASS | DENY `secret-claude-config` |
| `Bash` `cat /home/u/.codex/auth.json`; `Bash` `cat ~/.gemini/oauth_creds.json` | PASS | DENY `secret-codex`; `secret-gemini` |
| `Grep` path `~/.config/github-copilot` (`search …`) | PASS | DENY `secret-copilot` |
| `Edit` `~/.claude.json`; `ls -l ~/.claude.json` | PASS | DENY `secret-claude-config` (accepted, Q3) |
| `cd ~/.codex && cat auth.json` | PASS | PASS (accepted gap, Q3) |
| `look up system mcp` (#83) | PASS | PASS |
| `look up omarchy command 'claude'`, `claude mcp list`, `codex --version`, `gemini`, `opencode run hi`, `gh copilot suggest ls`, `find_app claude`, `launch Claude Desktop` | PASS | PASS |
| `grep claude.json README.md`, `read ~/notes/claude.json-notes.md`, `read /tmp/x.claude.json` | PASS | PASS |
| `read` `~/.claude/settings.json`, `~/proj/.claude/settings.json` (Q1b) | PASS | PASS |
| `read` `~/.codex/AGENTS.md`, `~/.codex/skills/x/SKILL.md`, `~/.gemini/GEMINI.md`, `~/.gemini/google_accounts.json`, `~/proj/opencode.json`, `~/.config/opencode/agents/x.md`, `~/src/codex/config.toml`, `~/.config/claudette/x` | PASS | PASS |

## Design

Two source files and the README. No logic changes: `Policy.check`,
`describe`, `describe_tool`, `LIST_UNION_KEYS` and the `_remove` machinery are
untouched. The new rules reach every engine and every route the way #100's
did, through the one deny list.

1. **`src/omarchy_voice/config.py`**: six entries are appended to
   `DEFAULT_DENY_RULES` after `secret-keyrings` (`:177`), in this order:
   `secret-claude-config`, `secret-codex`, `secret-gemini`, `secret-copilot`,
   `secret-claude-desktop`, `secret-opencode`. They go under a comment,
   "Agent credential and MCP config files (#144)", with a trailing comment on
   each row as in #100. Appending keeps every existing name and position.
   `DEFAULT_DENY` grows from 29 to 35 and stays derived from the dict.
   Existing configs gain the rules through the union. `deny_patterns_remove`
   accepts each new name with no further change, because #109 builds the
   name map from `DEFAULT_DENY_RULES`.
2. **`README.md`**:
   - "Deny (29)" becomes "Deny (35)", with the six rows added to the table.
   - One paragraph says that these cover login tokens and MCP server tables
     whose `env` / `headers` can hold tokens, and that `system_query mcp`
     still answers with server names.
   - The paragraph also says that `~/.claude/settings.json` is not covered,
     and gives the one-line `deny_patterns` addition for a user who keeps a
     token in its `env`.
   - It names the relative-path gap next to the existing symlink note.
3. **Tests** (below). `verify_gate.py` needs no change: its cases use temp
   directories and user rules, and none of their paths match the new
   patterns.

## Alternatives rejected

- **The issue's `/\.claude\.json\b`, slash only.** It leaves
  `cat .claude.json` from `~` open for no gain: no ordinary text was found
  where a boundary-preceded `.claude.json` is not the file.
- **One `secret-agent-configs` rule for every agent.** It cannot be removed
  for one agent, which is the thing #109 exists for.
- **Separate rules for `~/.claude.json` and its backups.** The backups hold
  the same contents. Two names would let a user turn one off and not the
  other.
- **Whole directories for Codex and Gemini.** They hold `AGENTS.md`,
  `GEMINI.md`, skills and commands, which a user might ask to read.
- **Named files for Copilot and Claude Desktop.** Both directories hold
  tokens in several files (`auth.db`, `Cookies`, `buddy-tokens.json`).
  Listing them would miss the next one, and neither directory holds anything
  worth a voice read.
- **Cover `~/.claude/settings.json`.** Q1b: there is no token here, the rule
  would misfire on every project's settings, and a user can add it
  themselves.
- **Word rules for the relative gap (`\bauth\.json\b`).** They would refuse
  every project's `auth.json`, which breaks "paths, not words".
- **A reads-only rule list, or a change to `describe_tool`.** Rejected in
  #100 for the same reasons: `cat` through `Bash` leaks the same data, and
  one list covers every route.

## Risks

- **Harmless commands that name the paths are refused.** For example
  `ls`/`stat` of the file, a Claude Code `Edit` of `~/.claude.json`, or any
  command inside `~/.config/Claude/`. This is accepted (Q3). The refusal
  names the rule, and each rule is removable by name.
- **`secret-claude-desktop` also matches `~/.config/claude-<anything>`**,
  because `\b` holds before `-`. None exists here. `/.config/claudette/` is
  not matched (shown). A user with such a directory can remove the rule by
  name.
- **The rules are a heuristic.** They do not catch:
  - a symlink to one of the files;
  - a relative path to the generic names (`cd ~/.codex && cat auth.json`);
  - an indirect spelling in `Bash` (`cat ~/.co""dex/auth.json`);
  - an agent's credentials at a non-default location (`$CODEX_HOME`,
    `$XDG_CONFIG_HOME` pointed elsewhere).

  The last two are the same gaps #100 accepted. #94 removes `Bash` from the
  voice brain, so `Read` with a literal path is the realistic case, and it is
  covered.
- **A user with `deny_patterns_replace`** opts out of these as of every
  default. #109's note names the missing defaults, so the six new names show
  up in it. That is the intended, visible behaviour.
- **Rule names are an interface from now on.** They are pinned by the test
  below.
- **Hosts:** every host that runs omarchy-voice. The files exist on p620,
  where the claude-code backend runs. On a host without these agents, the
  rules match nothing.

## Verification

Unit tests on fakes only, in the existing files. No real credential file is
opened. No live desktop. Every run uses
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`.

- `tests/test_policy.py`, new class `AgentSecretPaths`, with one table of
  `(rule name, fake path)` covering every DENY row above:
  - **Every route.** For each path:
    - `Executor.call("run_shell", {"command": f"cat {path}"})` under
      `dry_run=True` is refused with `pending is None`;
    - the `run_in_terminal` description from `Executor.describe` raises
      `Denied`;
    - `describe_tool("Bash", …)` raises `Denied`;
    - `describe_tool("Read", …)` with `read=True` raises `Denied`.

    Each assertion checks that the error names the expected rule.
  - `cd ~ && cat .claude.json` is refused by `secret-claude-config`.
  - **Mentions still run.** Every PASS row above passes `Policy(Config())`,
    including `look up system mcp`, `claude mcp list`, both
    `.claude/settings.json` paths and the named neighbour files.
  - **Each rule stands alone.** For each new name, `Policy` built from that
    one pattern refuses its own paths. This is the
    `test_the_ssh_path_rule_stands_alone` pattern, so deleting a rule cannot
    hide behind another.
- `tests/test_config.py`:
  - `test_rule_names_are_stable` appends the six names, in order, to the
    pinned `DEFAULT_DENY_RULES` list.
  - The count assertions move with the list: 28 → 34 (`:229`), 29 → 35
    (`:316`), and the replaced-list missing set 14 → 20 (`:278`).
  - A new subTest loop: for each of the six names,
    `deny_patterns_remove = ["<name>"]` gives a list without that pattern,
    with the other five and `secret-claude-login` still present, and a note
    `deny rules removed: <name>`. A read of that rule's path then passes, and
    a read of another agent's path is still refused.
- `tests/test_find_services.py` (#83's `mcp_servers()` tests): unchanged
  and still green. They read a temp `.claude.json` through Python, not
  through the gate.
- **Mutation checks**, each of which must turn a test red:
  - delete any one of the six entries;
  - revert `secret-claude-config` to `/\.claude\.json\b` (the relative-path
    test fails);
  - widen `secret-codex` to `/\.codex(/|\b)` (the `AGENTS.md` pass fails);
  - drop `settings` from `secret-gemini`;
  - rename any one of the six;
  - reorder two of the six.
- `python3 -m unittest discover -s tests` in the dev shell has no new
  failures.
- `nix flake check --no-write-lock-file` passes.
