---
status: approved
issue: 24
intent: intent/2026-09-20-24-empty-versus-unknown.md
---

# Spec: a failed hyprctl query must not read as an answer

## The intent's two open questions, answered

The approver approved without answering them. One of the two was not a
judgement call — it was a claim I had not tested — so it is answered here by
demonstration. The other is a design choice, decided here with the reasoning
exposed so it can be rejected at this gate rather than discovered later.

### Q2: how far does the audit go? Further than the issue says.

The intent called the `before` baselines "plausible, not yet demonstrated".
Demonstrated now, with the baseline query failing and later queries
succeeding:

```
_await_new_window(before=set(), timeout=1.0, hint="foot")
  -> 0xold   ("a window you had open")
```

`_await_new_window` returned a window that had been open all along as the one
just launched. That is not a missed detection. `compose_windows` would tile a
window the user was working in, and `web_search` would OCR it and report its
contents as the search results.

So `window_gone` is not the only real case, and the sweep is not optional.
Three sites invert or baseline on emptiness:

| site | what `[]` means to it | consequence |
|---|---|---|
| `wait_for(window_gone)` (`tools.py:3233`) | the window closed | the model proceeds on a precondition that never held |
| `_await_new_window` baseline (`2551`, `2909`) | nothing was open before | a pre-existing window is claimed as the launched one |
| `_dismiss_browser_error_dialogs` (`3029`) | no dialogs | a dialog stays up; harmless, it retries |

The other thirteen fail closed — `_resolve_window` says "nothing is open",
`_visible_workspaces` returns an empty set so reads refuse, the close guard
declines to close. They are left alone.

### Q1: a companion, not raising.

`_query_json` keeps its signature and its swallowing. A new
`_query_rows(kind) -> tuple[list[dict], str | None]` returns the failure, and
the three sites above use it.

Raising is the more rigorous answer and it was rejected on a specific risk, not
on effort: sixteen call sites, thirteen of which are correct as they stand,
would each grow handling for a case they already survive. That is thirteen
chances to write a wrong `except` into code that works today, in a file where
two tests have already passed for the wrong reason this week. The failure mode
of the companion — someone writes a new caller using the swallowing version
without thinking — is caught by review; the failure mode of a bad `except` is
silent.

`_query_json` becomes a two-line wrapper over `_query_rows` so there is one
implementation, and its docstring says plainly what it discards and who should
not use it.

## Design

### 1. `_query_rows`

```python
def _query_rows(self, kind: str) -> tuple[list[dict], str | None]:
    """Rows, or (,) why the query could not be answered."""
```

Three failures, each with its own message, because the caller's user-facing
wording differs and "it failed" is not actionable:

- the shell call failed or timed out → the trimmed stderr
- the output did not parse → "hyprctl returned something that is not JSON"
- it parsed to something that is not a list → the same

`_query_json` becomes `return self._query_rows(kind)[0]`.

### 2. `wait_for`, both window branches

On a query failure the wait **stops** and says so. It does not keep polling: if
`hyprctl` is not answering, the next fifteen attempts will not answer either,
and a wait that silently burns its budget is worse than one that reports.

The message says what happened and that the condition is unknown — not that
the window is still open, which would be the same guess in the other
direction.

### 3. `_await_new_window`'s baseline

The baseline is taken by the two callers (`tools.py:2551`, `2909`) before
launching, and passed in. They use `_query_rows`; on failure they do not
launch. Launching with a broken baseline is how a pre-existing window gets
claimed, and the launch is the irreversible half — a window has been opened by
then whatever the caller decides next.

`_await_new_window` itself also queries in its loop. There, a failure is a
retry rather than a stop: the window may still be coming, and the deadline
already bounds it.

### 4. `_dismiss_browser_error_dialogs`

Left as it is, and said so here so it reads as a decision. It returns a count,
its caller treats zero as "nothing to do", and the path retries. A failed query
costs one loop, not a wrong answer.

## Alternatives rejected

- **Raise from `_query_json`.** See Q1 above.
- **Return `None` on failure.** Every caller that iterates gets a `TypeError`
  instead of a message, at a distance from the cause.
- **Retry inside `_query_json`.** Hides the failure rather than reporting it,
  and doubles the latency of the path that is already the slow one. The
  callers that should retry are the ones with a deadline, and they have one.
- **Fix only `window_gone`**, as the issue title says. The demonstration above
  is why not.

## Risks

- **A wait that now stops used to keep going.** If `hyprctl` fails
  intermittently — a single timeout under load — a wait that would previously
  have recovered on the next poll now ends. The wait reports a fact rather
  than a wrong answer, which is the trade this issue is about, but it is a
  behaviour change and it needs a test either way.
- **Thirteen callers unchanged is a judgement.** It rests on reading each one;
  if one of them inverts emptiness in a way not spotted, it keeps the bug. The
  table above is the audit, and it is the thing to check rather than trust.
- **`_query_rows` is one more way to query.** Two functions where there was
  one. Mitigated by `_query_json` being a wrapper rather than a parallel
  implementation, and by its docstring naming the three callers that must not
  use it.

## Verification

1. **The bug in the issue.** `wait_for(window_gone)` with a failing query
   reports the failure and does **not** say the window closed. Fails against
   today's code.
2. **The bug the audit found.** With the baseline query failing,
   `compose_windows` and `web_search` do not launch, and no pre-existing
   window is returned as the launched one. Fails against today's code — the
   demonstration above is the shape of the test.
3. **An honestly empty desktop still reads as empty.** A successful query
   returning `[]` behaves exactly as it does now, everywhere. This is the test
   that stops the fix becoming "treat empty as broken".
4. **The thirteen unchanged callers are unchanged**, by the existing suite
   passing untouched.
5. **A mid-wait failure is a stop, not a spin**, asserted on the number of
   queries issued.
6. **Suite and gate.** `nix develop -c python3 -m unittest discover -s tests`
   green (680 at the time of writing), `nix flake check` passes,
   `omarchy-voice verify-gate` exits 0.
