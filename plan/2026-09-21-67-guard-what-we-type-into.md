---
status: approved
issue: 67
spec: spec/2026-09-21-67-guard-what-we-type-into.md
---

# Plan: one seam every input tool passes through

## The approved decisions, carried over

1. **Refuse**, not a confirmation gate and not a new setting.
   `sensitive_patterns` is already user-configurable with a `_replace` flag, so
   the opt-out exists; the gate would need the window title in the description,
   which `_sensitive_kind` exists to prevent.
2. **Fail closed** when the target cannot be identified, mirroring
   `_capture_refused`'s `_CannotSee` branch. The cost — a `hyprctl` hiccup makes
   input unavailable — is accepted and named.
3. **One helper, called explicitly** at the top of each input tool. No invented
   chokepoint; the tools do not share one.
4. **Four tools**: `type_text`, `send_shortcut`, `click_text`, `scroll`.
   `click_text`'s incidental protection becomes deliberate.
5. **An enumeration test** is the real deliverable, so a new input tool added
   later fails the suite until guarded.
6. **The default target is guarded too**; focused is not intended.
7. **The refusal names the category and the human, never the title.**

## Measured before planning

| | |
|---|---|
| `hyprctl clients -j` | **14.2 ms**, not cached |
| `hyprctl activewindow -j` | 13.0 ms |

So the guard costs about **14 ms** per input call. Accepted: it is a fraction of
`click_text`'s OCR (~4 s) and comparable to the dispatch it protects. Two
consequences the plan acts on rather than ignores:

- `click_text` and `scroll` **already resolve the window** for their own
  geometry, so the helper takes an optional already-resolved window and those
  two pay nothing.
- `send_shortcut` resolves nothing today and pays the full 14 ms. That is the
  honest price of the decision and is recorded here so it is not rediscovered
  as a regression.

### The enumeration test has a working detector

Scanning each `_tool_*` source for input markers finds exactly the four tools,
with no misses:

```
send_shortcut    ['send_shortcut']
type_text        ['send_key_state', 'send_shortcut']
click_text       ['cursor.move', '_press_button']
scroll           ['cursor.move', '_send_input', 'wheel']
```

Over-matching is safe here and under-matching is not: a false positive fails the
suite until someone lists the tool, a false negative ships an unguarded tool
silently. `type_text` matches `send_shortcut` only because its docstring names
it — left alone deliberately, for that reason.

## Steps

1. **`tools.py`: `_input_refused(self, target, window=None) -> str | None`**,
   beside `_capture_refused`. Resolves the window unless one is passed, runs
   `_sensitive_kind` on its class and title, and returns a refusal naming the
   category and telling the user to type it themselves. On an unresolvable
   window or an unreadable client list, refuses — decision 2.

   **Revised during implementation.** Written first as "refuse whenever the
   window cannot be resolved", which failed 21 existing tests with *"nothing is
   open"*. The guard was right and too blunt: decision 2 conflates **no windows
   open** with **cannot read the windows**, and this codebase already separates
   those — `_query_json` discards the reason and is safe only where both lead
   to the same behaviour, `_query_rows` is for callers where emptiness is a
   fact (#24). It is a fact here. Nothing open means nothing sensitive to
   protect and the input lands nowhere; an unreadable list means the target is
   unknown. Only the second refuses. Decision 2 holds for the case it was
   actually about.
   → verify by unit tests: a sensitive window refuses, an ordinary one returns
   None, an unreadable list refuses.

2. **`_tool_type_text`**: call it first, before `_kb_layout` or any mapping.
   → verify by a test that a sensitive target sends **no hyprctl call at all**.

3. **`_tool_send_shortcut`**: call it first, before `normalise_mods`.
   → verify by a test asserting no dispatch, and that an ordinary window still
   works with its existing tests unedited.

4. **`_tool_click_text`**: call it with the window already resolved from
   `_target_geometry`, making deliberate what `_ocr_words` does by accident.
   → verify by a test that it refuses **before** OCR runs, not merely that it
   refuses — the point is that it no longer depends on reading.

5. **`_tool_scroll`**: same, reusing `_window_geometry`'s resolution.
   → verify by a test asserting no pointer move.

6. **`tools.py`: `INPUT_TOOLS`**, a module constant naming the four, with a
   comment that membership is a claim about sending input, not about risk.
   → verify by step 7.

7. **The enumeration test.** Walks `TOOL_SCHEMAS`, reads each `_tool_*` source,
   and fails if a tool containing an input marker is not in `INPUT_TOOLS`. Plus
   a test that every member of `INPUT_TOOLS` actually refuses a sensitive
   target — so the constant cannot drift from the behaviour either way.
   → **Done, 2026-09-21.** A `cursor.move` dispatch was temporarily added to
   `_tool_read_notifications`, standing in for someone adding an input-sending
   tool without guarding it. The enumeration test failed with
   `AssertionError: Items in the first set but not the second`, and passed
   again once the change was reverted. The detector detects; it is not just
   asserted to.

8. **Tests** for the refusal text: it contains the category, and does **not**
   contain the window title. A title carrying an email address is the case
   `_sensitive_kind` was written for.
   → verify by a test using a title with an address in it.

9. `nix flake check` and the suite inside `nix develop`.

10. **Live on razer**: an ordinary window is still typed into normally. The
    regression that would matter most is over-refusal, and no unit test proves
    a real window still works.
    → **Done, 2026-09-21**, `tools/verify_input.py` on razer, 6/6:

    ```
    PASS  the target window is NOT the focused one
    PASS  no compositor errors
    PASS  text reached the UNFOCUSED window
    PASS  the focused window received nothing
    PASS  an AltGr character types as itself, not its base key
    PASS  an off-layout character refuses
    ```

    Over-refusal, the predicted failure, did not happen: ordinary windows are
    typed into exactly as before.

    Getting there took three harness fixes, none of them product bugs, all
    recorded because each was a wrong test rather than a flake:

    * **Two windows shared a title.** The AltGr phase re-spawned `PROBE`
      without closing the first, so `address()` returned whichever the
      compositor listed first and the check read a trailing `y` from the
      previous phase's `x-y/z`. Distinct titles and sinks per phase.
    * **The harness asked `hyprctl activewindow`; the code reads
      `focusHistoryID`.** On an idle machine with no seat focus these
      disagree — `activewindow` returns `{}` while the focus history is
      intact — so the harness failed while the code worked.
    * **It asserted a decoy held focus.** That is a precondition, not the
      claim. `hl.dsp.focus` does not take when the seat has no focus, so the
      assertion failed while input was demonstrably reaching an unfocused
      window throughout. It now asserts the target is not the focused one,
      which is what the claim actually needs.

## Tests

```
nix develop -c python3 -m unittest discover -s tests
nix flake check
ssh razer '... python3 tools/verify_input.py'   # existing harness, unchanged
```

## Rollback

`git revert`. Every change is an added guard clause plus one constant; nothing
is restructured, no state, no config default changes. The four tools return to
their current behaviour exactly.

## Risks

- **Over-refusal is the likely failure, not under-refusal.** The patterns match
  on class and title, and a browser tab titled "…password…" is a credential
  category by the rules. That is #46's accuracy question, not this one, but
  this change makes it bite on four more tools. Step 10 exists because of it.
- **`send_shortcut` gains 14 ms.** Measured, accepted, recorded.
- **The marker list in step 7 is a heuristic.** It is checked against the
  current four and can only over-match, which fails safe.

## Out of scope

The accuracy of the sensitive-window patterns (#46), and anything about what is
typed rather than where.
