---
status: draft
issue: 46
intent: intent/2026-09-20-46-sensitive-windows.md
---

# Spec: refuse the capture, not the window, and say which kind

## The intent's open questions, answered

1. **Refuse, do not redact.** Blacking out a region means image work before OCR
   and more ways to be subtly wrong; a refusal is one branch. It also matches
   what this codebase already chose twice — ai-mirror#15's guard and #18's
   empty-find note both say so rather than return something.
2. **The unit is the capture rectangle, not the focused window.** This is the
   hard half and the answer is available for free: `hyprctl clients` returns
   `at`, `size`, `workspace`, `mapped` and `hidden`, and `_visible_workspaces()`
   (`tools.py:1977`) already exists. Every mapped, non-hidden window on a visible
   workspace whose rectangle intersects the capture rectangle is checked. A
   password manager beside the thing being read is caught.
3. **`click_text` and `wait_for(text)` get it too, because the guard is not
   per-tool.** It sits at the capture seam, so every consumer inherits it and a
   capture path added later cannot forget. Refusing `read_screen` while
   `click_text` still OCRs the same pixels would leak the same text through word
   boxes.
4. **A hard-coded floor plus user additions**, in the shape `deny_patterns` and
   `deny_patterns_replace` already use in `[hands]`. Precedent exists; a second
   idiom would be worse than a familiar one.
5. **The matcher is tested directly; the seam is tested with fabricated client
   lists.** The desktop state that matters is one a test must not create.
6. **`mic_in_use` is out of scope.** Different subsystem, different failure. Its
   own issue.

## Design

### 1. One matcher, two regular expressions

A class pattern and a title pattern, seeded from
[omarchy-hermes-companion](https://github.com/PSthelyBlog/omarchy-hermes-companion)
`daemon/perception.py:20-33` (MIT, attributed in the source). Both are needed:
measured on this desktop a Gmail window reports
`class='chrome-fmgjjmmmlfnkbppncabfkddbjimcfncm-Profile_4'`, an opaque extension
id, and only its title says what it is. A class-only guard would miss every web
app, which is how this desktop runs most things.

The matcher returns **which pattern matched and a category**, never the text it
matched on.

### 2. The guard sits at the capture seam

`_ocr_region` and `_ocr_words` both resolve a geometry and then shoot it. The
check goes with the geometry, beside `_screen_unavailable()` (`tools.py:2044`),
which is already where "do not shoot this" lives.

Given the capture rectangle, it asks `hyprctl clients` — a query the capture
path already makes — for mapped, non-hidden windows on a visible workspace, and
refuses if any intersecting one matches.

### 3. The refusal names a category, and tells the model what to do instead

The title is the sensitive material: on this desktop right now, window titles
carry an inbox count, an email address, and what is being watched. So the
refusal says *what kind of thing* was refused and nothing more:

> a password manager is visible in that area, so it was not read. Read a
> specific window instead — `read_screen` with `target` set to a window address
> from `hypr_query(clients)` — or ask the user to close it.

The second sentence matters as much as the first. A monitor-wide refusal with no
way forward turns one blocked read into a stuck task, and `target` already
accepts an address, so the way out exists and only needs naming.

### 4. It says it is a heuristic

Whatever documents this states plainly that a blocklist misses things and
misfires. Overclaiming here would be worse than the gap: an agent — or a person
— who believes the screen is now safe behaves differently from one who knows it
is best-effort.

## Alternatives rejected

- **Guard the focused window only.** What the prior art does, and it has an
  easier problem: it only ever captures the focused window. `read_screen`
  defaults to a whole monitor, so this would pass exactly the case worth
  catching.
- **Redact the region.** Keeps the rest of the read usable. Needs image
  manipulation before OCR, and a redaction that is off by a few pixels is a leak
  that looks like it worked.
- **Guard per tool.** Three call sites to keep in step, and a fourth added later
  that nobody wires up.
- **Match on title only.** Simpler, and it misses `pinentry` and `gcr-prompter`,
  whose titles are generic and whose classes are exactly what identifies them.
- **Block the process by pid.** More precise and needs a process table walk;
  measured at 323 ms for `pgrep` on this host's 21,731 processes (#47), against
  a client query the capture already performs.
- **Refuse only when the model asks for text, not coordinates.** `click_text`
  leaks through word boxes just as well.

## Risks

- **Over-refusal on monitor-wide reads.** A password manager open anywhere on
  the focused monitor blocks every `read_screen` on it. Mitigated by the refusal
  naming the per-window route, but it will annoy somebody, and that is the cost
  of the safe default.
- **Title false positives.** A documentation page about passwords is refused.
  The right side to err on, and the reason additions are configurable.
- **A heuristic sold as a guarantee.** Called out in the design because it is the
  failure mode that turns a useful mitigation into a false sense of safety.
- **`pinentry` and `gcr-prompter` may be short-lived or unmapped.** Layer
  surfaces on this desktop are only `omarchy-background` and `omarchy-bar`, so
  they should be ordinary clients — but neither was running to confirm it
  directly, and that is the case the guard most wants to catch.
- **A window can appear between the check and the capture.** The gap is
  milliseconds and unclosable without compositor support. Worth stating, not
  worth engineering around.

## Verification

**The matcher, directly.** Each seeded class and title pattern matches what it
should, and the returned category never contains the matched text. A window
titled `Gmail - Freundcloud - Inbox (7) - <address>` is caught by title, since
its class cannot identify it.

**The seam, with fabricated client lists.** No real desktop, per the existing
discipline. A sensitive window intersecting the rectangle refuses; the same
window on a non-visible workspace does not; the same window on another monitor
does not; a non-sensitive window never refuses.

**The refusal text, by reading it.** It must name a category, contain no title,
and name the per-window route. That is checked by a human reading it, not only
by asserting a field exists.

**Nothing regressed.** The full suite green, and `read_screen` on an ordinary
screen behaves exactly as before.

**Not verified here.** Whether a live `pinentry` or `gcr-prompter` is reported as
a mapped client with a usable class. That needs one on screen, and is the first
thing to check by hand when this lands.
