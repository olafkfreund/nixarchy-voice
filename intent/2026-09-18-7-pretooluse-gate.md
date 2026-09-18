---
status: approved
issue: 7
author: olafkfreund
---

# Intent: the policy gate must see every tool call

## Problem

The claude-code backend's safety rules — deny, confirm, and dry-run — live in
`ClaudeBrain._gate`, wired as the SDK's `can_use_tool` callback. Its docstring
says it sees "every tool call Claude Code makes". It does not.

`can_use_tool` is Claude Code's *ask* path: it runs only when the CLI would
otherwise prompt. Calls the CLI auto-approves never reach it. Reproduced live
against Claude Code 2.1.274 and claude-agent-sdk 0.2.152, with
`deny_patterns=[r"grocery-list"]` and a unique token in each file:

| Case | Tool used | `_gate` consulted? | Deny fired? | Content leaked? |
| --- | --- | --- | --- | --- |
| File outside the working dir (control) | `Read` | yes | yes | no |
| File inside, natural request | `Bash cat` | yes | yes | no |
| **File inside, via `Read`** | **`Read`** | **no** | **no** | **yes** |

`claude_cwd` defaults to `~`, so every `Read` under `$HOME` bypasses the
policy entirely — and never reaches the transcript or `omarchy-voice log`.

Three separate failures follow:

1. **Rules silently do nothing.** A deny rule a user writes to protect a path
   — `~/.ssh`, the `.env` holding the OpenAI key — does not fire for reads
   under the working directory. Nothing reports that it didn't.
2. **The log is incomplete.** Auto-approved calls leave no trace, so the log
   cannot answer "what did it read?". Separately, and already true before this
   issue: a *confirmed* real execution returns before the `RUN` transcript
   line, `on_action` and `_actions`, so a confirmed reboot is never logged
   either (found by the second-opinion review; `claude_backend.py`, the
   `_confirmed` branch of `_gate`).
3. **Auto-approval is not limited to reads.** Anthropic's tool reference
   documents `EnterWorktree` and `TaskStop` as not prompting; both are in
   CLI 2.1.274's tool list. If so, #5's dry-run fix (PR #10) — which lives in
   the same callback — cannot refuse them, and a dry run can still act.
   **Not yet verified live.**

On shipped defaults the practical exposure is small: the default deny list
targets commands, not reads, and the `Bash`/`Write` paths were observed going
through the gate. The gap is in the guarantee — which is the thing a safety
gate exists to give.

## Proposed outcome

Every tool call the claude-code backend makes passes through one policy
decision before it runs, whether or not Claude Code would have asked
permission. Deny, confirm and dry-run apply to all of them. Every call —
allowed, refused, held, or confirmed and run — leaves a line in the log.

Observable: repeat the table above and every row reads "consulted: yes". A
read-protecting deny rule refuses a `Read` inside the working directory.
`omarchy-voice log` shows the reads, and shows a confirmed action running.

The docstring and README stop claiming more than the code does.

## Affected users and systems

- The claude-code backend: `ClaudeBrain` and `WarmBrain`, so both
  `omarchy-voice say` and the local-engine daemon (`local_engine.py`).
- Anyone who has added read-targeting rules to `deny_patterns`.
- The log (`omarchy-voice log`) and `turn.actions`.
- PR #10 / issue #5: `_dry_run_refusal` moves with whatever replaces `_gate`.
- Not the realtime/OpenAI backend or the MCP server: both drive `Executor`
  directly and have no auto-approval path.

## Constraints

- **PR #10 merges first.** This branch is off `main`, which does not yet have
  `_dry_run_refusal`. Designing around a gate that is about to change would be
  wasted; rebase once #10 lands.
- **One owner of the decision.** Whatever runs the policy must be the only
  thing that does. Evaluating in two places spends a one-shot approval in the
  first and re-holds the replay in the second — the user would be asked twice
  (point made in the second-opinion review).
- **Must not weaken anything that works today.** Deny before confirm before
  dry-run, one-shot confirmation, the `mcp__omarchy__*` delegation to
  `Executor`, and the ai-mirror full-text description all survive unchanged.
- **Must fail closed.** An error in the policy path — an exception, a
  malformed response — must refuse the call, not allow it.
- **Never `bypassPermissions`.** The existing comment in `_options()` says it
  shadows `can_use_tool`; any design touching permission modes has to show it
  does not reopen what it closes.
- **Verified against the real CLI, not just unit tests.** The existing tests
  call `_gate` directly, so they prove the rules are right *when called* and
  cannot prove the CLI calls them. This bug is exactly the gap between those.

## Open questions

1. **Hook, or not?** The second opinion (OpenAI Codex, read-only) recommends
   an unfiltered `PreToolUse` SDK hook, which Anthropic documents as running
   even for auto-approved calls, with `can_use_tool` kept only to answer the
   prompts the CLI still raises, doing no policy of its own. The installed SDK
   supports it (`ClaudeAgentOptions.hooks`, `HookMatcher`). It argued against
   an empty `cwd` (shrinks one auto-approve scope, forces nothing through the
   gate, breaks relative paths) and `permissions.ask` rules (narrower;
   sandbox auto-approval can override them). My reading agrees. Is a hook the
   direction the spec should take?

2. **Does a hook actually work on this CLI?** Documented, not observed. The
   spec should not be approved on documentation alone: the first thing to
   establish is whether a `PreToolUse` deny blocks an auto-approved `Read` on
   2.1.274. If it does not, this intent needs a different answer.

3. **Fold in the confirmed-execution logging gap?** It predates #7 but lives
   in the same function, and a single decision point is the natural place to
   log every outcome. I would fold it in. Separate fix instead?

4. **Verify `EnterWorktree` / `TaskStop` auto-approval here, or in the spec?**
   It decides how incomplete PR #10 is, not whether this work is needed. I
   would verify during the spec's reproduction step rather than before.
