---
status: approved
issue: 5
spec: spec/2026-09-18-5-dry-run-claude-backend.md
---

# Plan: a dry run must not act

## The decisions being implemented

Carried over from the approved spec so this file stands alone.

**The bug.** `ClaudeBrain._gate` (`claude_backend.py:213`) never reads
`config.dry_run`. It runs the deny and confirm regexes, returns
`PermissionResultAllow()`, and the SDK executes Claude Code's own `Bash`,
`Write` or `Edit` for real. `_options()` sets no `allowed_tools` or
`disallowed_tools`, so nothing else is in the way. Meanwhile `Executor`
honours the setting at `tools.py:1309` and narrates `[dry-run] would run: …`.
Same config, same command, two answers. `cmd_say` prefers `ClaudeBrain`
whenever the CLI is installed (`cli.py:118`), so this is the default path.

**The fix.** A dry-run check in `_gate`, placed *after* the deny and confirm
checks so it can only refuse more than they do, never less. Under `dry_run`, a
tool not on a read-only allowlist is denied with a message the model reads as
narration.

**Five decisions that are settled and must not be relitigated while
implementing:**

1. **An allowlist, `DRY_RUN_READS`, not a denylist.** A tool Claude Code adds
   next month must arrive as "not known to be safe". Fail closed.
2. **`Bash` is denied outright**, never classified by its command text.
   `cat x > y` is a write spelled as a read; classifying command text is the
   deny list's known weakness, and putting a dry run's whole promise on it is
   the thing this change exists to avoid.
3. **`allow_shell` is untouched.** Same root cause, out of scope: denying
   native `Bash` would contradict `README:256` and change behaviour for every
   existing user. Product decision, not a bug fix.
4. **The check lives in `_gate`, not in `_options()` via `disallowed_tools`.**
   `disallowed_tools` is cleaner in principle but splits what-may-run across
   two places, and `mcp_server.py:12` says the gate is the one place that must
   not drift. Revisit only if #7 concludes `_gate` is skippable, in which case
   all three rules move together.
5. **`mcp__omarchy__*` keeps its existing short-circuit** at
   `claude_backend.py:220`. Those route through `Executor`, which narrates
   dry-run itself; checking here would narrate twice.

**Known limit, accepted:** `_gate` can only permit or refuse — the SDK gives
it no way to substitute a fake result. So a dry run here is a *refused action
described*, where `Executor`'s is a simulated one. The model may retry or
route around it; the message wording is the only lever, and it is the same
lever the existing deny branch uses. If this proves unworkable in practice,
the reserve option is refusing to start the backend at all under `dry_run` —
do not invent a third approach without a new spec.

## Steps

1. **Establish the real tool names** before writing the set. The list in the
   spec is drawn from what Claude Code is known to offer; this repo names none
   of these tools, so none of it is verified. Read the installed CLI's own
   tool surface — the `claude-agent-sdk` package in the dev shell, and the
   CLI's docs for the version `cli_path(config)` resolves to — and reconcile.
   → verify by: a written list of confirmed names, with anything unconfirmed
   **left out**. Omitting a reader costs a needless refusal; admitting a
   writer is the bug. Record what was checked and how in the commit message,
   because no test can prove this and the next person will want to know.

2. `src/omarchy_voice/claude_backend.py`: add `DRY_RUN_READS` next to
   `_PATH_TOOLS` (line 69), from step 1's confirmed list, with the comment
   explaining it is an allowlist and why. Reuse the reader half of
   `_PATH_TOOLS` (`Read`, `Glob`, `Grep`) rather than restating those names.
   → verify by: `DRY_RUN_READS` contains no name that `_PATH_TOOLS` maps to a
   writer (`write`, `edit`).

3. `src/omarchy_voice/claude_backend.py`: in `_gate`, after the
   `except NeedsConfirmation` block and before the
   `self.executor.transcript.append(f"RUN     {description}")` line, add:

   ```python
   if self.config.dry_run and tool not in DRY_RUN_READS:
       self.executor.transcript.append(f"DRYRUN  {description}")
       return PermissionResultDeny(
           message=(f"[dry-run] would have run: {description}. Nothing was "
                    "done, because this is a dry run. Do not try another "
                    "route around it — tell the user what you would have "
                    "done."),
           interrupt=False)
   ```

   Placement is the whole point: after deny and confirm, so a denied action is
   still reported as denied and a gated one is still held, rather than all
   three collapsing into "dry run".
   → verify by: the existing deny and confirm tests pass unchanged.

4. `tests/test_claude_backend.py`: add a `DryRunTests` class with the cases in
   the Tests section below.
   → verify by: each test asserting the fix fails if step 3 is reverted; the
   regression guards (ordering, reads, normal path) pass either way, by design.

5. Run the manual check in the Tests section. This is the one that would have
   caught the original bug, and no automated test replaces it.
   → verify by: the file does not appear, and the reply says what it would
   have done.

6. Open the PR linking `intent/`, `spec/` and `plan/`, with `Closes #5`.
   → verify by: `nix flake check` green on the branch, PR body links all
   three files.

## Tests

```
nix develop --command python -m pytest tests/test_claude_backend.py -q
nix flake check --no-write-lock-file
```

