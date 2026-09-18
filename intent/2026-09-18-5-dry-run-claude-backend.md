---
status: approved
issue: 5
author: olafkfreund
---

# Intent: a dry run must not act

## Problem

`--dry-run` acts. On the claude-code backend it is not a simulation at all —
the model's tool calls execute for real, and the run reports what happened as
though it had been asked to do it.

`Executor` honours the setting (`tools.py:1309`) and narrates: `[dry-run]
would run: touch /tmp/x`. `ClaudeBrain._gate` (`claude_backend.py:213`) never
reads `config.dry_run`. It runs the deny and confirm regexes, returns
`PermissionResultAllow()`, and the SDK executes Claude Code's own `Bash`,
`Write` or `Edit`. `_options()` (`claude_backend.py:255`) sets no
`allowed_tools` or `disallowed_tools`, so nothing else is in the way.

Same config, same command, two answers:

```
config: dry_run=True, allow_shell=False
  Bash  {"command": "touch /tmp/x"}    -> allow          (and it runs)
  Write {"file_path": "/tmp/x", ...}   -> allow          (and it writes)
  Executor.call("run_shell", ...)      -> [dry-run] would run: touch /tmp/x
```

`cmd_say` prefers `ClaudeBrain` whenever the CLI is installed
(`cli.py:118`), so this is the default path, not an edge case.

Why this is worse than a setting that merely does nothing: dry-run is the
affordance someone reaches for *because* they are unsure. It is what you use
before letting a voice assistant near your files. Someone testing
`omarchy-voice say --dry-run "tidy up my downloads"` is doing the careful
thing, and the careful thing deletes their files. The failure is silent —
there is no warning, and the output reads like a successful simulation.

This shares a root cause with #6: settings that gate `Executor` do not reach
the backend that bypasses `Executor`. #6 covered `doctor` misreporting
`allow_shell`. This one is the same gap where the consequence is action
rather than a misleading label.

## Proposed outcome

With `dry_run = true`, no action taken through any backend changes the
machine. A dry run on the claude-code backend narrates what would have
happened, the way the other one does, and the user can tell from the output
that nothing ran.

Read-only work still functions, so a dry run can still answer questions about
the desktop — otherwise the feature is only useful for watching it fail.

Observable: `omarchy-voice say --dry-run "<anything destructive>"` leaves the
filesystem and the session untouched, and says so.

## Affected users and systems

- `omarchy-voice say --dry-run` and `ask --dry-run` on any machine with the
  `claude` CLI installed — the default backend there.
- `dry_run = true` in `config.toml` (`share/config.example.toml`), for anyone
  running the daemon that way.
- `ClaudeBrain` and `WarmBrain`, so both `cmd_say` and the local engine
  daemon (`local_engine.py:496`).
- Not the realtime/OpenAI backend, which drives `Executor` directly and is
  correct today.
- Not the MCP server, same reason.

## Constraints

- **Must not weaken the gate.** Whatever lands here is additional to the deny
  and confirm checks, never a replacement or a reordering of them.
- **Must fail closed.** A tool nobody has classified must be treated as
  capable of changing things. `_gate` already takes this stance for unknown
  tools (`test_an_unknown_tool_is_still_described_and_checked`), and Claude
  Code gains tools on its own schedule.
- **Must cover MCP tools from other servers**, notably ai-mirror, which types
  into real windows. Our own `mcp__omarchy__*` calls are already fine, since
  they route through `Executor` — which is why `_gate` short-circuits them at
  `claude_backend.py:220`.
- **Must not change behaviour when `dry_run` is false.** This is the shipped
  default and the normal path; a regression there is worse than the bug.
- No new dependency, and no second copy of the policy rules. The gate is the
  one place that must not drift (`mcp_server.py:12`).

## Open questions

These need deciding before a spec, and are the reason this is not a patch:

1. **What counts as read-only?** A dry run has to let reads through or it
   cannot simulate anything. That means an allowlist of Claude Code tool
   names — `Read`, `Glob`, `Grep`, `WebFetch`, `WebSearch`, `TodoWrite`? —
   and a decision on `Bash`, which is mostly reads in practice but cannot be
   classified by name. Is `Bash` simply denied under dry-run, or is there an
   appetite for classifying the command text? (I would not classify command
   text: that is the deny-list's known weakness, reused somewhere it would be
   load-bearing.)

2. **Deny, or allow-and-narrate?** `_gate` can only permit or refuse; it
   cannot substitute a fake result. So a dry run denies with a message the
   model reads as narration (`would have run: …`). The model is then told its
   write failed, and may retry, route around it, or report failure to the
   user. Is that acceptable — a dry run that is visibly refused rather than
   silently simulated — or should the backend refuse to start at all under
   `dry_run`?

3. **Is `--dry-run` worth keeping on this backend?** Option (2b): print "dry
   run is not supported on the claude-code backend" and exit non-zero,
   rather than shipping a simulation that behaves differently from the other
   one. Honest and cheap; loses a useful feature.

4. **`allow_shell` at the same time?** Same root cause, and tempting to fix
   together. I think not: `allow_shell = false` denying native `Bash` would
   contradict README:256, which advertises `Bash` as what this backend is,
   and would change behaviour for every existing user. Confirm that this
   stays out of scope.

5. **Does the gate even see every call?** #7 questions whether
   `can_use_tool` is consulted for every tool, or whether settings-level
   allow rules can pre-approve some. If it can be bypassed, a dry-run check
   placed there inherits the same hole. Does #7 need resolving first, or does
   this proceed and move with the gate later?
