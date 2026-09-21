---
status: draft
issue: 64
spec: spec/2026-09-21-64-stub-follows-the-compositor.md
---

# Plan: ask the compositor, keep the stub for when it cannot answer

## The approved decisions, carried over

1. **Ask the running compositor** for its dispatcher set, by walking `hl.dsp`
   through `hyprctl repl`. It cannot disagree with the compositor because it is
   the compositor.
2. **Cache for the process lifetime.** The set changes only on a compositor
   restart, which ends the session.
3. **The stub file stays as the no-compositor fallback**, because `nix flake
   check` is hermetic and has no Hyprland — required, not speculative.
4. **`package.nix` sets `OMARCHY_VOICE_HL_STUB_FALLBACK`**, not the primary
   variable. `--set-default` made "somebody chose this" and "the packaging
   guessed" share one name, which is why explicit-first could never reach the
   running system.
5. **Still fail closed** (#22): no compositor and no stub means every
   dispatcher is refused, naming both causes.

## One conflict between decisions, resolved here

Decision 1 says ask the compositor first; decision 4 says an explicit
`OMARCHY_VOICE_HL_STUB` "wins over everything". Read together those disagree
about a developer who sets the variable on a machine that *has* a compositor.

**Resolved in favour of decision 4.** The full order is:

1. `OMARCHY_VOICE_HL_STUB` — explicit, beats the live compositor too. Someone
   pointing this at a specific file is debugging or testing, and silently
   ignoring them would waste exactly the time the variable exists to save.
2. the live compositor
3. `/run/current-system/sw/share/hypr/stubs/hl.meta.lua`
4. `/etc/profiles/per-user/$USER/…`, `~/.nix-profile/…` — home-manager
5. `OMARCHY_VOICE_HL_STUB_FALLBACK` — what the packaging shipped
6. `/usr/share/hypr/stubs/hl.meta.lua` — non-NixOS

## Measured before planning

| | |
|---|---|
| live dispatchers, `hl.dsp` walked recursively | **51** |
| the stub `dispatchers()` parses today | **51**, zero difference either way |
| `hyprctl repl` with no compositor | exit **4**, **17 ms**, no hang |
| bad Lua | `error: 3 [string "…"]: syntax error` — parseable |
| `hl.dsp` metatable | **none**, so `pairs()` is complete |

`dispatchers()` (`capabilities.py:152`) is the only seam. Its docstring already
says the set is "read off the installed compositor, never hardcoded" — which is
aspirational today, since it reads a stub file pinned by a different nixpkgs.
This makes the docstring true.

## Steps

1. **`capabilities.py`: `_LIVE_DISPATCHER_LUA`**, a module-level constant that
   walks `hl.dsp` and returns comma-separated dotted names.
   → verify by a test asserting it is a plain string literal containing no
   `%`, `{`, or f-string marker — the spec asked for this explicitly because
   #22 is about Lua never being built from input.

2. **`capabilities.py`: `_live_dispatchers() -> frozenset[str]`.** Runs
   `hyprctl repl <constant>` with a short timeout. Returns an empty set on
   non-zero exit, on output starting `error:`, or on an unparseable line.
   → verify by unit tests with a stubbed runner for each failure shape.

3. **`capabilities.py`: guard against a silently short list.** If `hl.dsp` ever
   gains an `__index`, `pairs()` returns a fraction and dispatchers start being
   refused. Treat a result below a floor as failure and fall through.
   **Floor: 20.** Measured, the bare top level of `hl.dsp` is 20 entries and
   the full walk is 51, so 20 is the largest value that cannot be reached by a
   metatable hiding the sub-tables, and is well under any real set.
   → verify by a test that a 19-name result is rejected and a 51-name one is not.

4. **`capabilities.py`: `_stub_path() -> Path | None`**, the ordered lookup
   above, minus the live step. Existing `HL_STUB` becomes this.
   → verify by unit tests over a temp tree: the fallback variable loses to the
   system path, and the explicit variable beats both.

5. **`capabilities.py`: `dispatchers()` becomes explicit → live → stub.**
   Cached with `functools.lru_cache`, like `_parse_stub` already is. Still
   returns an empty frozenset when nothing answers, because callers refuse on
   empty and #22 says that is correct.
   → verify by the existing dispatcher tests passing unedited, which is the
   check that this seam did not change shape.

6. **`nix/package.nix:95`**: `--set-default OMARCHY_VOICE_HL_STUB` becomes
   `--set-default OMARCHY_VOICE_HL_STUB_FALLBACK`.
   → verify by `nix build` and reading the wrapper.

7. **`flake.nix`**: leave the check and devShell setting `OMARCHY_VOICE_HL_STUB`
   as they are. That is now exactly right: the sandbox has no compositor and is
   deliberately pinning a known stub, which is what the explicit variable means.
   → verify by `nix flake check` passing, which exercises the fallback by
   construction.

8. **`tools/verify_dispatchers.py`**, committed beside the other harnesses:
   assert the live set and the stub set agree on a real machine, and print both
   counts and any difference.
   → verify by the recorded output in this file, from razer.

9. `nix flake check` and the suite inside `nix develop`.
   → verify by `all checks passed!` and no new failures.

## Tests

```
nix develop -c python3 -m unittest discover -s tests
nix flake check
ssh razer '... python3 tools/verify_dispatchers.py'
```

## Rollback

`git revert`. `dispatchers()` returns to reading the stub, and `package.nix` to
setting the primary variable. One caveat that belongs in the same commit: if
the package has already shipped with `OMARCHY_VOICE_HL_STUB_FALLBACK`, a revert
must restore the old name or an installed wrapper sets a variable nothing reads
— so step 6 is not separable from step 5.

## Risks

- **`hyprctl repl` is newer than `hyprctl dispatch`.** An older Hyprland
  without it fails the same way as no compositor (non-zero exit), which the
  chain already handles. Measured clean and fast, not hanging.
- **The floor in step 3 is a heuristic.** It cannot distinguish "metatable
  hides things" from "a future Hyprland genuinely has few dispatchers". Chosen
  so that the current top-level count is the boundary; if it ever fires
  wrongly the symptom is falling back to the stub, which is today's behaviour.
- **A cached set survives a compositor restart within one process.** Accepted;
  in practice the session ends with the compositor.

## Out of scope

The `type_text` sensitive-window guard, which is the agreed next piece.
