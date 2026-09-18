---
status: approved
issue: 7
spec: spec/2026-09-18-7-pretooluse-gate.md
---

# Plan: the policy gate must see every tool call

## The decisions being implemented

Carried over from the approved spec so this file stands alone.

**The bug.** The claude-code backend's rules — deny, confirm, dry-run — live in
`ClaudeBrain._gate`, registered as the SDK's `can_use_tool` callback. Claude
Code only calls that when it would otherwise prompt. Calls it auto-approves
never reach it. Observed live on Claude Code 2.1.274 / claude-agent-sdk
0.2.152:

- `Read` inside the working directory (default `~`) ran past a deny rule
  written against its path, and left no log line.
- Under `--dry-run`, `EnterWorktree` created a real git worktree — #5's fix
  lives in the same callback and never saw the call.

**What a throwaway spike established,** which the design depends on:

| Case | Hook returns | `_gate` consulted | Outcome |
| --- | --- | --- | --- |
| A | `deny` on an auto-approved `Read` | no | blocked |
| B | no decision | **yes** | evaluated twice |
| B2 | explicit `allow` | **no** | callback skipped |
| C | no decision, `EnterWorktree`, dry run | no | worktree created |
| D | *raises* | no | **ran** — a crashing hook fails open |

**Settled decisions — do not relitigate while implementing:**

1. **One unfiltered `PreToolUse` hook owns every decision**
   (`HookMatcher(matcher=None, …)`), registered in `ClaudeBrain._options()`.
2. **The hook always returns an explicit `allow` or `deny`, never nothing.**
   B2: an explicit decision is final, so nothing is evaluated twice and a
   one-shot confirmation cannot be spent in one place and re-held in another.
3. **The decision logic moves unchanged.** Today's `_gate` body becomes
   `_decide`, in the same order: `mcp__omarchy__*` → confirmed replay (+
   dry-run) → `Policy.check` → dry-run → allow.
4. **The hook's whole body is inside `except Exception` → explicit `deny`,
   logged `ERROR`.** D: without it, one bug lets every auto-approved call
   through, silently.
5. **`can_use_tool` becomes an alarm.** Unreachable in normal operation (B2),
   so if it runs the hook failed to decide: log `ALARM`, return `deny`. Stays
   registered so an undecided call meets our refusal, not the CLI's default.
6. **Every outcome reaches the persistent log** — see the correction below
   for why this needed more than the spec described. What *ran* is logged by
   `on_action`, as today (`action …`); a confirmed replay now calls it too,
   where today it returns before `on_action`, the transcript and `_actions`.
   What *did not run* — `DENIED`, `HOLD`, `DRYRUN`, `ERROR`, `ALARM` — goes to
   a new `on_record` sink on the brain, which the daemon points at
   `feedback.log`. Records are pre-execution (authorised or refused, not
   succeeded); no `PostToolUse`.
7. **`DRY_RUN_READS` gains `ToolSearch`:** `{"Read", "WebSearch",
   "ToolSearch"}`. It only loads tool schemas, now reaches the gate for the
   first time, and refusing it would narrate the wrong call.
8. **Claims corrected** in the code docstring, README:257 and the test module
   docstring — they name `can_use_tool`, the mechanism that could not see
   every call.

**Never `bypassPermissions`.** Unchanged: `permission_mode="default"`.

**One refinement of the spec, stated rather than drifted into:** the spec
writes `_decide -> (allowed, message)`. `_decide` keeps returning the
`PermissionResultAllow` / `PermissionResultDeny` objects `_gate` returns today,
which carry exactly those two fields (`.behavior`, `.message`). That leaves the
moved body literally unchanged, which is decision 3's point; the hook
translates them into its output.

### Correction to the approved spec: the transcript is not the log

Found while writing this plan, before any code. The spec says "the decision
point writes one transcript line for every call" and that auto-approved reads
then "appear", closing the audit gap. **Nothing reads `executor.transcript`.**
It is an in-memory list, appended to and never persisted. What reaches
`omarchy-voice log` is `on_action` → `feedback.log("action …")`, which only
fires for calls that ran. So `DENIED`, `HOLD` and `DRYRUN` have never been in
the log, and `ERROR` / `ALARM` would not have been either.

