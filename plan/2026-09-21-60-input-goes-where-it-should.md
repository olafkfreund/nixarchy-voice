---
status: approved
issue: 60
spec: spec/2026-09-21-60-input-goes-where-it-should.md
---

# Plan: input that can say where it landed

## The approved decisions, carried over

1. **`type_text` takes a `window`** (default `"activewindow"`, matching
   `send_shortcut`) and routes through `send_key_state`, so the compositor does
   the routing and there is no gap between choosing the target and the key
   arriving.
2. **`type_text` fails closed** on a character it cannot map — an error naming
   the character, never a silent drop. Dropping a `/` from a path or a `-` from
   a flag produces a command that runs and does the wrong thing.
3. **`click_text` re-reads a small region** around the matched box immediately
   before clicking. Present → click; absent → refuse and say it moved.
4. **`_find_phrase` matches whole tokens, not substrings**, fixing
   `click_text("delete")` clicking the word `deleted`. Accepted cost: prefix
   queries stop matching.
5. **The sensitive-window guard is not extended** to `type_text` here.
6. `wtype` is **removed**, not kept as a fallback. Two ways to type, one
   guaranteed and one not, is where the unguaranteed one gets picked by accident.

## Verified on razer before planning, not assumed

The spec said the plan **must** confirm that an unfocused window actually
receives text before anything is built on decision 1. Done, on razer
(Hyprland 0.56.0, idle), with two terminals each capturing keystrokes to its
own file:

| window | received |
|---|---|
| `probe_a` — **focused** | *(nothing)* |
| `probe_b` — **unfocused target** | `hi` |

Decision 1 holds: `send_key_state` delivers to an unfocused window and the
focused window sees nothing.

### The mapping rule, characterised — and it is not what the spec assumed

| behaviour | evidence |
|---|---|
| A bare keysym name **ignores case**: `key="H"` types `h` | `Hi-There/42` came out `hi-there/42` |
| `mods="SHIFT"` is what produces the shifted character: `h`→`H`, `1`→`!` | exact match `[Hi! x]` |
| Symbols, digits and space work by keysym name (`minus`, `slash`, `space`) | as above |
| A character **not on the active layout** fails with `key not found` | `oslash` errored; everything else typed |
| `state` is `"down"`, `"up"` or `"repeat"` — nothing else | probed |

Two consequences the spec did not account for:

- **`key not found` gives decision 2 for free.** An unmappable character already
  fails loudly rather than silently dropping. We surface it, we do not build it.
- **Shift pairings are layout-dependent, so they cannot be hardcoded.** On a
  Norwegian layout `shift+2` is `"`, not `@`. A US-centric table would type the
  wrong character on a real user's keyboard and report success — the exact
  failure this issue exists to remove. **This is the main work of the plan.**

## Steps

1. **`keys.py`: `char_to_key(ch) -> tuple[str, str] | None`**, returning
   `(keysym_name, mods)` for one character, or None when the active layout
   cannot produce it.
   Derived from the **compiled active keymap**, not a table: `keys.py` already
   loads libxkbcommon through ctypes (`_xkb`), so extend it to compile the
   keymap and, for each keycode and shift level, record which keysym that
   key/level produces. The character's keysym is then looked up to find the
   physical key's **level-0** keysym (what `send_key_state` wants) and whether
   the level needs SHIFT.
   → verify by unit tests on a known layout fixture; and by the property that
   every character the layout claims to produce round-trips.

2. **`keys.py`: fall back honestly when the keymap is unavailable.** `_xkb()`
   already returns None where libxkbcommon is absent (documented and
   deliberate). Here that must mean *"cannot map, so cannot type"*, not a
   guessed US table — decision 2.
   → verify by a test with `_xkb` stubbed to None: `type_text` refuses and names
   the reason.

3. **`tools.py`: `_tool_type_text(self, text, window="activewindow")`.**
   Map every character **first**, and refuse the whole call naming the offending
   character if any fails. Only then build the key events.
   Whole-string-first is the point: half a typed command is worse than none,
   because half a command still runs.
   → verify by a test that an unmappable character sends **no dispatch at all**.

4. **`tools.py`: emit one `hyprctl --batch`**, two events (`down`, `up`) per
   character. Measured: 80 events cost 16.8 ms batched against 1104 ms
   individually, so the batch is not an optimisation, it is what makes the
   approach viable.
   → verify by a test asserting a single batched invocation, not N.

5. **`tools.py`: remove `wtype`** from `_tool_type_text` and from the flake
   inputs if nothing else uses it.
   → verify by `grep -r wtype` showing only unrelated hits, and the suite green.

6. **`tools.py:590` `TOOL_SCHEMAS`**: add `window` to the `type_text` schema.
   The description states that naming a window is a real guarantee and that the
   default is the focused one — the same honesty `send_shortcut`'s carries.
   → verify by `tools_for(config)` and the schema round-trip test.

7. **`tools.py` `_tool_click_text`**: after `_find_phrase` returns a point,
   re-capture a region around the matched box, OCR it, and confirm the phrase is
   still present. Absent → refuse, naming that it moved **and** that a small or
   stylised glyph can cause a false negative (spec risk 4).
   Measured: a 300x60 region costs ~187 ms against ~4050 ms for the full screen.
   → verify by a test where the re-read no longer contains the phrase asserting
   **no pointer event is dispatched** — the #58/#48 shape, because a
   message-level assertion passes on an implementation that clicks first.

8. **`tools.py` `_find_phrase`**: score by token equality (or `_ocr_fold`
   equality, #48) instead of substring containment.
   → verify by every `DISTINCT` pair from #48's table still failing to match,
   `delete`/`deleted` specifically, and the existing matching tests passing
   unedited except any that depended on a prefix.

9. **Live verification on razer**, scripted and committed under `tools/` beside
   `live_check.py` so it is repeatable rather than a one-off:
   text reaches a **named unfocused** window; capitals, symbols and space
   survive a round trip; an off-layout character refuses; and `click_text`
   refuses when the target moves between read and click.
   → verify by the recorded output in this file.

10. `nix flake check`, and the suite inside `nix develop`
    (`tests/test_environment.py` explains why a bare shell lies).
    → verify by `all checks passed!`.

## Tests

```
nix develop -c python3 -m unittest discover -s tests
nix flake check
ssh razer ...  # step 9, the live harness
```

## Rollback

`git revert` the implementation commit. `type_text` returns to the three-line
`wtype` call, `click_text` to a single read, `_find_phrase` to containment. No
state, no migration. The one-way door is decision 6 — if `wtype` is removed from
the flake, a revert must restore that too, so it goes in the same commit.

## Risks

- **Step 1 is the whole risk.** Everything else is small. If compiling the
  keymap through ctypes proves too awkward, the fallback is **not** a US table;
  it is decision 2 — refuse and say the layout could not be read.
- **Razer is Hyprland 0.56.0, this machine is 0.56.2.** The probes behaved
  identically on both, but step 9's harness should record the version so a
  future divergence is visible rather than mysterious.
- **Step 8 loses prefix matches.** No existing test depends on one; a real user
  might. #48's near-miss message names the near hit, which softens it.

## Out of scope

- The sensitive-window guard on `type_text` (decision 5).
- Terminal process-tree inspection to refuse conversational text in a modal
  editor, which the prior art does. It depends on this landing first.
