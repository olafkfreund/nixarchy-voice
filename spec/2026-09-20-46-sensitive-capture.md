---
status: draft
issue: 46
intent: intent/2026-09-20-46-sensitive-capture.md
---

# Spec: do not read the screen when the screen is a password

The intent's four open questions were not answered at approval, so they are
decided here with the reasoning exposed, and the one that was testable was
tested rather than argued.

## The design

### 1. `_sensitive_in_frame(geometry)` — what the capture would include

A new check in `_screen_unavailable()`'s family, consulted by the same three
callers. It takes the rectangle about to be captured and asks whether any
**visible** window intersecting it is a credential surface.

Not the focused window. Measured on this desktop, `hyprctl -j clients` at rest:

```
DP-1: workspace 1, 3 window(s) in frame
DP-2: workspace 11, 1 window(s) in frame
HDMI-A-1: workspace 21, 1 window(s) in frame
```

A `read_screen(target="screen")` on DP-1 captures three windows. The prior art
checks only the focused one, so two of those three would be unexamined — and
"password manager open beside the browser" is the ordinary case, not the
exotic one. The check has to match the capture's extent or it is theatre.

For a window-scoped capture (`target="address:0x…"`, which #27 made the
common case) the rectangle is that window, so the check narrows with it.

### 2. Q2 — classes and titles, and the measurement that decided it

**Both, and the error leans toward refusing.**

The worry was that a title list would cry wolf. Tested against hermes' two
lists over all 12 windows open on this desktop: **neither list fires**. Not one
false positive on a working machine with a browser, six web apps, terminals,
Discord and a VM console.

That is one desktop at one moment and it is not proof of a low rate. It is
enough to say the fear was unevidenced, and the cost asymmetry decides the
rest: a false refusal costs one read and a sentence telling the user why. A
false pass sends a vault to a model and into the log. Those are not comparable,
so the list includes titles.

### 3. Q1 — refuse, do not redact

Refusing. Three reasons, in order of weight:

- **Redaction has to be right about geometry to be safe.** It would black out
  the rectangle `hyprctl` reports, and anything that rectangle does not cover —
  a popup, a tooltip, an autocomplete dropdown rendered as its own surface —
  stays in the frame. A guard that is *nearly* right about where the secret is
  is worse than one that declines, because it looks like it worked.
- It puts image manipulation in the capture path, which #26 spent its effort
  making cheaper.
- Refusing is legible. The user learns their vault is open; redaction teaches
  them nothing and they never find out the assistant was looking.

### 4. Q4 — no override

None. A user who wants their vault read closes the vault, and that is a
complete answer. An override is a setting that exists to be turned on once and
left on, and its whole function is to remove the protection — at which point
the guarantee is "unless someone disabled it", which is not a guarantee.

If real use shows this is too strict, the evidence will be specific and the
fix should be specific too: a narrower list, not a switch.

### 5. Q3 — `screen_shared` yes, `mic_in_use` no

`screen_shared()` is the same guard wearing a different hat: capturing while a
screencast is running puts the desktop into a recording the user is making for
someone else. Same path, same refusal, a few lines.

`mic_in_use()` is about the wake word, not capture. It is a good idea and it
belongs to a different part of the program; folding it in here would mean this
issue touched listening, which nothing in it is about. Filed separately rather
than absorbed.

### 6. Failing closed, against the file's own rule

`_screen_unavailable()`'s docstring says the checks must never be the reason
nothing works, and that an unanswerable query means the capture proceeds. That
stays true for the lock and DPMS checks and is **reversed for this one**, in
the same docstring, saying why: failing open there costs a confusing read;
failing open here costs the secret.

Concretely: if `hyprctl -j clients` cannot be read, the capture does not
happen. #24 made that distinguishable — `_query_rows` returns the failure — so
the check can tell "no sensitive windows" from "I do not know", which before
#24 it could not have.

### 7. The refusal says the category, never the title

> a password manager is on screen, so this was not captured. Ask the user to
> close or minimise it.

The class that matched is a category name. The **title** is the protected
thing, and it must not reach the refusal, the transcript, `session.log`, or the
`describe()` line the policy gate matches.

## Alternatives rejected

- **Match the focused window only**, as the prior art does. Measured above: it
  would examine one of the three windows in a DP-1 capture.
- **Classes only.** Misses a bank in a browser tab, which is the case a voice
  assistant is most likely to meet.
- **Redact.** See Q1 — a guard that is nearly right about where the secret is.
- **A config switch to disable it.** See Q4.
- **An allowlist of readable applications** instead of a blocklist of forbidden
  ones. Safer in principle and unusable in practice: the assistant's job is
  reading arbitrary windows, so the allowlist would be "everything", maintained
  by hand.

## Risks

- **A blocklist is a floor.** An unlisted password manager is unprotected, and
  no amount of list-tending fixes that. What survives an unlisted application
  is the lock-screen guard and nothing else. The spec should not pretend
  otherwise, and the README should not either.
- **False refusals are invisible to me and annoying to the user.** Zero on this
  desktop today is one sample. The refusal names the category, so a wrong one
  is at least diagnosable by the person who hits it.
- **Failing closed can make reads fail where they used to work**, when
  `hyprctl` is briefly unavailable. That is the intended direction and it is
  new behaviour; it needs its own test.
- **Intersection maths is a place to be wrong.** A window partly off-screen, a
  negative monitor origin, or a fullscreen surface could all be mis-tested.
  Simplest safe rule: if the geometry cannot be decided, treat the window as in
  frame.

## Verification

1. **A listed class in the frame refuses**, and nothing is captured —
   `subprocess` never called.
2. **A listed class on another workspace does not refuse.** The guard is about
   what is in the frame, not what exists.
3. **The unfocused case**, which is the one the prior art misses: a vault
   beside the focused browser, both on the captured monitor, refuses.
4. **A window-scoped capture narrows with it**: `read_screen(target=the
   browser)` succeeds while the vault is open on the same monitor.
5. **The refusal leaks nothing.** A window titled with a distinctive sentinel
   refuses, and the sentinel appears in neither the result, the transcript nor
   the log.
6. **Failing closed**: with the client query failing, the capture does not
   happen. New behaviour, asserted deliberately.
7. **The other two guards still fail open**, unchanged.
8. **No false refusal on an ordinary desktop** — the 12 real windows measured
   above, as a fixture.
9. Suite green, `nix flake check`, `verify-gate` 0.
