---
status: approved
issue: 30
intent: intent/2026-09-20-30-virtual-input.md
---

# Spec: drive the pointer through Wayland, not a root daemon

## What the approver decided

- **Depend on ai-mirror's flake**, rather than vendoring the C.
- **Remove `ydotool`**, rather than keeping it as a fallback.
- **Answer SUPER+SHIFT+ESC**, ai-mirror's revoke.
- Helper lifetime left to me. Decided below, with the reasoning exposed.

## The subtlety in "use that key", and what it must not become

ai-mirror's revoke is not a signal. Reading `control.py` and the live state:

```
$ cat $XDG_RUNTIME_DIR/ai-mirror/state.json
{"enabled_by": null, "generation": 167, "owner": "off", "since": "..."}
```

A revoke writes `owner: off` and **bumps `generation`**, and ai-mirror's own
rule is that "input observed under an older grant is refused". Registered
clients live in `mcp.d/<pid>.json`.

There are two ways to honour that key and they are very different:

1. **Voice requires a grant** — it refuses to click unless `owner == "agent"`.
2. **Voice closes its helper when the generation changes** — it does not need a
   grant, but the key stops it.

**This spec takes the second, and the first would be a mistake.** `owner` is
`off` right now, and it is off most of the time; ai-mirror is granted control
for a task and revoked after. Requiring a grant would mean voice cannot click
at all until the user has enabled a *different program's* agent mode — and #19
deliberately made voice's ai-mirror integration (`desktop_control`) opt-in and
off by default, so wiring voice's basic clicking to ai-mirror's switch would
invert that decision by the back door.

What the user asked for is one panic key. That is the second reading: voice
takes no grant, and the key stops it along with everything else.

So voice records the `generation` it saw when it opened its helper, and closes
the helper when that number changes. Which is ai-mirror's own semantics —
"input observed under an older grant is refused" — applied to a lifetime
rather than to a single call.

## Design

### 1. The helper comes from ai-mirror's flake

`ai-mirror-input` is exported as `packages.<system>.ai-mirror-input`: a
`stdenv.mkDerivation` over `wayland`, `libxkbcommon` and `wlr-protocols`. One
flake input, `inputs.nixpkgs.follows = "nixpkgs"`, and **no Python, no
PyGObject, no MCP server** comes with it.

This does change something the repo was deliberate about: ai-mirror is
*optional* today, offered to the Claude backend only when on PATH
(`claude_backend.py:143`). After this it is a build dependency of the package.
The distinction worth keeping is that the **MCP server** stays optional — what
becomes required is a 478-line C binary, which is a different thing from
handing a model the desktop.

### 2. `_press_button` and the wheel speak to a pipe

The helper reads a line protocol (`input.py`: `M`/`B`/`S`/`K`/`T`). Voice needs
`B` (buttons) and `S` (scroll). Pointer motion stays on
`hl.dsp.cursor.move` — one dispatch through the compositor that owns the
pointer, and moving it for symmetry would be change without benefit.

`YDOTOOL_BUTTONS` and its `0xC0`/`0xC1`/`0xC2` codes go; the helper uses evdev
codes (`left: 272`) which `input.py` already maps.

### 3. Lifetime: lazily opened, closed at the end of a turn

The decision the whole benefit rests on.

- **Opened** on the first click or scroll in a turn, not at daemon start. A
  session that never clicks never spawns it.
- **Closed** when the turn ends — however it ends. Normally, cancelled,
  or by an exception.
- **Also closed** on: listening stopping, the daemon exiting, and a
  `generation` change in ai-mirror's state.

Per-turn rather than per-action, because a turn may click several times and a
spawn each time is waste. Per-turn rather than per-process, because holding the
pipe open across turns is exactly the window where an abandoned chord stays
held — which is the bug this issue exists to close.

The release is then structural, not remembered: nothing has to call
`release_all`, because the helper owning the keys stops existing. That is
ai-mirror's insight and it is the reason to copy the mechanism rather than just
the binary.

### 4. Watching the revoke

A small watcher on `$XDG_RUNTIME_DIR/ai-mirror/state.json`. On any change to
`generation`, close the helper.

