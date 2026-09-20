---
status: draft
issue: 52
spec: spec/2026-09-20-52-recording-guard.md
---

# Plan: ask PipeWire first, then the process list, and fail open

## The approved decisions, in full

Implementable without opening the intent or the spec.

**The problem.** A `read_screen` during a screencast puts that frame into a
recording the user is making for an audience — seen by the model *and* by
everyone who watches it later. `_capture_refused` (`tools.py:2012`) already
refuses over what is in frame; this is the same family.

Decisions carried over:

1. **Both signals, PipeWire first.** Neither alone suffices and which recorder
   uses which path could not be established here: `gpu-screen-recorder` is a
   wrapper script whose real binary was not cheaply readable. PipeWire sees the
   portal path, including a browser sharing a screen in a meeting, which no name
   list catches. A process scan sees recorders driving `wlr-screencopy` directly.
2. **The scan truncates its needles to 15 characters.** The issue correctly
   notes `pgrep -x` never matches `gpu-screen-recorder`, 19 characters, and then
   proposes a `/proc` scan comparing against that same full name, inheriting the
   bug. Measured here, `comm` reads `gpu-screen-reco`.
3. **It fails OPEN**, three lines from `_windows_in` which fails closed (#51).
   The code says why: there, being wrong costs the vault; here, it costs a frame
   in a video the user is already choosing to make.
4. **One boolean, `refuse_while_recording`, default on.** The likeliest person
   to meet this refusal is somebody recording a demonstration of this assistant,
   who wants exactly the captures it blocks. Off, and neither detector is
   consulted.
5. **The message names the signal and that it is transient** — the user will
   stop recording, unlike a window they must close.
6. **Cost is paid in the common case.** 24 ms plus up to 66 ms when nothing is
   recording. The 66 ms is inflated: this host has 3164 processes, roughly ten
   times ordinary, where the scan is nearer 10 ms.

## Steps

**1. `config.py:423` — the switch, beside the other capture policy.**
`refuse_while_recording: bool = True` in `[hands]`.
→ verify by a unit test that the default is on and the key parses.

**2. `tools.py` — `_screen_is_recorded() -> str | None`, PipeWire half.**
Run `pw-dump`, parse, and look for a node that is **both** `media.class`
`Stream/Input/Video` **and** attributable to a screen capture rather than a
camera — a webcam in a call is the same class. Return a short reason or None.
→ verify by a unit test over a fabricated payload: a screen-capture node is
detected, a webcam-only node is not, malformed JSON returns None rather than
raising.

**3. `tools.py` — the process half, only if PipeWire said no.**
Read `/proc/<pid>/comm`, compare against `RECORDERS` truncated to 15 characters.
A comment explains the truncation, because the next person adding a name will
add a long one.
→ verify by a unit test against a fabricated `/proc` whose `comm` reads
`gpu-screen-reco` — the case the issue's own proposal would miss.

**4. `tools.py:2012` — wire it into `_capture_refused`, after the window check.**
The window check costs one `hyprctl` query and is the more important refusal, so
it runs first and short-circuits. Both are skipped when the switch is off.
→ verify by a test that `read_screen` refuses while recording, that `click_text`
refuses without being touched, and that neither detector is called when the
switch is off — asserted by the detectors not running, not by the outcome.

**5. Fail open, deliberately and visibly.**
A missing `pw-dump` or an unreadable `/proc` lets the capture proceed and says
nothing about recording. The docstring names #51 as the contrasting case so the
inconsistency reads as a decision.
→ verify by a test with both detectors raising: the capture proceeds.

**6. The message.**
Names which signal fired and that it is transient: stop the recording and ask
again, or turn `refuse_while_recording` off.
→ verify by reading it, and a test that it mentions the switch.

## Tests

```
nix develop -c python3 -m unittest discover -s tests -v
nix flake check
```

Expected green. Every positive case is fabricated — no recorder is started, and
the sandbox has neither PipeWire nor a populated `/proc`. Tests that exercise
the capture path must stub the new detectors the way #51 taught them to stub
`_query_rows`, or they will fail in the sandbox and pass locally.

## Rollback

`git revert` the implementation commits. Additive: one config field, two
detectors, one call in `_capture_refused`. With the switch off the behaviour is
byte-identical to today, which is also the quickest way to rule it out as the
cause of anything.
