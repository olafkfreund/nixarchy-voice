---
status: approved
issue: 13
intent: intent/2026-09-18-13-refusals-in-log.md
---

# Spec: a refused call must leave a line in the log

## What is and is not logged today

Read off the code rather than assumed. `Executor` writes six tags to
`executor.transcript`, which nothing reads. Whether each reaches
`omarchy-voice log` by some other route:

| Tag (`tools.py`) | Reaches the log today? |
| --- | --- |
| `RUN` (1307) | yes — `on_action` → the session logs `action …` |
| `CONFIRM` (1334) | yes — `run_pending` calls `on_action`; sessions also log `confirm … released` |
| `CANCEL` (1350) | yes — both sessions log `cancel …` |
| **`DENIED` (1293)** | **no** |
| **`HOLD` (1304)** | **no** |
| **`HOLD … refused second gate` (1298)** | **no** |

A rejected spoken confirmation is also already logged, as `reject …`
(`realtime._confirm`). So the gap is exactly the three decisions the *policy*
makes against a call. Cancels and rejects are the user's decisions and are
already recorded by the sessions; logging them again at the `Executor` level
would write them twice.

## Design

### A sink on `Executor`

```python
Executor(config, on_action=None, on_record=None)
```

`on_record: Callable[[str], None]`, a no-op by default — the same shape and
default as `on_action`. One method writes a refusal:

```python
def record(self, line: str) -> None:
    """A call that did not run: into the transcript and out to the log."""
    self.transcript.append(line)
    self.on_record(line)
```

The three policy refusals in `_call_locked` (1293, 1298, 1304) call
`self.record(...)` instead of `self.transcript.append(...)`. `RUN`, `CONFIRM`
and `CANCEL` stay plain appends, because the table above shows they are
already logged.

Fixed once, in the function every backend routes through, so the realtime
session, the chat planner and the MCP server are all covered by one change.

### Wiring

| Constructs `Executor` | `on_record` | Why |
| --- | --- | --- |
| `RealtimeSession` (`realtime.py:397`) | `self.feedback.log` | the gap this issue is about |
| `LocalSession` (`local_engine.py:139`) | `self.feedback.log` | the daemon; replaces #7's wiring, below |
| `cmd_say` (`cli.py:117`) | none | prints heard/action/reply to the terminal; a refusal already shows as the reply |
| MCP server (`mcp_server.py:115`) | none | stdout is the protocol; a stray line is a client-side parse error |

### #7's sink folds into this one

#7 gave `ClaudeBrain` its own `on_record`, set through
`brain_for(config, executor, on_record=...)`. With a sink on `Executor`, the
claude-code backend would have two routes to the log for the same kind of
line. It keeps one:

- `ClaudeBrain._note(line)` becomes `self.executor.record(line)`.
- `ClaudeBrain.on_record` is removed.
- `brain_for` loses its `on_record` parameter; `LocalSession` passes
  `on_record=self.feedback.log` to its `Executor` instead, which the brain
  already holds.

Every line #7 logs — `DENIED`, `HOLD`, `DRYRUN`, `ERROR`, `ALARM` — still
reaches the daemon's log, through the `Executor` the brain was always given.
#7's tests are the guard; they change only in where the sink is attached.

### Threading

`on_action` is already called inside `Executor._lock`, from the worker thread
`asyncio.to_thread` runs `Executor.call` on, and it calls `feedback.log`. The
sink is called from the same place, so it adds no thread-safety exposure
`feedback.log` does not already have.

## Alternatives rejected

**Each session logs refusals itself,** from the `Result` it gets back.
Rejected: the fix would be repeated once per backend, and the next backend
would start without it — which is how #7 ended up fixing one backend only.

**Log every transcript line through the sink,** `RUN` and `CONFIRM`
included. Rejected: they are already logged, so every allowed call would
appear twice.

**Keep #7's `ClaudeBrain.on_record` alongside the new sink.** Rejected: two
routes for one kind of line, and wiring the daemon twice. The approved
intent chose to fold it.

**Log `CANCEL` at the `Executor` level too.** Rejected on the table above:
already logged by both sessions.

## Risks

- **Touches code merged this morning (#7).** The fold changes where the
  claude-code backend's sink lives. #7's tests, retargeted, plus a live
  daemon-shaped check below, are the guard.
- **MCP server must stay silent.** Covered by not wiring it and by a test
  asserting the MCP server's `Executor` has the default sink.
- **Log volume.** A model that keeps retrying a denied command writes a line
  per attempt. Accepted: that is exactly what the log should show.

## Verification

- `nix flake check`, locally and in CI.
- Unit:
  - `Executor.call` with a deny rule → `DENIED` reaches `on_record`.
  - A gated call → `HOLD` reaches `on_record`; a second gated call while one
    is held → the second-gate `HOLD` reaches it.
  - A call that runs → does **not** reach `on_record` (only `on_action`).
  - `run_pending` and `drop_pending` → do not reach `on_record`.
  - Default sink is a no-op: an `Executor` with no `on_record` raises nothing.
  - `RealtimeSession` and `LocalSession` wire `feedback.log`; the MCP server
    and `cmd_say` do not.
  - #7's `HookTests`, with the sink attached to the `Executor`: every line
    they asserted still arrives.
- End to end, no model needed: drive a real `RealtimeSession`'s `_dispatch`
  with a deny-rule call and a gated call, with its `feedback` writing to a
  temporary log file, and read the file back. This is the measurement from
  the intent, repeated through the real session instead of a bare `Executor`.
