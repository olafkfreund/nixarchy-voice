---
status: approved
issue: 39
spec: spec/2026-09-20-39-subprocess-phase.md
---

# Plan: record the phase, and name the binary that spent the time

## The approved decisions, in full

Implementable without opening the intent or the spec.

**The problem.** `trace.py:41` defines `SUBPROCESS = "subprocess"` and that is
its only occurrence in the repository. Every span created is `LOCK`, `TOOL`,
`CAPTURE`, `OCR` or `TURN`, so every `hyprctl` invocation is counted inside the
enclosing `TOOL` span with no way to separate it. A TIMING line reads
`tool=4.31s` and says nothing about how much of that was forking `hyprctl` —
which is the one number that would settle #23 on task evidence rather than on a
component benchmark. I re-proposed #23's rejected raw-socket optimisation today
before finding #23 had already answered it; that is the cost of the gap.

Decisions carried over:

1. **Record the phase, do not delete the constant.**
2. **One span per `_shell` invocation, named `argv[0]`.** A bare
   `subprocess=4.1s` lumping a 13 ms `hyprctl` query with an `omarchy launch`
   deliberately allowed to take seconds would invite exactly the wrong
   conclusion. A misleading measurement is worse than an absent one.
3. **`argv[0]` only, never the arguments.** #26 decision 3 keeps the trace to
   durations and phase names. A program name from a fixed, code-determined set
   is not user content; the arguments are.
4. **Break down only `SUBPROCESS` in the TIMING line.** Doing it for every phase
   changes the line's shape for a question nobody is asking.
5. **The span closes when `_shell` returns, not when the child exits.** `grace`
   (`tools.py:1681-1685`) deliberately returns while a launched application
   keeps running; timing to child exit would report a terminal as costing
   minutes.
6. `CAPTURE` and `OCR` wrap `subprocess.run` directly (`tools.py:1954`, `:1965`)
   and do not pass through `_shell`, so `grim` and `tesseract` are not double
   counted.
7. Correct `trace.py`'s docstring, which names `tools/bench_local.py` as a
   consumer. It is not one — its only matches for the string are the word
   "traceback".

## Correction to the spec, found before implementing

The spec says dropping `@staticmethod` is "source-compatible with all 22 call
sites, which already call it as `self._shell(...)`". **That is wrong.** Checked:

- 18 sites are `self._shell(...)` — unaffected;
- `tools.py:1175` and `:1195` are `executor._shell(...)`, on an instance —
  unaffected;
- **four sites call `Executor._shell(...)` on the class** — these break:
  `tests/test_policy.py:201`, `:206`, `:211`, and `tests/test_compose.py:557`.

Fixed in step 1 by binding an instance. All four exercise `timeout`, `grace` or
output-limit behaviour and none needs a trace, so `Executor(Config(dry_run=False))`
is enough.

**Corrected during step 1:** this section first said *two* sites. It was four. I
had grepped `tools.py` carefully and the tests carelessly, and the suite found
the other two — which is the argument for step 1 existing at all, since the
signature change was proven green before any span was added and the two failures
could only have been the refactor.

## Steps

**1. `tools.py:1676` — `_shell` becomes an instance method.**
Drop `@staticmethod`, take `self`. Update `tests/test_policy.py:201,206` to call
it on an instance rather than the class.
→ verify by the full suite green *before* any span is added, so the signature
change is proven separately from the instrumentation.

**2. `tools.py:1677` — open the span.**
`if self.trace`, one span, phase `trace_mod.SUBPROCESS`, name `cmd[0]`, closed
when `_shell` returns — including on the `grace` early return and on every error
path. Nothing but `cmd[0]` reaches the span.
→ verify by a unit test asserting the span's name is exactly the program name,
that a `grace` return still closes it, and that nothing is recorded when `trace`
is None.

**3. `trace.py:92` — aggregate `SUBPROCESS` by name.**
A sibling to `phase_seconds()` returning `{name: seconds}` over `SUBPROCESS`
spans only.
→ verify by a unit test in `tests/test_trace.py` with two differently named
spans.

**4. `trace.py:98` — render it.**
```
TIMING  6.42s continuations=2 capture=0.06s ocr=1.93s subprocess=0.34s(hyprctl=0.21 omarchy=0.13) tool=4.31s
```
Only this phase is broken down; the rest of the line is unchanged.
→ verify by a unit test on the rendered string, and by reading one real line.

**5. `trace.py:11` — correct the docstring.**
→ verify by grep: `bench_local` no longer claimed as a consumer.

**6. Run the #28 check that this exists for.**
Turn `trace_timings` on, run a handful of representative tasks, read the
`subprocess=` share against `ocr=` and `model-turn=` in `session.log`. Record
what it shows here — **including if it shows `hyprctl` is a meaningful share**,
which would reopen #23's rejection rather than confirm it.
→ verify by the numbers being written into this file, and a comment on #28
either way.

## Tests

```
nix develop -c python3 -m unittest discover -s tests -v
nix flake check
```

Expected green, including the new cases in `tests/test_trace.py` and the two
updated in `tests/test_policy.py`.

## Rollback

`git revert` the implementation commits. The trace is off by default
(`config.trace_timings`), so nothing a user sees changes either way. The only
behavioural change outside the flag is `_shell` becoming an instance method,
which is internal to `Executor`.
