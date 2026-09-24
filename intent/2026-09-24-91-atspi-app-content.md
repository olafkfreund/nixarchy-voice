---
status: draft
issue: 91
author: olafkfreund
---

# Intent: read app content through the accessibility tree once Chrome exposes one

## Problem

Every question about what is on screen, and every click by visible text, goes
through pixels today. `_read_screen_text` finds a rectangle and hands it to
`_ocr_region` (`src/omarchy_voice/tools.py:3294-3298`), which runs
`grim -t ppm` and pipes the result into `tesseract`
(`src/omarchy_voice/tools.py:2796-2839`). `click_text` and `wait_for(text)` use
the same capture through `_ocr_words` (`src/omarchy_voice/tools.py:2901`). The
tool description tells the model outright that "OCR is imperfect on small or
stylised text" (`src/omarchy_voice/tools.py:1102`). #91 records 0.7–5.1 s for
grim and tesseract. That is paid on every read, and a misread word either
misses a click or needs a "did you mean" fallback (`_ocr_fold`,
`src/omarchy_voice/tools.py:313`).

The desktop already has an exact, structured, cheap source for the same text:
the AT-SPI tree. It was measured at about 0.068 s for a desktop query. #73
scoped it out (`spec/2026-09-24-73-read-before-ocr.md:15-55`, decision "no
AT-SPI code") for a reason that still holds today. The app the user actually
reads is Chrome, and Chrome exposes only its window frames.

Re-checked on p620 on 2026-09-24, read only:

```
$ busctl --user get-property org.a11y.Bus /org/a11y/bus org.a11y.Status IsEnabled ScreenReaderEnabled
b true
b true
$ tr '\0' '\n' < /proc/301556/cmdline | grep -c force-renderer-accessibility    # google-chrome-153.0.8010.52
0
# same check on both running Electron processes (Vesktop, and the other one): 0
```

A read-only walk of the live tree (PyGObject + the Atspi typelib, no bus
writes, 0.086 s for the whole desktop):

| App            | Nodes |
| -------------- | ----- |
| Google Chrome  | 9     |
| Chromium       | 2     |
| electron (x2)  | 2     |
| claude-desktop | 3     |
| spotifast      | 190   |

The bus is on, and GTK apps (spotifast here; Nautilus gave 291 nodes in #73)
publish real trees. The browser gives the reader nothing to read. #73's
census put 19 of the 28 logged screen actions in Chrome. Today's
`session.log` holds 14 `read screen` and 9 `click` actions but records no
window class, so that share was not recounted here.

The flag is not set anywhere in the NixOS config.
`~/.config/nixos/Users/olafkfreund/p620_home.nix:93-110` sets
`programs.chromium.commandLineArgs` for `google-chrome` without it, and the
web apps start through `omarchy-launch-webapp`
(`~/.config/nixos/home/browsers/chrome.nix:10-31`), which adds only
`--profile-directory`. `~/.config/chromium-flags.conf` doesn't have it either.
`grep -r force-renderer-accessibility ~/.config/nixos` finds nothing.

So there are two problems, and the second waits on the first:

1. Chrome and the Chrome web apps don't expose page content to AT-SPI on
   this desktop.
2. Even where a tree exists (GTK apps now, Chrome once it is launched with
   the flag), Oma doesn't read it. It captures and OCRs pixels.

## Proposed outcome

Once the browser exposes its content, "read me this page", "click Sign in"
and "wait until it says Done" in Chrome, a Chrome web app or a GTK app give:

- **Exact text**, not OCR guesses, with no misread-word fallbacks needed.
- **An answer in well under a second** rather than 0.7–5 s per read, when
  the answer comes from the tree.
- **Clicks that land** at the element's real on-screen position.
- **The same privacy behaviour as today.** Anything the sensitive-window,
  lock or recording guards refuse to OCR, they refuse to read from the tree
  as well.
- **Unchanged behaviour in every other case.** An app with no tree, or a
  tree that doesn't answer, falls back to OCR as it does now. The user never
  hears "nothing on screen" just because the tree was empty.

Until the flag is set, nothing the user sees changes.

## Affected users and systems

- **The user on p620**, whose screen reads are mostly in Chrome and its web
  apps (Discord, Google Maps and the others in `chrome.nix`).
- **razer**, which has its own `commandLineArgs`
  (`~/.config/nixos/Users/olafkfreund/razer_home.nix:64`) and would need the
  same launch change for the same outcome.
- **`src/omarchy_voice/tools.py`**: `read_screen`, `click_text`, `wait_for`
  and the capture guards (`_capture_refused` `:2649`, `_screen_unavailable`,
  `_sensitive_kind` `:2492`).
- **The Nix wrapper** for this package, which would need the Atspi typelib
  and PyGObject at runtime.
- **The nixarchy / NixOS config** (outside this repo), which owns how Chrome
  and the web apps are launched.
- **Every window on the desktop.** An accessibility tree exposes more than
  the pixels do (off-screen rows, hidden fields, full document text), so the
  privacy surface grows.

## Constraints

- **Dependency outside this repo.** `--force-renderer-accessibility` is a
  browser launch change in the user's NixOS config (`p620_home.nix`,
  `razer_home.nix`, the `omarchy-launch-webapp` wrapper). This repo can't
  and must not edit it. Oma must not relaunch Chrome with the flag or change
  it at runtime. Until the flag lands, work here only makes sense for apps
  that already expose a tree.
- **No bus writes.** A voice query never sets `IsEnabled` or
  `ScreenReaderEnabled`. It reads only when accessibility is already on. The
  state varies between sessions on this desktop, so "off" is a normal case,
  not an error. The ai-mirror reader turns the bus on as a side effect
  (`/mnt/data/Source-home/GitHub/ai-mirror/src/ai_mirror/a11y.py:62-74`), so
  it isn't the reader to use.
- **Privacy guards apply to every read path.** The sensitive-window, lock and
  recording guards (#46, #51, #52, #67) must cover tree reads exactly as they
  cover captures today, at one seam, so a later path can't skip them. The
  tree also exposes text that isn't painted (hidden or off-screen content),
  which OCR can't reach. The intent flags that exposure here; the spec should
  decide how to limit it. Nothing read from the tree goes into logs or traces
  (`trace.py` records phase names only).
- **OCR stays the fallback.** An empty tree means "unknown", not "empty
  screen". No app loses the OCR path it has today.
- **Known AT-SPI quirks from #73 and ai-mirror.** Extents are
  window-relative even when screen coordinates are asked for, so the window
  origin from `hyprctl clients` has to be added before any click. Chromium's
  role names are not the ATK spellings (`button`, not `push button`), and a
  wrong role string silently matches nothing.
- **In-repo, small reader.** Use PyGObject with the Atspi typelib, not the
  ai-mirror CLI (as #91 says).

## Open questions

1. **Who sets the flag, and where?** Is `--force-renderer-accessibility`
   added in the user's NixOS config (`programs.chromium.commandLineArgs` on
   p620 and razer, plus the web-app launcher), or in nixarchy's own Chromium
   and web-app defaults for every user? Does it need its own tracking issue
   in that repo, and does this issue wait for it to land?
2. **Is the flag's cost acceptable?** Forcing renderer accessibility makes
   Chrome build and keep an accessibility tree for every tab, all the time.
   Is the memory and CPU cost on p620 worth measuring first, or accepted
   as is?
3. **Scope before the flag lands.** Should this issue deliver tree reads for
   apps that already expose a tree (GTK apps), or stay blocked until Chrome
   does, as #73 decided? The logged usage shows no GTK app today.
4. **Privacy scope.** Should tree reads be limited to what is visible (on
   screen, not hidden), matching what OCR could see? Or is reading the whole
   document acceptable once the existing window-level guards have passed?
5. **Electron apps.** Vesktop and claude-desktop also expose only frames.
   Are they in scope (the same flag, set per app), or is it Chrome and its
   web apps only?
