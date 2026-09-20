---
status: approved
issue: 22
spec: spec/2026-09-20-22-lua-dispatch-bypass.md
---

# Plan: hypr_dispatch stops accepting Lua source from the model

## The approved decisions, in full

Implementable without opening the intent or the spec.

**The defect.** `_DISPATCH_RE` (`tools.py:45-48`) is
`^hl\.dsp(?:\.[A-Za-z_][\w]*)+\s*\(.*\)\s*;?\s*$` with `re.DOTALL`, so anything
at all may sit between the parentheses, and `_validate_hypr_dispatch`
(`tools.py:1522`) only checks the *outer* method name against
`SHELL_DISPATCHERS`. Hyprland 0.56 evaluates the argument position as Lua, where
`hl.exec_cmd` is in scope. Verified on this host:

```
$ hyprctl dispatch 'hl.dsp.focus((function() error("PROBE_ARBITRARY_LUA_RAN") end)())'
error: [string "return hl.dispatch(hl.dsp.focus((function() e..."]:1: PROBE_ARBITRARY_LUA_RAN
```

`allow_shell = false` withholds `run_shell` from the model entirely
(`tools_for`, `tools.py:1024`), yet this grants arbitrary shell execution. The
deny patterns *do* see the payload — `describe()` has a `hypr_dispatch` branch
returning the raw Lua (`tools.py:1372-1373`) — so `DEFAULT_DENY`
(`config.py:132-156`) is the only remaining barrier. Measured with a dry-run
executor at `allow_shell = false`: `touch`, `cp`, `kill -9` and `xdg-open`
nested in `hl.exec_cmd` are all **allowed with no confirmation**; `rm -rf` is
denied.

**The fix.** The model stops authoring Lua. It names a dispatcher and supplies a
flat table of scalars; Python renders the Lua through one function. Raw Lua
survives only when `allow_shell` is true.

Decisions carried over:

1. The dispatcher allowlist is parsed from the installed Hyprland's LuaLS stub,
   never hardcoded. Missing stub means refuse, not fall open.
2. `args` values are string, integer, float or boolean only. No nesting, arrays
   or null.
3. Some dispatchers take a positional string, via `message`. **Deviation
   during implementation:** the spec said `layout` was the only one. It is not
   — Omarchy's own bindings also use
   `hl.dsp.workspace.toggle_special("scratchpad")`, and the stub declares every
   dispatcher as `fun(...)`, so it cannot say which take a string. Restricting
   `message` to `layout` would have been a hardcoded guess of exactly the kind
   decision 1 forbids, so `message` is the string form for any dispatcher. It
   is escaped like any other value, which is what makes that safe.
4. One renderer, `render_dispatch`, is the only place Lua is built — including
   for the eleven internal call sites.
5. `_DISPATCH_RE` is deleted, not tightened.
6. The three Lua-rewriting helpers keep their checks but operate on the `args`
   dict instead of on source text.
7. `describe()` is built from the validated values; for `exec_cmd` / `exec_raw`
   the command string goes in verbatim so the deny patterns keep working.
8. Everything that teaches the model the Lua form changes in the same commit.

## Steps

**1. `src/omarchy_voice/capabilities.py`: add `dispatchers()`.**
Return `frozenset[str]` of dotted names (`"focus"`, `"window.close"`,
`"workspace.move"`) from the same stub parse `dispatcher_tree()` already does at
`capabilities.py:103-130`. Root-namespace members get no prefix; the others get
`"<namespace>."`. Cache on the existing manifest key (`capabilities.py:555-571`)
so a Hyprland upgrade re-reads it. Empty set when `HL_STUB` is absent.
→ verify by: on this host it contains `focus`, `window.close`,
`workspace.change_id`, `layout`, `no_op`, `exec_cmd` and has ~55 members;
pointing `OMARCHY_VOICE_HL_STUB` at a missing file yields an empty set.

**2. `src/omarchy_voice/tools.py`: add `render_dispatch`.**
`render_dispatch(dispatcher, args=None, message=None, *, allow_shell) -> tuple[str | None, str | None]`,
returning `(lua, error)` to match the `normalise_*` convention in `keys.py`.

- Refuse a dispatcher outside `capabilities.dispatchers()`; if that set is
  empty, refuse with the install hint rather than accepting anything.
