---
status: draft
issue: 13
author: olafkfreund
---

# Intent: a refused call must leave a line in the log

## Problem

When the policy stops the assistant, `omarchy-voice log` says nothing
happened.

`Executor._call_locked` (`tools.py:1285`) records a denied call as `DENIED`
and a held one as `HOLD` in `executor.transcript`. Nothing reads that list; it
is appended to and never persisted. The log gets only what `on_action` sends,
and `on_action` fires only for calls that run. Measured:

```
ls /tmp               -> ran      -> log: "action  ls /tmp"
sudo rm -rf /         -> DENIED   -> log: nothing
systemctl poweroff    -> HOLD     -> log: nothing
```

On the realtime backend — the one used most — no log tag exists for a refusal
or a new hold at all. So the question an audit log exists to answer, "did it
try anything it should not have?", has no answer. The log looks cleanest
precisely when the policy was working hardest.

This is the same gap #7 found and fixed for the claude-code backend alone,
with an `on_record` sink on `ClaudeBrain`. The root is shared: every backend
records refusals through `Executor`, and `Executor` writes them where nobody
looks.

## Proposed outcome

Every call the policy refuses or holds leaves a line in `omarchy-voice log`,
on every backend, the same way a call that ran already does. Reading the log
after a session shows what was attempted, not only what succeeded.

Observable: repeat the measurement above through a real session and all
three lines appear in the log. A dry-run refusal on the claude-code backend
still appears, as it does since #7.

## Affected users and systems

- `Executor` (`tools.py`) — the shared cause.
- The realtime session (`realtime.py`), which builds an `Executor` and owns
  a `feedback.log`.
- The chat planner behind `omarchy-voice say` — it prints rather than logs,
  so this may be a no-op there; the spec should confirm.
- The MCP server (`mcp_server.py`) — stdio, and stdout is protocol, so it
  must not start printing log lines there.
- The claude-code backend's `on_record` sink from #7 — which may fold into
  whatever is built here.

## Constraints

- **Never write to stdout from the MCP server.** A stray line is a protocol
  error at the client (`mcp_server.py`'s `run` docstring).
- **No duplicate lines.** A call that ran is already logged via `on_action`;
  a refusal must be logged once, not once per layer.
- **Must not change what the model is told.** Only what is recorded.
- **Must not change any policy decision.** Logging only.
- **No behaviour change for `say`** unless the spec shows it gains
  something.

## Open questions

1. **Fix it once in `Executor`, or per session?** `Executor` is where every
   refusal is written, so a sink there fixes every backend at once — the
   shared-function fix. The alternative is each session reading the result
   and logging refusals itself, which repeats the fix per backend. I would
   put it in `Executor`. Agree?

2. **Does #7's `ClaudeBrain.on_record` fold into it?** If `Executor` gets a
   sink, the claude-code backend has two ways to log a refusal. Folding
   `ClaudeBrain._note` onto the `Executor` sink leaves one. That touches code
   merged this morning, so it is a real choice rather than tidying — I would
   fold it, in this change, with #7's tests as the guard.

3. **Which refusals count?** `DENIED` and `HOLD` certainly. Also `CANCEL`
   (a hold the user dropped) and a rejected confirmation? My view: anything
   the policy decided against, yes; the spec should list them.
