---
status: approved
issue: 55
spec: spec/2026-09-20-55-headless-output.md
---

# Plan: refuse a sleeping screen, not an unfamiliar one

## The approved decisions, in full

Implementable without opening the intent or the spec.

**The problem.** In a headless VM the only output reports `dpms=True,
disabled=True`, and `_screen_unavailable()` (`tools.py:2201`) refuses on
`disabled`. `read_screen` answers "the display is asleep" and prescribes
`hl.dsp.dpms({ state = "on" })`, which cannot help — `dpmsStatus` is already
true. Meanwhile `grim -t ppm -` returns a valid 1280x800 capture whose OCR
matches five lines of windows deliberately launched in that guest.

Decisions carried over:

1. **Drop the `disabled` term.** A monitor counts as usable when
   `dpmsStatus is not False`. Nothing else changes: the lock check still runs
   first, the empty-list case still fails open, and every monitor asleep still
   refuses.
2. **No experiment was needed, and none was run.** The guard's docstring
   records that grim "blocks until the timeout" rather than failing, so a bound
   already exists and this guard is an optimisation over it. Wrong in the new
   direction costs one capture up to fifteen seconds; wrong in the current
   direction means no virtual or nested display can be read at all. Measuring
   it would have meant disabling a monitor on a machine somebody is using, for
   a fact the trade does not need.
3. **No name or `make`/`model` heuristic.** `Virtual-*` matching would be wrong
   on some compositor eventually, in the direction of refusing a real screen —
   and an unexplained heuristic is exactly how the `disabled` term arrived.
4. **The docstring explains the absence**, or the term returns defensively.
5. **The message stops prescribing a fix that may not apply.**

## Steps

**1. `tools.py:2228` — the condition loses one term.**
```python
awake = [m for m in monitors if m.get("dpmsStatus") is not False]
```
→ verify by the existing cases at `tests/test_compose.py:531-546` still
passing: they set `dpmsStatus` alone and never `disabled`, so they are the
regression check for the two documented behaviours.

**2. `tools.py:2201` — the docstring says why `disabled` is not consulted.**
A headless or virtual output reports it while rendering normally, and `grim`
reads such an output correctly. With the evidence, so nobody restores it.
→ verify by reading it against what the code now does.

**3. `tools.py:2232` — the refusal reports rather than prescribes.**
It says every monitor is asleep and offers the dispatch as something to try,
because a display can report `dpmsStatus: true` on 0.56 and still not be what
the user is looking at.
→ verify by reading it, and a test that it no longer claims the dispatch is
the answer.

**4. `tests/test_compose.py` — the case nobody wrote.**
A monitor with `dpmsStatus: True, disabled: True` must **not** refuse. Plus:
all asleep refuses, a mix does not, an empty list returns None.
→ verify by the suite.

**5. The lock check still wins.**
Session locked, monitor healthy: the lock refusal is what comes back. It is the
dangerous case — a capture that succeeds and shows a blurred photograph
reported as somebody's desktop — and this change must not reach it.
→ verify by a test with both conditions true.

**6. End to end in the VM, which is the point.**
Boot `.#vm` headless, stop the screensaver, and run `read_screen`. It must
return text rather than "the display is asleep", and #35's harness must collect
`ocr` and `capture` phases — which it was built for and has never yet done.
→ verify by the harness output, recorded here.

## Measured (step 6)

Run in a headless `.#vm` with the fixed package, screensaver stopped, one
`foot` window launched with known text:

```
monitors: "disabled": true
TIMING  8.42s continuations=0 capture=0.10s lock=0.00s model-turn=8.42s
        ocr=0.50s subprocess=0.16s(hyprctl=0.09 omarchy-shell=0.06) tool=0.79s
action  read screen (screen)
reply   The screen shows "LIVE CHECK PANE A" at the top...
```

The monitor **still reports `disabled: true`** and the read now works, returning
the exact text of the window that was launched. `ocr` and `capture` are
populated for the first time — the phases #35's harness was built to collect and
had never once obtained.

Worth noting the numbers themselves: `ocr=0.50s` here against 4.19-5.40 s
measured on the real desktop. Not a speedup — a small screen with little text,
which is precisely the controlled condition #35 wanted and the real session
could never give.

## Tests

```
nix develop -c python3 -m unittest discover -s tests -v
nix flake check
```

Expected green. The VM step is manual and deliberate: nothing here may make the
suite need a compositor, which is #35's standing constraint and #28's lesson.

## Rollback

`git revert`. One term in one condition, a docstring, and a message. A machine
whose monitors report `disabled: false` — every physical desktop — behaves
identically before and after, which is also why the existing tests are the
regression check.
