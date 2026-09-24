---
status: draft
issue: 73
author: olafkfreund
---

# Intent: read the screen from a source that knows before photographing it

Closes #73.

## Problem

Every look at the screen is a photograph. `read_screen` goes `_target_geometry`
→ `_ocr_region` (`src/omarchy_voice/tools.py:2897-2901`); `click_text` and
`wait_for(text)` go through `_ocr_words` (`tools.py:2504`). There is no other
read path for a window's contents. tmux (`read_terminal`), the clipboard and
`system_query` exist as separate tools, but nothing routes a screen question to
them, and nothing in the repo reads AT-SPI or MPRIS.

Before each photograph, the privacy guards run as separate processes. Counted
by wrapping `subprocess.Popen` around the real code on this machine
(Hyprland 0.56.0, 2560x1440), read-only, with the pointer dispatch left out of
`click_text`:

| Path | External processes before the answer |
|---|---|
| `read_screen("screen")` | 8: `hyprctl monitors` ×3, `clients` ×1, `omarchy-shell lock isLocked`, `pw-dump`, `grim`, `tesseract` — plus a `/proc/*/comm` scan |
| `read_screen("activewindow")` | 9: the above plus one more `clients` |
| `click_text("screen")` | **19**: `monitors` ×7, `clients` ×4, lock ×2, `pw-dump` ×2, `grim` ×2, `tesseract` ×2 — plus two `/proc` scans |

`click_text` OCRs twice by design: the full read, then the 300x60 re-check in
`_target_moved` (`tools.py:2665`, called at `2725`) that #60 added. Each OCR
carries the whole guard chain, because the guards live at the capture seam
(`_screen_unavailable() or _capture_refused()` in `_ocr_region` and
`_ocr_words`). That placement is deliberate and correct; the repetition inside
one tool call is not.

Each guard, timed individually through the real `Executor` methods (median of
9 unless stated):

