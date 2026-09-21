---
status: approved
issue: 64
author: olafkfreund
---

# Intent: validate against the compositor that is running

> **This issue's own framing was wrong, and measuring it is what showed that.**
> #64 says the risk is "a dispatcher present in the newer stub would pass
> validation and fail at runtime". Measured, that is not happening, and the
> larger divergence runs the other way. The mechanism is still wrong; the
> reason is different.

## What was measured

`hypr_dispatch` validates every dispatcher name against a LuaLS stub (#22).
That stub is pinned by `flake.nix` from **this repo's** nixpkgs, and baked into
the installed binary by `nix/package.nix:95`:

```
--set-default OMARCHY_VOICE_HL_STUB ${hyprland}/share/hypr/stubs/hl.meta.lua
```

The compositor a user actually runs comes from **their system configuration**,
which is a different nixpkgs. Comparing the two on this machine:

| | running system | pinned by flake |
|---|---|---|
| reported version | 0.56.0 (`0bd11c7a`, git build) | 0.56.2 (release) |
| stub size | 1861 lines | 1694 lines |
| `---@field` entries only on one side | **43** | 2 |
| **dispatchers** | **128** | **128** |

**The dispatcher sets are identical — zero divergence in either direction.**
The 43 and 2 are configuration options in other namespaces, which
`hypr_dispatch` never consults. So the failure #64 describes is not occurring,
and neither is its opposite.

Recorded because it is the kind of thing that gets re-feared later: the numbers
above are the reason not to.

## The defect that is real

**An installed user validates against the wrong Hyprland**, and it is only
harmless by luck. `--set-default` bakes in the stub from the Hyprland *this
repo* pinned, which has no relationship to the one running. Two builds agreeing
today is not a property anyone arranged, and nothing notices when they stop.

Both directions are bad, and one is worse than #64 assumed:

- A dispatcher in the stub but not the compositor → validation passes, dispatch
  fails at runtime. This is the one #64 named, and it is the *less* likely
  direction, because the stub comes from a release while users tend to run
  something newer.
- A dispatcher on the compositor but not in the stub → **`hypr_dispatch`
  refuses something that would have worked**, and tells the model the
  dispatcher does not exist. That is a false refusal in a tool whose whole
  purpose is to stop silent failures, and it is the direction the field counts
  above actually lean.

## The thing that makes this cheap

**The running system already ships the correct stub**, at a stable path:

```
/run/current-system/sw/share/hypr/stubs/hl.meta.lua
```

It is there on this machine, it is by construction the stub for the compositor
that is running, and the code never looks at it. `capabilities.py:35` falls back
to `/usr/share/hypr/stubs/hl.meta.lua` — a path that does not exist on NixOS,
which is the only platform this targets.

## Desired outcome

Dispatcher validation is checked against the compositor the user is running, by
construction rather than by coincidence — and when it cannot be, that is
visible rather than silently falling back to a stub from somewhere else.

## Affected

`capabilities.py` (`HL_STUB`), `nix/package.nix` (the `--set-default`), possibly
`flake.nix` if the test stub should be sourced differently from the runtime one.
`hypr_dispatch` is the only consumer.

## Constraints

- **The tests need a fixed stub.** `nix flake check` is hermetic and has no
  running system, so whatever is built must keep working in the sandbox — the
  test path and the runtime path may legitimately differ.
- **Do not fall open.** #22 established that refusing everything is the correct
  response to a missing stub, because falling open reintroduces the hole that
  check exists to close. Any new lookup order must preserve that.
- An explicit `OMARCHY_VOICE_HL_STUB` must still win; packaging and users set it
  deliberately.
- Non-NixOS is not a target, but the code should not get *worse* there.

## Open questions

1. **Order of precedence.** Explicit env var first is obvious. But the installed
   wrapper *always* sets the variable via `--set-default`, so a naive
   "env first" means the running system's stub is never consulted. Does the
   wrapper stop setting it, or does the lookup prefer the system path over a
   value it cannot distinguish from a user's?
2. **Should a mismatch be reported rather than silently resolved?** If the stub
   and the running compositor disagree on the dispatcher set, that is worth
   saying once, not hiding.
3. **Is `/run/current-system` the right anchor** for a home-manager user whose
   Hyprland comes from their HM config rather than the system closure? The
   hm-module could pass the exact package instead.
4. **Does this want a test at all, or a live check?** The divergence is between
   two store paths and cannot be reproduced in the hermetic sandbox;
   `tools/verify_input.py` already records the running version.
