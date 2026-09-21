---
status: approved
issue: 64
intent: intent/2026-09-21-64-stub-follows-the-compositor.md
---

# Spec: ask the compositor, and keep the stub for when it cannot answer

The intent asked which stub file to trust. Measuring turned up a better
question: **the running compositor can list its own dispatchers**, which is
authoritative and cannot drift. The file only needs to cover the case where
there is no compositor to ask.

## What was measured

### The compositor is enumerable, completely

`hyprctl repl` evaluates Lua and prints the result. `hl.dsp` has **no
metatable**, so `pairs()` sees everything: 16 functions and 4 sub-tables
(`cursor`, `group`, `window`, `workspace`). Walking it recursively gives 51
fully-qualified names — `cursor.move`, `window.close`, `send_key_state` —
which is exactly the dotted form `_render` builds and `_dispatch` takes.

### It agrees exactly with the stub that ships beside it

```
stub: 51   live: 51
in stub only : []
in live only : []
```

So today's validation is correct. It is correct *by coincidence*, which is the
defect the intent identified, and asking the compositor removes the coincidence.

### Where a stub file can live, on this machine

| path | |
|---|---|
| `/run/current-system/sw/share/hypr/stubs/hl.meta.lua` | **exists** |
| `/etc/profiles/per-user/$USER/share/hypr/stubs/hl.meta.lua` | missing |
| `~/.nix-profile/share/hypr/stubs/hl.meta.lua` | missing |
| `/usr/share/hypr/stubs/hl.meta.lua` (the current default) | **missing** |

The code's fallback is the one path that does not exist on the only platform
this targets.

## Decisions

### 1. Ask the compositor first

`hypr_dispatch` validates against the dispatcher set reported by the running
Hyprland, obtained by walking `hl.dsp` through `hyprctl repl`. This cannot
disagree with the compositor, because it *is* the compositor.

**On the Lua**: the string sent is a fixed literal in our source, never
anything derived from model input. #22 locked down accepting Lua *from the
model*; this is the same posture as every other `hyprctl` call the executor
makes, which are also fixed strings with interpolated values from hyprctl
itself. No new seam, and the plan should assert that with a test that the
introspection string contains no interpolation.

### 2. Cache it for the process lifetime

The set changes only when the compositor restarts, and a restart takes the
session with it. One `hyprctl` call is ~14 ms; doing it per dispatch would add
that to every desktop action for information that cannot have changed.

### 3. The stub file remains, as the fallback for "no compositor"

`nix flake check` is hermetic and has no Hyprland, so a file-based path is
**required**, not speculative. Order:

1. `OMARCHY_VOICE_HL_STUB` — an explicit, deliberate override. Tests and the
   sandbox set it; a user debugging sets it. It wins.
2. `/run/current-system/sw/share/hypr/stubs/hl.meta.lua` — the running system.
3. `/etc/profiles/per-user/$USER/…` and `~/.nix-profile/…` — a Hyprland
   installed by home-manager rather than the system closure.
4. `OMARCHY_VOICE_HL_STUB_FALLBACK` — what the packaging shipped.
5. `/usr/share/hypr/stubs/hl.meta.lua` — non-NixOS, kept so nothing gets worse
   there.

### 4. `package.nix` sets the FALLBACK variable, not the primary

This is the intent's open question 1, and the reason it was hard: the wrapper
sets `OMARCHY_VOICE_HL_STUB` with `--set-default`, so it is *always* set on an
installed system, and "explicit override first" would mean the running
system's stub is never consulted. The two names separate the two meanings that
were sharing one:

- `OMARCHY_VOICE_HL_STUB` — *somebody chose this*. Wins over everything.
- `OMARCHY_VOICE_HL_STUB_FALLBACK` — *the packaging guessed*. Loses to the
  running system, which is the whole point.

### 5. Still fail closed

#22 established that refusing every dispatcher is the correct response to a
missing stub, because falling open reintroduces the hole the check exists to
close. Unchanged: if the compositor cannot be asked **and** no stub is found,
everything is refused, with a message naming both.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| Bump the pinned Hyprland to match | Fixes today's instance and leaves the mechanism. The two are pinned from different nixpkgs; they will drift again. |
| Stub file only, better lookup order | Still guessing which file describes the running compositor. The compositor can simply be asked. |
| Compositor only, drop the stub | `nix flake check` has no compositor. The fallback is required. |
| Compare the two and report a mismatch | Intent open question 2. Superseded: once the compositor is the source, there is nothing to compare it against. |
| Re-read per dispatch | ~14 ms on every desktop action for a set that cannot change without ending the session. |
| Have `hm-module.nix` pass the exact package | Intent open question 3. Helps only home-manager users and still guesses; decision 1 covers them too, and the path is in the fallback list anyway. |

## Risks

- **`hyprctl repl` is a newer interface than `hyprctl dispatch`.** If an older
  Hyprland lacks it, the call fails and the fallback chain handles it — which
  is exactly what the chain is for. The plan must verify the failure is clean
  (non-zero exit or unparseable output), not a hang.
- **Walking `hl.dsp` assumes no metatable.** Verified on 0.56.0; if a future
  version adds `__index`, enumeration silently returns a short list and
  dispatchers start being refused. **Mitigation: treat a suspiciously small
  result as failure and fall through to the stub**, rather than trusting it.
  The plan should pick that threshold from the measured 51.
- **A cached set survives a compositor restart within one process.** In
  practice the session ends with the compositor. Accepted, and noted rather
  than engineered around.

## Verification

- The live list and the stub list agree, asserted against a real compositor in
  a committed harness (`tools/`), the way #60's claims are.
- A unit test that the introspection Lua is a constant with no interpolation.
- Unit tests for the fallback order, including that
  `OMARCHY_VOICE_HL_STUB_FALLBACK` loses to the system path and
  `OMARCHY_VOICE_HL_STUB` beats both.
- A test that no compositor and no stub refuses everything (#22 preserved).
- `nix flake check` — which exercises the fallback path by construction, since
  the sandbox has no compositor.

## Out of scope

- The `type_text` sensitive-window guard, which is the agreed next piece.
- Anything about *which* dispatchers are allowed; this is only about knowing
  which exist.