The **outcome** the spec and the approved intent ask for — every call,
allowed or refused, leaves a line in the log — is unchanged. Only the
mechanism was wrong, and this plan supplies one that works: decision 6 and
step 5. Approving this plan approves that correction.

The same false claim is in merged work from #5, and is corrected here:

- #5's spec said "the transcript line is `DRYRUN` … so `omarchy-voice log`
  shows which of the two happened". It did not; a dry-run refusal was only in
  memory. After this change it is in the log.
- `tests/test_claude_backend.py`, `test_a_dry_run_is_in_the_log` — its
  docstring says "`omarchy-voice log` must show a refusal" while it asserts
  only the transcript. Step 9 makes it assert the sink.

## Steps

1. **`src/omarchy_voice/claude_backend.py`: rename, don't rewrite.**
   `async def _gate(self, tool, tool_input, ctx)` →
   `async def _decide(self, tool, tool_input)`. Body unchanged. Update its
   docstring: it is the decision, not the entry point, and nothing calls it
   except the hook.
   Other references to update in the same step:
   - `claude_backend.py:14` module docstring, "`_gate` below".
   - `_dry_run_refusal` docstring, "Both of `_gate`'s ways of saying yes".
   - `cli.py:238`, `shell_status` docstring, "see `claude_backend._gate`".
   → verify by: `grep -rn "_gate\b" src/` returns nothing except `silence_gate`.

2. **`claude_backend.py`: log confirmed replays.** In `_decide`'s `_confirmed`
   branch, after `_dry_run_refusal` returns `None` and before the allow:
   ```python
   self.executor.transcript.append(f"CONFIRM {description}")
   self.executor.on_action(tool, description)
   self._actions.append(description)
   ```
   `CONFIRM` matches the tag `Executor.run_pending` already writes
   (`tools.py:1334`), so the log reads the same whichever backend ran it.
   → verify by: the one-shot confirm tests still pass; the new test in step 9.

3. **`claude_backend.py`: add the hook.** (`_note` is added in step 5; steps
   3–5 land in one commit.)
   ```python
   async def _pre_tool_use(self, hook_input, tool_use_id, context) -> dict:
       tool = hook_input.get("tool_name", "")
       try:
           result = await self._decide(tool, hook_input.get("tool_input") or {})
           allowed = result.behavior == "allow"
           reason = getattr(result, "message", "") or "allowed by policy"
       except Exception as exc:  # fail closed: case D ran the call
           self._note(f"ERROR   {tool} ({type(exc).__name__}: {exc})")
           allowed, reason = False, ("Refused: the safety check failed on "
                                     "this call. Tell the user it was not run.")
       return {"hookSpecificOutput": {
           "hookEventName": "PreToolUse",
           "permissionDecision": "allow" if allowed else "deny",
           "permissionDecisionReason": reason}}
   ```
   Docstring states the guarantee and why a callback could not give it.
   → verify by: unit tests in step 9 — explicit decision for every input, and
   an exception inside `_decide` yields `deny` + `ERROR`.

4. **`claude_backend.py`: add the alarm.**
   ```python
   async def _alarm(self, tool, tool_input, ctx):
       self._note(f"ALARM   {tool} reached can_use_tool; the hook did not decide")
       return PermissionResultDeny(
           message="Refused: this call skipped the safety check. It was not run.",
           interrupt=False)
   ```
   → verify by: unit test — returns `deny`, logs `ALARM`.

5. **Refusals reach the log: `on_record`.**
   - `ClaudeBrain.__init__`: `self.on_record: Callable[[str], None] =
     lambda line: None`. A no-op by default, so `omarchy-voice say`, which
     prints its result instead of logging, is unaffected.
   - `ClaudeBrain._note(line)`: appends to `executor.transcript` *and* calls
     `self.on_record(line)`. Every refusal-path append in `_decide`,
     `_dry_run_refusal`, the hook's `except` and `_alarm` goes through it:
     `DENIED`, `HOLD`, `DRYRUN`, `ERROR`, `ALARM`. `RUN` and `CONFIRM` stay
     plain appends, because `on_action` already logs them — routing them
     through `_note` too would write every allowed call twice.
   - `local_engine.brain_for(config, executor, on_record=None)`: sets
     `brain.on_record` when given. `LocalSession.run` passes
     `on_record=self.feedback.log` — a one-argument change at
     `local_engine.py:496`.
   Wired through `brain_for` rather than inside `run()` because the daemon's
   tests inject a `FakeBrain` directly and never call `run()`; `brain_for` is
   a plain function a test can call.
   → verify by: unit test — a denied call reaches `on_record`, an allowed one
   does not; `brain_for(..., on_record=sink).on_record is sink`.

