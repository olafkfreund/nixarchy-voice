---
status: draft
issue: 7
intent: intent/2026-09-18-7-pretooluse-gate.md
---

# Spec: the policy gate must see every tool call

## Evidence this design rests on

The intent asked that the spec not be approved on documentation alone. Before
writing this, a throwaway spike added a `PreToolUse` hook to the real
`ClaudeBrain` and counted who was consulted for each call. Claude Code
2.1.274, claude-agent-sdk 0.2.152, live model:

| Case | Hook returns | Hook saw | `_gate` saw | Outcome |
| --- | --- | --- | --- | --- |
| A. `Read` inside cwd — the reproduced bypass | `deny` | `Read` | nothing | **blocked**, nothing leaked |
| B. `Read` outside cwd | no decision | `Read` | `Read` | ran — **evaluated twice** |
| B2. same | explicit `allow` | `Read` | nothing | ran — **callback skipped** |
| C. `EnterWorktree`, dry run, current `main` | no decision | `ToolSearch`, `EnterWorktree` | nothing | **a real worktree was created** |
| D. `Read` inside cwd | *raises* | `Read` | nothing | **ran — a crashing hook fails open** |

Five things follow, and the design is built from them:

- **A:** a hook deny blocks a call the permission system auto-approves. The
  direction works on this CLI.
- **C:** auto-approval reaches beyond reads. On `main` today, a dry run
  creates git worktrees, because `EnterWorktree` never reaches `_gate`. The
  hook saw it. This is #5's gap, now confirmed rather than predicted.
- **B vs B2:** with no decision, the call goes on to `can_use_tool` and is
  evaluated a second time. With an explicit `allow`, the callback is skipped.
- **D:** an exception in the hook is logged by the CLI and the call runs.
  Not a refusal — nothing. For an auto-approved call there is no second line
  of defence.
- **C, incidentally:** `ToolSearch` is auto-approved too, and will reach the
  gate for the first time.

## Design

### The hook owns every decision

`_options()` (`claude_backend.py:342`) gains one unfiltered `PreToolUse` hook:

```python
hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[self._pre_tool_use])]}
```

`matcher=None` matches every tool, including ones Claude Code adds later — the
spike saw `Read`, `ToolSearch` and `EnterWorktree` through it.

**The hook always returns an explicit decision, `allow` or `deny`, never
none.** That is B2's result used on purpose: an explicit decision is final, so
nothing is evaluated twice, and the one-shot confirmation cannot be spent in
one place and re-held in another.

```python
{"hookSpecificOutput": {"hookEventName": "PreToolUse",
                        "permissionDecision": "allow" | "deny",
                        "permissionDecisionReason": <message>}}
```

The model reads the reason — case A's reply quoted it — so today's refusal
messages carry over word for word.

### The decision logic moves, it does not change

Today's `_gate` body becomes `_decide(tool, tool_input) -> (allowed, message)`,
a plain function the hook calls. Same order as now:

1. `mcp__omarchy__*` → allow. `Executor` runs policy and dry-run itself.
2. Confirmed replay (`_confirmed`, one-shot, spent on use) → dry-run check →
   allow.
