---
status: draft
issue: 5
intent: intent/2026-09-18-5-dry-run-claude-backend.md
---

# Spec: a dry run must not act

## Design

`_gate` gains a dry-run check, placed after the deny and confirm checks so it
can only ever refuse more than they do, never less. Under `dry_run`, a tool
not known to be read-only is denied with a message the model reads as
narration.

### The read-only set

An allowlist of Claude Code tool names, in `claude_backend.py`:

```python
# Tools that cannot change anything, so a dry run may let them through.
# An allowlist, not a denylist: Claude Code gains tools on its own schedule
# and a new one must arrive as "not known to be safe" rather than as safe.
# Mirrors tools.READ_ONLY_TOOLS, which does the same job for our own tools.
DRY_RUN_READS = {"Read", "Glob", "Grep", "WebFetch", "WebSearch", "TodoWrite"}
```

**The exact membership is confirmed at implementation, not here.** This repo
names none of these tools today, so the list above is drawn from what Claude
Code is known to offer rather than from anything checked in — and an
allowlist is the wrong place to guess, because a misspelled or retired name
silently never matches and a read-only tool is then refused with no clue why.
Step 1 of the plan is to read the installed CLI's own tool list and reconcile
against it. Anything unconfirmed is left out, since the cost of omitting a
reader is a needless refusal, and the cost of admitting a writer is the bug.

This is not a new concept. `tools.READ_ONLY_TOOLS` (`tools.py:37`) is the
same set for our own tools, consumed by the same `not in` test at
`tools.py:1309`. `_PATH_TOOLS` (`claude_backend.py:69`) already splits six of
Claude's tools into readers (`Read`, `Glob`, `Grep`) and writers (`Write`,
`Edit`, `NotebookEdit`) for describing them — the reader half is reused here
rather than restated, and a test asserts the two stay consistent.

`TodoWrite` writes only to the agent's own scratch list, which is why it is
in the set despite the name.

### `Bash` is denied outright under dry-run

Not classified by command text. The deny list's known weakness is that it
matches strings against a shell command, and `README` and the intent both say
so; reusing that weakness somewhere a dry run's whole promise rests on it
would be putting weight on the part we already distrust. `ls` being refused
in a dry run is a small loss. `rm` being allowed because it was spelled
oddly is the bug this issue exists to fix.

### Everything else fails closed

Anything not in the set is denied: `Write`, `Edit`, `Bash`, every
`mcp__ai-mirror__*` call, and any tool nobody has heard of yet. This matches
what `_gate` already does with unknown tools
(`test_an_unknown_tool_is_still_described_and_checked`).

`mcp__omarchy__*` keeps its existing short-circuit at `claude_backend.py:220`
and is unaffected: those route through `Executor`, which does its own
dry-run narration. Checking them here would narrate twice.

### The refusal reads as narration

```
PermissionResultDeny(
    message=(f"[dry-run] would have run: {description}. Nothing was done, "
             "because this is a dry run. Do not try another route around "
             "it — tell the user what you would have done."),
    interrupt=False)
```

Worded like the `Denied` and `NeedsConfirmation` branches beside it, which
already tell the model not to route around a refusal, because it otherwise
treats a denial as an obstacle to solve.

`_gate` can only permit or refuse — the SDK gives it no way to substitute a
fake result — so a dry run on this backend is a refused action described,
where `Executor`'s is a simulated one. The transcript line is `DRYRUN`
alongside the existing `RUN`, `DENIED` and `HOLD`, so `omarchy-voice log`
shows which of the two happened.

### Scope

`allow_shell` is untouched. Same root cause, deliberately out of scope: see
Alternatives rejected.

## Alternatives rejected

**Classify `Bash` by its command text.** Read `ls`, `cat`, `grep` as safe and
refuse the rest. Rejected: that is the deny list's mechanism, applied where
being wrong means a dry run wrote to disk. `cat x > y` is a write spelled as
a read, and the intent's constraint is to fail closed.