6. **`claude_backend.py` `_options()`: wire them.** Import `HookMatcher`
   lazily beside `ClaudeAgentOptions` (the SDK is optional). Pass
   `hooks={"PreToolUse": [HookMatcher(matcher=None,
   hooks=[self._pre_tool_use])]}` and `can_use_tool=self._alarm`. Keep
   `permission_mode="default"` and its comment. `WarmBrain._options` and
   `local_engine.LocalBrain._options` both call `super()._options()`, so all
   three brains inherit this with no change of their own.
   → verify by: unit test — `ClaudeBrain`, `WarmBrain` and `LocalBrain`
   options all carry the hook and `can_use_tool is self._alarm`.

7. **`claude_backend.py`: `DRY_RUN_READS` gains `ToolSearch`,** with a line in
   its comment: observed auto-approved in the spike, loads schemas only,
   refusing it narrates the wrong call.
   → verify by: `test_the_read_set_admits_no_writer` still passes; step 9's
   `ToolSearch` test.

8. **`README.md:257` and the test module docstring:** name the `PreToolUse`
   hook as the mechanism and say it covers calls Claude Code auto-approves.
   Keep README's "regexes over a tool description, not a sandbox" caveat —
   still true.
   → verify by: `grep -n "can_use_tool" README.md` no longer claims coverage.

