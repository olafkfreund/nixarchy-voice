---
status: approved
issue: 100
author: olafkfreund
---

# Intent: A read-only lookup is not an action, so the confirm gate must not hold it

Closes #100.

## Problem

The policy gate judges a line of text, not an action. For our tools that
line is `Executor.describe(name, args)`, and for a read-only tool it is a
sentence around the model's search words: `look up omarchy command 'reboot'`,
`find apps: 'shutdown'`, `read screen (screen) looking for 'reboot'`. The
confirm patterns are searched anywhere in that sentence, so a *search for*
a word with a confirm pattern is held as if it were the thing itself.

Seen in #94's spec dry runs (run 7): asked to reboot, the model first called
`omarchy_help` with "reboot" to find the route, and that lookup was held. The
user is asked to confirm a search. Worse, the held lookup takes the only
confirmation slot, so the real reboot that comes next cannot be held at all.

### Where the check runs (origin/main 72f9ab6)

- Our tools: `Executor._call_locked` (`src/omarchy_voice/tools.py:1721`)
  calls `self.describe(name, args)` then `self.policy.check(description)` at
  `tools.py:1732-1734`, with no exception for any tool. `READ_ONLY_TOOLS`
  (`tools.py:57-58`) is only consulted *after* the gate, for dry-run
  (`tools.py:1752`, `:1779`). Every engine goes through here: `planner.py:199`,
  `realtime.py:1284`, `mcp_server.py:169` (the Claude Code path, since
  `claude_backend._decide` allows `mcp__omarchy__*` unchecked at
  `claude_backend.py:372` precisely so the Executor gates it once) and
  `local_engine.py:499`.
- `Policy.check` (`tools.py:198-205`): every `deny_patterns` regex, then every
  `confirm_patterns` regex, each `re.search(pattern, description,
  re.IGNORECASE)`. Unanchored: a match anywhere in the line counts.
- The patterns: `DEFAULT_CONFIRM` (`config.py:108-128`: shutdown, reboot,
  poweroff, suspend, hibernat, omarchy update/drive/pkg/install/refresh/
  reinstall, hl.dsp.exit, close-all, nixos-rebuild, home-manager switch,
  nixarchy-apply, nix flake update) and `DEFAULT_DENY` (`config.py:132-154`:
  rm -r/-f, mkfs, dd if=, shred/wipefs, > /dev/sd|nv, passwd, sudo, pkexec,
  cryptsetup, curl|sh, git push, ssh, and the five nix garbage-collection
  forms).
- Claude Code's own tools: the PreToolUse hook's `_decide`
  (`claude_backend.py:331`) builds `describe_tool` (`claude_backend.py:194`)
  and calls the same `Policy.check` (`claude_backend.py:382`). Its readers are
  `Read` (described `read <path>`, via `_PATH_TOOLS` at
  `claude_backend.py:72`) and `WebSearch` (described as its JSON input);
  `DRY_RUN_READS` (`claude_backend.py:101`) is, again, only a dry-run list.

### What gets held today

An `Executor` subclass whose read-only handlers are fakes returning a fixed
string, default `Config()`, each call on a fresh executor. Nothing touched the
desktop. RUN = reached the fake handler.

