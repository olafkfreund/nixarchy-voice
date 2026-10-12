---
status: draft
issue: 183
intent: intent/2026-10-12-183-gate-self-approval-secrets-hold-ttl.md
---

# Spec: close self-approval, secret-read and stale-hold paths in the gate

## Decisions on the intent's open questions

The intent was approved without answers, so the spec takes these defaults.
Each one can still be changed at this review.

1. **Hold lifetime:** a constant, `HOLD_SECONDS = 60`. No config key until
   someone needs a different value.
2. **Removing the keys from the CLI environment:** a follow-up issue, not part
   of this task. The `/proc` deny rule closes the read path described in the
   intent. Taking the keys out of the environment needs a separate check of
   what the CLI's own tools depend on.
3. **MCP agent-chosen phrase (#4) and the 400-character cut (#10):** both stay
   out of scope and get a follow-up issue each. The MCP server gets only the
   hold timeout (fix 3 below), because that reuses `pending_since`, which it
   already reads.

## Design

### 1. Nothing the model calls can confirm a hold

Every path the model has into the daemon ends in text that `Policy.check`
matches. That covers `omarchy_cli` (described as `omarchy voice confirm`),
`run_shell` (`omarchy-voice say yes`), `hypr_dispatch exec …`, ai-mirror typing
into a terminal (described uncut), and Claude Code's built-in tools. `Policy.check`
(`src/omarchy_voice/tools.py:224`) is the single choke point both gates
already call: `Executor.call` for our tools, `_decide` in `claude_backend.py`
for the rest.

Add a module-level `_SELF_RELEASE` list in `tools.py` and check it at the top of
`Policy.check`, ahead of the configured deny rules:

```python
# The model must never answer its own hold (#183). In code, not in
# DEFAULT_DENY_RULES: deny_patterns_replace / _remove can drop those.
_SELF_RELEASE = (
    r"\bomarchy[-\s]+voice[-\s]+(confirm|say)\b",   # omarchy voice confirm, omarchy-voice-say, store paths
    r"omarchy-voice/control\.sock\b",               # the control socket itself
)
```

A match raises `Denied("only the user can confirm a held action")`. That one
message is enough for the model to stop retrying.

- `voice cancel` stays allowed. Cancelling is safe, and `cancel_last` is
  already allowed for the same reason.
- `start`, `stop` and `announce` are not affected. They cannot release a hold,
  and the capability manifest already leaves the `voice` family out
  (`capabilities.py:58`).
- The user's own paths never go through `Policy.check`, so they keep working:
  the keybinding (`omarchy-voice-confirm` reaches the socket directly), the bar
  widget, the voice "yes" (`LocalEngine._consent`) and the terminal.

### 2. Secret reads: `/proc` and relative paths

In `DEFAULT_DENY_RULES` (`src/omarchy_voice/config.py:137`):

- Add `"secret-proc-environ": r"/proc/(\S*/)?(environ|mem)\b"`. This covers
  `/proc/self/environ`, `/proc/1234/environ` and `/proc/self/task/5/environ`.
- Give the dot-directory secret rules the leading-boundary form that
  `secret-claude-config` already uses, so `.aws/credentials`,
  `~/.aws/credentials` and `/home/u/.aws/credentials` all match. Concretely:
  - Add one prefix constant: `_AT = r"""(^|[\s/"'=~])"""`.
  - Rewrite every rule that starts with `/\.` to start with `_AT + r"\."`.
    That is `secret-ssh-dir`, `-gnupg`, `-login-stores`, `-aws`,
    `-gh-token`, `-claude-login`, `-pass-store`, `-keyrings`, `-codex`,
    `-gemini`, `-copilot`, `-claude-desktop` and `-opencode`.
  - Rule names do not change, so `deny_patterns_remove` entries in users'
    configs keep working.

### 3. Holds lapse

**Where a hold lives.** Both gates already report a new hold through
`Executor.on_hold`, which the engine wires to `_show_hold`
(`local_engine.py:249`). The engine is the only place that hears a "yes", so the
timeout belongs there, plus one line in the MCP server, which has no engine.

**Engine (`local_engine.py`):**

- `_show_hold` runs on whichever thread the gate is on. It increments a hold
  counter (`self._hold_gen`) and schedules `self._lapse(gen)` with
  `loop.call_soon_threadsafe(loop.call_later, HOLD_SECONDS, …)`.
- `_lapse(gen)` does nothing if `gen != self._hold_gen` (a newer hold
  replaced this one) or if `self._held()` is None (it was confirmed or
  cancelled).
- **The clock runs from when she stops speaking, not from when the hold is
  made.** A long recipe read-back must not eat the user's time to answer. If
  `time.monotonic() < self._voice_until + HOLD_SECONDS`, `_lapse` reschedules
  for the time remaining. While she is speaking, `_voice_until` is `inf`
  (`local_engine.py:455`), so the check simply waits.
- When the hold lapses, it reuses `_local_cancel`'s drop logic: the executor
  hold, or `brain.cancel()`. It logs `lapse  <held>`, says "That timed out, so
  I didn't do it.", calls `brain.note(...)` so the model knows the action did
  not run, and calls `_settle()`, which closes the critical card.
- A "yes" after that point finds `_held()` returns None, so `_consent` does not
  consume it, and it goes to the model as ordinary text. The model cannot
  release anything (`confirm_last` is denied, and fix 1 blocks the other
  routes).

**MCP server (`mcp_server.py:137`):** `_confirm` already computes `waited`.
When `waited > HOLD_SECONDS`, it calls `executor.drop_pending()` and returns
`ERROR: <held> timed out and was dropped. Ask the user again if they still want it.`

`HOLD_SECONDS` is defined once, in `tools.py` next to `Executor`, and imported
by both.

## Alternatives rejected

- **Putting fix 1 in `DEFAULT_DENY_RULES`.** Users can remove or replace those
  rules, and this one is part of the gate's own integrity rather than a
  preference.
- **Refusing the `voice` family only inside `_tool_omarchy_cli`.** That leaves
  `run_shell`, `hypr_dispatch exec`, typing into a terminal, and the socket
  itself open. `Policy.check` sees every one of them.
- **Expiring the hold inside `Executor`, and in the brain separately.** That
  means two clocks, and neither knows when she stopped speaking. The engine
  sees both holds through `_held()` and owns `_voice_until`.
- **Checking the timeout only when a "yes" arrives.** The critical card would
  then stay on screen forever and the user would never hear that the action
  lapsed. A timer fixes both, and the generation counter makes it safe
  against a stale timer firing.
- **Removing the keys from the CLI environment now.** Deferred; see decision 2.

## Risks

- **A wider secret prefix denies more.** Relative paths now match too, so a
  read of `.ssh-notes/` is refused, because `\b` matches before `-`. That
  over-match already exists today for `~/.ssh-notes`, so this extends a
  known, accepted limit and does not add a new one. The existing non-match
  tests (`.env.example`, `.envrc` and so on) must keep passing.
- **`_SELF_RELEASE` over-matches prose,** for example the model reading a file
  that contains "omarchy voice say". That costs one refused read, and the
  release path matters more.
- **The timer runs on the event loop.** `_lapse` must not block, and dropping a
  hold takes `Executor._lock`. `drop_pending` is quick, but it can wait behind
  a running tool call (the `_spawn` timeout issue from the review, a separate
  bug). To keep the loop free, `_lapse` runs the drop with
  `asyncio.to_thread`, as `_release` does.
- **An MCP client that is slower than 60 s** gets its hold dropped. That is
  what the change intends.
- No Nix or host impact.

## Verification

All of these must fail on `main` and pass after the change, under
`nix develop -c python -m pytest -q tests`, and `nix flake check` must stay
green.

- **`tests/test_policy.py`:**
  - `Policy.check` denies each of: `omarchy voice confirm`, `omarchy voice say yes`,
    `omarchy-voice say yes`, `/nix/store/x-omarchy-voice/bin/omarchy-voice-confirm`,
    and `socat - UNIX:/run/user/1000/omarchy-voice/control.sock`. These are denied
    even with `deny_patterns_replace = true` and an empty list.
  - It does not deny `omarchy voice cancel`.
  - It denies `read /proc/self/environ`, `read /proc/42/environ` and
    `read .aws/credentials`, `cat ~/.netrc`, `read .config/gh/hosts.yml`.
  - Existing non-match cases still pass.
- **`tests/test_claude_backend.py`:** `_decide("Bash", {"command": "omarchy-voice say yes"})`
  is a deny, and `_decide("Read", {"file_path": "/proc/self/environ"})` is a deny.
- **`tests/test_local_engine.py`,** with time faked or `HOLD_SECONDS` patched:
  - A hold with no answer lapses. `_held()` is None, the brain received a note,
    and a later "yes" is not consumed by `_consent`.
  - A hold made while she is speaking does not lapse until `HOLD_SECONDS`
    after `_voice_until`.
  - A confirmed hold's timer does nothing.
  - A replaced hold's old timer does not drop the new hold.
- **`tests/test_mcp.py`:** `_confirm` after `HOLD_SECONDS` drops the hold and
  returns the timed-out error.
- **Manual, on the running daemon:** hold a `reboot` in dry-run, wait 60 s,
  then hear the lapse line and see the card close.
