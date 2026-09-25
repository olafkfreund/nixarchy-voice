---
status: draft
issue: 144
spec: spec/2026-09-25-144-claude-json-deny.md
---

# Plan: ~/.claude.json and the other agents' credential files are secret paths

Closes #144. Branch `fix/144-claude-json-deny`, based on main `806804a`.
Main has 1170 tests passing. The branch differs from `806804a` only in
`intent/` and `spec/` (`git diff --stat 806804a HEAD`), so every `file:line`
below was checked in this tree and holds on `806804a`.

This file is enough on its own to implement the change. You do not need to
open the intent or the spec.

## Approved decisions, carried over from the spec

1. **Six new default deny rules, and nothing else in the source changes.**
   `Policy.check`, `Executor.describe`, `describe_tool`, `LIST_UNION_KEYS`,
   `_DEFAULT_DENY_NAMES` and the `*_remove` machinery are untouched. The
   rules reach every engine and route (`run_shell`, `run_in_terminal`,
   Claude Code `Bash`/`Read`/`Edit`/`Grep`, the Executor paths) through the
   one deny list, as #100's did.
2. **The six rules, appended to `DEFAULT_DENY_RULES` after `secret-keyrings`
   (`config.py:177`), before the closing `}` (`:178`), in this order, under
   the comment `# Agent credential and MCP config files (#144)`, with a
   trailing comment on each row:**

   ```python
   # Agent credential and MCP config files (#144). Login tokens, and MCP server
   # tables whose env / headers can hold tokens. Paths, not words.
   "secret-claude-config": r"""(^|[\s/"'=])\.claude\.json\b""",  # ~/.claude.json and every backup
   "secret-codex": r"/\.codex/(auth\.json|config\.toml)\b",      # not AGENTS.md, skills/
   "secret-gemini": r"/\.gemini/(oauth_creds|gemini-credentials|settings)\.json\b",  # not GEMINI.md
   "secret-copilot": r"/\.config/github-copilot(/|\b)",          # apps.json token, auth.db
   "secret-claude-desktop": r"/\.config/claude(/|\b)",           # Electron profile: cookies, tokens
   "secret-opencode": r"/(\.local/share/opencode/(mcp-)?auth|\.config/opencode/opencode)\.jsonc?\b",
   ```

   `DEFAULT_DENY` (`:179`) stays derived from the dict and grows from 29 to
   35. Appending keeps every existing name and position.
3. **`secret-claude-config` is one rule for the file and its backups**
   (`.backup`, `.bak`, `.bak-<N>`, `.bak2-<N>`, `.bak-<N>-…-ollama`,
   `~/.claude/backups/.claude.json.backup.<N>`): `\b` after `json` covers
   every `.`/`-` suffix. It uses `secret-dotenv`'s leading boundary class
   (start, whitespace, `/`, quote, `=`), not the issue's slash-only
   `/\.claude\.json\b`, so `cd ~ && cat .claude.json` is refused too.
   `$CLAUDE_CONFIG_DIR/.claude.json` (`capabilities.py:956`) is matched
   because a `/` precedes the name.
4. **`~/.claude/settings.json` and `settings.local.json` stay out.** Their
   `env` has 0 keys here, and a rule would also refuse every project's
   `.claude/settings.json`. The README gives the one-line `deny_patterns`
   addition (`/\.claude/settings`) for a user who keeps a token there.
5. **The other agents are in scope, one rule per agent** (#109 made names
   the unit of removal): Codex, Gemini, Copilot, Claude Desktop, opencode.
   Codex and Gemini are named files, since their directories hold
   `AGENTS.md`, `GEMINI.md`, skills and commands. Copilot and Claude Desktop
   are whole directories, since they hold tokens in several files and
   nothing worth a voice read. `secret-claude-desktop` also covers the
   lowercase `~/.config/claude/` on purpose (matching is `re.IGNORECASE`).
   Left out: `google_accounts.json`, `~/.gemini/settings.nix`, Codex
   `sessions/`/`history.jsonl`, Gemini `history/`, `antigravity*`, and the
   non-existent `~/.cursor/mcp.json` and `~/.mcp.json`.