| Tool | Input | Description the gate sees | Verdict |
| --- | --- | --- | --- |
| omarchy_help | reboot | `look up omarchy command 'reboot'` | **HOLD** |
| omarchy_help | shutdown | `look up omarchy command 'shutdown'` | **HOLD** |
| omarchy_help | poweroff | `look up omarchy command 'poweroff'` | **HOLD** |
| omarchy_help | suspend | `look up omarchy command 'suspend'` | **HOLD** |
| omarchy_help | hibernate | `look up omarchy command 'hibernate'` | **HOLD** |
| omarchy_help | omarchy update | `look up omarchy command 'omarchy update'` | **HOLD** |
| omarchy_help | omarchy pkg install | `look up omarchy command 'omarchy pkg install'` | **HOLD** |
| omarchy_help | close all windows | `look up omarchy command 'close all windows'` | **HOLD** |
| omarchy_help | nixos-rebuild | `look up omarchy command 'nixos-rebuild'` | **HOLD** |
| omarchy_help | power off | `look up omarchy command 'power off'` | RUN (no `\bpoweroff\b` match with a space) |
| omarchy_help | lock screen | `look up omarchy command 'lock screen'` | RUN |
| omarchy_help | sudo / ssh / passwd | `look up omarchy command 'sudo'` ... | **DENY** |
| find_app | shutdown / reboot | `find apps: 'shutdown'` ... | **HOLD** |
| find_app | ssh | `find apps: 'ssh'` | **DENY** |
| find_app | passwords / firefox | `find apps: 'passwords'` ... | RUN |
| read_screen | query reboot | `read screen (screen) looking for 'reboot'` | **HOLD** |
| read_screen | query sudo | `read screen (screen) looking for 'sudo'` | **DENY** |
| read_terminal | target nixos-rebuild | `read terminal nixos-rebuild` | **HOLD** |
| read_terminal | target ssh | `read terminal ssh` | **DENY** |
| hypr_query | all 10 `QUERY_KINDS` | `query hyprctl <kind>` | RUN |
| system_query | all 12 topics | `look up system <topic>` | RUN |
| screenshot, list_terminals | — | fixed text | RUN |

