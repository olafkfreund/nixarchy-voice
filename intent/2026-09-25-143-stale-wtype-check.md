---
status: approved
issue: 143
author: olafkfreund
---

# Intent: doctor stops reporting wtype, and missing_tools() stops pretending to be used

Closes #143. Split out of #77 (`spec/2026-09-24-77-engine-duplication.md`,
decision 2), which must not change anything a user sees.

## Problem

`omarchy-voice doctor`'s `hands` section checks five tools by name
(`src/omarchy_voice/cli.py:504`):

```python
for tool in ("hyprctl", "omarchy", "wtype", "notify-send", "uwsm-app"):
    print(f"  {_tick(bool(shutil.which(tool)))} {tool}")
```

One of the five is not used. #60 removed `wtype`: `_tool_type_text`
(`tools.py:3975-4012`) now sends `send_key_state` dispatches in one
`hyprctl --batch`, and its docstring says it "used to be three lines around
`wtype`" and that "wtype is gone rather than kept as a fallback". On main
(806804a) `grep -rn wtype src/` finds only that docstring, two comments
(`tools.py:369`, `keys.py:217`), the doctor line and `missing_tools()`.
Nothing runs it. So a machine without wtype gets `✗ wtype` from doctor for
a tool it does not need. A doctor that shows a red cross for something that
does not matter teaches the user to ignore its red crosses.

`capabilities.missing_tools()` (`capabilities.py:1388-1389`) checks
`hyprctl`, `omarchy`, `wtype`, `pw-record`. It has **no callers**:
`grep -rn missing_tools src tests nix` finds only its definition. It is dead
code, and its list is out of date too.

### What the hands line checks, against what the code runs

| Tool | Checked by doctor `hands` | Actually used |
| --- | --- | --- |
| `hyprctl` | yes | yes, all dispatch, **typing included** (`tools.py:4012`) |
| `omarchy` | yes | yes (`tools.py:791-799`, `2510`, `4164`, `4336`) |
| `wtype` | yes | **no** |
| `notify-send` | yes | yes, soft (`feedback.py:123`, `cli.py:327`) |
| `uwsm-app` | yes | yes, falls back to `gtk-launch` (`tools.py:804`, `2662`) |
| `ai-mirror-input` | **no** | yes: `click` and `scroll` (`virtual_input.py:58`, `tools.py:3303`, `4611`) |
| `grim`, `tesseract` | no | yes, screen reading; tools check at call time (`tools.py:2997`, `3048`, `3168`) |

One correction to the issue text: typing does **not** go through the Wayland
virtual-keyboard helper. It goes through `hyprctl` `send_key_state`, which
doctor already checks. The helper, `ai-mirror-input`
(`zwp_virtual_keyboard_v1` / `zwlr_virtual_pointer_v1`, per
`nix/package.nix`), is used for clicking and the wheel. Doctor does not
check it anywhere. `grep` finds no `ai-mirror-input` or `virtual_input` in
`cli.py`. So the one needed hands tool the line misses is the pointer
helper, not a keyboard one. `pw-record` is already reported by the
microphone section through `listen_local` (`listen_local.py:260-261`).

### Packaging still ships wtype

- `nix/package.nix:7,69`: `wtype` is still in `runtimeInputs` of the
  wrapper. The comment above it (`package.nix:65-67`) still gives wtype as
  the example: "a missing wtype means the model silently cannot type".
- `flake.nix:62`: in the devShell packages.
- `flake.nix:128`: in the check sandbox's PATH, with the comment at
  `flake.nix:141` ("the same reason grim and wtype are on PATH above").
  No test runs wtype. `tests/test_policy.py:215` and `tests/test_web.py:92`
  assert it is **absent** from the commands.
- `nix/hm-module.nix`: no mention.
- `README.md:61`: the architecture diagram lists `hyprctl · omarchy · wtype
  · uwsm-app`. `README.md:219` and `426` name wtype among the tools the
  daemon shells out to.

## Proposed outcome

- `omarchy-voice doctor` on a machine without wtype shows no `wtype` line
  and no cross for it. Every tool the hands section lists is one the daemon
  actually runs.
- `missing_tools()` no longer exists as dead code with a stale list. It is
  either gone or has a real caller and a correct list.
- Nothing the user reads (doctor, README) calls wtype a dependency.

## Affected users and systems

- `src/omarchy_voice/cli.py` (doctor `hands`), `capabilities.py`.
- Possibly `nix/package.nix` and `flake.nix` (closure size and wrapper
  PATH), `README.md`.
- Anyone who runs `omarchy-voice doctor`, and anyone reading its output to
  decide what to install.

## Constraints

- Must not change typing, clicking or any other tool behaviour. This is a
  reporting and packaging fix only.
- Doctor stays read-only and fast. It must not spawn the input helper, since
  that opens Wayland globals, just to report it. A `shutil.which` check,
  like the other hands entries, is the ceiling.
- Removing wtype from the wrapper must not break the check sandbox. The
  suite must stay green without it on PATH.
- No new dependency.

## Open questions

1. **`missing_tools()`: delete it, or give it a caller?** Nothing has
   needed it since it was written, and doctor already does the same job
   inline. Deleting it is the smaller change. A caller would mean a second
   list of tools to keep in step with doctor's.
2. **What should doctor's hands line check instead of wtype?** Drop wtype.
   Add `ai-mirror-input` (the pointer helper for `click`/`scroll`), which is
   used and not checked? Also `grim`/`tesseract`, which the screen-reading
   tools already report at call time?
3. **Should wtype leave the package?** Remove it from `runtimeInputs`, the
   devShell and the check sandbox (and fix the comments that cite it), or
   leave packaging alone and change only what doctor reports? And update
   the README diagram and tool lists in the same change, or separately?
