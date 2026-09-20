---
status: approved
issue: 55
intent: intent/2026-09-20-55-headless-output.md
---

# Spec: refuse a sleeping screen, not an unfamiliar one

## The intent's open questions, answered

1. **Drop the `disabled` clause.** A monitor counts as usable when
   `dpmsStatus is not False`, and `disabled` stops being consulted. The
   argument is question 2.
2. **The experiment is unnecessary, because the downside is already bounded.**
   The guard's own docstring says grim "does not fail on one, it blocks until
   the timeout — a read at half past midnight hung for fifteen seconds and then
   blamed OCR". So a timeout already exists and the guard is an *optimisation*
   over it plus a clearer message, not the only thing standing between a user
   and a hang.
   That sets the trade. Wrong in the new direction: one capture takes up to
   fifteen seconds and returns a poor error. Wrong in the current direction:
   **no virtual or nested display can ever be read at all**, which is where we
   are, and which blocks #35 entirely. A bounded, recoverable cost against a
   total one.
   Disabling one of the three monitors on this machine to measure it would have
   answered it directly and would have disrupted the person using them for a
   fact this argument does not need.
3. **Yes, the message changes.** It currently prescribes
   `hl.dsp.dpms({ state = "on" })`, which in the reported case cannot help —
   `dpmsStatus` was already true. The new text says what was observed rather
   than prescribing a fix, and names the possibility that the display is fine
   and something else is wrong.
4. **`make` and `model` are not used.** Real outputs here report `Razer Taiwan
   Co. L` / `RZ39-0276` and `GWD` / `ARZOPA`, and a virtual one plausibly
   reports neither — but the guest's values were never observed, and a
   heuristic built on unobserved data is how the `disabled` clause got here.

## Design

### 1. The condition loses one term

```python
awake = [m for m in monitors if m.get("dpmsStatus") is not False]
```

Everything else stays: the lock check first, the fail-open when the query gives
nothing, the refusal when every monitor is asleep.

### 2. The docstring explains the absence

The clause that goes was never explained, which is how it survived. Its
replacement is a sentence saying `disabled` is deliberately not consulted,
because a headless or virtual output reports it while rendering normally, and
`grim` reads such an output correctly — with the evidence, so nobody restores it
defensively.

### 3. The refusal stops prescribing a command that may not work

It reports what was seen — every monitor asleep — and offers the dispatch as
something to try rather than the answer, since on 0.56 a display can report
`dpmsStatus: true` and still not be what the user is looking at.

## Alternatives rejected

- **Qualify `disabled` by output name.** Treating `Virtual-*` or `HEADLESS-*`
  as capturable is narrower and introduces a heuristic that will be wrong on
  some compositor, some day, in the direction of refusing a real screen.
- **Qualify it by `make`/`model` being empty.** Same objection, and built on a
  value never observed in the case it is meant to catch.
- **Keep the clause and special-case the VM.** Puts knowledge of one deployment
  into a general guard, and leaves nested compositors broken.
- **Attempt a short capture and decide from the result.** Answers the question
  properly and costs a capture on every read, on the path #26 spent a chain
  making faster.
- **Remove the guard entirely** and rely on grim's timeout. Gives up the clear
  message and the fifteen seconds, both of which were worth having for the case
  the docstring describes.

## Risks

- **A genuinely disabled monitor is now attempted.** Up to fifteen seconds and a
  worse error than today's. Bounded and recoverable; stated rather than hidden.
- **The lock case is untouched and must stay that way.** It is the dangerous one
  — a capture that succeeds and shows a blurred photograph reported as the
  user's desktop — and it is checked before this and by a different mechanism.
- **This makes a class of screen readable that was not before.** That is the
  point, and it means a virtual display could now be read where somebody
  previously relied on the refusal. Nobody has, since it also refused the only
  machine where that mattered.
- **Whether `grim` hangs on a config-disabled physical output is still
  unverified.** The argument above says the cost is bounded either way, but it
  is an argument, not a measurement.

## Verification

**Unit, on fabricated monitor lists.** Every monitor `dpmsStatus: false`
refuses. A monitor with `dpmsStatus: true, disabled: true` — the reported case —
does **not** refuse. A mix of one asleep and one awake does not refuse. An empty
list still returns None, because the query failing must never be the reason
nothing works.

**The lock check still wins.** With the session locked and a perfectly healthy
monitor, the lock refusal is what comes back.

**In the VM, end to end.** With this change, `read_screen` in a headless guest
returns text rather than "the display is asleep" — and #35's harness collects
`ocr` and `capture` phases, which is the thing it was built for and has never
yet done.

**Nothing regressed.** Full suite green, `nix flake check` green.

**Not verified here.** That a config-disabled physical monitor behaves as the
argument assumes. It would need a second physical output to disable, on a
machine somebody is using.
