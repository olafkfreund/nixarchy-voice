---
status: approved
issue: 22
author: olafkfreund
---

# Intent: hypr_dispatch accepts arbitrary Lua, so allow_shell=false is bypassable

## Problem

`hypr_dispatch` is meant to let the model run exactly one Hyprland dispatcher.
It does not. `_DISPATCH_RE` (`src/omarchy_voice/tools.py:45-48`) is

```
^hl\.dsp(?:\.[A-Za-z_][\w]*)+\s*\(.*\)\s*;?\s*$
```

with `re.DOTALL`, and `_validate_hypr_dispatch` (`tools.py:1522`) derives the
method name with `expr.split("(", 1)[0].split(".")[-1]` — the *outer* call only.
Everything between the parentheses is unexamined, Hyprland evaluates it as Lua,
and `hl.exec_cmd` is in scope there.

Reproduced on this machine (Hyprland 0.56.0), in two halves so nothing was
actually executed as a shell command:

```
# the validator accepts it, with allow_shell=False
payload = 'hl.dsp.focus((function() hl.exec_cmd("id > /tmp/pwn") return { workspace = "1" } end)())'
>>> Executor(Config(allow_shell=False, dry_run=True))._validate_hypr_dispatch(payload)
None

# and Hyprland does evaluate arbitrary Lua in that position
$ hyprctl dispatch 'hl.dsp.focus((function() error("PROBE_ARBITRARY_LUA_RAN") end)())'
error: [string "return hl.dispatch(hl.dsp.focus((function() e..."]:1: PROBE_ARBITRARY_LUA_RAN
```

Two guarantees fail at once:

- **`allow_shell = false` does not hold.** `SHELL_DISPATCHERS`
  (`tools.py:40-42`) is checked against the outer method, so a nested
  `hl.exec_cmd` never meets it.
- **The deny patterns never see the command.** `Policy.check`
  (`tools.py:63-77`) matches regexes against a one-line English description. The
  description of this call reads as a workspace switch. `rm -rf`, `sudo`,
  `nixos-rebuild` and the rest of `DEFAULT_DENY` (`config.py:132-156`) are all
  reachable behind text that does not contain them.

This is the foundation the whole safety story rests on: the README, the
confirmation hold, and `verify-gate` all describe a system where a dangerous
action is either denied or spoken aloud before it runs.

Found by an independent Codex review of the integration direction; reproduced
locally before filing.

## Proposed outcome

- A tool call that would run a shell command through Hyprland is refused, or
  held for confirmation, on the same terms as any other shell command — whatever
  syntax it is wrapped in.
- The one-line description the policy gate matches contains the effectful part
  of the action verbatim. It is derived from validated values, not recovered by
  parsing a string the model wrote.
- The reproduction payload above has a regression test and cannot come back.
- `omarchy-voice verify-gate` still exits 0 against the installed Claude Code
  CLI, and the existing suite stays green.

## Affected users and systems

- Anyone running the daemon with the default `allow_shell = false`, which is
  every default install (`share/config.example.toml:159-181`).
- `src/omarchy_voice/tools.py` — `_DISPATCH_RE`, `_validate_hypr_dispatch`,
  `_tool_hypr_dispatch`, the `hypr_dispatch` schema in `TOOL_SCHEMAS`, and
  `describe()`.
- Every brain, since all four entry shapes share one `Executor`: the local
  engine, the realtime engine, `omarchy-voice say/ask`, and `omarchy-voice mcp`.
- Hyprland 0.56+, whose Lua dispatcher API is what makes the argument position
  executable. Earlier string dispatchers did not have this property.

## Constraints

- Must not weaken what `hypr_dispatch` can legitimately reach. Workspace
  focus, window movement, resize, layout and `send_shortcut` all go through it
  today and the compose path depends on them (`tools.py:2158-2261`).
- Must keep the existing mocking seam. Tests fake `Executor._shell`
  (`tests/test_reach.py:44-64`); a fix that moves execution elsewhere has to
  keep that seam or update every test that uses it.
- Must stay correct against the installed Hyprland, not a remembered one. The
  dispatcher tree is already read off `hl.meta.lua` at runtime
  (`capabilities.py:103-130`) precisely because this API changes shape between
  releases — a fix must not hardcode a dispatcher list that goes stale.
- No new runtime dependency. The package wraps a fixed tool list
  (`nix/package.nix:63-83`); a Lua parser is not on it.
- `_normalise_shortcut_lua` and `_normalise_window_addresses`
  (`tools.py:1034-1126`) exist only to repair model-authored Lua with regexes.
  If the model stops authoring Lua, their reason to exist goes with it.

## Open questions

1. **Does raw Lua survive at all?** Removing it entirely is the strongest fix
   and costs the model an escape hatch for dispatchers nobody anticipated. If it
   survives behind `allow_shell`, the description must carry the full text.
2. **Scope of the adjacent bug.** `_query_json` (`tools.py:2057-2062`) returns
   `[]` when `hyprctl` fails, and `wait_for(what="window_gone")`
   (`tools.py:2824`) reads `[]` as "the window is gone" — a failed query reports
   success. Same file, same review, unrelated mechanism. Fix here, or its own
   issue?
3. **Disclosure.** The repo is public and `verify-gate` advertises the gate as
   proven. Does the fix land quietly first, or does the commit message say
   plainly what it closes?
