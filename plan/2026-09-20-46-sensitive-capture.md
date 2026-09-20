---
status: draft
issue: 46
spec: spec/2026-09-20-46-sensitive-capture.md
---

# Plan: do not read the screen when the screen is a password

Worktree `/mnt/data/vmtest/voice-46-capture`, branch `fix/46-sensitive-capture`,
already carrying the intent and the spec. A deviation updates this file in the
same commit as the code.

## The approved decisions, in full

- **Refuse, do not redact.** Redaction has to be right about geometry to be
  safe, and a popup or dropdown rendered as its own surface sits outside the
  rectangle it would black out.
- **Classes and titles both.** Tested against hermes' lists over the 12 windows
  open on this desktop: neither fires. The false-positive fear was unevidenced,
  and the costs are not comparable — a false refusal is one read, a false pass
  is a vault in the log.
- **Check the whole captured rectangle, not the focused window.** Measured:
  DP-1 has three windows in one frame, so a focused-only rule would leave two
  unexamined.
- **No override.** A switch whose function is to remove the protection makes
  the guarantee "unless someone disabled it".
- **`screen_shared` comes; `mic_in_use` is filed separately** — it is about
  listening, not capture.
- **Fail closed**, reversing `_screen_unavailable()`'s documented rule for this
  one check, in the same docstring, saying why. #24's `_query_rows` is what
  makes "I do not know" distinguishable from "nothing sensitive".
- **The refusal names the category, never the title.**

## A correction to the spec, before step 1

The spec says the check is consulted by "the same three callers". There are
three `_screen_unavailable()` call sites, but only **two of them capture**:

| site | captures? | needs this |
|---|---|---|
| `_ocr_region` (`tools.py:1997`) | yes, `grim` | **yes** |
| `_ocr_words` (`tools.py:2094`) | yes, `grim` | **yes** |
| `scroll` (`tools.py:3269`) | no — moves the pointer and turns the wheel | **no** |

Scrolling a password manager sends nothing to a model. Adding the guard there
would refuse a harmless action and make the feature look capricious. Both
capture sites already have the `geometry` in hand, which is exactly what the
check wants.

## Steps

**1. `tools.py`: the lists, with attribution.**
`SENSITIVE_CLASSES` and `SENSITIVE_TITLES`, adapted from
omarchy-hermes-companion `daemon/perception.py:20-33` (MIT). The comment names
the source and the licence, and says plainly that a blocklist is a floor: an
unlisted password manager is unprotected, and what survives one is the
lock-screen guard alone.
→ verify by test 8 — the 12 real windows from this desktop as a fixture, none
of which may match.

**2. `tools.py`: `_sensitive_in_frame(geometry)`.**
Parse the grim geometry back to a rect, list clients, keep those on a visible
workspace whose own rect intersects it, and match class and title against the
two lists. Returns the matched **category**, or None.

Two rules where being wrong is cheap in one direction only:

- If the client query fails, return a refusal rather than None. Fails closed,
  unlike everything else in this family.
- If a window's geometry cannot be parsed, treat it as in frame. The spec's
  "simplest safe rule".

→ verify by tests 1–4 and 6.

**3. `tools.py`: `_screen_unavailable(geometry=None)` consults it.**
An optional geometry: passed by the two capture sites, omitted by `scroll`,
which therefore keeps exactly today's behaviour. The docstring gains the
carve-out — why this one check fails closed when the two above it do not.
→ verify by tests 5 and 7.

**4. `tools.py`: `screen_shared()`.**
Refuse while a screencast or recorder is running: `wf-recorder`,
`gpu-screen-recorder`, `obs`, or a portal screencast node. Same refusal path.
Kept small and behind the same call, because it is the same guard wearing a
different hat.
→ verify by a test with a faked process list.

**5. `README.md`.** A line in the privacy section: what is refused, that the
list is a floor rather than a ceiling, and that there is no switch. The floor
sentence matters most — a reader who believes this is complete is worse off
than one who knows its shape.
→ verify by reading it.

**6. File `mic_in_use` as its own issue**, with the prior art link, so the
decision to exclude it is visible rather than silent.
→ verify by the issue existing.

## Tests

`tests/test_sensitive_capture.py`. The seam is `_query_rows` / `_query_json` on
the executor, as everything else in this suite uses. No test needs a
compositor.

A fixture of the 12 windows actually open on this desktop when the spec was
written, with one swapped for a vault where a test needs one.

1. **A listed class in frame refuses**, and `subprocess.run` is never called.
2. **A listed class on another workspace does not refuse.**
3. **The unfocused case** — a vault beside the focused browser, both on the
   captured monitor. This is the one the prior art misses and the reason the
   check is not focused-only.
4. **A window-scoped capture narrows**: reading the browser succeeds while the
   vault is open on the same monitor.
5. **The refusal leaks nothing.** A window titled with a sentinel refuses, and
   the sentinel is absent from the result, the transcript and the log.
6. **Fails closed** when the client query fails. New behaviour, asserted.
7. **The lock and DPMS guards still fail open** — unchanged, and the test says
   so, because this change edits the docstring that governs them.
8. **No false refusal on the real desktop fixture.** The test that stops the
   lists being tightened into uselessness.
9. **`scroll` is unaffected** — it does not capture, and refusing there would
   be a bug of its own.

```
nix develop -c python3 -m unittest discover -s tests   # green; 711 before
nix flake check
nix develop -c python3 -m omarchy_voice verify-gate
```

**Tests 1 and 3 must be seen failing against the pre-change source.** Four
tests passed for the wrong reason in this repo this week; a regression test
nobody has watched fail proves nothing.

## Rollback

One branch in a worktree. `git revert` the merge and the capture path returns
to refusing only for a locked session and a dark monitor.

The one thing to know: reverting restores a path that will OCR a vault, so it
should be a deliberate choice rather than a reflex if something else in the
branch turns out wrong. Steps 4, 5 and 6 are independent of 1–3 and can be
dropped on their own.