Polling, not inotify: the file is written by atomic replace, which swaps the
inode out from under a watch — the same trap `plugin/voice.orb/VoiceOrb.qml`
already records for the level file, and the reason it polls. A check on each
input action plus a slow tick is enough; the key's job is to stop input, and
the only input that matters is the input about to happen.

If ai-mirror is not installed the file does not exist. That is not an error and
not a reason to refuse: voice clicks as it does today, and there is no revoke
to honour because there is no ai-mirror. Absence must not become a gate.

### 5. `ydotool` goes

Out of `nix/package.nix:63-83`, out of `tools.py`, and `CLICK_UNAVAILABLE` with
it. The Page Up/Down fallback in `scroll` goes too — it existed because
`ydotool` was frequently absent, and the reason for its absence was the root
daemon.

What replaces the fallback is an honest refusal when the helper cannot start:
the protocols are missing, or the binary is not there. A compositor without
`zwlr_virtual_pointer_v1` genuinely cannot be clicked at, and saying so is
better than pressing Page Down and calling it scrolling.

## Alternatives rejected

- **Vendor the 478 lines of C.** Keeps ai-mirror optional, and buys a second
  copy of a file that will drift from the one next door. The flake exports the
  helper alone, so the dependency is small and precise.
- **Require `owner == "agent"` before clicking.** See above — it makes voice's
  basic function depend on another program's mode being on, and quietly
  reverses #19.
- **Keep `ydotool` as a fallback.** Two input paths and two sets of failure
  modes forever, and the fallback would be exercised so rarely that it would
  rot unnoticed. The approver chose removal.
- **Send a release command instead of closing.** The helper has one (`:376`).
  It is strictly weaker: it works only if the code remembers to call it, which
  is the property that failed with `ydotool`.
- **Move `cursor.move` to the helper too.** Symmetry is not a reason.

## Risks

- **A hard dependency where there was an optional one.** Anyone building this
  package now builds a C helper too. Small, but it is a change in kind and the
  README should say it plainly rather than let it be discovered in a closure.
- **Removing the fallback removes a working path for someone.** A host with
  `ydotoold` running and no `zwlr_virtual_pointer_v1` loses clicking. Hyprland
  implements it; another compositor might not. This is the approver's call and
  it is recorded as one.
- **Per-turn lifetime pays a spawn per clicking turn.** Unmeasured. The helper
  is a small C binary and the turn it sits inside costs 1.5–7.6 s, so it should
  disappear into the noise — but "should" is not a measurement, and the plan
  measures it.
- **The revoke watcher can be wrong in one direction only.** If it misses a
  generation bump, voice keeps an input channel the user thinks they closed.
  That is the direction that matters, so the check belongs immediately before
  each input action rather than only on a tick.
- **A SIGKILL release is assumed, not proven.** `release_all()` runs on
  graceful exit. Whether the compositor releases held keys when the client
  dies uncleanly is Hyprland's behaviour, not the helper's. Proving it means
  holding a key down and killing the process, which is not a thing to do on
  someone's live session — so it is a VM check (#35, nixarchy#821).

## Verification

1. **No root, no uinput.** With `ydotoold` stopped, `click_text` and `scroll`
   work. This is the headline and it is the one to do first, because if the
   protocols are not reachable the rest is moot.
2. **A turn that ends mid-chord releases.** Hold a modifier through the helper,
   end the turn abnormally, and assert the key is not still down. Against
   today's `ydotool` path this is the bug; it should be impossible after.
3. **The panic key stops voice.** With a helper open, bump `generation` and
   assert the helper is closed and the next input refuses.
4. **No ai-mirror is not a gate.** With the state file absent, input works.
5. **An unavailable helper refuses honestly** — no silent no-op, and no Page
   Down pretending to be a scroll.
6. **The suite needs no compositor.** `nix flake check` green, and no test
   opens a real helper.
7. **Existing behaviour is unchanged** where it should be: every current
   `click_text` and `scroll` test passes with only the argv assertions updated.
8. **Spawn cost measured**, not assumed, and recorded in the plan.
