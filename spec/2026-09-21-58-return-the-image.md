---
status: draft
issue: 58
intent: intent/2026-09-21-58-return-the-image.md
---

# Spec: Let the calling agent see the screen

## Design

A new MCP tool, `screenshot`, returning the pixels of a target alongside a short
text line saying what was captured. `read_screen` is untouched.

```
screenshot(target="active") -> [TextContent(what was captured),
                                ImageContent(image/png)]
```

Four decisions, each measured rather than argued.

### 1. A separate tool, not an image bolted onto `read_screen`

Measured on one 2560x1440 screen: `read_screen` returns 119 words, 753
characters, **~188 tokens**. The same screen as an image costs **~1843 tokens**
at Claude's 1568-long-edge cap. **Roughly 10x.**

A caller that only wants to grep for a word should not pay 10x to do it, and a
caller that wants pixels should not have to parse text it did not ask for. The
choice belongs to the caller, so it is a separate call. This settles open
question 1 in the intent.

### 2. No downscaling on our side

The intent posed this as a trade: shrink the image for the token budget, or keep
it large so small text stays legible. **The trade is false.** Clients cap
independently — Claude resizes anything over 1568 on the long edge — so a
2560-wide capture arrives as 1568 regardless, and a 13 px row becomes 8 px. If we
downscale first, the client downscales our already-degraded image again.

So we send the target at native resolution and let each client apply its own cap
once. This also keeps us out of guessing caps we do not control and cannot track.

### 3. Default `target="active"`, which deliberately differs from `read_screen`

Consequence of the cap arithmetic: a full monitor is close to useless to a vision
client. 2560x1440 arrives as 1568x882, so body text at 13 px lands at 8 px.
A typical window is already under the cap and arrives untouched, with its text at
full size.

`read_screen` maps `"screen"` to the focused monitor (`tools.py:2155`) and that
stays. `screenshot` defaults to the focused window instead. **The divergence is
deliberate** and is documented in the tool description, because OCR reads a large
region fine while a vision client cannot. `target` still accepts the same values,
so a caller that genuinely wants the monitor can ask.

### 4. PNG

Matches ai-mirror (`mcp.py:122`), and is lossless, which matters most for exactly
the small text that is already marginal after the client's cap. `CAPTURE_CMD`
uses PPM today because #26 measured it fastest for piping into tesseract; that
stays for OCR. The image path uses `grim -t png`.

### Privacy: the same seam, unchanged

Both OCR entry points open with one expression — `_ocr_region` (`tools.py:2167`)
and `_ocr_words` (`tools.py:2278`):

```python
if blocked := self._screen_unavailable() or self._capture_refused(geometry):
```

`_capture_refused` (2065) already covers sensitive windows (#46) and recording
(#52, via `_screen_is_recorded` at 2100), keyed on the captured rectangle.

`screenshot` uses **the same expression, before capture**, so the guard cannot be
bypassed by preferring the new tool. This is a stronger requirement than for OCR
and the intent says why: OCR of a password field yields little, a screenshot of
one yields everything. No new guard, no second code path — the existing one, in
the same position.

## Alternatives rejected

- **`read_screen` returns text plus image.** Imposes ~10x tokens on every caller,
  including the ones that only wanted to grep. Rejected on the measurement above.
- **Downscale to a fixed width before returning.** Double-degrades, because the
  client caps anyway. 1280-wide was measured in #48 to halve a 13 px row to 6.5 px
  and wreck small-text reading.
- **JPEG to save bytes.** Lossy on the text that is already marginal. Revisit only
  if message size proves to be a real limit; the format is a one-line change.
- **Pick a vision model and OCR with it.** #48: localisation spread across six
  models was 0.1 to 110 word-heights. Not ours to choose.
- **Default `target="screen"` for symmetry with `read_screen`.** Symmetry would
  hand vision clients a near-illegible image by default. Consistency is worth less
  than a usable default.

## Risks

- **A screenshot discloses more than OCR of it.** The mitigation is the whole of
  the privacy section: identical guard, same position, before capture. The test
  for this asserts no capture is spawned when the guard refuses, rather than
  asserting on the returned text.
- **Message size.** A 2560x1440 PNG measured 499,796 bytes, ~666 kB base64. Some
  MCP clients may have limits we do not know. The `active` default keeps the
  common case far smaller; if a client balks on a full monitor, JPEG is the lever.
- **Callers may not expect a 1843-token reply.** The tool description states the
  cost and says `read_screen` is ~188 tokens for the same screen.
- **A second capture seam to maintain.** Real but small: it reuses
  `_target_geometry` and the guard expression, so what is new is the grim format
  and the MCP return type.

## Verification

- Unit: guard refuses (sensitive window, recording, all monitors asleep) and
  **no capture subprocess is spawned** — asserted on the spawn, not the message.
- Unit: a permitted target returns one `TextContent` and one `ImageContent` with
  `mimeType: image/png`.
- Unit: `read_screen` output is byte-identical to before, so nothing regressed.
- `nix flake check` green, full suite green.
- Manual: call `screenshot` from an MCP client and confirm the image renders and
  its text is legible at the client's own scaling.
