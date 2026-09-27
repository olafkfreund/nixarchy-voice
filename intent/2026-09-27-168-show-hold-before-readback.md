---
status: draft
issue: 168
author: olafkfreund
---

# Intent: what you are confirming is on screen while she reads it out

## Problem

When a call is held (an action `save`, or a command with the shell off), Oma asks for a yes. What is waiting reaches the user two ways:

1. **Spoken.** At the end of the turn she says `HELD_PROMPT`: "{held} is
   waiting for you. Say the word on the screen to run it, or cancel."
   (`local_engine.py:177`, `:739-745`).
2. **On screen.** `_settle()` writes it to the bar state and posts a "Waiting
   for confirmation" notification with the description and how to answer
   (`local_engine.py:316-327`).

`_settle()` runs only after that sentence has finished (`:746`). So:

- **Nothing on screen shows the hold while it is being read out**, and for a
  recipe that takes 10 s or more. The spoken line tells you to "say the word
  on the screen" while no word is on the screen.
- **A quick yes means it is never shown at all.** In the #163 demo take on
  razer (`session.log`, 2026-09-27):
  - 08:19:52: `HOLD save action demo-focus: …`;
  - 08:19:58: the readback starts;
  - 08:20:03: confirmed while she was still speaking.

  The video frames from 2.5 s to 35 s are an empty desktop. The user approved
  a three-step recipe that they only heard.
- **What is shown gets clipped.** nixarchy's shell (quickshell, the
  notification server on razer) shows three lines of body. A notify-send with
  the same summary and body as that hold showed steps 1-3 and cut off the
  last line, "Say confirm, or press the confirm key." A recipe with more steps
  loses its last steps, and those are part of what is being approved.

Hearing is not reading. A save or a held command is the moment the user most
needs to *see* exactly what will run: a misheard URL or path is easy to miss
when spoken and obvious when written.

## Proposed outcome

- The moment a call is held, the bar and a notification show what is waiting,
  before she starts reading it out and whether or not she finishes.
- Every step of what is being approved can be read on screen, however long
  the recipe is, together with how to say yes or cancel.
- A yes or a cancel clears it. Nothing on screen claims a hold that is gone.
- Nothing about what is held, or how it is released, changes. This is display
  only.

## Affected users and systems

- `src/omarchy_voice/local_engine.py`: where the hold is announced and
  `_settle()` is called.
- Possibly `src/omarchy_voice/feedback.py`, depending on how the long form is
  shown.
- Every hold on the local engine, not only actions: a command with the shell
  off, a reboot, and so on.
- The voice-indicator and voice-orb plugins read the bar state
  (`VoiceIndicator.qml`, `VoiceOrb.qml`). How much of a long text they show
  needs checking.
- razer and p620 (nixarchy's quickshell notifications), and plain Omarchy
  (mako).

## Constraints

- The policy gate is not touched: what holds, the confirm key, confirm words,
  and `barge_in` behaviour (#86: the confirm word is on screen, never in her
  mouth) all stay as they are.
- No secret is put on screen that the spoken line would not say. It is the
  same `describe()` text.
- It works on both notification servers (quickshell and mako), and
  degrades to the bar alone when `notify = false`.
- No new dependency.

## Open questions

1. **Where the full recipe is shown when it is longer than a notification
   allows.** Recommended: keep the notification short (the summary is the
   action's name and step count, and the body is the steps one per line),
   and make sure the whole text is also on the bar/indicator state, which
   has no line limit. The alternative is a dedicated window or a menu page
   for the pending hold. That is clearer for long recipes, but it is more to
   build and it takes focus.
2. **Can the notification be replaced rather than stacked?** Recommended:
   yes. Post it with a stable ID (`notify-send -r` / `--replace-id`, or
   `-p` to learn it) so a later hold or its release updates the same one,
   and close it on yes or cancel. This needs checking against quickshell's
   server. If it can't be done, the fallback is a short timeout.