- Refuse `exec_cmd`, `exec_raw`, `exec` (`SHELL_DISPATCHERS`, `tools.py:40-42`)
  unless `allow_shell`.
- Refuse an argument key not matching `^[A-Za-z_]\w*$`.
- Refuse a value that is not `str`, `bool`, `int` or `float`; refuse a
  non-finite float. Check `bool` before `int` — `isinstance(True, int)` is true.
- `message` is accepted only for `layout`, and only as a string.
- Emit `hl.dsp.<dotted>()` with no argument when both are absent.

Write an explicit Lua string escaper; do **not** reuse `json.dumps`, which
`_tool_send_shortcut` currently uses at `tools.py:1578-1579`. I tested that
Hyprland's Lua happens to accept the `\uXXXX` form `json.dumps` emits for
non-ASCII (`hyprctl dispatch 'hl.dsp.no_op({ x = "café" })'` → `ok`, while
`"\q"` → `invalid escape sequence`, so errors do surface), but that is an
undocumented overlap between two escape dialects and not something to build a
security boundary on. Escape `"`, `\`, newline, carriage return, tab and any
other C0 control; pass other UTF-8 through, which the same test showed works.
→ verify by: step 2 of Tests.

**3. `tools.py`: rewrite the three helpers as argument checks.**

- `_normalise_window_addresses` (`tools.py:1117`) → if `args["window"]` is bare
  hex, prefix `address:`. Drop `_BARE_ADDRESS_RE` (`tools.py:1031`).
- `_normalise_shortcut_lua` (`tools.py:1089`) → when the dispatcher is
  `send_shortcut`, run `normalise_mods` / `normalise_key` (`keys.py:161,190`) on
  `args["mods"]` / `args["key"]`. Drop `_LUA_KEY_RE` and `_LUA_MODS_RE`
  (`tools.py:1033-1034`).
- `_misused_change_id` (`tools.py:1231`) → when the dispatcher is
  `workspace.change_id`, require both `workspace` and `id` keys. Keep the error
  string verbatim; it names the right call and the session log shows this is the
  single most repeated model mistake.

→ verify by: `tests/test_keys.py:156-162` ("a bad key never reaches hyprctl")
passes unchanged against the structured path.

**4. `tools.py`: rewrite the `hypr_dispatch` schema and handler.**
Schema properties become `dispatcher` (required), `args`, `message`, and `lua`.
`_tool_hypr_dispatch` routes: `lua` present → refuse unless
`config.allow_shell`, otherwise dispatch it as today; otherwise
`render_dispatch`. Delete `_DISPATCH_RE` and `_validate_hypr_dispatch`'s regex
half. The description tells the model the structured form and says `lua` needs
`allow_shell`.
→ verify by: steps 1 and 3 of Tests.

**5. `tools.py`: `describe()` branch.**
Replace `return args.get("lua", "")` (`tools.py:1372-1373`) with:
`dispatch <dotted> <k>=<v> …`, values via `repr`, keys sorted so the transcript
is stable. No args → `dispatch <dotted>`. A raw `lua` call still describes as
the full source. For `exec_cmd` / `exec_raw` the command value appears
verbatim.
→ verify by: `Executor.describe("hypr_dispatch", {"dispatcher": "focus", "args":
{"workspace": "3"}}) == 'dispatch focus workspace=\'3\''`, and a deny pattern
still fires on an `exec_cmd` carrying `rm -rf`.

**6. `tools.py`: move the eleven internal call sites to `render_dispatch`.**
`tools.py:1581` (`send_shortcut`), `1909`, `2121`, `2168`, `2195`, `2199`
(`layout`, the `message` case), `2226`, `2235`, `2614`, `2633`, `2766`. They
interpolate values from `hyprctl`, not from the model, so this is not the
vulnerability — it is what makes "the renderer is the only producer" true and
checkable. Pass `allow_shell=True` internally: these are our own calls, and
none of them is a shell dispatcher.
→ verify by: `grep -n 'hl\.dsp' src/omarchy_voice/tools.py` returns only
`render_dispatch`, docstrings and `SHELL_DISPATCHERS` — no f-strings.

**7. `capabilities.py`: stop teaching the Lua form.**
`RECIPES` (`capabilities.py:421-433`) becomes
`("Switch to workspace N", {"dispatcher": "focus", "args": {"workspace": "4"}})`
and the manifest renders from it, so it cannot drift from the schema. Rewrite
the "tables, not positional strings" prose (`capabilities.py:523-524`) and the
`hl.dsp.workspace.change_id` warning (`capabilities.py:433`) for the new shape.
`dispatch_examples` (`capabilities.py:376-408`) keeps scraping Omarchy's Lua —
that is where version-correct argument shapes come from — but presents each
example structured.
→ verify by: `omarchy-voice manifest` contains no `hl.dsp.` outside the
dispatcher tree listing; step 5 of Tests.

**8. `README.md`.** State that `allow_shell = true` installs keep raw Lua and
therefore keep this route, and that the fix is not universal. The repo is public
and `verify-gate` advertises the gate as proven, so per the approved intent this
is said plainly rather than landed quietly.
→ verify by: read it.

## Tests

New cases in `tests/test_policy.py`, plus one file for the renderer. Plain
`unittest`, `sys.path.insert` header, `Executor._shell` as the seam — the
conventions in `tests/test_reach.py:44-64` and `tests/test_compose.py:149-160`.

1. **The reproduction is refused.** With `Config(allow_shell=False)`,
   `call("hypr_dispatch", {"lua": 'hl.dsp.focus((function() hl.exec_cmd("touch /tmp/probe") return { workspace = "1" } end)())'})`
   returns `ok=False`, and `shell.assert_not_called()`.
2. **The blast radius is closed.** The four commands measured in the intent —
   `touch /tmp/probe`, `cp ~/notes /tmp/x`, `kill -9 4242`,
   `xdg-open http://example.com` — each nested in `hl.exec_cmd` inside a
   `hl.dsp.focus` argument, are all refused at `allow_shell = False`. These pass
   today with no confirmation, so this test fails before the change and is the
   one that proves the fix.