6. **False positives are accepted.** `ls`/`stat` of a named path, a Claude
   Code `Edit` of `~/.claude.json`, and `Grep` over
   `~/.config/github-copilot` are refused, as every #100 rule does. The
   refusal names the rule, which is removable by name.
7. **The relative-path gap is accepted for the five other agents**
   (`cd ~/.codex && cat auth.json` passes). A word rule would refuse every
   project's `auth.json`. `secret-claude-login`'s own relative gap is not
   changed here.
8. **#83 is unaffected.** `mcp_servers()` (`capabilities.py:949`) is a
   Python read reached through `system_query` topic `mcp`, which the gate
   sees as `look up system mcp` (`tools.py:2291`). No new rule matches it.
9. **Tests are on fakes only**, in the existing files: a new class
   `AgentSecretPaths` in `tests/test_policy.py`, and count/name updates plus
   a remove-by-name loop in `tests/test_config.py`. `test_find_services.py`
   and `tools/verify_gate.py` do not change.

## Steps

0. **Baseline**: `git log -1 --format=%h main` and
   `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent nix develop -c pytest tests -q`
   → verify by: `806804a`, **1170 passed**, `git status` clean apart from
   `.claude/worktrees/`. Record any failure; later steps compare against it.

1. `tests/test_policy.py`: after `SecretPaths` (`:789-851`), before
   `TUI_WINDOWS` (`:853`), add a module table and a class. Fake paths only:

   ```python
   AGENT_SECRET_PATHS = (  # (rule, fake path) -- #144
       ("secret-claude-config", "/home/u/.claude.json"),
       ("secret-claude-config", "/home/u/.claude.json.backup"),
       ("secret-claude-config", "/home/u/.claude.json.bak"),
       ("secret-claude-config", "/home/u/.claude.json.bak2-1"),
       ("secret-claude-config", "/home/u/.claude.json.bak-1-07-22-ollama"),
       ("secret-claude-config", "/home/u/.claude/backups/.claude.json.backup.1"),
       ("secret-codex", "/home/u/.codex/auth.json"),
       ("secret-codex", "/home/u/.codex/auth.json.chatgpt-bak"),
       ("secret-codex", "/home/u/.codex/config.toml"),
       ("secret-codex", "/home/u/.codex/config.toml.bak"),
       ("secret-gemini", "/home/u/.gemini/oauth_creds.json"),
       ("secret-gemini", "/home/u/.gemini/gemini-credentials.json"),
       ("secret-gemini", "/home/u/.gemini/settings.json"),
       ("secret-gemini", "/home/u/.gemini/settings.json.orig"),
       ("secret-copilot", "/home/u/.config/github-copilot/apps.json"),
       ("secret-copilot", "/home/u/.config/github-copilot/auth.db"),
       ("secret-claude-desktop", "/home/u/.config/Claude/claude_desktop_config.json"),
       ("secret-claude-desktop", "/home/u/.config/Claude/Cookies"),
       ("secret-claude-desktop", "/home/u/.config/Claude/buddy-tokens.json"),
       ("secret-claude-desktop", "/home/u/.config/claude/config.json"),
       ("secret-opencode", "/home/u/.local/share/opencode/auth.json"),
       ("secret-opencode", "/home/u/.local/share/opencode/mcp-auth.json"),
       ("secret-opencode", "/home/u/.config/opencode/opencode.json"),
   )
   ```

   Class `AgentSecretPaths(unittest.TestCase)`, "Default deny rules for
   agent credential and MCP config files (#144)", with tests:
   - `test_every_route_refuses_each_path`: for each `(rule, path)`, one
     subTest per route, each asserting that the rule's name appears as
     `` `rule` `` in the refusal:
     - `run_shell`: `Executor(Config(dry_run=True)).call("run_shell",
       {"command": f"cat {path}"})` is not `ok`, its output contains
       `refused` and the name, and `executor.pending is None`;
     - `run_in_terminal`: `Policy(Config()).check(Executor.describe(
       "run_in_terminal", {"command": f"cat {path}"}))` raises `Denied`;
     - Claude Code `Bash`: `check(describe_tool("Bash", {"command":
       f"cat {path}"}))` raises `Denied`;
     - Claude Code `Read`: `check(describe_tool("Read", {"file_path":
       path}), read=True)` raises `Denied`.

     Import `describe_tool` from `omarchy_voice.claude_backend`.
   - `test_relative_claude_json_is_refused`: `run_shell`
     `cd ~ && cat .claude.json` and `run_in_terminal`
     `jq .mcpServers ~/.claude.json` are refused by `secret-claude-config`.
   - `test_mentions_still_run`: `Policy(Config()).check(d, read=True)` does
     not raise for each of: `look up system mcp`,
     `look up omarchy command 'claude'`, `claude mcp list`,
     `codex --version`, `gemini`, `opencode run hi`,
     `gh copilot suggest ls`, `launch Claude Desktop`,
     `grep claude.json README.md`, `read /home/u/notes/claude.json-notes.md`,
     `read /tmp/x.claude.json`, `read /home/u/.claude/settings.json`,
     `read /home/u/proj/.claude/settings.json`,
     `read /home/u/.codex/AGENTS.md`, `read /home/u/.codex/skills/x/SKILL.md`,
     `read /home/u/.gemini/GEMINI.md`,
     `read /home/u/.gemini/google_accounts.json`,
     `read /home/u/proj/opencode.json`,
     `read /home/u/.config/opencode/agents/x.md`,
     `read /home/u/src/codex/config.toml`, `read /home/u/.config/claudette/x`.
     The same with `read=False` for the non-`read` descriptions, so the
     shell routes are covered too.
   - `test_known_false_positives_are_refused` (pins decision 6 as known):
     `ls -l /home/u/.claude.json`, `stat /home/u/.claude.json`,
     `describe_tool("Edit", {"file_path": "/home/u/.claude.json"})` and
     `describe_tool("Grep", {"path": "/home/u/.config/github-copilot"})`
     raise `Denied`.
   - `test_known_relative_gap_is_open` (pins decision 7 as known):
     `cd /home/u/.codex && cat auth.json` does not raise.
   - `test_each_agent_rule_stands_alone`: for each of the six names,
     `Policy(Config(deny_patterns=[config_mod.DEFAULT_DENY_RULES[name]]))`
     refuses `read <path>` with `read=True` for every row of that name.
     Same idea as `test_the_ssh_path_rule_stands_alone` (`:808`).

   → verify by: `nix develop -c pytest tests/test_policy.py -q -k AgentSecretPaths`
   on the tree without step 3: `test_every_route_refuses_each_path`,
   `test_relative_claude_json_is_refused`,
   `test_known_false_positives_are_refused` and
   `test_each_agent_rule_stands_alone` (a `KeyError`) **fail on main**;
   `test_mentions_still_run` and `test_known_relative_gap_is_open` pass
   (guards).

2. `tests/test_config.py`, `RemoveDefaultRuleTests` (`:220`):
   - `:225` rename to `test_ssh_allowed_and_the_other_34_still_apply`, and
     `:229` `28` → `34`. The `secret-` loop (`:231-233`) picks up the new
     names on its own.
   - `:278` `14` → `20` (the replaced-list missing set is computed from
     `DEFAULT_DENY_RULES`, so only the count moves).
   - `:316` `29` → `35`.
   - `test_rule_names_are_stable` (`:331-341`): append
     `"secret-claude-config", "secret-codex", "secret-gemini",
     "secret-copilot", "secret-claude-desktop", "secret-opencode"` after
     `"secret-keyrings"`.
   - New `test_each_agent_rule_is_removable_by_name`: for each of the six
     names, one subTest. `cfg.load(self.write(f'[hands]\ndeny_patterns_remove = ["{name}"]\n'))`
     gives 34 patterns, lacks that rule's pattern, keeps the other five and
     `secret-claude-login`, and has `policy_notes ==
     [(True, f"deny rules removed: {name}")]`. Then `Policy(loaded)` lets
     `read <that rule's first path>` pass with `read=True`, and still
     refuses a path of a different agent. Paths are the same fakes as step 1
     (a small local table, or import `AGENT_SECRET_PATHS` from
     `test_policy`; `test_policy` already imports from `test_shell_off`, so
     cross-test imports are the local style).

   → verify by: `nix develop -c pytest tests/test_config.py -q` on the tree
   without step 3: the three counts, `test_rule_names_are_stable` and
   the new test **fail on main**.

3. `src/omarchy_voice/config.py:177`: after `"secret-keyrings": …`, append
   the six rows and comment from decision 2 exactly as written there. →
   verify by: steps 1 and 2 go green; `python -c "from omarchy_voice.config
   import DEFAULT_DENY_RULES as R, DEFAULT_DENY as D; print(len(R), len(D),
   list(R)[-6:])"` prints `35 35` and the six names in order.

4. `README.md`:
   - Safety, "Denied outright" (`:766-769`): extend the list of secret files
     with "agent logins and MCP configs (`~/.claude.json`, Codex, Gemini,
     Copilot, Claude Desktop, opencode)".
   - Rule names (`:884`): `Deny (29):` → `Deny (35):`, and append six rows
     after `secret-keyrings` (`:916`), each pattern with `|` escaped as
     `\|` like the existing rows.
   - One paragraph after the Deny table, before `Confirm (17):` (`:918`):
     these rules cover login tokens and MCP server tables whose `env` /
     `headers` can hold tokens; `system_query mcp` still answers with server
     names, scope and transport; `ls`, `stat` or an edit that names one of
     these paths is refused too, and the refusal names the rule;
     `~/.claude/settings.json` is not covered, with the one-line
     `deny_patterns = ['/\.claude/settings']` for a user who keeps a token in
     its `env`; the rules do not catch a symlink to one of the files, or a
     relative path to the generic names (`cd ~/.codex && cat auth.json`).

   → verify by: reading the diff; `grep -c '^| `secret-' README.md` prints
   18.

