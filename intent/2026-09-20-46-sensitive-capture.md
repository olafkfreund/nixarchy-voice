---
status: draft
issue: 46
author: olafkfreund
---

# Intent: do not read the screen when the screen is a password

Closes #46. Filed by another agent; this is the framing before a spec.

## Problem

`read_screen` captures pixels and sends the OCR'd **text** to a model. Nothing
in the capture path declines because of *what* is on screen. `click_text` and
`wait_for(what="text")` capture the same way, so all three will read a
1Password vault, a `pinentry` prompt mid-passphrase, a `gcr-prompter` dialog,
an incognito window or a bank dashboard.

Text is worse than a picture here. It is already extracted, already quotable,
and it goes into a conversation that is logged.

The capture path already has the right place for this. `_screen_unavailable()`
(`tools.py:2031`) is exactly "why the screen cannot be read", and it already
refuses two cases — a locked session and a DPMS-off monitor. It simply has no
notion of *what* is being shown.

**And one of its own principles is wrong for this check.** Its docstring says:

> Neither check is allowed to be the reason nothing works: if the query does
> not answer, the capture is attempted anyway.

That is right for the two it has. A lock check that fails open costs a
confusing read; a DPMS check that fails open costs fifteen seconds. A
sensitive-window check that fails open costs a vault. **The new guard has to
fail closed, and the file currently says the opposite** — so this is not only
adding a rule, it is carving out an exception and saying why.

## Proposed outcome

- A capture that would include a credential surface does not happen, and the
  model is told enough to change course without being told what it could not
  see.
- The guard fails closed. If the window list cannot be read, the capture does
  not proceed — the opposite of the two checks beside it, deliberately.
- The existing two guards keep failing open, because their reasoning still
  holds and this does not overturn it.
- Nothing on a normal desktop becomes harder. A guard that fires on a browser
  tab called "password reset" would be worse than the bug.

## Affected users and systems

- `src/omarchy_voice/tools.py` — `_screen_unavailable()` and the three capture
  callers that go through it.
- Everyone, on every read. This is not opt-in and should not be: a privacy
  guard nobody turns on protects nobody.
- Not the policy gate, not the schemas, not the model-facing tool descriptions.

## Constraints

- **Refusing must not leak what it refused.** "The focused window is a password
  manager" is a sentence about a category. The window's *title* is exactly the
  thing being protected, and must not appear in the refusal, the transcript or
  the log.
- **The check must cover what the capture covers.** `read_screen(target="screen")`
  captures a whole monitor, so a vault visible beside the browser is in the
  frame even though nothing about the focused window says so. The prior art
  checks only the focused window; that is not sufficient here, and matching it
  exactly would leave the common case — a password manager open on a second
  monitor — unprotected.
- It must not depend on a list staying current to be safe at all. A blocklist
  is a floor, not a ceiling, and the design should say which parts of the
  protection survive an unlisted application.
- No new runtime dependency; the window list already comes from `hyprctl`.

## Prior art

[PSthelyBlog/omarchy-hermes-companion](https://github.com/PSthelyBlog/omarchy-hermes-companion)
carries two blocklists at `daemon/perception.py:20-33` — one of window classes
(`1password|bitwarden|keepass|pinentry|gcr-prompter|polkit|…`) and one of title
substrings (`incognito|private browsing|bank|otp|…`) — and produces no frame
when either matches. MIT, so liftable with attribution.

It also has two guards this repo lacks and that belong to the same family:
`screen_shared()` (do not capture while a recorder or screencast is running)
and `mic_in_use()` (do not listen while another application holds the
microphone). The second is about the wake word rather than capture.

## Open questions

1. **Refuse the capture, or redact the window?** Refusing is honest, simple,
   and costs the user a read they may legitimately want. Blacking out the
   offending rectangle keeps the rest of the screen usable and is more code in
   the path that is already the slowest.

2. **Titles as well as classes?** A class list is precise and misses a bank in
   a browser tab. A title list catches that and will also fire on an article
   about passwords. Given the constraint above — that a false refusal is
   cheaper than a leak — which way should the error lean, and does the answer
   differ for `read_screen` (whole monitor) and `click_text` (one window)?

3. **Do `screen_shared()` and `mic_in_use()` come too?** They are the same
   family and the prior art is right there. They are also scope this issue did
   not ask for, and `mic_in_use` is about listening rather than capture.

4. **Does anything need to be able to override it?** A user who genuinely wants
   their vault read has no way to say so. Adding an escape hatch weakens the
   guarantee; not adding one means the answer is "close the window first",
   which may simply be correct.