3. **Escaping holds.** Direct on `render_dispatch`: values containing `"`, `\`,
   a newline, `)`, `--`, `}` and a non-ASCII character each render to a literal
   whose closing quote is the intended one. Assert on the rendered string; no
   subprocess.
4. **The allowlist is live.** With `OMARCHY_VOICE_HL_STUB` pointed at a fixture
   stub declaring one dispatcher, only that one is accepted. Pointed at a
   missing file, structured dispatch refuses rather than accepting anything.
5. **Nothing legitimate regressed.** Table-driven over `RECIPES`: each renders
   to the Lua string it produces today. This pins the manifest to the schema.
6. **Existing suites pass unchanged**, particularly `tests/test_keys.py` (the
   keysym check moved but must behave identically) and `tests/test_compose.py`
   (six of the eleven moved call sites are compose's).

Commands and expected results:

```
python3 -m unittest discover -s tests     # green; 589 before, 602 after
nix flake check                           # green
omarchy-voice verify-gate                 # exit 0, four live model turns
omarchy-voice say "switch to workspace 3" # workspace moves; transcript reads
                                          #   dispatch focus workspace='3'
```

**Deviation:** run these inside `nix develop`. Outside it, `libxkbcommon` is
not on `LD_LIBRARY_PATH` — the wrapper sets it at `package.nix:88-99` — so
`keys._xkb()` returns None and eleven `test_keys` cases fail for reasons that
have nothing to do with the change. The plan's "341 tests" came from
`HANDOFF.md` and was stale; the real baseline is 589.

The last two need the live session and are run by hand, as `HANDOFF.md:689-701`
prescribes. `verify-gate` is the one check that proves the gate end to end
against the installed Claude Code CLI (`verify_gate.py:148-171`).

## Rollback

Everything is one branch, `fix/22-lua-dispatch-bypass`, and nothing outside the
repo changes — no state files, no schema migration, no new dependency, no
packaging change. `git revert` the merge, or `nixarchy-apply` the previous
generation, and the daemon is back to the old behaviour on restart.

The one caveat worth stating: reverting restores the bypass. If the structured
API turns out to block something legitimate in real use, the smaller move is to
set `allow_shell = true` for that session — which re-enables the `lua` field by
design — rather than reverting the commit.
