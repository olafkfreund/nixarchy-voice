---
status: draft
issue: 30
author: olafkfreund
---

# Intent: drive the pointer through Wayland, not a root daemon

Closes #30.

## Problem

Clicks and the wheel go through `ydotool`. That has three costs, and only the
third is about speed.

**It needs root.** `ydotool` writes to `/dev/uinput`, so it needs a daemon
running as root — on this host, `ydotoold --socket-path=/run/ydotoold/socket`.
A voice assistant that otherwise needs nothing privileged drags a root daemon
into the install, and `nix/package.nix` wraps `ydotool` onto PATH so every user
of this package inherits it.

**It fails in a way the user has to decode.** Without the daemon, `scroll`
degrades to pressing Page Up/Down and `click_text` returns the long
`CLICK_UNAVAILABLE` NixOS hint. Both are handled, and both are handling a
problem that need not exist.

**It cannot release what it was holding, and this is the one that matters.**
There is no equivalent of "let go of everything" anywhere in this codebase. A
turn abandoned mid-chord — the daemon restarted, the model cancelled, an
exception between a press and its release — leaves the key down. The desktop is
then in a state the user has to notice and fix, caused by an assistant that has
stopped talking to them.

[ai-mirror](https://github.com/olafkfreund/ai-mirror) already solves all three,
and it is by the same author, already on this machine, and already offered to
the Claude backend when it is on PATH. Read and verified while scoping:

- `src/ai_mirror_input/ai_mirror_input.c`, 478 lines, speaking
  `zwp_virtual_keyboard_v1` and `zwlr_virtual_pointer_v1` directly. **No root,
  no `/dev/uinput`** — these are Wayland protocols and Hyprland implements
  both.
- **It is exported as its own flake package**, `packages.<system>.ai-mirror-input`,
  a small C derivation over `wayland`, `libxkbcommon` and `wlr-protocols`.
  Depending on it does not drag in the MCP server, the Python, or PyGObject.
- **Persistent.** `control.py` holds it as a `subprocess.Popen` with a pipe, so
  an action is a pipe write rather than a process spawn.
- `keys_down[KEY_MAX + 1]` and `release_all()` (`:202`), called on the release
  command (`:376`) and at the end of `main` (`:474`).

And the part that reframes the design rather than just supplying a binary:
ai-mirror's kill switch works by **closing the helper**. Its own comment:

> the registered MCP server, which closes its helper so the helper releases all
> held keys and buttons (a virtual keyboard can only be released by its owner).

So "release everything" is not a call anyone has to remember to make. It is a
property of the pipe's lifetime.

## Proposed outcome

- Clicking and scrolling need no root daemon and no `/dev/uinput`.
- A turn that ends — normally, cancelled, or by an exception — cannot leave a
  key or a button held, because the thing holding them is tied to the turn
  rather than to a daemon that outlives it.
- `click_text` and `scroll` behave as they do now when everything is working,
  so no caller and no test of their *behaviour* has to change.
- The `CLICK_UNAVAILABLE` hint and the Page Up/Down fallback either go away or
  become genuinely rare, rather than being the documented normal for anyone
  without the root daemon.

## Affected users and systems

- `src/omarchy_voice/tools.py` — `_press_button`, the wheel path in `scroll`,
  `YDOTOOL_BUTTONS`, `CLICK_UNAVAILABLE`, and the `shutil.which("ydotool")`
  gates.
- `nix/package.nix:63-83` — the wrapped tool list, and a new input if the
  helper comes from ai-mirror's flake.
- `flake.nix` — one more input, if that is the route.
- Anyone running the daemon who has `ydotoold` enabled today, and anyone who
  does not and has been living with the degraded path.
- Not the policy gate, not the schemas, not the model-facing descriptions:
  `click_text` and `scroll` keep their arguments and their meaning.

## Constraints

- **Pointer motion stays where it is.** `hl.dsp.cursor.move` is one dispatch
  through a compositor that owns the pointer; moving it to the helper for
  symmetry would be change without benefit.
- The mocking seam stays `Executor._shell` and `subprocess`
  (`tests/test_reach.py:44-64`). The suite must not need a compositor — that
  was demonstrated the hard way in #28, where wiring an event listener in
  unconditionally made unit tests reach the real Hyprland.
- `nix flake check` has no Wayland session, so nothing may require a live
  compositor to build or to test.
- Whatever replaces `ydotool` must degrade honestly when it is unavailable. The
  current failure is at least loud; a silent no-op would be worse than what is
  there.

## Open questions

1. **Depend on ai-mirror's flake, or vendor the helper?** Scoping moved me
   towards depending: `ai-mirror-input` is a separate package, so the input
   costs one flake input and no Python, and vendoring means keeping 478 lines
   of C in sync with a project that is right next door. Against: this repo
   deliberately treats ai-mirror as *optional* today — offered when on PATH,
   never required — and a flake input makes it a hard dependency of the
   package.

2. **Does `ydotool` stay as a fallback?** Keeping both means two input paths
   and two sets of failure modes for the rest of time. Removing it means a host
   whose compositor lacks the virtual-pointer protocol has no clicking at all.
   Hyprland implements it; that is not the only compositor this could run on.

3. **Should voice share ai-mirror's kill switch?** ai-mirror revokes control
   with SUPER+SHIFT+ESC by closing *its* helper. If voice spawns its own, that
   keypress will not stop voice — and a user's mental model is one panic key,
   not one per program. Against sharing: voice is the user's own assistant
   answering them, not an agent that was handed control, and putting it behind
   ai-mirror's switch makes one program's UI silently govern another's.

4. **What is the helper's lifetime?** The whole `release_all` benefit follows
   from this and nothing else. Per process is simplest and holds keys across
   turns; per turn gives the strongest guarantee and pays a spawn each time;
   per listening session sits between. This is the question the outcome above
   actually depends on.
