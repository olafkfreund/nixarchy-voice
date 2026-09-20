---
status: approved
issue: 52
intent: intent/2026-09-20-52-recording-guard.md
---

# Spec: ask PipeWire first, then the process list, and fail open

## The intent's open questions, answered

1. **Both signals, PipeWire first.** Neither is sufficient. PipeWire sees the
   portal path — OBS, kooha, and a browser sharing a screen in a meeting, which
   is probably the commonest case and which no name list catches. A process scan
   sees recorders that drive `wlr-screencopy` directly and never appear as a
   PipeWire node. Which of the installed recorders does which was **not**
   established: `gpu-screen-recorder` here is a wrapper script, so its real
   binary's protocols could not be read cheaply. Betting on one signal would be
   betting on that unknown.
2. **A recording and a share are refused identically**, and the message says
   which signal fired. The audiences differ, but the disclosure is the same
   shape, and one refusal is easier to reason about than two. If the distinction
   turns out to matter, the message already carries the information needed to
   split it later.
3. **Yes, it is refusable by configuration.** The likeliest person to meet this
   refusal is somebody recording a demonstration **of this assistant**, who
   wants exactly the captures it blocks. A single boolean, default on.
4. **The message names the signal and that it is transient.** #49 refuses over
   something the user must close; this refuses over something they are doing and
   will stop. "Stop the recording and ask again" is actionable in a way that
   naming a window category is not.
5. **`pw-dump`'s behaviour around a paused or stopped recording is unverified**
   and stated as such rather than assumed.

## Design

### 1. PipeWire first, because it is cheaper and broader

`pw-dump` costs **24 ms** here and reads clean at baseline — zero
`Stream/Input/Video` nodes with nothing recording. A match short-circuits, so a
capture during a recording pays only that.

### 2. The process scan, with the truncation bug actually fixed

Only when PipeWire says no. It reads `/proc/<pid>/comm` and compares against
**truncated** names.

This is the point the issue itself gets wrong. It correctly notes `pgrep -x`
silently never matches `gpu-screen-recorder`, which is 19 characters — and then
proposes a `/proc` scan comparing `comm` against that same full name, which
inherits exactly the same bug. Measured here, `comm` for that process reads
`gpu-screen-reco`. The needles are truncated to 15 characters before comparison,
and a comment says why, because the next person to add a recorder name will add
a long one.

### 3. It fails OPEN, and the code says why beside #51 saying the opposite

If PipeWire cannot be reached or `/proc` cannot be read, the capture proceeds.
Not knowing whether OBS is running is a far weaker signal than not knowing what
is on screen, and refusing every capture because `pw-dump` was missing would be
the check breaking the feature.

`_windows_in` fails **closed** three lines away, which looks like an
inconsistency unless it is written down. It is the deliberate distinction: there,
being wrong costs the vault; here, being wrong costs a frame in a video the user
is already choosing to make.

### 4. One boolean, default on

`refuse_while_recording`, beside the other capture policy in `[hands]`. Off, and
neither signal is consulted at all, so the demonstration case also pays nothing.

## Alternatives rejected

- **Process names only**, as the issue proposes. Misses a browser sharing the
  screen, and its own implementation inherits the `comm` truncation bug.
- **PipeWire only.** Cheapest and broadest, and it would miss a recorder driving
  `wlr-screencopy` directly — which may be several of the installed ones, and
  that could not be checked from here.
- **`pgrep` in any form.** 354 ms with an alternation, 1689 ms per-name, and
  silently wrong for the longest and likeliest name.
- **Refusing on any PipeWire video node.** A webcam in a call is
  `Stream/Input/Video` too. The node has to be attributable to a screen capture,
  not merely to video.
- **Failing closed like #51.** Would make a missing `pw-dump` disable screen
  reading entirely, which is a worse outcome than the thing being guarded.

## Risks

- **It costs the common case, not the rare one.** When nothing is recording both
  signals run: 24 ms plus up to 66 ms here. That 66 ms is inflated — this host
  has 3164 processes, roughly ten times an ordinary desktop, where the scan is
  nearer 10 ms. So 34 ms typically and 90 ms here, against a capture whose OCR
  is 2-5 s. Worth stating plainly because #26 spent a chain removing 0.69 s from
  this path, and this gives a little back.
- **Both signals are heuristics.** A PipeWire node's properties are not a
  declaration of intent, and a process name is not proof a recording is running
  rather than idle. The documentation must not imply otherwise.
- **The demonstration case is a real user, not an edge case.** Mitigated by the
  boolean, and worth watching: if people turn it off permanently, the default is
  wrong.
- **Unverified: which installed recorder uses which path**, and whether
  `pw-dump` still reports a node for a paused recording. Both were reasoned
  about, neither observed.

## Verification

**The detectors, separately and with fabricated input.** A fabricated `pw-dump`
payload containing a screen-capture node is detected and one containing only a
webcam node is not. A fabricated `/proc` where `comm` reads `gpu-screen-reco` is
detected — that is the truncation case, and it is the test that would have
failed against the issue's own proposal.

**The seam.** With either detector positive, `read_screen` refuses and
`click_text` refuses without being touched. With both negative, neither does.

**It fails open.** With `pw-dump` missing and `/proc` unreadable, a capture
proceeds and says nothing about recording.

**The switch.** With `refuse_while_recording` off, neither detector is consulted
at all — asserted by the detectors not being called, not merely by the outcome.

**Nothing regressed.** Full suite green, `nix flake check` green.

**Not verified here.** Behaviour against a real recording. No recorder was
started, so every positive case in the tests is fabricated.
