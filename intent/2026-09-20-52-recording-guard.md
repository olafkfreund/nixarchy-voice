---
status: approved
issue: 52
author: olafkfreund
---

# Intent: reading the screen during a recording puts the read into the recording

## Problem

#49 refuses a capture that would include a password manager. The same family
has one more member it does not cover: capturing while the desktop is already
being recorded or shared.

A `read_screen` during a screencast puts that frame into a recording the user is
making for an audience. The read is then seen by the model **and** by everyone
who watches the recording later, which is a disclosure the user did not make and
cannot take back.

`_capture_refused` (`tools.py:2012`) is the seam this belongs in: it already
tests what is in frame, and `click_text` and `wait_for(text)` already inherit it.

**What measuring established, and one of it contradicts the issue's own
proposal:**

1. **Process-name detection is expensive here.** `pgrep -x` once per recorder
   name costs 1689 ms; one call with an alternation, 354 ms; a `/proc` scan
   reading `comm`, 66-110 ms. #26 spent a whole chain removing 0.69 s from a
   capture, so spending a fifth of it back on a convenience check would be
   careless.
2. **`comm` is truncated at 15 characters, and that defeats the proposed fix
   too.** The issue correctly notes `pgrep -x` silently never matches
   `gpu-screen-recorder`, which is 19 characters. But a `/proc` scan comparing
   `comm` against the same full name inherits exactly that bug: measured here,
   `comm` for that process reads `gpu-screen-reco`. The replacement would miss
   the recorder most likely to be running, for the same underlying reason.
3. **PipeWire answers the question more cheaply and more broadly.** `pw-dump` is
   24 ms, against 66 ms for the scan on this host. It reads clean at baseline —
   zero `Stream/Input/Video` nodes with nothing recording — and it sees the
   **portal** path, which covers a browser sharing the screen in a meeting. No
   process-name list catches that, and it is arguably the commonest case of all.

## Proposed outcome

- A capture does not happen while the screen is being recorded or shared, and
  the caller is told why.
- Detection costs a small fraction of a capture rather than a visible share of
  it.
- What is detected includes screen sharing that is not a named recorder binary,
  because that is how most people share a screen.
- The check does not become a new way for captures to fail: not knowing whether
  a recorder is running is a weak signal, and must not block the feature.

## Affected users and systems

- Everyone using `read_screen`, `click_text` and `wait_for(text)`, on every
  backend.
- `src/omarchy_voice/tools.py`, in and around `_capture_refused`.
- Anyone recording a demonstration of this assistant, who is the person most
  likely to meet the refusal and the person it is for.

## Constraints

- **This one fails OPEN**, unlike #51. Not knowing whether OBS is running is a
  much weaker signal than not knowing what is on screen, and refusing every
  capture because `/proc` or PipeWire could not be read would be the check
  breaking the feature. #51's docstring already explains why that check is the
  exception; this must not blur it.
- **Must not cost a visible share of a capture.** #26 removed 0.69 s from this
  path deliberately.
- **Must not depend on a process name being under 15 characters**, per finding 2.
- Must degrade honestly: if detection is unavailable, capture and say nothing,
  rather than implying the screen was checked.
- Tests must not reach the real desktop.

## Open questions

1. **PipeWire, process names, or both?** PipeWire is cheaper, catches portal
   sharing, and is a heuristic over node properties. A name list is explicit and
   misses browser sharing. Both is more code and more to keep right.
2. **Is a recording the same thing as a share?** A local `wf-recorder` capture to
   disk and a screen shared into a meeting have different audiences, and the
   user may feel differently about each. One refusal for both is simpler and may
   be too blunt.
3. **Should this be refusable by configuration?** Someone recording a
   demonstration *of this assistant* wants exactly the captures this would
   block. That is not a hypothetical user; it is the likeliest one.
4. **Where does the message say it?** #49's refusal names a window category.
   "The screen is being recorded" is a different kind of fact and may want
   different wording, especially as it is transient.
5. **Does `pw-dump`'s signal survive a paused or stopped recording?** Measured
   only at baseline with nothing running. The state that matters was not
   observed.
