---
status: draft
issue: 13
spec: spec/2026-09-18-13-refusals-in-log.md
---

# Plan: a refused call must leave a line in the log

## The decisions being implemented

Carried over from the approved spec so this file stands alone.

**The bug.** `Executor` records a denied call (`DENIED`) and a held one
(`HOLD`) in `executor.transcript`, which nothing reads. The log only gets what
`on_action` sends, and that fires only for calls that run. Measured: a
`sudo rm -rf /` the policy stopped, and a `poweroff` it held, left no line in
`omarchy-voice log`. The realtime backend has no log tag for either.

**Which lines are missing** — established by reading, not assumed:

| Tag (`tools.py`) | Reaches the log today? |
| --- | --- |
| `RUN` (1307) | yes, via `on_action` |
| `CONFIRM` (1334) | yes, via `on_action` and the session's `confirm … released` |
| `CANCEL` (1350) | yes, both sessions log `cancel …` |
| **`DENIED` (1293)** | **no** |
| **`HOLD` (1304)** | **no** |
| **`HOLD … refused second gate` (1298)** | **no** |

**Settled decisions — do not relitigate while implementing:**

1. **Fix it once, in `Executor`.** A sink, `on_record`, the same shape and
   no-op default as `on_action`, plus `Executor.record(line)`, which appends
   to the transcript and calls the sink.
2. **Exactly the three policy refusals go through it** — lines 1293, 1298,
   1304. `RUN`, `CONFIRM`, `CANCEL` stay plain appends; they are already
   logged, and routing them too would write every one twice.
3. **Wired in the two sessions only:** `RealtimeSession` and `LocalSession`
   pass `on_record=self.feedback.log`. `cmd_say` prints its result, and the
   MCP server's stdout is the protocol — neither is wired.
4. **#7's `ClaudeBrain` sink folds into this one.** `_note` becomes
   `self.executor.record(line)`; `ClaudeBrain.on_record` and `brain_for`'s
   `on_record` parameter go. The daemon's `Executor` already carries the sink
   and the brain already holds that `Executor`, so every line #7 logs still
   arrives.
5. **No change to any policy decision or to what the model is told.**

**Threading:** `on_action` is already called inside `Executor._lock` from
the worker thread and already calls `feedback.log`; the sink is called from
the same place, so nothing new is exposed.

## Steps

1. **`src/omarchy_voice/tools.py` — the sink.**
   `Executor.__init__(self, config, on_action=None, on_record=None)`;
   `self.on_record = on_record or (lambda line: None)`. Add
   `def record(self, line: str) -> None` (append, then `self.on_record(line)`),
   with a docstring naming the gap: the transcript is not the log.
   → verify by: existing `Executor` tests pass unchanged.

2. **`tools.py` — route the three refusals.** In `_call_locked`, lines 1293,
   1298 and 1304: `self.transcript.append(...)` → `self.record(...)`. Nothing
   else in that method changes.
   → verify by: `grep -n "self.record(" src/omarchy_voice/tools.py` shows
   exactly three, and `self.transcript.append` remains on `RUN`, `CONFIRM`,
   `CANCEL`.

3. **`src/omarchy_voice/realtime.py:397` —** add `on_record=self.feedback.log`.

4. **`src/omarchy_voice/local_engine.py` — move the daemon's wiring.**
   - Line 139: `Executor(config, on_action=self._on_action,
     on_record=self.feedback.log)`.
   - `brain_for` (line 98): drop the `on_record` parameter, its docstring
     paragraph and the `if on_record is not None` block (lines 109–125).
   - Line 506: back to `brain_for(self.config, self.executor)`.
   → verify by: `grep -n on_record src/omarchy_voice/local_engine.py` shows
   only line 139.

5. **`src/omarchy_voice/claude_backend.py` — fold #7's sink.**
   - `_note`: body becomes `self.executor.record(line)`; docstring keeps the
     rule (only for calls that did not run) and says where the sink now lives.
   - Remove `self.on_record` and its comment from `__init__` (line ~224).
   → verify by: `grep -rn on_record src/omarchy_voice/claude_backend.py`
   returns nothing.

6. **`tests/test_claude_backend.py` — retarget #7's tests** to the sink on
   the `Executor` (lines 248, 327, 347, 353, 356): `subject.on_record = ...`
   → `subject.executor.on_record = ...`. Assertions unchanged.
   `test_the_daemon_hands_its_log_to_the_brain` (406) becomes a test that
   `LocalSession` wires its `Executor`'s sink to `feedback.log` (step 7).
   → verify by: #7's `HookTests` all pass, same assertions.

7. **New tests** — see the Tests table. `Executor` unit tests in
   `tests/test_policy.py`, beside the existing `Executor` policy tests;
   wiring tests in `test_realtime.py` and `test_local_engine.py`.
   → verify by: mutate each piece on a scratch copy (sink not called,
   a refusal left as a plain append, a session not wired) and confirm its test
   fails; the same tests pass on an unmutated copy in the same harness.

8. **End-to-end, no model.** A test that builds a real `RealtimeSession`,
   points `omarchy_voice.feedback.LOG_FILE` at a temporary file, drives
   `_dispatch` with a deny-rule call, a gated call and an ordinary call, and
   reads the file back.
   → verify by: the file holds `DENIED`, `HOLD` and `action` lines — the
   intent's measurement, through the real session.

9. **PR** linking all three artifacts, `Closes #13`.
   → verify by: CI green on the PR.

## Tests

```
nix develop --command python -m pytest tests -q
nix flake check --no-write-lock-file
```

| Case | Expect |
| --- | --- |
| `Executor.call`, deny-rule command | `DENIED …` reaches `on_record` |
| `Executor.call`, gated command | `HOLD …` reaches `on_record` |
| second gated call while one is held | `HOLD    refused second gate …` reaches `on_record` |
| `Executor.call`, ordinary command | nothing reaches `on_record`; `on_action` fires |
| `run_pending`, `drop_pending` | nothing reaches `on_record` |
| `Executor(config)` with no sink | a refusal raises nothing |
| `RealtimeSession(...).executor.on_record` | is its `feedback.log` |
| `LocalSession(...).executor.on_record` | is its `feedback.log` |
| MCP server's `Executor` | default sink — writes nothing anywhere |
| #7's `HookTests` via `executor.on_record` | every asserted line still arrives |
| End to end: real `RealtimeSession`, temp `LOG_FILE` | `DENIED`, `HOLD`, `action` lines in the file |

## Rollback

`git revert` the merge commit. Nothing persists beyond extra lines in
`session.log`; no config key is added or renamed. Reverting also restores
#7's `ClaudeBrain.on_record` wiring, so the claude-code backend keeps logging
its refusals either way.
