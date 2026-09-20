---
status: draft
issue: 39
author: olafkfreund
---

# Intent: the trace declares a phase it never records, so the one number that would settle #23 is missing

## Problem

`trace.py:41` defines `SUBPROCESS = "subprocess"`, and that is its only
occurrence in the repository. `grep -rn SUBPROCESS src/ tests/ tools/` returns
the definition and nothing else.

Every span actually created is one of five: `LOCK` and `TOOL`
(`tools.py:1497`, `:1501`), `CAPTURE` and `OCR` (`tools.py:1953`, `:1964`,
`:2054`, `:2061`), and `TURN` from `local_engine`. `_shell` and `_query_json`
time nothing, so every `hyprctl` invocation is counted inside the enclosing
`TOOL` span with no way to separate it. A TIMING line reads `tool=4.31s` and
says nothing about how much of that was forking `hyprctl`.

**Why that particular gap matters.** #23 is the approved framing that the
desktop loop is slow in the wrong places — OCR and model turns, not `hyprctl` —
and it set the standard that later changes are argued against p50/p95 task
completion rather than a component benchmark. The trace exists to be that
evidence. But the one measurement that would settle the `hyprctl` question
cannot be read off it.

This is not hypothetical. Reviewing this repository today I proposed replacing
`hyprctl` subprocesses with the raw IPC socket, and ranked it first, before
finding that #23 had already measured and rejected exactly that. A subprocess
share in a TIMING line would have answered it in seconds instead of costing a
review, a Codex round, and two turns of the user's time. The argument will
otherwise be had again, by someone with the same reasonable instinct.

It also matters more now than when the trace was written. #28 has landed, and
its own risks section says "Over-waking costs a `hyprctl` query per event.
During a workspace switch that is a burst." That burst is precisely what a
subprocess phase would make visible, and nothing currently would.

## Proposed outcome

- A phase the code declares is a phase the code records, or it is not declared.
- Someone asking "is `hyprctl` a meaningful share of a task?" can answer it from
  a real task trace rather than from a microbenchmark, and #23's rejection stops
  being re-litigated on instinct.
- Whatever is decided, the trace's own contract — that its phases mean something
  — holds.

## Affected users and systems

- Anyone arguing about desktop-loop performance in this repository, which on
  today's evidence includes future reviewers who have not read #23.
- `src/omarchy_voice/trace.py`, and `tools.py` if a span is added, most likely
  around `_shell` since every subprocess already goes through it.
- No runtime behaviour for users: the trace is off by default
  (`config.trace_timings`, default `False`).

## Constraints

- **Must not become a second measurement standard.** #23 was explicit that
  changes are argued against task completion, not component benchmarks. A
  subprocess number is only useful as a share of a real task, and should not
  become a thing optimised for its own sake.
- **Must stay content-free.** The trace deliberately carries durations and phase
  names only — never window titles, OCR text or tool arguments (#26 decision 3).
  A subprocess phase must not start recording argv.
- Must not slow the untraced path. The trace is off by default and the
  instrumentation is already written to cost nothing when `self.trace` is None.
- Whatever lands must be honest about what it measures: `_shell` wraps
  `omarchy launch`, `wl-paste`, `tmux` and `bash` as well as `hyprctl`.

## Open questions

1. **Record the phase, or delete the constant?** Both are honest. Recording it
   makes #23 settleable on task evidence; deleting it removes a claim the code
   does not honour. The second is smaller and is the right answer if the
   considered view is that subprocess time will never be interesting.
2. **If recorded, at what granularity?** One span in `_shell` is one line and
   catches everything, but lumps a 13 ms `hyprctl` query together with an
   `omarchy launch` that is deliberately allowed to take seconds — which would
   make the number nearly useless for the question that motivates it. Splitting
   by binary answers it properly and is more code, and starts to look like
   recording arguments, which constraint two forbids.
3. **Does anything outside `src/` consume the phase list?** `trace.py`'s
   docstring names `tools/bench_local.py` as a consumer, but that file does not
   import `trace`. Worth confirming before either answer.
