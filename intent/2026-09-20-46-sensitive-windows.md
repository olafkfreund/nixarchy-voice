---
status: approved
issue: 46
author: olafkfreund
---

# Intent: read_screen will OCR a password manager, and hand the model the text

## Problem

Nothing here declines to capture a window because of what is in it.
`grep -rniE "1password|keepass|pinentry|sensitive"` over `src/omarchy_voice/`
returns an unrelated `incognito` launch string and some `case-insensitive`
comments in `keys.py`. That is all.

So `read_screen` — and `click_text` and `wait_for(text)`, which capture the same
way — will OCR a 1Password vault, a `pinentry` prompt mid-passphrase, a
`gcr-prompter` dialog or a bank's dashboard, and hand the model **the text**.

Text is the worse half of this problem. A picture of a passphrase has to be
read; extracted text is already quotable, already in the transcript, and already
in whatever the model says next. 1Password is installed on this machine and
`gcr-prompter` is in the store, so this is a live exposure.

`_screen_unavailable()` (`tools.py:2044`) already asks whether the session is
locked and whether the monitor is DPMS-off, so the capture path has a place
where "do not shoot this" lives. It has no notion of *what* is on screen.

**Three findings from scoping, each of which shapes the answer:**

1. **A class blocklist cannot see a Chrome web app.** Measured here, a Gmail
   window reports `class='chrome-fmgjjmmmlfnkbppncabfkddbjimcfncm-Profile_4'`,
   an opaque extension id. Only its title says what it is. This desktop runs
   most things as web apps, so a class-only guard would miss the common case.
2. **The title is itself sensitive.** The same window's title carries an inbox
   count and an email address. Others on screen carry what is being watched.
   The title is the only usable signal *and* must never appear in a refusal.
3. **`read_screen` defaults to a whole monitor, not a window.**
   `_target_geometry` maps `"screen"`, `""`, `"monitor"` and `"all"` to the
   focused monitor's rectangle. So a guard keyed on the focused window would
   pass a capture that includes a password manager sitting beside it. That is
   the harder half and the one most likely to be waved past.

## Proposed outcome

- A read that would include a credential prompt, a password manager or a private
  browsing window does not happen, and the model is told why in terms it can act
  on.
- The reason names the kind of thing refused and never the window's title.
- The refusal is not silently a partial read. An OCR that quietly omitted a
  region would be worse than one that refused, because the model would believe
  it had seen the screen.
- Somebody adding a capture path later trips over the guard rather than missing
  that it exists.

## Affected users and systems

- Everyone using `read_screen`, `click_text` and `wait_for(text)`, on every
  backend — this is the native path, not a `desktop_control` feature.
- `src/omarchy_voice/tools.py`: the capture helpers, `_target_geometry`,
  `_screen_unavailable`.
- The persona and tool descriptions, if the model needs to know a refusal is
  possible and what to do instead.
- The human, who is the person this is for.

## Constraints

- **A refusal must not leak what it refused.** "the focused window is a password
  manager" is defensible; the title is not. Easy to get wrong while trying to be
  helpful.
- **Must not be class-only.** See the Gmail finding.
- **Must not return a partial or blank read.** Say so, do not silently omit.
  `scroll`'s old Page Up/Down fallback is the shape to avoid: it looked like it
  worked.
- **Must not claim more than a blocklist can deliver.** It is a heuristic; it
  will miss and it will misfire, and whatever documents it must say so rather
  than imply the screen is now safe.
- **Must not need a new dependency.** `hyprctl clients` already returns class
  and title, and the capture path already queries it.
- Tests must not reach the real desktop.

## Open questions

1. **Refuse, or redact?** Refusing is simple and honest. Blacking out a region
   keeps the rest readable, needs image work before OCR, and has more ways to be
   subtly wrong.
2. **Monitor-wide reads are the hard case.** Does a sensitive window anywhere in
   the captured rectangle refuse the whole read? That is the safe answer and it
   will refuse a lot — a password manager open on a second workspace of the same
   monitor would block ordinary work.
3. **Does `click_text` get the same treatment?** It captures the same way, but
   its output is coordinates rather than text. Refusing it is more disruptive and
   leaks less.
4. **Whose list, and can it be changed?** A hard-coded floor plus user additions
   is the usual shape; for this subject a floor nobody can lower is also
   defensible.
5. **How is it tested?** The desktop state that matters — a pinentry on screen —
   is one a test may not create. The matcher can be tested directly; the wiring
   needs something else.
6. **Is the adjacent `mic_in_use` guard part of this?** An assistant that speaks
   mid-call is a different failure in a different subsystem, and probably its own
   issue.

## Prior art

[PSthelyBlog/omarchy-hermes-companion](https://github.com/PSthelyBlog/omarchy-hermes-companion),
MIT, does exactly this for an always-on screen-watcher at
`daemon/perception.py:20-33` — two regular expressions, one over class and one
over title, and no frame is produced when either matches. Reusable with
attribution, and a reasonable floor. Its title list already covers banking and
private browsing, which given finding 1 is the half that matters most.

Note that it has an easier problem than this one: it only ever looks at the
focused window, so finding 3 does not arise for it.
