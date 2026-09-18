---
status: draft
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
   `self.transcript.append(f"RUN     {description}")` line, add:

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
   → verify by: each new test fails if step 3 is reverted.

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
| `Read`, `Glob`, `Grep` | allow — a dry run can still look |
| `SomeNewTool` (unknown) | deny — fails closed |
| `mcp__ai-mirror__input` | deny |
| `mcp__omarchy__run_shell` | allow, and `policy.check` not called — `Executor` narrates it |
| A denied command, e.g. `sudo rm -rf /` | deny with the **refused** message, not the dry-run one — ordering holds |
| A gated command, e.g. `reboot` | deny with the **confirm** message, and `subject.pending` set |
| Every row above with `Config(dry_run=False)` | exactly today's behaviour |
| `DRY_RUN_READS` vs `_PATH_TOOLS` | no name is a reader in one and a writer in the other |

Manual, on this host:

```
omarchy-voice say --dry-run "write a file called /tmp/dryrun-probe containing hello"
test -e /tmp/dryrun-probe && echo FAIL || echo PASS
```

Expected `PASS`, and a reply that describes writing the file rather than
claiming it was written or reporting a broken machine. If the model instead
retries or announces failure, that is the "routes around the refusal" risk
showing up — record what it said in the PR, do not paper over it by loosening
the gate.

## Rollback

The change is one branch, one `if` block, one module-level constant and one
test class. `git revert` the merge commit, or drop the `if` block: with it
gone, `_gate` returns to allowing whatever the deny and confirm rules allow,
which is today's behaviour. Nothing persists, no state is migrated, no config
key is added or renamed, and `dry_run = false` — the shipped default — takes
the same path before and after, so a revert cannot strand anyone.
