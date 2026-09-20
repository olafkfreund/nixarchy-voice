---
status: approved
issue: 24
author: olafkfreund
---

# Intent: a failed hyprctl query must not read as an answer

Closes #24.

## Problem

`_query_json` (`src/omarchy_voice/tools.py:2407-2416`) turns every failure into
an empty list:

```python
result = self._shell(["hyprctl", "-j", kind], timeout=5, limit=1 << 22)
if not result.ok:
    return []
try:
    data = json.loads(result.output)
except json.JSONDecodeError:
    return []
return data if isinstance(data, list) else []
```

A timed-out `hyprctl`, a compositor mid-reload, and a desktop with genuinely no
windows all produce `[]`. The caller cannot tell them apart, and some callers
read `[]` as a positive fact.

The clearest is `wait_for(what="window_gone")` (`tools.py:3233-3235`):

```python
clients = self._query_json("clients")
exists = any(_window_matches(c, value) for c in clients)
found = exists if what == "window" else not exists
```

`not exists` on an empty list means "the window closed". So a `hyprctl` that
failed is reported to the model as the thing it was waiting for having
happened — and the model moves on to the next step of a request whose
precondition never held.

This is the same class of bug the write path already guards against, and the
reason is written down at `_dispatch_lua`:

> hyprctl reports a missing target as `warning: ... not found` on stdout with a
> zero exit code, so it read as success. The model was told a window had been
> closed when nothing had happened, and moved on to the next step of a request
> whose first step had silently failed.

That lesson was applied to dispatches and not to queries.

Found while reviewing the dispatch bypass (#22) and split out to keep that
security fix narrow.

## Proposed outcome

- A query that failed is distinguishable from a query that legitimately
  returned nothing, at every call site that treats the difference as
  meaningful.
- `wait_for(what="window_gone")` reports a failed query as a failed query, not
  as the window having closed.
- Call sites where the distinction genuinely does not matter keep reading as
  simply as they do now. Sixteen call sites should not each grow error
  handling for a case most of them are already safe about.

## Affected users and systems

- `src/omarchy_voice/tools.py` — `_query_json` and its sixteen callers, of
  which only a few invert the meaning of empty.
- Everything that waits on or reads the desktop, since all four entry shapes
  share one `Executor`.
- Nothing outside this file; no config, no packaging, no schema.

## Constraints

- **Most callers are already safe and must not be disturbed.** An empty
  `clients` list makes `_resolve_window` say "nothing is open",
  `_visible_workspaces` return an empty set (so reads refuse), and the
  close-by-address guard decline to close. Those fail closed. Only the callers
  that read emptiness as a positive fact need to change.
- The mocking seam stays `Executor._shell` / `_query_json`
  (`tests/test_reach.py:44-64`); a change that breaks how the suite fakes
  queries would touch far more than this bug.
- A failure must reach the model as a *fact it can act on* — "the query
  failed" — not as an exception that becomes a generic tool error, and not as
  silence.

## Open questions

1. **Where does the distinction live?** Either `_query_json` grows a companion
   that returns the failure, and the few callers that care use it; or it starts
   raising and every caller gains handling. The first is a much smaller diff
   and leaves the safe callers alone; the second makes it impossible to write a
   new caller that ignores the difference by accident. Which matters more here?

2. **How far does the audit go?** `window_gone` is certain. The `before` sets
   in `compose_windows` and `web_search` (`tools.py:2551`, `2909`) use `[]` as
   a baseline, so a failed query there makes every existing window look newly
   opened — plausible, not yet demonstrated. Fix what is proven, or sweep all
   sixteen?
