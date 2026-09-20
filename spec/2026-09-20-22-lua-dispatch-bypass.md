---
status: approved
issue: 22
intent: intent/2026-09-20-22-lua-dispatch-bypass.md
---

# Spec: hypr_dispatch stops accepting Lua source from the model

## Design

The defect is that the model authors Lua and we try to validate it with regexes.
A regex cannot describe what executable code does, and `_DISPATCH_RE`
(`tools.py:45-48`) demonstrates it: `\(.*\)` with `DOTALL` accepts any program
as long as it is wrapped in an `hl.dsp.*(...)` call.

So the model stops authoring Lua. It names a dispatcher and supplies arguments;
Python renders the Lua. A raw-Lua escape hatch survives, but only behind
`allow_shell`, per the approved intent.

### 1. The dispatcher allowlist comes from the installed Hyprland

`capabilities.dispatcher_tree()` (`capabilities.py:103-130`) already parses the
`hl.dsp` namespace out of Hyprland's own LuaLS stub. On this machine it yields
five namespaces and about fifty-five dispatchers:

```
hl.dsp.cursor.{move, move_to_corner}
hl.dsp.group.{active, lock, lock_active, move_window, next, prev, toggle}
hl.dsp.{dpms, event, exec_cmd, exec_raw, exit, focus, force_idle, …, submap}
hl.dsp.window.{alter_zorder, bring_to_top, center, …, toggle_swallow}
hl.dsp.workspace.{change_id, move, rename, swap_monitors, toggle_special}
```

Add `capabilities.dispatchers() -> frozenset[str]` returning dotted names
(`"focus"`, `"window.close"`, `"workspace.move"`) from the same parse, cached on
the same key as the manifest (`capabilities.py:555-571`). This satisfies the
intent's constraint that no dispatcher list is hardcoded: it is read off the
installed compositor and re-read when Hyprland's version changes.

If the stub is missing, `dispatchers()` is empty. Treat that as "cannot
validate" and refuse structured dispatch with an install hint, rather than
falling open. `dispatcher_tree()` already has a "(Lua stub not found)" path
(`capabilities.py:586`); this is the enforcing half of it.

### 2. The tool schema

`hypr_dispatch` in `TOOL_SCHEMAS` becomes:

```python
{
  "dispatcher": {"type": "string",
                 "description": 'Dotted name, e.g. "focus", "window.close", "workspace.move".'},
  "args":       {"type": "object",
                 "description": "The dispatcher's single table argument. Omit for none."},
}
# required: ["dispatcher"]
```

`args` values are restricted to string, integer, float and boolean. Nested
tables, arrays and null are refused — nothing in `RECIPES`
(`capabilities.py:421-433`) or in the scraped examples needs one, and allowing
them reopens the question of what a value may contain.

`hl.dsp.layout` is the documented exception: it takes a layout message string,
not a table (`capabilities.py:523-524`, and the call at `tools.py:2199`
sends `hl.dsp.layout("preselect r")`). Model it as a dispatcher whose argument
is a bare string, via an optional `"message"` property used only by `layout`.

### 3. Rendering

One function, `render_dispatch(dispatcher, args, message=None) -> str`, the only
place Lua is built:

- Reject a dispatcher not in `capabilities.dispatchers()`.
- Reject `exec_cmd`, `exec_raw`, `exec` (the existing `SHELL_DISPATCHERS`,
  `tools.py:40-42`) unless `config.allow_shell`.