9. **`tests/test_claude_backend.py`:**
   - **Route the `gate()` helper through the hook**, translating its output
     back to `.behavior` / `.message`. Every existing gate test then keeps its
     assertions unchanged *and* exercises hook → `_decide`, not `_decide`
     alone.
   - The `WarmBrain` test calling `_gate` directly (currently ~line 632):
     call through the hook the same way.
   - `test_a_dry_run_is_in_the_log`: assert the `DRYRUN` line reaches
     `on_record`, so the test finally checks what its docstring claims.
   - New cases in a `HookTests` class — see the Tests table.
   → verify by: disable each piece in turn (hook's `except`, the alarm, the
   `CONFIRM` lines, `_note`'s sink call) and confirm the test aimed at it fails.

10. **Live verification against the installed CLI** — scratchpad script, not
   repo code, re-running spike cases A–D against the implementation. See
   Tests. Record the output in the PR.
   → verify by: every row matches the Expected column.

11. **PR** linking `intent/`, `spec/`, `plan/`, `Closes #7`, with the live
    results.
    → verify by: `nix flake check` green; PR body links all three.

## Tests

```
nix develop --command python -m pytest tests/test_claude_backend.py -q
nix flake check --no-write-lock-file
```

Expected: all pass; no existing assertion changes, only the helper.

New unit cases (`HookTests`):

| Case | Expect |
| --- | --- |
| Any tool, any input | hook output has `permissionDecision` of exactly `allow` or `deny` — never absent |
| `Read` of a path matching a deny rule | `deny`, reason contains `Refused` |
| `Read` with no rule | `allow`, `RUN read <path>` logged |
| `_decide` raises | `deny`, `ERROR` logged, reason says it was not run |
| `_alarm` called | `deny`, `ALARM` logged |
| Denied / held / dry-run call | line reaches `on_record` |
| Allowed call | does **not** reach `on_record` (`on_action` logs it) |
| `brain_for(..., on_record=sink)` | `brain.on_record is sink` |
| Confirm → replay → replay | allow once with `CONFIRM` + `on_action` + `_actions`; then held again |
| Dry run: `EnterWorktree` | `deny`, `would have run` |
| Dry run: `ToolSearch` | `allow` |
| `ClaudeBrain`, `WarmBrain`, `LocalBrain` options | hook registered with `matcher=None`; `can_use_tool is _alarm` |

Live, installed CLI 2.1.274, scratchpad script using the real `ClaudeBrain`:

| Case | Setup | Expected |
| --- | --- | --- |
| A | `Read` inside cwd, deny rule on its name | refused, token not in reply, `DENIED` logged |
| B | `Read` outside cwd, no rule | runs once, `RUN` logged, no `ALARM` |
| C | `EnterWorktree`, `--dry-run`, throwaway git repo | refused, `git worktree list` shows **only** the main tree |
| D | `_decide` patched to raise, `Read` inside cwd | refused, token not in reply, `ERROR` logged |
| Repro | the intent's three rows | every row: consulted yes |

If B shows `ALARM`, decision 5's assumption is wrong on this CLI — stop and
revise the spec, do not weaken the alarm to make it pass.

## Deviations during implementation

Recorded in the same commit as the code, as the workflow requires. None
changes an approved decision.

1. **One more `_gate` reference than step 1 listed** — the ai-mirror comment
   above `AI_MIRROR_ENV` ("they go through `_gate` like Bash does"). Now "the
   policy hook". Step 1's grep checkpoint found it.
2. **The test helper is two functions, not one.** Step 9 said to route the
   `WarmBrain` test "through the hook the same way", but that test runs inside
   a live event loop and cannot call `asyncio.run`. So `verdict()` is the
   async path through the hook and `gate()` is its sync wrapper; both kinds of
   test drive the same hook code.
3. **The hook's first line is guarded.** The plan's snippet read
   `hook_input.get(...)` outside the `try`, so a malformed `hook_input` would
   have raised *before* the fail-closed handler — the one statement able to
   defeat decision 4. `isinstance` guards it; anything malformed reaches the
   `try` and is refused. Covered by `test_a_malformed_hook_input_is_refused`.
4. **The stand-in SDK in `AiMirrorTests` needed `HookMatcher`.** `_options()`
   now imports it, and the fake module only offered `ClaudeAgentOptions`.
5. **The test module docstring lost a second false claim**, besides naming
   the mechanism: it said the gate holds `allow_shell = false` up, which #6
   showed it never did.

### Step 9 checkpoint: each new piece is caught by its test

Mutated on a copy of `src/`, the real tree untouched, one piece at a time:

| Mutation | Test that failed |
| --- | --- |
| hook catches only `ZeroDivisionError` | `test_a_crash_in_the_policy_refuses_the_call` |
| alarm returns allow | `test_reaching_the_callback_is_an_alarm` |
| `CONFIRM` logging removed | `test_a_confirmed_run_is_logged_like_any_other` |
| `_note` skips the sink | `test_refusals_reach_the_log_and_runs_are_not_logged_twice` |
| hook not registered | `test_every_brain_carries_the_hook` |

Control: the same five tests on an unmutated copy, same harness — 5 passed.
Without it, a failure for an environmental reason would have read as caught.

### Step 10: live, Claude Code 2.1.274

| Case | Before | After |
| --- | --- | --- |
| A — `Read` inside cwd, deny rule on its name | leaked | refused, `DENIED` logged |
| B — `Read` outside cwd, no rule | evaluated twice | ran once, `RUN`, no `ALARM` |
| C — `EnterWorktree` under `--dry-run` | real worktree created | narrated; `git worktree list` shows only the main tree |
| D — `_decide` forced to raise | leaked | refused, `ERROR` logged |
| Repro — outside-dir control | refused | refused |
| Repro — natural request, model used `Bash cat` | refused | refused |

`ALARM` did not fire in any case, so decision 5's premise — the callback is
unreachable when the hook always answers — holds on this CLI; the stop rule
did not trigger. C also confirmed decision 7 live: `ToolSearch` ran, so the
refusal landed on `EnterWorktree` and the model narrated the call it meant.

## Rollback

`git revert` the merge commit. For an emergency switch-off without a revert:
in `_options()`, drop the `hooks=` argument and set `can_use_tool=self._decide`
with a `ctx` parameter added back — that is exactly today's wiring, with its
known gap. Nothing persists; no config key is added or renamed; the transcript
gains line tags (`CONFIRM`, `ERROR`, `ALARM`) that readers of
`omarchy-voice log` ignore if unknown.
