---
status: draft
issue: 39
intent: intent/2026-09-20-39-subprocess-phase.md
---

# Spec: record the phase, and name the binary that spent the time

## The intent's open questions, answered

1. **Record it, do not delete the constant.** Deleting is smaller and honest,
   and it forecloses the thing the trace exists for. #23 set the standard that
   changes are argued against task completion rather than component benchmarks,
   and the `hyprctl` question is the one that keeps coming back — it came back
   today, from me, ranked first, before I found #23 had already answered it. A
   number in a TIMING line ends that permanently and cheaply.
2. **Granularity: one span, named by the binary.** A bare `subprocess=4.1s` that
   lumps a 13 ms `hyprctl` query together with an `omarchy launch` deliberately
   allowed to take seconds would be worse than nothing — it would invite exactly
   the wrong conclusion. `Span` already carries a `name` and `mark(phase, name)`
   already takes one, so `argv[0]` needs no schema change.
3. **Nothing outside `src/` consumes the phase list.** `tools/bench_local.py`
   does not use `trace`; its only matches for the string are the word
   "traceback". `trace.py`'s docstring naming it as a consumer is aspirational,
   and is corrected here rather than left to mislead the next reader.

## Design

### 1. `_shell` becomes an instance method and opens a span

`tools.py:1676` is `@staticmethod`, so it has no `self` and cannot reach
`self.trace`. Dropping the decorator and taking `self` is **source-compatible
with all 22 call sites**, which already call it as `self._shell(...)`.

It opens one span per invocation: phase `SUBPROCESS`, name `argv[0]` — the bare
program name, `hyprctl`, `omarchy`, `wl-paste`, `tmux`, `bash`. Guarded by
`if self.trace`, like every existing span, so the untraced path costs nothing.

**No double counting.** `CAPTURE` and `OCR` wrap `subprocess.run` directly
(`tools.py:1954`, `:1965`), not `_shell`, so `grim` and `tesseract` are already
counted under their own phases and do not reach this one.

### 2. The TIMING line gains a breakdown, for this phase only

`phase_seconds()` keeps aggregating by phase. A sibling aggregates the
`SUBPROCESS` spans by name, and `line()` renders it inline:

```
TIMING  6.42s continuations=2 capture=0.06s ocr=1.93s subprocess=0.34s(hyprctl=0.21 omarchy=0.13) tool=4.31s
```

Only `SUBPROCESS` is broken down. Doing it for every phase would be a bigger
change to the line's shape for no question anybody is asking.

### 3. `argv[0]` only, never the arguments

The name recorded is `cmd[0]` and nothing else. #26 decision 3 keeps the trace
to durations and phase names — never window titles, OCR text or tool arguments —
and a program name from a fixed, code-determined set is not user content. This
must be explicit in the code, because "record a bit more of the command" is the
obvious next change and is the one that would leak.

### 4. Correct `trace.py`'s docstring

It names `tools/bench_local.py` as a consumer. That is not true, and it is the
kind of stale claim that cost a reader time today.

## Alternatives rejected

- **Delete the constant.** The honest minimal answer, and it gives up the
  measurement that settles #23. Recorded because it becomes right again if the
  phase proves uninteresting after a few real traces.
- **One span with no name.** One line shorter, and it produces a number that
  conflates a 13 ms query with a seconds-long launch. A misleading measurement
  is worse than an absent one — that is the lesson of #12 step 7.
- **A span per call site, naming the tool rather than the binary.** More precise
  about *why* a subprocess ran, and it duplicates what the enclosing `TOOL` span
  already says.
- **Record the full command.** Answers more questions and breaks the trace's
  content-free contract. Not worth reopening.
- **Instrument `_query_json` instead**, so only `hyprctl` is counted. Narrower
  and it answers only today's question; the next one will be about something
  else `_shell` runs.

## Risks

- **A number invites optimising it.** A visible `subprocess=` invites someone to
  drive it down, which is precisely the component-benchmark thinking #23
  rejected. The spec's own framing has to travel with it: it is a **share** of a
  task, and only meaningful next to `ocr` and `model-turn`.
- **`_shell` changing shape.** It is called 22 times and has `grace` semantics
  that return early while the process keeps running (`tools.py:1681-1685`). The
  span must close when `_shell` returns, not when the child exits, or a launched
  terminal will appear to cost minutes.
- **Only `session.log` consumes this.** With nothing in `bench_local`, reading
  the number means turning `trace_timings` on and reading the log by hand. That
  is enough to answer the #28 check, and it is not a reporting tool.
- The flag is off by default, so no user sees any of this unless they ask.

## Verification

**Unit.** A trace with two `SUBPROCESS` spans of different names aggregates into
one phase total and a per-name breakdown. `line()` renders the breakdown only
for that phase. Nothing is recorded when `trace` is None, and `argv[0]` is the
only part of the command that reaches the span.

**Runtime, and this is the point.** Turn `trace_timings` on, run a handful of
representative tasks, and read the `subprocess=` share against `ocr=` and
`model-turn=` in `session.log`. That is the #28 post-landing check, which
currently cannot be performed at all. Record what it shows — including if it
shows `hyprctl` is a meaningful share after all, which would reopen #23's
rejection rather than confirm it.

**Not regressed.** The full suite green, and a traced run's `tool=` total still
accounts for its own work rather than double-counting the subprocesses inside it.

**Not verified here.** Whether the share generalises beyond this host. Every
number in #23, #26 and #39 is from p620.
