---
status: approved
issue: 58
author: olafkfreund
---

# Intent: Let the calling agent see the screen

## Problem

nixarchy-voice exposes an MCP server, so the model is **the caller's** — Claude
for one user, opencode with a local model for another. We do not choose it.

But no caller can look at the screen. There is no screenshot tool:
`_tool_read_screen` (`tools.py:2434`) is the only way to see anything and it
returns tesseract's text, and every MCP return path is `TextContent`
(`mcp_server.py:158,162,164,169`). There is no `ImageContent` in the repo.

So we capture the screen, degrade it to `wordow`, `Toaay`, `1ssues-«`,
`Xpiinsitbelpackaging` (#32), and hand over the degraded version — to a client
that may have been perfectly able to read the original. The capture is already
there; we are throwing the good version away.

ai-mirror already does the right thing (`mcp.py:122` returns
`{'type': 'image', 'mimeType': 'image/png', ...}`). voice is the outlier, and
the two repos should not disagree about something this basic.

## Proposed outcome

A client that can see, sees. A client that cannot, behaves exactly as today.

Concretely: a caller can obtain the pixels of a target it is allowed to look at,
and `read_screen` keeps returning text for everyone, unchanged.

## Why this and not a better OCR engine

#48 measured six vision models and found single-word localisation ranging from
**0.1 to 110 word-heights**. That spread says we cannot depend on the caller's
vision ability — but it cuts both ways: we also cannot *deny* it. Some callers
read a screen far better than tesseract does, and today we prevent them from
trying.

Handing over pixels requires no model choice, no new dependency, no API key, and
no bet on anyone's grounding ability. The client decides what it can do with
them.

## Affected users and systems

`mcp_server.py` and `tools.py` in nixarchy-voice. MCP callers only. The spoken
voice path has no use for an image and should be unaffected.

## Constraints

- **The privacy guards must cover this, and more strictly than they cover OCR.**
  `_screen_unavailable`, `_capture_refused` and `_screen_is_recorded` (#46, #52,
  #55) currently gate reads because a capture can contain a password manager, a
  credential prompt or banking. An image is a *stronger* disclosure than a lossy
  transcription of it: OCR of a password field yields little, a screenshot of one
  yields everything. Returning an image must not open a seam around those checks.
- tesseract stays as the floor for callers without vision (#48).
- `click_text` is untouched. Its boxes must stay model-independent.
- No new runtime dependency; the capture path already exists.

## Open questions

1. **A new `screenshot` tool, or `read_screen` gaining an image?** A screenshot
   costs a vision client a large slice of its context window; OCR text is a few
   hundred tokens and greppable. A client may want the text *even when it can
   see*, so the caller should choose rather than always receive both.
2. **What does it cost?** `grim -t jpeg -q80` was 124 ms for a full layout and
   `grim -o DP-1 -t ppm` 48 ms for one monitor (#26), so capture is cheap — but
   an image is large in tokens, and that is the real budget. Worth measuring
   before choosing a default format and scale.
3. **Should the image be scaled down by default?** #48 found 1280-wide halves a
   13 px row to 6.5 px and wrecks small-text localisation, so a downscale that
   helps the token budget may make the image useless for the thing a vision
   client would want it for.
4. **Does ai-mirror's shape port directly**, or does voice's `Result` type need a
   different seam? ai-mirror returns text plus an optional image from one tool;
   voice's tools all return a single `Result` carrying a string.
