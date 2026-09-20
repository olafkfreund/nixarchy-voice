---
status: approved
issue: 24
spec: spec/2026-09-20-24-empty-versus-unknown.md
---

# Plan: a failed hyprctl query must not read as an answer

Branch `fix/24-empty-versus-unknown`, already carrying the intent and the spec.
A deviation updates this file in the same commit as the code.

## The approved decisions, in full

Implementable without opening the intent or the spec.

**The bug.** `_query_json` (`tools.py:2407-2416`) returns `[]` for a failed
`hyprctl`, unparseable output, and a genuinely empty desktop alike. Callers
cannot tell them apart and three read emptiness as a positive fact.

**Demonstrated**, with the baseline query failing and later ones succeeding:

```
_await_new_window(before=set(), timeout=1.0, hint="foot")
  -> 0xold   ("a window you had open")
```

A window that had been open all along, returned as the one just launched — so
`compose_windows` tiles a window the user was working in and `web_search` OCRs
it and reports its contents as the results.

**The three sites, and the thirteen left alone:**

| site | what `[]` means to it | consequence |
|---|---|---|
| `wait_for(window_gone)` (`tools.py:3233`) | the window closed | the model proceeds on a precondition that never held |
| `_await_new_window` baseline (`2551`, `2909`) | nothing was open before | a pre-existing window claimed as the launched one |
| `_dismiss_browser_error_dialogs` (`3029`) | no dialogs | harmless; it retries — **left as it is, deliberately** |

The other thirteen fail closed and are not touched.

**The shape.** A companion `_query_rows(kind) -> tuple[list[dict], str | None]`,
not raising. Thirteen correct call sites would otherwise each grow handling for
a case they already survive — thirteen chances to write a wrong `except` into
working code. A bad `except` fails silently; a new caller reaching for the
swallowing version is caught by review. `_query_json` becomes a wrapper so
there is one implementation.

**A behaviour change this introduces:** a wait that hits a query failure stops
and reports rather than polling on, so a single `hyprctl` timeout under load no
longer recovers on the next poll.

## Steps

**1. `tools.py`: `_query_rows`, and `_query_json` as its wrapper.**
Three distinct messages — the trimmed stderr for a failed call, and one for
output that is not JSON or not a list. `_query_json` becomes
`return self._query_rows(kind)[0]`, and its docstring names the three callers
that must not use it and says what it discards.
→ verify by test 3 (an honestly empty result is unchanged) and by the suite
passing untouched, since every existing caller still goes through it.

**2. `tools.py`: `wait_for`'s two window branches stop on a failure.**
`tools.py:3233-3235`. The message says the query failed and the condition is
unknown — **not** that the window is still open, which is the same guess in the
other direction and the reason this bug exists.
→ verify by tests 1 and 5.

**3. `tools.py`: the two baselines refuse to launch on a failure.**
`compose_windows` (`tools.py:2551`) and `_search_window` (`2909`). Both take
the baseline immediately before spawning; on failure they do not spawn.

The launch is the irreversible half — once a window is open, no later decision
un-opens it — so the check goes before the spawn and not after.
→ verify by test 2.

**4. `_await_new_window`'s own loop: retry, do not stop.**
`tools.py:2373`. Different from step 2 on purpose: the window may still be
coming, and the deadline already bounds the loop. A failure here skips that
iteration.
→ verify by test 6.

**5. `_dismiss_browser_error_dialogs`: no change.** Listed so the absence is a
decision and not an oversight. A comment says why.
→ verify by reading it.

## Tests

`tests/test_reach.py` for the wait cases, `tests/test_web.py` for the launch
baseline. The seam is `_query_json` / `_query_rows` on the executor, as the
existing tests already stub.

1. **The bug in the issue.** `wait_for(what="window_gone")` with a failing
   query returns a result naming the failure and **not** containing "closed".
   Fails against today's code.
2. **The bug the audit found.** With the baseline query failing, `web_search`
   does not spawn — `_shell` is never called with an `omarchy launch` argv —
   and no pre-existing window comes back as the launched one. Fails against
   today's code.
3. **An honestly empty desktop still reads as empty.** A *successful* query
   returning `[]` behaves exactly as today at all three changed sites. This is
   the test that stops the fix turning into "treat empty as broken", which
   would be the same bug with the sign flipped.
4. **The thirteen unchanged callers are unchanged**, by the existing suite
   passing without edits.
5. **A failed wait stops rather than spinning**, asserted on the number of
   queries issued — one, not the fifteen the budget allows.
6. **A failed query inside `_await_new_window` is a retry**, asserted by a
   sequence where the first loop query fails and a later one succeeds, and the
   window is still found.

Commands:

```
nix develop -c python3 -m unittest discover -s tests   # green; 680 before
nix flake check                                        # green
nix develop -c python3 -m omarchy_voice verify-gate    # exit 0
```

`nix develop` is required — outside it `libxkbcommon` is missing and eleven
`test_keys` cases fail for unrelated reasons.

**Tests 1 and 2 must be confirmed failing against the pre-change `src/`.** Two
tests in this file have passed for the wrong reason this week — the #26
geometry test asserted only the first read and stayed green against a broken
retry — so a regression test that has not been seen to fail proves nothing.

## Deviation: the test seam moved, and the plan said it had not

The plan's Tests section said "the seam is `_query_json` / `_query_rows` on the
executor, as the existing tests already stub". That was wrong, and it cost the
detour worth recording.

Making `_query_rows` the primitive meant the three moved callers no longer go
through `_query_json` — so **eight tests that stubbed the wrapper silently
started reaching the real `hyprctl`**, and the suite went from 16s to 33s while
they spun against a live compositor. Tests that stub a wrapper the code has
stopped calling are exactly the "green for the wrong reason" failure this
branch's own plan warns about, two paragraphs further down.

Fixed by moving every stub of a changed path onto `_query_rows`: a
`stub_queries` helper in `tests/test_reach.py`, `_query_rows` on
`SearchingExecutor`, and the `mock.patch.object` forms in `tests/test_compose.py`.
The thirteen unchanged callers keep stubbing `_query_json`, correctly, because
that is still the seam for them.

## Rollback

One branch, one file of source. No state, no dependency, no schema, no
packaging: `git revert` the merge and restart the daemon.

Steps are independent — step 1 is additive, and steps 2, 3 and 4 each touch one
call site. Reverting any one leaves the others correct.

The thing to watch after a partial revert is step 1: steps 2–4 depend on
`_query_rows` existing, so it is the one to keep.