5. **Full suite, both runners, flake check**:
   `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent nix develop -c pytest tests -q`,
   then `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent nix develop -c python3 -m unittest discover -s tests`,
   then `nix flake check --no-write-lock-file` → verify by: 1170 plus the new
   tests, no failure beyond step 0's, the same count under unittest,
   `test_find_services.py` (#83) green unchanged, flake check exit 0.

6. **Mutation checks**, one per rule, then the order/name checks. Edit
   `config.py` by hand, run `pytest tests/test_policy.py tests/test_config.py -q`,
   confirm at least one test goes red, `git checkout -- src/omarchy_voice/config.py`.
   Record each red test's name in the PR.
   - a. `secret-claude-config` → `r"/\.claude\.json\b"` (slash only).
     Expect red: `test_relative_claude_json_is_refused`.
   - b. `secret-codex` → `r"/\.codex(/|\b)"` (whole directory).
     Expect red: `test_mentions_still_run` (`AGENTS.md`).
   - c. `secret-gemini`: drop `|settings`. Expect red: the `settings.json`
     rows of `test_every_route_refuses_each_path`.
   - d. `secret-copilot` → `r"/\.config/github-copilot/apps\.json\b"`.
     Expect red: the `auth.db` rows.
   - e. `secret-claude-desktop` → `r"/\.config/claude/claude_desktop_config\.json\b"`.
     Expect red: the `Cookies` and `buddy-tokens.json` rows.
   - f. `secret-opencode`: drop `(mcp-)?`. Expect red: the `mcp-auth.json`
     rows.
   - g. Delete each of the six rows in turn (six runs). Expect red:
     `test_rule_names_are_stable`, `test_each_agent_rule_stands_alone` and
     that rule's route rows.
   - h. Rename one of the six. Expect red: `test_rule_names_are_stable` and
     `test_each_agent_rule_is_removable_by_name`.
   - i. Swap two of the six. Expect red: `test_rule_names_are_stable`.

7. **Commit and PR**: one `fix(policy): …` commit (#144) with steps 1-4.
   The PR links `intent/`, `spec/` and `plan/`, lists the red test for each
   mutation, and says `verify-gate` needs no change (its temp-dir paths match
   no new rule).

## Tests

| Command | Expected |
|---------|----------|
| `nix develop -c pytest tests -q` (step 0) | 1170 passed |
| `git stash push -m plan144-main -- src/` (unique-tag procedure), `nix develop -c pytest tests/test_policy.py tests/test_config.py -q`, `git stash apply <sha>`, drop | tests marked "fail on main" in steps 1-2 fail; the guards pass |
| `nix develop -c pytest tests/test_policy.py tests/test_config.py tests/test_find_services.py -q` (after) | all pass |
| `nix develop -c pytest tests -q` (after) | 1170 plus the new tests, no new failures |
| `nix develop -c python3 -m unittest discover -s tests` (after) | same count, OK |
| mutations a-i (step 6) | each turns at least one named test red |
| `nix flake check --no-write-lock-file` | exit 0 |

Every run sets `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`. Test
modules import `_isolated` first, as the existing ones do. All paths are
under the fake `/home/u`: no real credential file is opened, and nothing
touches the live desktop.

## Risks

- **Harmless commands naming the paths are refused** (decision 6): pinned by
  `test_known_false_positives_are_refused`, documented in step 4.
- **`secret-claude-desktop` also matches `~/.config/claude-<anything>`.**
  None exists here; `/.config/claudette/` is not matched (tested). Removable
  by name.
- **Heuristic gaps**: a symlink, a relative path to a generic name (pinned
  as known), an indirect spelling in `Bash`, a non-default location
  (`$CODEX_HOME`, `$XDG_CONFIG_HOME`). #94 leaves `Read` with a literal
  path as the realistic route, and it is covered.
- **`deny_patterns_replace` users** opt out of these as of every default;
  #109's note now names 20 missing defaults (step 2).
- **Rule names are an interface from now on**, pinned by
  `test_rule_names_are_stable`.
- **Hosts**: every host that runs omarchy-voice. The files exist on p620,
  which runs the claude-code backend. Elsewhere the rules match nothing.

## Landing order and overlap

**#144 lands first** of this batch (#144, #145, #143).

- **#145** (`fix/145-tui-typing-hold`): touches `tools.py` and
  `tests/test_shell_off.py`, which #144 does not. Both edit README's Safety
  section: #144 the "Denied outright" bullet (`:766-769`), #145 the "Held
  when the shell is off" bullet (`:781-`). The hunks are about a dozen lines
  apart; #145 rebases on #144 and resolves by hand if git joins them.
- **#143** (`fix/143-stale-wtype-check`): touches `capabilities.py`,
  `cli.py`, `tests/test_doctor_hands.py` and README `:61`, `:219`, `:426`.
  No overlap with #144's files or README lines.
- Test counts: #144 adds tests, so later branches re-take their baseline
  after rebasing.

## Note for the owner

p620's config now uses `deny_patterns_remove = ["ssh"]`, not
`deny_patterns_replace`, so the six rules reach it through the union with
no config change. `omarchy-voice doctor` there will still report only
`deny rules removed: ssh`.

## Rollback

Per rule, without a release: add its name to `deny_patterns_remove` in
`[hands]` (for example `deny_patterns_remove = ["ssh", "secret-codex"]`).
Whole change: `git revert <fix commit>`. No data or config migration: the
rules live in code and are unioned at load time, so a revert removes them
everywhere, and a `deny_patterns_remove` naming a removed rule is then
reported as unknown and removes nothing.
