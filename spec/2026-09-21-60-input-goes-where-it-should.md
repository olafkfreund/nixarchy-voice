---
status: draft
issue: 60
intent: intent/2026-09-21-60-input-goes-where-it-should.md
---

# Spec: input that can say where it landed

Three defects, one user-visible outcome: the agent acted on the wrong thing and
reported success. Each gets a different fix because each has a different cause.

**The intent's open question 1 turned out to have a better answer than the issue
assumed, and it changes the design.** Measured before specifying, below.

## What was measured

### `send_key_state` accepts a `window`, so text can be compositor-routed

The issue assumed `type_text` probably could not be targeted: Hyprland
dispatches a *chord* to a named window and there is no send-text equivalent.
There is a third option it did not consider. Probed against live Hyprland
0.56.2 with a **nonexistent** window address, so nothing could receive a key:

```
$ hyprctl dispatch 'hl.dsp.send_key_state({ key="a", state="down", window="address:0xdeadbeef" })'
error:   hl.send_key_state: 'mods' is required
warning: send_key_state: window not found        ← only appears when window is passed

$ hyprctl dispatch 'hl.dsp.send_key_state({ key="a", state="down", frobnicate="x" })'
error:   hl.send_key_state: 'mods' is required   ← no window warning
```

The `window not found` warning appears **only** when `window` is supplied, and a
bogus field produces nothing. So `send_key_state` resolves a window the same way
`send_shortcut` does — **the compositor does the routing, and there is no window
between choosing the target and the key arriving.**

### Batching makes per-character dispatch free

One dispatch per key event, at 13.8 ms each, would cost 1.10 s for a 40-character
string. `hyprctl --batch` collapses it:

| | 80 key events |
|---|---|
| one dispatch each | 1104 ms |
| one `--batch` call | **16.8 ms** |

66x. A 40-character string routed to a named window costs about **17 ms**,
which is comparable to the `wtype` subprocess spawn it replaces.

### Re-reading a small region is ~21x cheaper than the read it validates

| | full screen (2560x1440) | 300x60 region |
|---|---|---|
| `grim` | 59.8 ms | 19.9 ms |
| `tesseract` | 3990.6 ms | 167.1 ms |
| **total** | **~4050 ms** | **~187 ms** |

`click_text`'s staleness window is the OCR, not the capture (#26 measured the
same shape). So the window can be cut from ~4050 ms to ~187 ms by re-reading a
small region around the target immediately before clicking, at about 5% added
latency on the tool as a whole.

## Decisions

### 1. `type_text` takes a `window`, and routes through `send_key_state`

Parameter defaults to `"activewindow"`, matching `send_shortcut`. A caller that
names a window gets the compositor's guarantee; one that does not gets today's
behaviour, honestly described.

`wtype` is **not** kept as a second path. Two ways to type, one guaranteed and
one not, is the kind of seam where the unguaranteed one gets picked by accident.

### 2. `type_text` fails closed when it cannot map a character

The character-to-keysym mapping is the real work and the real risk. Text a
person dictates contains punctuation, capitals and non-ASCII, and each needs a
keysym plus possibly a shift modifier. A character that cannot be mapped is an
**error naming the character**, never a silently dropped one — dropping a `/`
from a path or a `-` from a flag produces a command that runs and does the
wrong thing.

### 3. `click_text` re-reads a small region immediately before clicking

Having found the phrase in the full read, re-capture a region around the matched
box, OCR it, and confirm the phrase is still there. Present → click. Absent →
**refuse and say it moved**, rather than clicking a coordinate whose contents are
unknown.

This does not make the click atomic and the spec does not claim it does. It cuts
the window from ~4 s to ~187 ms and, more importantly, converts an undetected
wrong click into a detected refusal. Per #24, the honest report is the point.

### 4. `_find_phrase` matches whole tokens, not substrings

The `delete`/`deleted` defect. `hits` is currently scored by substring
containment against the run's joined text, so `"delete" in "deleted"` counts.
Match each query word against the run's **tokens** instead, by equality or
`_ocr_fold` equality (#48).

**Accepted cost:** a query that is a genuine prefix stops matching —
`click_text("Setting")` no longer finds a button reading "Settings". That is a
recall loss in exchange for not clicking "deleted" when told "delete", and the
intent is explicit that a silent wrong action is the worse failure. The near-miss
message from #48 already softens it: the refusal will name "Settings".

### 5. The sensitive-window guard is not extended here

Intent open question 4. Refusing to *type into* a password manager is a real
question and a different one from refusing to *read* it. `type_text` currently
has no guard at all, and adding one is a behaviour change for a working tool
that deserves its own argument. Recorded as out of scope, not overlooked.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| Keep `wtype`, document the blindness | The intent asks for input that can *say where it landed*. A tool description does not make the success message true. |
| Focus-then-type with verification either side | Strictly worse than `send_key_state` now that routing is available: it moves the user's focus as a side effect, and still has a gap between the focus check and the keystroke. |
| One dispatch per key, unbatched | Measured at 1.10 s for 40 characters against 16.8 ms batched. No reason to pay it. |
| Make `click_text`'s read atomic | Not available. The gap is a 4 s OCR; there is nothing to make atomic. |
| Re-read the *full* screen before clicking | Doubles the tool's cost to validate a 4 s-old read with another 4 s-old read. |
| Fix `delete`/`deleted` by raising `MIN_OCR_CONFIDENCE` | Unrelated cause. Containment matches a *confident, correct* reading of "deleted". |

## Risks

- **The keysym mapping is the whole risk of decision 1.** Capitals and symbols
  need modifiers, and getting one wrong types a different character than asked.
  Mitigation: fail closed (decision 2), and test the mapping against xkb rather
  than by example. `keys.py` already owns xkb access and is the place for it.
- **`send_key_state` may behave differently for a window that is not focused.**
  The probe proves the parameter is parsed and resolved, not that an unfocused
  window receives text. **The plan must verify this against a real window before
  anything is built on it** — if it silently does nothing when unfocused, the
  guarantee is smaller than it looks and decision 1 needs revisiting.
- **Decision 4 loses prefix matches** (above). Measured against the existing test
  fixtures, no current test depends on a prefix match.
- **Decision 3 adds a failure mode**: the region re-read can fail to find text
  that *is* still there, on small or stylised glyphs. It must refuse with a
  message naming that possibility rather than claiming the target moved.

## Verification

- The probe above, rerun as a test against a real window: text sent to a named,
  **unfocused** window arrives there and not in the focused one.
- Mapping tests over capitals, punctuation and non-ASCII; an unmappable
  character produces an error naming it.
- A `click_text` test where the region re-read no longer contains the phrase
  asserts **no pointer event is dispatched** — the shape used in #58 and #48,
  because a message-level assertion passes on an implementation that clicks
  first.
- Every `DISTINCT` pair from #48's table still fails to match under decision 4,
  and `delete`/`deleted` specifically.
- `nix flake check`, and the suite run inside `nix develop` (see
  `tests/test_environment.py`).

## Out of scope

- The sensitive-window guard on `type_text` (decision 5).
- Terminal process-tree inspection to refuse conversational text in `vim`, which
  the prior art does. It depends on decision 1 landing first and is a separate
  judgement about what the tool should refuse.