- Render each value as a Lua literal: booleans bare, numbers via `repr` after a
  finiteness check, strings double-quoted with `"`, `\`, newline, carriage
  return and any other control character escaped. A string can then never close
  its own quote, so the payload in the intent has nowhere to live.
- Reject an argument key that is not `[A-Za-z_]\w*`.

Because the renderer is the only producer, "is this one dispatcher call?" stops
being a question asked of a string and becomes a property of the construction.
`_DISPATCH_RE` is deleted.

### 4. The three Lua-rewriting helpers become argument checks

They exist only to repair model-authored Lua with regexes, and the intent asks
whether they go. They do not go — their *checks* are load-bearing — but they
move from source text to the `args` dict:

| Today | Becomes |
|---|---|
| `_normalise_window_addresses` (`tools.py:1117`), `_BARE_ADDRESS_RE` over source | if `args["window"]` is bare hex, prefix `address:` |
| `_normalise_shortcut_lua` (`tools.py:1089`), `_LUA_KEY_RE` / `_LUA_MODS_RE` over source | run `normalise_key` / `normalise_mods` (`keys.py`) on `args["key"]` / `args["mods"]` when dispatcher is `send_shortcut` |
| `_misused_change_id` (`tools.py:1231`) | if dispatcher is `workspace.change_id`, require both `workspace` and `id` keys |

Same rules, same error strings, no parsing. The keysym check in particular must
survive: `keys.py:3` records that `hl.dsp.send_shortcut({ key = "Enter" })`
returns `ok` and presses nothing.

### 5. describe(), and the raw escape hatch

`describe()` gains a real `hypr_dispatch` branch replacing
`return args.get("lua", "")` (`tools.py:1372-1373`):

```
dispatch focus workspace="3"
dispatch window.close
```

Built from the validated values, so it cannot disagree with what runs. For
`exec_cmd` and `exec_raw` the command string is included verbatim, which is what
lets the deny patterns keep working on the one dispatcher that takes a command.

The escape hatch: `hypr_dispatch` also accepts `{"lua": "<source>"}`, **only**
when `config.allow_shell` is true. Otherwise it is refused with a message
pointing at `dispatcher`/`args`. Its description stays the full source text, as
today. This is the approved answer to intent open question 1: the model keeps a
route to a dispatcher nobody anticipated, but only on an install that has
already accepted that the model can run commands.

### 6. Internal call sites

Eleven places build Lua by f-string and call `_dispatch_lua` directly
(`tools.py:1909`, `2121`, `2168`, `2195`, `2199`, `2226`, `2235`, `2614`,
`2633`, `2766`, and `send_shortcut` at `1581`). They interpolate addresses,
coordinates and workspace names that come from `hyprctl`, not from the model, so
they are not the vulnerability. They move to `render_dispatch` anyway, because a
single renderer is the thing that makes the guarantee checkable, and
`compose_windows` already reaches `_dispatch_lua` without passing
`_validate_hypr_dispatch` at all.

### 7. What the model is told

The schema description, the `RECIPES` table (`capabilities.py:421-433`), the
"tables, not positional strings" prose (`capabilities.py:523-524`) and
`dispatch_examples`, which scrapes real `hl.dsp.*` calls out of Omarchy's
bindings (`capabilities.py:322`, `376-408`), all currently teach the Lua form.
They are rewritten to the structured form in the same change. `RECIPES` becomes
`("Switch to workspace N", {"dispatcher": "focus", "args": {"workspace": "4"}})`
and renders from there, so the manifest cannot drift from the schema.

`dispatch_examples` keeps scraping Omarchy's Lua — that is where version-correct
argument *shapes* come from — but presents each example in the structured form.

## Alternatives rejected

- **Tighten the regex.** Forbid `function`, `exec_cmd`, parentheses inside the
  argument, and so on. This is the approach that already failed; a blocklist
  over a Turing-complete syntax has no defensible stopping point. Codex's
  finding was that the outer-method check is the wrong shape, not that it was
  missing a case.
- **Parse the Lua.** Correct, and it adds a Lua parser to a package whose
  runtime inputs are a fixed list (`nix/package.nix:63-83`). The intent forbids
  a new runtime dependency without justification, and generating Lua we already
  control needs no parser.
- **Drop raw Lua entirely.** Considered and set aside by the approver. It is the
  strongest fix; it also removes the only route to a dispatcher the structured
  API has not anticipated, on a compositor whose dispatcher API changed shape
  within the last minor release.
- **Hardcode the dispatcher allowlist.** Rejected by the intent's constraint. The
  0.56 API change is exactly the event that would strand a hardcoded list, and
  `capabilities.py` already exists because of it.
- **Fix `_query_json` here.** Split to issue #24 per the approver, to keep this
  diff scoped.

## Risks

- **The model keeps emitting the old shape.** Biggest risk, and it is a
  behaviour change rather than a crash: an unmigrated prompt means every
  `hypr_dispatch` fails. Mitigated by changing schema, recipes, prose and
  examples together, and by making the refusal message name the new fields.
  Both engines and all four entry shapes share one `Executor`, so there is one
  place to get it right and no partial rollout.
- **Argument shapes that only exist in the wild.** The structured API assumes
  flat scalar tables. If some dispatcher genuinely needs a nested table, it is
  unreachable except through the `allow_shell` escape hatch. Checked against
  every example in `RECIPES` and every scraped Omarchy binding on this host;
  none needs one. Unverified for dispatchers nobody here uses.
- **`allow_shell = true` installs are unchanged.** They keep raw Lua and keep
  the bypass, by construction — on those installs `run_shell` is already
  offered, so it grants nothing new. Worth stating plainly in the README rather
  than implying the fix is universal.
- **Escaping.** The one piece of genuinely security-relevant new code. A string
  that can close its own quote reopens the hole. Tested directly rather than
  only through the tool.
- **`hyprctl dispatch` argument length.** Rendering is not shorter than what the
  model wrote, so no new limit is crossed.

## Verification

1. **The reproduction fails.** `tests/test_policy.py` gains the exact payload
   from the intent:
   `hl.dsp.focus((function() hl.exec_cmd("touch /tmp/probe") return { workspace = "1" } end)())`
   submitted as `{"lua": ...}` with `allow_shell = false` is refused, and
   `Executor._shell` is never called — the `shell.assert_not_called()` pattern
   from `tests/test_compose.py:149-160`.
2. **The blast radius is closed.** The four commands measured in the intent
   (`touch`, `cp`, `kill -9`, `xdg-open`), which pass today with no
   confirmation, are refused with `allow_shell = false`.
3. **Escaping holds.** A direct test of `render_dispatch` with values containing
   `"`, `\`, a newline, `)` and `--` produces Lua whose string literals still
   terminate where intended.
4. **The allowlist is live, not baked.** With `OMARCHY_VOICE_HL_STUB` pointed at
   a fixture stub declaring one dispatcher, only that dispatcher is accepted.
   With the variable pointed at a missing file, structured dispatch refuses
   rather than falling open.
5. **Nothing legitimate regressed.** Every example in `RECIPES` renders to the
   Lua string it names today — a table-driven test, which also pins the manifest
   to the schema.
6. **The keysym check survived the move.** The existing `tests/test_keys.py`
   cases ("a bad key never reaches hyprctl", `test_keys.py:156-162`) pass
   against the structured path.
7. **Suite and gate.** `python3 -m unittest discover -s tests` stays green (341
   tests today). `nix flake check` passes. `omarchy-voice verify-gate` exits 0
   against the installed Claude Code CLI — it spends four live model turns and
   is the one check that proves the gate still holds end to end
   (`verify_gate.py:148-171`).
8. **A real turn still works.** `omarchy-voice say "switch to workspace 3"` on
   this host moves the workspace, and the transcript line reads
   `dispatch focus workspace="3"`.

Steps 1-6 are offline and belong in CI. Steps 7-8 need the session and are run
by hand, as `HANDOFF.md:689-701` already prescribes.