| Guard | Where | Median | Range |
|---|---|---|---|
| `omarchy-shell lock isLocked` | `_session_is_locked`, `tools.py:2487-2502` | **92.8 ms** | 52–247 ms |
| `/proc/*/comm` scan | `_recorded_by_process`, `tools.py:2158` | 52.8 ms | 50–56 ms |
| `pw-dump` | `_recorded_by_pipewire`, `tools.py:2133` | 27.3 ms | 26–29 ms |
| `hyprctl -j clients` | `_query_rows` | 14.7 ms | 14–16 ms |
| `hyprctl -j monitors` | `_query_json` | 14.5 ms | 12–16 ms |
| `_screen_unavailable` (lock + monitors) | `tools.py:2438` | 68.6 ms | 65–80 ms |
| `_capture_refused` (monitors, clients, pw-dump, /proc) | `tools.py:2252-2292` | 112.4 ms | 107–113 ms |
| **whole guard chain, one capture** | | **179.4 ms** | 174–185 ms |
| `_input_refused("screen")` (#67) | `tools.py:2179` | 55.4 ms | 54–60 ms |

Against that, the photograph itself:

| Step | Median (n=5) |
|---|---|
| `grim -t ppm`, active window (2530x1384, one tiled window) | 60.6 ms |
| `tesseract` on that frame | **4967 ms** (4.8–5.2 s) |
| `_ocr_region` end to end, same window | **5125 ms** |
| `_ocr_words`, 300x60 verify region | 376 ms |
| `read_screen("activewindow")` on a lighter window, minutes later | 685 ms |

So the guards are not the main cost: 0.18 s per capture, ~0.4 s across a
`click_text`, against 0.7–5 s of tesseract that swings with how much text is
in the window. "Window-scoped OCR" (#26) does not help much on this desktop,
because a single tiled window is nearly the whole monitor. **The expensive
thing is OCR, and the only way to make it cheaper is not to do it.**

### What could answer instead, measured here today

**AT-SPI is currently enabled on this desktop.** Read, not changed:

```
busctl --user get-property org.a11y.Bus /org/a11y/bus org.a11y.Status IsEnabled ScreenReaderEnabled
b true
b true
gsettings get org.gnome.desktop.interface toolkit-accessibility  ->  true
```

That contradicts the 2026-09-20 finding that it is off by default, and the
cause is not established: ai-mirror's control state is `owner: off` and its
audit log shows no grant since 2026-09-22 22:48, so it is not an agent holding
control. `QT_ACCESSIBILITY` is unset and no running Chrome carries
`--force-renderer-accessibility`.

A read-only census (`Atspi.get_desktop(0)` and a depth-30 walk, no bus
property written, no action taken):

| Application | Nodes | Walk |
|---|---|---|
| desktop root, 15 apps | — | 5.7 ms |
| Google Chrome (2 windows + 6 web apps) | 14: `frame` ×13, `application` | 3.6 ms |
| claude-desktop, electron | 3, 2: frames only | <1 ms |
| quickshell, 1password, tray apps | 1 each: `application` only | <1 ms |
| `org.gnome.Nautilus` (GTK4) | **291**: panel, table cell, label, button… | 142.6 ms |
| `foot` | absent | — |
| whole desktop | | 162 ms |

So with both bus flags on, exactly one open app has content. The Chrome
windows, which are where every screen read in the log happened (below), expose
frames and nothing else, as #31 found. `Chromium` wants
`--force-renderer-accessibility` at launch whatever the bus says. When it has
it, a link comes back with bounds and an action in 0.068 s — but those bounds
are **window-relative** even for `CoordType.SCREEN`, and Chromium's role is
`button`, not ATK's `push button`, so a wrong role string returns `[]` that
looks exactly like "exposes nothing".

**MPRIS is available and nearly empty.** `playerctl -l` answers in 6.7 ms
(median of 7) and lists one player, `chromium.instance301556`, status
`Stopped`, with no metadata (`playerctl -a metadata` → "No player could handle
this command"). `playerctld` is running. `playerctl` is on the system PATH but
not in this package's wrapper (`nix/package.nix:63-87`).

**D-Bus for network and battery is already covered** without D-Bus:
`system_query` reads `nmcli` and `/sys` (`tools.py:1255-1326`). The issue's
"D-Bus for NetworkManager/UPower" is not a gap.

### What people actually asked to read

`~/.local/state/omarchy-voice/session.log` (2906 lines; `action` lines from
2026-09-11 to 2026-09-13, none recorded after that — small sample):

| Action | Count | Chrome (web page or web app) | Spotify |
|---|---|---|---|
| `read screen` | 14 | 11 (NRK, cinema listings, YouTube) | 3 |
| `click … on` | 9 | 5 | 4 (Search, Find, a playlist, Play) |
| `wait for text` | 5 | 3 | 2 |
| `screenshot` | 0 | | |

Terminals went through tmux (`read/run/watch terminal`, 14 actions) and never
through OCR, which is the one semantic path that already works. Of the 28
screen actions, **none would have been answered by AT-SPI as this desktop is
configured**: 19 were Chrome, which exposes frames only, and the Spotify
client is the one app #31 saw wake under `ScreenReaderEnabled`, but it is not
running now to re-measure. One Spotify sequence ended in `press space` to
play — which MPRIS does directly, without reading anything.

## Proposed outcome

- A screen question is answered by the cheapest source that actually knows,
  and OCR is the fallback, not the first attempt. The order to argue about is
  the issue's: tmux → clipboard → AT-SPI → MPRIS → window OCR → full-screen OCR.
- A semantic source that returns nothing is never reported as "nothing is
  there". Empty from an app that exposes no tree is *unknown*, and falls
  through to OCR (#24's rule, applied to a new source).
- One tool call pays for each guard at most once: `click_text` does not run
  the lock check, `pw-dump` and the `/proc` scan twice, or `hyprctl monitors`
  seven times.
- Media questions ("what's playing", "pause it") do not photograph Spotify.
- The trace (#23/#39) shows which source answered, so the fallback rate is
  measured rather than guessed.

## Affected users and systems

- `src/omarchy_voice/tools.py`: `_read_screen_text`, `_ocr_region`,
  `_ocr_words`, `_tool_click_text`, `_target_moved`, `_tool_wait_for`, and the
  guard helpers `_screen_unavailable`, `_session_is_locked`,
  `_capture_refused`, `_windows_in`, `_visible_workspaces`, `_screen_is_recorded`.
- `nix/package.nix` if a new runtime tool (`playerctl`, `ai-mirror`, or the
  Atspi typelib) joins the wrapper.
- `nix/hm-module.nix` if AT-SPI enablement becomes something this package sets.
- The sibling repo `ai-mirror`, whose `src/ai_mirror/a11y.py` already has
  `tree`/`find`/`act`, a per-app and per-frame privacy filter
  (`_walk`, `a11y.py:144-187`) and bus enablement with restore
  (`ensure_enabled`/`release_bus`, `a11y.py:77-107`). Its binary is already on
  PATH here (`/etc/profiles/per-user/olafkfreund/bin/ai-mirror`).
- Both engines and all four entry shapes, since they share one `Executor`.

## Constraints

- **Every guard that protects a photograph must protect any new read path,
  and must stay at a seam a later read path cannot forget** (the reason
  `_capture_refused` sits at the capture, `tools.py:2252-2262`):
  - Sensitive windows (#46): a password manager, credential prompt or private
    window is refused by category, never by quoted title. AT-SPI exposes
    `1password` as an application node on this desktop right now; it must be
    withheld exactly as `_sensitive_kind` withholds its window, using this
    repo's configurable `sensitive_patterns`, not ai-mirror's separate list.
  - Fail closed when the window list cannot be read (#51, `tools.py:2311-2319`).
  - Lock (`_session_is_locked`): AT-SPI and MPRIS keep answering behind a
    lock screen. The reason the lock guard exists — the pixels are the lock
    screen — goes away; the reason it matters — a voice at a locked machine
    reading what is behind it — does not. A semantic read must refuse while
    locked.
  - Recording (#52): fails open, deliberately. Whether it applies at all to a
    read that produces no frame is a question below, not an assumption.
  - Input (#67, `_input_refused`, `tools.py:2179`) stays in front of any
    AT-SPI action exactly as it stands in front of a click.
- **Guard caching must not weaken a guard.** A cached "not locked" or "no
  sensitive window" must not outlive the state it describes long enough for a
  vault opened in between to be read. Whatever is shared must be shared within
  one tool call, or carry a TTL the approver chooses.
- **AT-SPI bounds are window-relative.** Anything clicked from them adds the
  window origin from `hyprctl clients`; bounds are never clicked as returned.
- **Role names are not ATK spellings** (`button`, not `push button`); an empty
  match is not evidence of absence.
- **No setting on the live desktop changes as a side effect of reading it.**
  ai-mirror's `ensure_enabled` writes `IsEnabled` and `ScreenReaderEnabled`
  on first read; that must not happen implicitly from a voice query.
- Falls back to OCR only on a definite "not found", never on a timeout after
  an action may have landed (#31, Codex review).
- New runtime dependencies are justified against the wrapper list
  (`nix/package.nix:63-87`); `tests/` stays plain unittest with
  `Executor._shell` as the seam, so new sources route through it.

## Open questions

1. **Is this blocked, as #31 was?** #31 closed because no app worth reading
   exposes a tree here. Its unblockers, nixarchy#821 and #823, are now both
   closed, but Chrome on this desktop is still launched without
   `--force-renderer-accessibility`, and that is where 19 of 28 logged screen
   actions went. Should this intent cover only the guard sharing and MPRIS now,
   and leave AT-SPI until nixarchy launches Chromium with the flag?
2. **Who enables AT-SPI?** It is on here today for a reason not yet found.
   Options: this package's HM module sets it (and `QT_ACCESSIBILITY=1`); leave
   it to nixarchy; or read only when it is already on and never switch it.
3. **Reuse ai-mirror or read in-repo?** Shelling out to the `ai-mirror` CLI
   takes no Python dependency but inherits its bus-enabling side effect, its
   own privacy list and its ownership gate. Importing `a11y.py` skips that gate
   (#31). A small in-repo reader needs PyGObject and the Atspi typelib in the
   wrapper — `import gi` works in the system Python here, but the `Atspi`
   namespace does not load without `GI_TYPELIB_PATH`.
4. **Guard caching: per call or TTL?** Sharing within one tool call removes
   the duplicates in `click_text` (19 processes → 8: one each of `monitors`, `clients`, lock and `pw-dump`, two each of `grim` and `tesseract`) with no staleness at
   all. The issue proposes a ~2 s TTL across calls, which would also cover
   `wait_for(text)` polling. Which, and if a TTL, how long?
5. **Does the recording guard apply to a read with no frame?** Text from
   AT-SPI never lands in a video. The spoken answer can, if the microphone or
   speakers are being recorded — but that is equally true of every other tool.
6. **Is MPRIS worth a tool now?** One player is registered and it is stopped.
   Media showed up once in the log (a `press space` to play). Ship it as part
   of this, or split it out?
