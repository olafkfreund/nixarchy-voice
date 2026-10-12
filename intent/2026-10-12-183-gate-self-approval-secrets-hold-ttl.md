---
status: draft
issue: 183
author: olafkfreund
---

# Intent: close self-approval, secret-read and stale-hold paths in the gate

Closes #183.

## Problem

The confirm gate exists so that a risky action (reboot, a shell command, a
spawned program) only runs when the *user* says yes. Text the model reads —
a web page, OCR output, a window title — can carry instructions, so the gate
has to hold even when the model is working against the user. A code review on
2026-10-12 found three ways it does not:

1. **The model can confirm its own hold.** `omarchy_cli`
   (`src/omarchy_voice/tools.py:2721`) runs any `omarchy …` subcommand,
   including `omarchy voice confirm` and `omarchy voice say yes`. The direct
   `confirm_last` tool is already blocked for the model
   (`claude_backend.py:383`); this is a second door to the same room.
   `run_shell` with `omarchy-voice …` is the same door when `allow_shell` is on.
2. **The model can read the daemon's keys.** The built-in `Read` tool is
   allowed, and no deny rule covers `/proc/<pid>/environ`, which holds
   `ELEVENLABS_API_KEY` and `OPENAI_API_KEY` inherited by the Claude CLI
   subprocess. Separately, the secret-path rules in `config.py` require a
   leading `/` (`/\.aws/credentials`, `/\.netrc`, …), so a relative path such
   as `.aws/credentials` — resolved against the CLI's `cwd` of `~` — matches
   nothing.
3. **A held action never expires.** `Executor.pending_since` is set when an
   action is held (`tools.py:2259`) but only the MCP server reads it. A "yes"
   said an hour later, about something else, releases whatever is still held.

## Proposed outcome

- No tool the model can call — `omarchy_cli`, `run_shell`, or any other —
  can confirm, cancel or speak-to-confirm a held action. Only the user's
  voice, key or bar click can.
- Reading `/proc/*/environ` (and `/proc/*/mem`) is refused, and every secret
  rule matches the path whether it is written absolute, `~`-relative or
  relative.
- A held action that is not confirmed within a short window is dropped, and
  the user is told it lapsed. A late "yes" does nothing.
- Each of the three has a regression test that fails on today's `main`.

## Affected users and systems

- Everyone running the daemon with the Claude backend; the hold-expiry part
  also applies to the local engine.
- Code: `tools.py` (gate, `omarchy_cli`, hold state), `config.py` (deny
  rules), `claude_backend.py` / `local_engine.py` (where a hold is consumed),
  `mcp_server.py` (shares the hold).
- No Nix module or host changes expected.

## Constraints

- Must not change what the *user* can do: `omarchy voice confirm` from the
  keybinding, the bar widget and the voice "yes" keep working.
- Must not widen anything; this is refusals and a timeout only.
- Deny rules stay regexes over the tool description (the existing mechanism);
  no new sandboxing layer in this task.
- Each fix lands with a test in the existing suites (`test_policy.py`,
  `test_claude_backend.py`, `test_local_engine.py`), and
  `nix flake check` stays green.

## Open questions

1. **Hold lifetime.** 60 seconds is my proposal: long enough to hear the
   read-back and answer, short enough that a "yes" to a later question does
   not match. Should it be configurable, or a constant?
2. **Scrub the child environment too?** Besides the `/proc` deny rule, the
   Claude CLI subprocess could be started without the ElevenLabs/OpenAI keys
   at all (defence in depth). That needs checking that nothing the CLI runs
   relies on them. In scope, or a follow-up?
3. **Scope check.** The review also found the MCP `confirm` taking an
   agent-chosen phrase (#4) and the 400-character truncation before the deny
   check (#10). Both are the same class of bug. Keep them out of this task
   (my recommendation, to keep the diff reviewable), or fold them in?