`hypr_query` and `system_query` are safe today only because their argument is
a closed enum (`QUERY_KINDS`, `tools.py:33`; `SYSTEM_QUERIES`,
`tools.py:1310`) and no member contains a pattern word. The tools that take
free text (`omarchy_help`, `find_app`, `read_screen`'s query,
`read_terminal`'s target) are held or refused on the words.

The hold also blocks the action it was looking up. After
`omarchy_help("reboot")` is held, `omarchy_cli("system reboot")` on the same
executor returns `another action is already waiting for confirmation: look up
omarchy command 'reboot'` (`tools.py:1737-1744`). The user must confirm or
cancel the search before the reboot can even be asked for.

The Claude Code path has the same shape for its own readers, through
`describe_tool`:

| Tool | Description | Verdict |
| --- | --- | --- |
| Read | `read /home/u/notes/reboot-checklist.md` | **HOLD** |
| WebSearch | `WebSearch {"query": "how do I reboot hyprland"}` | **HOLD** |
| WebSearch | `WebSearch {"query": "sudo password prompt"}` | **DENY** |
| Read | `read /home/u/.ssh/id_ed25519` | **DENY**, but only because `\bssh\b` happens to match `.ssh` |
| Read | `read /home/u/.config/omarchy-voice/secrets.env` | ALLOW |
| Read | `read /etc/shadow` | ALLOW |

### Which deny patterns should still apply to reads

None of the defaults was written for a read. Every `DEFAULT_DENY` entry names a
command that changes or exfiltrates something (`rm`, `mkfs`, `sudo`, `ssh`,
`git push`, garbage collection). Against a read they fire only on a word in
the query, as in the tables above: refusing to *look up* sudo, or to read a
tmux pane that happens to be called `ssh`, protects nothing.

What a deny rule on a read *can* protect is a path, and only one reader takes
a path: Claude Code's `Read`. The comment at `claude_backend.py:69-71` says
this is deliberate ("a deny rule aimed at a path should still catch a read of
it"), but no default deny rule is aimed at a path: `~/.ssh` is caught by
accident and `/etc/shadow` or a secrets file is not caught at all. That rule
exists only if the user writes it in `deny_patterns`.

Our own read-only tools take no filesystem path. What they can expose is
screen or terminal *contents*:

- `read_screen` and `screenshot` already refuse sensitive windows (password
  managers, lock screens, etc.) through `_sensitive_kind`
  (`tools.py:2229`) and `DEFAULT_SENSITIVE` (`config.py:174`), from
  #46/#51/#52/#67. That guard is by window class and title, not by
  `Policy.check`, so it is unaffected by this issue.
- `read_terminal` (`tools.py:3537`) captures a tmux pane with no sensitive
  guard. A pane can show a secret (an `op read` or `cat .env` someone ran),
  but the deny list cannot see the pane text; it only sees
  `read terminal <target>`. No pattern-based rule can help here.

So a user-written deny rule for a path is the one deny case that must keep
working for reads, and today it can only matter for Claude Code's `Read`.

### Interaction with #76 and #71

- #76 (release turn): during a release, `_decide` refuses every tool not in
  `DRY_RUN_READS` that is not the confirmed call (`claude_backend.py:361-369`).
  `mcp__omarchy__omarchy_help` is not in that set, so a lookup is refused, not
  held, inside a release turn. Separate from this issue, but whatever this
  work decides "is a read" should probably be the same list there.
- #71 (router): `local_engine._route` does not route at all while something is
  held (`local_engine.py:473`), so a held lookup also switches the router off
  for the next sentence. The router itself only runs `hypr_dispatch` and
  `omarchy_cli` routes, plus the window list, which reads `_query_rows`
  directly and never reaches the gate. No read-only tool goes through it.

## Proposed outcome

- Asking to reboot (or shut down, update, rebuild) and having the model look
  the route up first holds exactly one thing: the reboot. The lookup runs.
- A read-only tool is never held for confirmation because of words in its
  query or target, on any engine (OpenAI realtime, planner, local engine,
  Claude Code via MCP).
- Anything a deny rule is meant to protect on a read (a path the user has
  denied) is still refused.
- The actions are gated exactly as before: nothing that changes the machine
  gets through with less checking than today.

## Affected users and systems

- `src/omarchy_voice/tools.py`: `Executor._call_locked`, `Policy`,
  `READ_ONLY_TOOLS`.
- `src/omarchy_voice/claude_backend.py`: `_decide` for Claude Code's `Read`
  and `WebSearch`, and `DRY_RUN_READS` if that list becomes the definition of
  a read.
- Every engine that calls `Executor.call`; and anyone with custom
  `confirm_patterns` / `deny_patterns` in their config.
- Tests: `tests/test_policy.py` and the gate cases in `verify_gate.py`.

## Constraints

- Must not let an action through ungated. "Read-only" must be a closed list
  of tools that cannot change anything (an allowlist, as `READ_ONLY_TOOLS` and
  `DRY_RUN_READS` already are), never inferred from the description.
- A tool whose side effects are not purely reading (`web_search`,
  `open_page`, `clipboard` write, `watch_terminal`, `wait_for`) is not a read
  for this purpose unless the approver says so.
- A user-configured deny rule aimed at a path must still refuse a read of
  that path.
- The sensitive-window guards (#46/#51/#52/#67) stay as they are.
- Demonstrated with fakes only; the live desktop is read-only.

## Open questions

1. For our read-only tools: skip **confirm only** (deny still applies, so
   `omarchy_help "sudo"` and `read_terminal ssh` stay refused), or skip
   **confirm and deny** (no default deny rule protects anything on these
   tools, and none of them takes a path)?
2. For Claude Code's `Read` and `WebSearch`: same answer as our tools, or
   keep deny (a path rule is the case the comment at
   `claude_backend.py:69-71` promises) and skip only confirm?
3. Should the defaults gain a deny rule aimed at secret paths for `Read`
   (e.g. `~/.ssh/`, `/etc/shadow`, `.env`, secrets files), so today's
   accidental `\bssh\b` catch becomes a real one? Or is that a separate issue?
4. Is `READ_ONLY_TOOLS` the right list for the gate as well as for dry-run
   (it includes `screenshot` and `read_screen`, excludes
   `read_notifications` and `clipboard` read)? Or a separate, narrower list?
5. Should the #76 release turn let the same reads through (today it refuses
   `mcp__omarchy__omarchy_help` inside a release), or is that out of scope?
6. `read_terminal` has no sensitive guard at all. Out of scope here (the deny
   list cannot see pane text), follow-up issue?