**Refuse to start the backend under `dry_run`** — print "dry run is not
supported on the claude-code backend" and exit non-zero. Honest and two
lines. Rejected: it removes the feature from the default backend rather than
fixing it, and it is strictly worse than a dry run that works for reads. Kept
in reserve if the narration proves unworkable in practice.

**Fix `allow_shell` in the same change,** so `allow_shell = false` denies
native `Bash` too. Rejected: `README:256` advertises `Bash` as what this
backend is, so denying it would contradict a documented feature and change
behaviour for every existing user on the shipped default. That is a product
decision, not a bug fix, and it does not belong in a change whose subject is
`dry_run`. #6 handled the reporting half; the rest stays open deliberately.

**Put the check in `_options()` via `disallowed_tools`** instead of in
`_gate`. Cleaner in principle — the model never sees a tool it cannot use,
so it does not waste a turn being refused. Rejected for now: it moves the
dry-run rule away from the deny and confirm rules, giving two places where
what-may-run is decided, and `mcp_server.py:12` says the gate is the one
place that must not drift. Worth revisiting if #7 concludes that `_gate` is
not consulted for every call, since the same argument would then move all
three rules together.

**A `dry_run` denylist rather than an allowlist.** Rejected by the intent's
fail-closed constraint: a tool Claude Code adds next month would arrive
permitted.

## Risks

- **The model may route around the refusal.** Told its write failed, it can
  retry, try a different tool, or report failure to the user as though the
  machine were broken. Mitigated by the message wording, which is the same
  lever the existing deny branch uses; not eliminated. This is the main thing
  to watch once it ships, and the reason the "refuse to start" alternative is
  kept in reserve.
- **A read-then-act chain stops halfway.** A dry run of "tidy my downloads"
  lists the files and is then refused, so the narration is shorter than the
  real run would have been. Accepted: a partial, honest simulation beats a
  complete, false one.
- **The allowlist will go stale, and starts unverified.** Claude Code adds and
  retires tools; a new read-only one will be refused until someone adds it,
  and a retired name sits in the set matching nothing. Failing closed makes
  staleness a nuisance rather than a hole, which is the right direction, but
  no test can catch either case — the truth lives in the installed CLI, not
  in this repo. Hence step 1 of the plan, and hence leaving out anything not
  confirmed there.
- **`dry_run = false` must be untouched.** The whole check sits behind
  `if self.config.dry_run`, and a test asserts the normal path is unchanged.
- **Inherits whatever #7 finds.** If `can_use_tool` is not consulted for
  every call, a dry-run check in `_gate` is skippable exactly as the deny
  check is. This does not block: the dry-run rule would move wherever the
  deny rule moves, and it is strictly better than today either way. Recorded
  here so the connection is not lost. Per the issue author, #7 waits.
- **Only this host was measured.** The behaviour depends on the installed
  `claude` CLI, and `local_engine` was exercised only through unit tests, not
  a live daemon.

## Verification

- `nix flake check` — all checks passed.
- New tests in `tests/test_claude_backend.py`, against `Config(dry_run=True)`:
  - `Bash` is refused, and the message says `would have run`.
  - `Write` and `Edit` are refused.
  - `Read`, `Glob`, `Grep` are allowed, so a dry run can still look.
  - An unknown tool is refused — fails closed.
  - An `mcp__ai-mirror__*` call is refused.
  - `mcp__omarchy__*` is still allowed through to `Executor`, which narrates
    it itself, so it is not narrated twice.
  - With `dry_run=False`, every one of the above behaves exactly as it does
    today.
  - `DRY_RUN_READS` and `_PATH_TOOLS` agree: no tool is a reader in one and a
    writer in the other.
- The deny and confirm paths keep their existing tests unchanged, proving the
  new check was added to them rather than in front of them.
- Manual, on this host: `omarchy-voice say --dry-run` with an instruction
  that would write a file, confirming the file does not appear and the reply
  says what it would have done. This is the check that would have caught the
  bug in the first place, and no automated test replaces it.
