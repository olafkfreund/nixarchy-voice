---
status: draft
issue: 67
intent: intent/2026-09-21-67-guard-what-we-type-into.md
---

# Spec: one seam every input tool passes through

The read paths are guarded and the write paths are not. This adds one helper
that every tool sending input to a window must call, and a test that checks
they all do — so the property is greppable rather than remembered.

## The two questions the intent left open, decided

### 1. Refuse. Not a confirmation gate, not a new setting.

The intent asked whether refusing is even right, since filling a login form is
a real use. Three things settle it:

- **The opt-out already exists.** `sensitive_patterns` is user-configurable and
  `sensitive_patterns_replace` overrides the defaults wholesale. Someone who
  genuinely wants their agent typing into a password manager removes the
  pattern. Adding a second setting for the same decision would be two places to
  look and two to get wrong.
- **The legitimate case is the one a human should do.** "Fill in my master
  password" is precisely the action where the value of a human doing it is
  highest. A refusal that names that is not an obstruction.
- **The gate does not fit without restructuring.** `_call_locked` wraps only
  `policy.check(description)` in its `NeedsConfirmation` handler; the tool
  handler runs afterwards and outside it. Routing a per-window decision through
  the gate means either matching on the description — which would have to
  contain the window title, and `_sensitive_kind` exists precisely so titles
  are never echoed — or restructuring the gate. Neither is worth it for a
  decision the config already exposes.

### 2. Fail closed when the target cannot be identified.

Mirrors `_capture_refused`'s `_CannotSee` branch. If the window list cannot be
read, what is about to be typed into is unknown, and #24's rule is that
guessing is the bug.

The cost is real and is accepted: a `hyprctl` hiccup makes typing unavailable,
not merely unverified. It is the same trade #46 already made for reading, and
it is a stronger case here — a bad read discloses something, a bad write
changes something.

## Decisions

### 1. One helper, called explicitly

`_input_refused(target) -> str | None`: resolve the window, run
`_sensitive_kind` on its class and title, return a refusal or None.

Called at the **top** of every tool that sends input, before any work. Not
folded into a lower-level dispatch seam: the tools do not share one, and
inventing a chokepoint big enough to cover them would be a larger change than
the guard.

### 2. The tools it covers

| tool | why |
|---|---|
| `type_text` | arbitrary text, the form that carries a password |
| `send_shortcut` | `Ctrl+A`, `Delete` into a vault; unguarded since long before #60 |
| `click_text` | guarded today only **incidentally**, via `_ocr_words` |
| `scroll` | moves the pointer onto the window and turns the wheel |

`scroll` is the weakest case and is included anyway. One rule with no
exceptions is cheaper to keep true than four tools with three reasons, and the
cost of being wrong about scroll is a refused scroll.

`click_text` keeps its incidental protection — this makes it deliberate, so it
survives someone changing how `click_text` reads.

### 3. A test that enumerates the input tools

The actual deliverable. A test that walks `TOOL_SCHEMAS`, selects the tools
that send input, and asserts each refuses a sensitive target — so a **new**
input tool added later fails the suite until it is either guarded or explicitly
listed as exempt with a reason.

This is what stops the `click_text` situation recurring: today it is protected
by an accident of implementation, and accidents do not survive refactoring.

### 4. The default target is guarded too

`activewindow` means the user is looking at it, not that they intended it. A
focused vault is still a vault, and `_capture_refused` does not exempt the
focused window either.

### 5. The refusal names the human, never the title

`_sensitive_kind` returns a category and never the matched text, because the
titles on this desktop carry inbox counts and email addresses. The message says
what kind of thing it is and that the user should type it themselves —
following `_capture_refused`'s habit of naming the way forward, since a refusal
with no route turns one blocked step into a stuck task.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| Route through the confirmation gate | Needs the title in the description, or a restructure of `_call_locked`. See above. |
| A new `allow_input_to_sensitive` setting | `sensitive_patterns` already is that setting. |
| Guard inside `_dispatch` | Not every input path goes through it — `type_text` builds one `hyprctl --batch` — and it would also catch dispatches that send no input. |
| Guard only `type_text` | The issue's framing. `send_shortcut` has the same hole and predates it. |
| Exempt `activewindow` | Focused is not intended, and the read guard does not exempt it. |
| Leave `click_text` alone since it already works | It works by accident. |

## Risks

- **Four tools gain a `hyprctl` query they did not have.** `send_shortcut`
  currently dispatches without resolving anything. Mitigation: the window list
  is already cached for other callers; the plan should measure the added cost
  rather than assume it is nothing.
- **Fail-closed makes input unavailable during a compositor hiccup.** Accepted
  above, and named here so it is not a surprise later.
- **The category list is a regex guess.** Unchanged from #46 — this spec
  extends its reach, not its accuracy. A window it does not recognise is not
  protected, and that is already true of reading.

## Verification

- A test per tool: a sensitive target refuses and **no dispatch is sent** — the
  #58/#48/#60 shape, because a message-level assertion passes on an
  implementation that acts first.
- The enumeration test in decision 3, including that it fails when a new input
  tool is added unguarded.
- A test that the refusal contains the category and **not** the window title.
- The unresolvable-window case refuses and sends nothing.
- Existing `type_text`, `send_shortcut`, `scroll` and `click_text` tests pass
  unedited for non-sensitive targets.
- `nix flake check`, and a live check on razer that an ordinary window is still
  typed into normally — the regression that would matter most.

## Out of scope

- The accuracy of the sensitive-window patterns (#46 owns that).
- Anything about what is typed, only where.