3. `Policy.check` → deny, or hold for confirmation.
4. Dry-run check (`_dry_run_refusal`, unchanged from #5) → deny.
5. Allow.

Moving it rather than rewriting it is what lets every existing gate test keep
asserting the same behaviour.

### A crashing hook fails closed

The hook's entire body sits inside `except Exception`, which logs `ERROR` with
the exception and returns an explicit `deny`. Case D is why this is not
optional: without it, one bug in a regex or in `describe_tool` lets every
auto-approved call through, silently.

### `can_use_tool` becomes an alarm

Because the hook always decides, B2 says `can_use_tool` is never reached in
normal operation. So it stops being a gate and becomes a tripwire: if it runs,
the hook did not decide, and that is a fault. It logs `ALARM` with the tool
name and returns `deny`.

It stays registered rather than being removed, so an undecided call meets a
refusal we wrote rather than whatever the CLI's default for an unanswered
prompt turns out to be.

### Every outcome is logged

The decision point writes one transcript line for every call, whatever the
outcome: `RUN`, `DENIED`, `HOLD`, `DRYRUN`, plus two new ones:

- `CONFIRM` when a confirmed replay is allowed, together with `on_action` and
  `_actions`. Today that branch returns before all three, so a confirmed
  reboot leaves no record (intent, open question 3 — folded in, as approved).
- `ERROR` / `ALARM` as above.

Auto-approved reads now appear as `RUN read <path>`, which closes the audit
gap the intent describes.

These are pre-execution records: they say what was authorised, not what
succeeded. Recording results would need a `PostToolUse` hook; see
Alternatives rejected.

### `ToolSearch` joins the dry-run allowlist

`DRY_RUN_READS` becomes `{"Read", "WebSearch", "ToolSearch"}`. `ToolSearch`
was invisible to the gate until now; with the hook, a dry run would refuse it.
It only loads tool schemas — it cannot act — and refusing it makes the dry-run
narration land on the wrong call: "would have run: ToolSearch", instead of
"would have run: EnterWorktree", the thing the model actually meant to do. It
is in CLI 2.1.274's declared tool list and was observed in case C, which
meets #5's rule of confirmed-not-remembered.

### Correct the claims

- `_gate`'s docstring (`claude_backend.py:236`) currently says "Every tool
  call Claude Code makes, through our policy". The hook's docstring says it,
  and says why a callback could not.
- README:257 ("gated by the same deny/confirm policy as every other tool
  here, wired through the SDK's `can_use_tool` callback") names the wrong
  mechanism; it names the hook.
- `tests/test_claude_backend.py` module docstring, same claim.

## Alternatives rejected

**Hook returns no decision on allow, `can_use_tool` returns allow** — the
shape the second-opinion review proposed. Rejected on case B: a hook with no
decision sends the call on to the callback, so a prompt-worthy call is seen
twice. The review's own mitigation, a callback that allows without looking,
fixes the double hold, but then the callback silently permits anything the
hook failed to decide. Explicit decisions plus an alarm callback remove the
second evaluation *and* make the fault visible.

**Keep policy in both hook and callback.** Rejected: the hook spends a
one-shot approval, the callback sees the replay as a fresh gated call and
holds it again, and the user is asked twice.

**Empty working directory.** Shrinks the auto-approved read scope to nothing
useful. Rejected: it forces nothing through the gate — case C's
`EnterWorktree` ignores cwd scope entirely — and it changes relative paths and
`Bash`'s working directory for the model.

**`settings={"permissions": {"ask": [...]}}`.** Routes named tools through
the callback. Rejected: per-tool, so a new tool arrives unlisted, and
Anthropic documents sandbox auto-approval overriding a bare `Bash` ask rule.
The opposite of fail closed.

**`permission_mode` changes.** No mode supplies our policy. `acceptEdits`
auto-approves writes; `bypassPermissions` shadows the callback, as the
existing comment at `claude_backend.py:343` says.

**A `PostToolUse` hook for result-side logging.** Would let the log say what
*succeeded* rather than what was authorised. Rejected for now: the log has
never claimed more than authorisation, and the intent's gap is calls with no
record at all. Worth doing if someone needs the log to prove execution.

## Risks

- **Hook semantics are the CLI's, and can change.** Everything above was
  observed on 2.1.274. A CLI upgrade could change how a hook `deny`, an
  explicit `allow` or a hook exception behaves. The live verification below
  is the defence, and should be re-run when the CLI moves.
- **The alarm could fire on a legitimate interactive tool.** If some tool
  still reaches `can_use_tool` after an explicit hook `allow`, it is now
  refused. Fails closed, and `ALARM` in the log names it. Not observed.
- **Behaviour changes for auto-approved calls.** They meet the deny, confirm
  and dry-run rules for the first time. On shipped defaults the deny list
  targets commands, so ordinary reads are unaffected — but a dry run will now
  refuse `EnterWorktree`, `TaskStop` and the rest, which is the point.
- **The hook runs on the SDK's event loop.** `_decide` does what `_gate` did,
  on the same loop, so there is nothing new to block on.
- **Unit tests still cannot prove the CLI calls the hook.** They test
  `_decide` and the hook's output shape. Only the live runs below test the
  wiring — the same gap that let this bug exist.

## Verification

- `nix flake check` — all checks passed.
- Unit tests, `tests/test_claude_backend.py`:
  - Every existing gate test passes against `_decide`, same assertions.
  - The hook returns an explicit `allow` or `deny` for every call, never none,
    in the documented output shape.
  - A deny rule refuses a `Read` through the hook.
  - An exception inside the decision returns `deny` and logs `ERROR`.
  - `can_use_tool` returns `deny` and logs `ALARM`.
  - Confirmed replay through the hook: allowed once, logged `CONFIRM` with
    `on_action` and `_actions`, then held again.
  - Dry run through the hook refuses `EnterWorktree` and allows `ToolSearch`.
- **Live, against the installed CLI — the check that matters.** Re-run spike
  cases A–D against the implementation instead of the spike:
  - A: `Read` inside cwd with a deny rule → refused, no leak, `DENIED` logged.
  - B: `Read` outside cwd, no rule → runs once, `_gate`/alarm never reached,
    `RUN` logged.
  - C: `EnterWorktree` under `--dry-run` → refused, **no worktree created**.
  - D: a forced exception in `_decide` → refused, no leak, `ERROR` logged.
  - Plus the original reproduction table from the intent, every row now
    "consulted: yes".