Expected: all pass; the claude-backend file gains the cases below and loses
none.

New cases, all against `Config(dry_run=True)` unless stated:

| Case | Expect |
| --- | --- |
| `Bash {"command": "touch /tmp/x"}` | deny, message contains `would have run` |
| `Write {"file_path": "/tmp/x", …}` | deny |
| `Edit {"file_path": "/tmp/x", …}` | deny |
| `Read`, `WebFetch`, `WebSearch` | allow — a dry run can still look |
| A gated command, confirmed, then replayed | deny with the dry-run message — a yes is not an exemption |
| Any refusal | `DRYRUN  <description>` in the transcript, nothing in `_actions` |
| `SomeNewTool` (unknown) | deny — fails closed |
| `mcp__ai-mirror__input` | deny |
| `mcp__omarchy__run_shell` | allow, and `policy.check` not called — `Executor` narrates it |
| A denied command, e.g. `sudo rm -rf /` | deny with the **refused** message, not the dry-run one — ordering holds |
| A gated command, e.g. `reboot` | deny with the **confirm** message, and `subject.pending` set |
| Every row above with `Config(dry_run=False)` | exactly today's behaviour |
| `DRY_RUN_READS` vs `_PATH_TOOLS` | no name is a reader in one and a writer in the other |

Manual, on this host:

```
nix develop --command python -m omarchy_voice --dry-run say --no-confirm \
  "write a file called /tmp/dryrun-probe containing hello"
test -e /tmp/dryrun-probe && echo FAIL || echo PASS
```

Expected `PASS`, and a reply that describes writing the file rather than
claiming it was written or reporting a broken machine. If the model instead
retries or announces failure, that is the "routes around the refusal" risk
showing up — record what it said in the PR, do not paper over it by loosening
the gate.

## Deviations during implementation

Recorded in the same commit as the code, as the workflow requires. None
changes an approved decision; each is the plan meeting the code.

1. **The allowlist is `{"Read", "WebFetch", "WebSearch"}` — half the spec's
   list does not exist.** Step 1 read the installed CLI's own tool list from
   the `init` message of `claude -p … --output-format stream-json`, Claude Code
   2.1.274. It declares 30 built-in tools, and `Glob`, `Grep` and `TodoWrite`
   are not among them; search goes through `Bash` now. Tools that only *sound*
   read-only (`ReadMcpResourceTool`, `ListMcpResourcesTool`, `LSP`,
   `ToolSearch`) were left out under the rule this step set: unconfirmed means
   omitted. This is the case step 1 existed for.

2. **Step 2 does not reuse `_PATH_TOOLS`.** Its reader half is `Read`, `Glob`,
   `Grep`, and two of those match nothing in this CLI. `DRY_RUN_READS` is
   standalone; the consistency test still asserts the two never disagree on
   what writes.

3. **`_gate` has two ways of saying yes, and the plan covered one.** The
   confirmed-replay short-circuit returns `Allow` before any check. Under
   `dry_run`, a gated action was held, the user confirmed, and the replay ran
   it for real — reproduced before fixing. The check is therefore a method,
   `_dry_run_refusal`, called from both exits, so the rule exists once and
   cannot be skipped by taking the other door. The approval is still spent.
   Covered by `test_saying_yes_does_not_make_a_dry_run_act`.

4. **Four existing tests changed, not zero.** The step 3 checkpoint said the
   existing tests would pass unchanged. The deny and confirm tests it named
   did. Four others failed, because the `brain()` harness runs every gate test
   under `dry_run=True` — a safety default that was free only because dry-run
   did nothing to the gate. Those four assert normal-operation behaviour, so
   they now say `dry_run=False`, and `brain()` accepts the override. Nothing
   executes in them either way; `_gate` only returns a permission.

5. **The manual check's command order was wrong.** `--dry-run` is a global
   flag and goes before the subcommand, and it has to run the branch's code
   through the dev shell — the installed binary is the pre-fix build.

### What the manual check found

With the fix, through the real claude-code backend:

```
reply   I would have written "hello" to /tmp/dryrun-probe — this is a dry
        run, so nothing was actually written.
PASS: no file
```

The same probe through the installed pre-fix build:

```
action  printf 'hello' > /tmp/dryrun-probe && cat /tmp/dryrun-probe
reply   Wrote "hello" to /tmp/dryrun-probe.
BUG REPRODUCED: dry run wrote the file
```

It wrote through `Bash`, as a redirect, not through `Write`. That is decision
2's case observed rather than argued: a classifier reading command text for
dangerous verbs would have let `printf` through.

The "model routes around the refusal" risk did not appear on this run. One
run is not evidence it never will.

## Rollback

The change is one branch: one module-level constant, one method
(`_dry_run_refusal`) called from `_gate`'s two allow exits, one test class,
and a `dry_run=False` on four existing tests. `git revert` the merge commit.
For an emergency switch-off without a revert, make `_dry_run_refusal` return
`None` on its first line: both call sites then fall through to `Allow`, and
`_gate` is back to allowing whatever the deny and confirm rules allow, which
is today's behaviour. Nothing persists, no state is migrated, no config
key is added or renamed, and `dry_run = false` — the shipped default — takes
the same path before and after, so a revert cannot strand anyone.
