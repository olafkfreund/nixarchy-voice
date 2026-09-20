---
status: draft
issue: 55
author: olafkfreund
---

# Intent: the capture guard refuses a screen that grim reads perfectly

## Problem

In a headless VM — the launch nixarchy's own `vm/configuration.nix:35`
documents — the single output reports:

```
Virtual-1 1280x800  dpms=True  disabled=True
```

`_screen_unavailable()` refuses on `disabled`, so `read_screen` answers *"the
display is asleep, so there is nothing on screen to read. Wake it first with
hypr_dispatch: hl.dsp.dpms({ state = "on" })"*.

**The advice does not work.** `dpmsStatus` is already `true`; the monitor stays
`disabled: true`; and `hyprctl keyword monitor` answers `unknown request` on
0.56. So the model is told to do something that cannot help, and will try.

**And the capture is fine.** Same guest, same moment:

```
$ grim -t ppm -  | od -c
0000000   P   6  \n   1   2   8   0       8   0   0  \n   2   5   5  \n ...

$ grim -t ppm - | tesseract stdin stdout --oem 1 --psm 3 -l eng --dpi 300 \
    | grep -ic 'live\|check\|pane'
5
```

Five OCR lines matching the text of windows deliberately launched in that guest.
grim captures the real screen, tesseract reads it, and the guard refuses anyway.

**What reading the guard established.** Its docstring justifies two things at
length: a DPMS-off monitor produces no frames and makes grim block for the full
timeout, and a locked session paints over everything while the capture still
succeeds. Both are well argued and neither is in question here.

It says **nothing at all** about `disabled`, which is the clause that refuses
this screen. The condition is
`m.get("dpmsStatus") is not False and not m.get("disabled")`, and the second
half appears to be defensive rather than reasoned — it is not the hang the
docstring describes, and it is not the lock screen.

## Proposed outcome

- A screen that `grim` can read is read.
- The two failures the guard was written for — a sleeping monitor that makes
  grim hang, and a lock screen that captures successfully but shows nothing
  real — are still caught.
- Nobody is told to run a command that cannot help.
- Measuring the OCR path in a throwaway machine becomes possible, which is what
  #35 exists for and is currently blocked on this.

## Affected users and systems

- `read_screen`, `click_text`, `wait_for(text)` and `scroll`, all of which pass
  through `_screen_unavailable()`.
- Anyone running the assistant anywhere the output is virtual: a VM, a nested
  compositor, a headless session.
- #35, which cannot measure anything until this changes.
- Not the sleeping-monitor or locked-session paths, which must keep working.

## Constraints

- **Must not weaken the two documented cases.** A DPMS-off monitor making grim
  block for fifteen seconds and then blaming OCR is a real bug this prevented;
  a lock screen reported as the contents of somebody's desktop is worse.
- **Must not trade a false refusal for a false capture.** The guard exists
  because a capture that succeeds and shows the wrong thing is more dangerous
  than one that fails.
- Must not need a new query. The monitor list is already fetched here.
- Must keep failing open when the query cannot be answered — the docstring is
  explicit that neither check may become the reason nothing works.
- Tests must not reach the real desktop.

## Open questions

1. **Drop the `disabled` clause, or qualify it?** Dropping it is the smallest
   change and rests on the argument that it was never justified. Qualifying it —
   treating a virtual or headless output as capturable — is narrower and
   introduces a name heuristic that will be wrong somewhere.
2. **What does `disabled` mean on a physical output, and does grim hang on
   one?** The docstring's hang argument is about DPMS. Whether a
   config-disabled physical monitor also hangs was not established, and it is
   the thing that decides question 1.
3. **Should the message change regardless?** It currently prescribes a command
   that does not work in this case. Even with the guard fixed, a monitor that is
   genuinely asleep may not be wakeable by that dispatch on 0.56.
4. **Is there a signal better than `disabled`?** The monitor JSON carries more
   than was looked at. A field that distinguishes "renders nothing" from
   "renders, just not to a physical panel" would settle this without heuristics.
