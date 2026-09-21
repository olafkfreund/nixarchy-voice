---
status: draft
issue: 67
author: olafkfreund
---

# Intent: guard what we type into, not only what we look at

The sensitive-window guard (#46) covers every path that **reads** pixels and no
path that **writes** input. We refuse to look at a credential prompt and will
type into one.

## Demonstrated, not inferred

Same executor, same window, same guard:

```
sensitive_kind : a password manager
read_screen    : "a password manager is visible in that area, so it was not read."
type_text      : ok=True | typed 18 characters into 1Password
                 (1 hyprctl call actually dispatched)
```

```
send_shortcut CTRL+a -> address:0xvault
   dispatched: hl.dsp.send_shortcut({ key = "a", mods = "CTRL", window = "address:0xvault" })
   ok = True
```

## One correction to #67's framing, made while checking it

The issue blames #60 for this, on the grounds that `type_text` used to be blind
and could only reach a focused window. That is true of `type_text` and **not**
of the gap as a whole: **`send_shortcut` has taken a `window` and been routed by
the compositor since long before #60.** So the ability to send input to a named,
unfocused, sensitive window is older than the capability I blamed for it.

What #60 did was widen it — from chords to arbitrary text, which is the form
that carries a password.

## What the current coverage actually looks like

| tool | targets a window | guarded |
|---|---|---|
| `read_screen` | yes | yes — `_ocr_region` |
| `screenshot` | yes | yes — `_tool_screenshot` |
| `click_text` | yes | **incidentally** |
| `send_shortcut` | yes | **no** |
| `type_text` | yes (since #60) | **no** |

`click_text` is the interesting row. It is guarded because it OCRs the region
first and `_ocr_words` carries the guard — so it is protected as a side effect
of needing to read, not because anyone decided clicking should be guarded. That
is the right outcome reached by accident, and accidents do not survive
refactoring.

`_capture_refused(geometry)` is called from exactly three places, all capture
paths. Everything else is uncovered.

## Why this is worth doing

The asymmetry is indefensible on its face: reading a password field discloses
it to the model, and typing into one can *change* a vault, dismiss a polkit
prompt, or put a secret somewhere it was not. We already decided the first is
not allowed.

It is also the harder one to notice. A refused read comes back as text the
model reports. Input that goes to the wrong window produces no output at all —
which is exactly the class of failure #60 was about.

## Desired outcome

Every tool that sends input to a window is subject to the same guard as the
tools that read one, by construction rather than by coincidence — and where
that is deliberately *not* wanted, the reason is written down.

## Affected

`_tool_type_text`, `_tool_send_shortcut`, and whatever seam ends up carrying
the check. `_sensitive_kind(cls, title)` already takes a window's class and
title and returns a category, so no new matching is needed.

## Constraints

- **`send_shortcut` and `type_text` both work today.** Making them refuse cases
  they currently handle is a behaviour change and has to be argued, not
  assumed — the same constraint #60 carried.
- **Do not leak the title in the refusal.** `_sensitive_kind` deliberately
  returns a category and never the text it matched, because on this desktop the
  titles carry inbox counts and email addresses.
- Whatever is built must hold for the default target, which is the focused
  window and by far the common case.
- Fail closed where the window cannot be identified, consistent with #46 and
  #22 — but see open question 3, because that trade is not obviously the same
  one here.

## Open questions

1. **Is refusing even right?** A person may legitimately want their agent to
   fill a login form — that is a real use, not an abuse. Refusing it is a cost,
   and "the agent must never touch a password manager" is a policy choice
   rather than an obvious truth. Does this want a refusal, a confirmation
   through the existing gate, or a setting?
2. **Does the default target deserve the same treatment?** `activewindow` means
   the user is looking at the thing. Typing into a focused vault is closer to
   "the user is doing this" than addressing an unfocused one by name.
3. **What happens when the window cannot be resolved?** Reading fails closed
   because a wrong read leaks. For input, failing closed means an agent stops
   being able to type whenever `hyprctl` hiccups — which may be worse than the
   risk, and is a different calculation from #46's.
4. **Should `click_text`'s incidental guard be made deliberate?** It works
   today for the wrong reason.
5. **Is there anything else that sends input?** `scroll` and the pointer
   dispatchers move and click; whether they belong here or are a separate
   question needs checking rather than assuming.
