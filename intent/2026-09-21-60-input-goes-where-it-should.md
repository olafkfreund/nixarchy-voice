---
status: approved
issue: 60
author: olafkfreund
---

# Intent: input that can say where it landed

Two tools can act on the wrong thing and report success. A third cause of the
same outcome turned up while closing #48 and is recorded here so the scope
question gets asked once rather than three times.

## The problem

**We are not uniformly blind, which is the interesting part.** `send_shortcut`
(`tools.py:1865`) takes a `window` and hands it to a Hyprland dispatcher, so
**the compositor does the routing**. There is no window between deciding the
target and the keys arriving. It defaults to `"activewindow"`, but a caller
that names a window gets a real guarantee. That is the bar the other two miss.

### 1. `type_text` is blind

```python
def _tool_type_text(self, text: str) -> Result:
    if not shutil.which("wtype"):
        return Result(False, "wtype is not installed")
    return self._shell(["wtype", "--", text])
```

Three lines. `wtype` types into whatever holds focus at the moment it runs.
Nothing records where the text was *meant* to go, so nothing can notice when it
goes elsewhere — and the tool reports success because `wtype` exited 0, which
it does whether the text landed in the intended editor or in a confirmation
dialog that appeared half a second earlier.

### 2. `click_text` has a gap between looking and clicking

`_tool_click_text` (`tools.py:2408`) OCRs a region, finds the phrase, then
dispatches `cursor.move` and a button press at that coordinate. Between capture
and click the screen can change: a panel opens, a window is restacked, a
notification slides in. The coordinate stays valid; what is under it does not.

This is a TOCTOU window, not a focus problem, so a focus check does not close
it. #26 measured capture at 0.06 s and OCR at 4–5 s, so **the gap is almost
entirely the OCR** — which is a useful shape, because it means the stale thing
is a 5-second-old full-screen read rather than an unavoidable race.

### 3. `click_text` can also click the wrong word outright

Found while closing #48, not previously tracked. `_find_phrase` scores runs of
words by **substring containment**, so `click_text("delete")` matches the word
`deleted` and clicks it — confidently, reporting success. It is on main today
with a test documenting it (`test_substring_containment_still_matches_a_longer_word`).

Distinct cause from 1 and 2, identical outcome from the caller's seat: the
agent did something to the wrong thing and said it did the right thing.

## Why this is worth doing now

Not theoretical. From the agent bus, one session to another after a shell
restart moved a panel underneath it:

> "one `y` keystroke of mine may have landed in your panel after the restart …
> **a layer-namespace guard is not ownership**"

The dialog underneath was a delete confirmation.

This is also the failure mode #24 was written about: reporting a guess as a
fact. A tool that cannot know where its input went should not say "done".

## Desired outcome

- `type_text` can say where its text went, and **fails closed** when it cannot.
- `click_text`'s staleness window is either closed or **honestly reported**.
- The containment defect in 3 is fixed, or split out with a reason.

None of these is "add a confirmation prompt". The goal is that the success
message is true, not that the user is asked more often.

## Affected

`_tool_type_text`, `_tool_click_text` and `_find_phrase` in `tools.py`. Anything
built on `click_text` inherits 2 and 3 — `wait_for(text)` shares `_find_phrase`.

## Constraints

- **`type_text` works today.** Making it refuse in cases it currently handles is
  a behaviour change, and needs to be argued rather than assumed.
- **Do not report unknown as known** (#24). "Probably went to the editor" is the
  bug, not the fix.
- Whatever is built must hold for a caller that never names a window, since that
  is the default and most calls will use it.
- Measured on tasks, not a stopwatch (#23).

## Prior art

[omribenami/Omarchy-AI](https://github.com/omribenami/Omarchy-AI) (MIT),
`src/omarchy_ai/execution/verified_input.py`, 108 lines: focus is established by
an explicit call and **verified by re-reading the focused address**, every
keystroke re-checks the pin, anything that moves focus clears it, and success is
worded honestly — *"Input command sent to verified window X. Application
acceptance and task completion are NOT verified."*

It also walks the terminal's process tree for `vim`/`nano`/`emacs`/`helix` and
refuses conversational text there: a stray keystroke at a shell prompt fails
harmlessly with "command not found", while in a modal editor it is interpreted
as editing commands.

## Open questions

1. **Can `type_text` be targeted at all?** The issue assumed probably not.
   Hyprland's stub lists a **`send_key_state`** dispatcher beside
   `send_shortcut`, which — if it takes a `window` the way `send_shortcut` does
   — would give text the same compositor-routed guarantee, at the cost of one
   dispatch per character. The stub types it as `fun(...)`, so this is a
   question to verify in the spec, **not a claim**. The alternatives remain
   focus-then-type with verification either side, or staying blind and saying so
   in the tool description.
2. **Should `type_text` refuse when focus changed** since the last deliberate
   targeting action? That is the behaviour change named in the constraints.
3. **Is `click_text`'s window worth closing**, given the gap is the OCR? The
   honest fix may be re-capturing a *small region* around the target immediately
   before clicking — 0.06 s against a 5-second-old read — rather than trying to
   make the whole read atomic.
4. **Does any of this interact with the sensitive-window guard (#46)?** Refusing
   to *type into* a password manager is a different question from refusing to
   *read* one, and only the second is answered today.
5. **Does defect 3 belong in this issue or its own?** It is a one-function fix
   with a different cause from 1 and 2, and folding it in risks a large issue
   getting larger. Splitting it risks it being forgotten, since it is already
   on main and only a test records it. **My recommendation is to fix it here**:
   it is small, it is the same user-visible failure, and the tests for "clicked
   the wrong thing" want to live together.
