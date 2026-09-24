---
status: draft
issue: 84
author: olafkfreund
---

# Intent: our own tools should not have to be searched for

Closes #84.

## Problem

Claude Code does not show the model our tools up front. Every MCP tool is
*deferred*: the model sees only its name and has to spend a whole turn on
`ToolSearch` to load the schema before it can call it. Our in-process server
`omarchy` has 24 tools (`tools_for(Config()) + GATE_SCHEMAS`, about 19 KB of
JSON schema), and nearly every request needs one of them. So nearly every
request pays for an extra model turn before any work starts.

Measured on 2026-09-24 against `main` (60a8be6), Claude Code CLI 2.1.281,
claude-agent-sdk 0.2.152, model `claude-sonnet-5`. Six typed dry runs,
`omarchy-voice -n say --no-confirm "<request>"`. Each `say` starts a fresh
session. Tool calls come from the CLI's own session logs
(`~/.claude/projects/-home-olafkfreund/<session>.jsonl`). The round trip for a
call is the time from its `tool_use` entry to the next model message.

| Request | Total | Model requests | `ToolSearch` calls (round trip) | Tool actually needed |
|---|---|---|---|---|
| what workspace am I on | 6.4 s | 1 | 0 | none (answered from the prompt) |
| open zed | 10.8 s | 4 | 1: `select:omarchy_cli,launch_app` (1.3 s) | `omarchy_cli` |
| turn the volume down | 6.6 s | 2 | 0 | none called; the model only narrated it |
| switch to workspace 3 | 10.1 s | 4 | 1: `select:hypr_dispatch` (1.4 s) | `hypr_dispatch` |
| what is on my screen | 16.3 s | 3 | 1: `read_screen` (2.4 s) | `read_screen` |
| find me a screen recorder | 15.7 s | 6 | **3**: `select:find_app` (3.4 s), `screen recorder app find` (1.9 s), `select:omarchy_help` (1.1 s) | `omarchy_help` |

Every request that reached one of our tools went through `ToolSearch` first.
That is 4 of 4 runs, with 6 searches in total costing 1.1–3.4 s each and
11.5 s across the four runs. The 1.1–3.4 s range sits inside the 1.5–7.6 s
per-turn cost measured in intent #23 (`intent/2026-09-20-23-desktop-loop-latency.md:28`).
The last row is the worst case. The model guessed a tool that is not on `main`
(`find_app` is still on the #70 branch). Because it could not see the real
list, it searched twice more before it found `omarchy_help`. With the schemas
visible it would have seen straight away that `find_app` does not exist.

Side observation, not in scope: in 4 of 6 runs the model also called
`Bash {"command": "true", "description": "noop"}` (1.4–2.0 s). Nothing in
`src/` asks for it.

### Where it comes from in our code

- `ClaudeBrain._options()` (`src/omarchy_voice/claude_backend.py:415-463`)
  registers the server as `{"type": "sdk", "name": "omarchy", "instance": ...}`
  (`:433`). The server entry has no loading hint, and neither does
  `ai-mirror` (`:439`).
- `_child_env()` (`claude_backend.py:465-488`) only blanks
  `ANTHROPIC_API_KEY` (`:488`). It does not set `ENABLE_TOOL_SEARCH`, and
  that variable is not set in the environment of this machine or in
  `~/.claude/settings.json`.
- `_to_mcp_tools()` (`src/omarchy_voice/mcp_server.py:93-107`) builds each
  `mcp.types.Tool` with only `name`, `description` and `inputSchema`
  (`:102-106`), so no tool carries `_meta`.
- `ToolSearch` is already allowed through the dry-run gate on purpose
  (`DRY_RUN_READS`, `claude_backend.py:101`). That is why these dry runs show
  the real cost.

### What controls deferral

Found in the Claude Code MCP docs (https://code.claude.com/docs/en/mcp,
fetched 2026-09-24) and confirmed in the CLI 2.1.281 binary
(`claude-code-native-2.1.281/lib/claude-code/claude`, strings):

1. **`ENABLE_TOOL_SEARCH` env var** (whole session). In the binary, the mode
   function reads `process.env.ENABLE_TOOL_SEARCH`:
   - unset → `"tst"` (every MCP tool is deferred). This is the current default.
   - `false`/`0`/`no`/`off`, or `auto:100` → `"standard"` (nothing is deferred).
   - `auto` / `auto:N` → `"tst-auto"`. Tools are deferred only when their size
     passes N% of context. The default N is 10.

   Setting it to off also stops deferral of Claude Code's own deferrable
   built-ins and of `ai-mirror`'s tools, not only ours. The docs also say a
   custom `ANTHROPIC_BASE_URL` turns tool search off.
2. **`alwaysLoad: true` on an MCP server entry** (per server). The docs show
   it on a server config. In the binary, a tool with `alwaysLoad === true` is
   never deferred (checked before the `isMcp` → defer rule), and a tool
   inherits `alwaysLoad` from its server's config. The SDK passes every key
   of an `sdk` server entry except `instance` through to the CLI
   (`claude_agent_sdk/_internal/transport/subprocess_cli.py:658-666`), so
   `"alwaysLoad": True` next to `"type": "sdk"` should reach it.
3. **`_meta: {"anthropic/alwaysLoad": true}` on a tool** (per tool). The docs
   describe it and the binary reads `_meta["anthropic/alwaysLoad"]`. The
   SDK's own `create_sdk_mcp_server` never sets it (`_build_meta` emits only
   `anthropic/maxResultSizeChars`). We do not use that helper, though. We build
   `mcp.types.Tool` ourselves, so we can set `_meta` directly.

**Not verified:** that options 2 and 3 take effect for an `sdk` server in
practice (the binary shows the code paths, but no run has tested them). Also
not verified: what the extra ~19 KB of schema on every request costs in
latency and prompt-cache behaviour, whether the model picks tools better with
all 24 schemas visible, and whether `alwaysLoad` keeps working on later CLI
versions. The flag is not in any SDK typed option (`McpSdkServerConfig`
has only `type`, `name`, `instance`).

### The warm voice session

`WarmBrain` (`claude_backend.py:567`) holds one client for the whole voice
session (`start`, `:603`). `ToolSearch` results stay in the conversation, so
each tool's search should be paid once per session, not once per request.
Voice is still affected, in three ways:
- The warm-up (`WARM_UP = "Reply with one word: ready."`, `:554`) is
  designed not to touch any tool, so it loads none. The first time a person
  uses each tool, they wait for its search.
- The session is rebuilt from scratch whenever `reset_turn` cannot drain an
  interrupted turn (`:697`) or the warm-up fails (`:639`). Every search is
  paid again after that.
- A guessed name that does not exist (the `find_app` case) costs extra
  searches whether or not the session is warm.

Typed `say` uses `ClaudeBrain._ask`, which opens a new `ClaudeSDKClient`
per call (`:497`), so it pays on every request. This was reasoned from the
code. A live voice session was not measured.

## Proposed outcome

- A request that needs one of our tools calls it on the model's first turn,
  with no `ToolSearch` before it. You can see this in the session log and in
  the `action` lines of `say`.
- The rerun table above shows that for the same six requests: zero
  `ToolSearch` calls for `omarchy` tools, and the "Total" column is measured
  again, not assumed.
- `trace_timings` reports the difference with and without the change.
- The choice is recorded with the CLI version it was verified on, so an
  upgrade that changes it is noticed.

## Affected users and systems

- `src/omarchy_voice/claude_backend.py` (`_options`, possibly `_child_env`)
  and/or `src/omarchy_voice/mcp_server.py` (`_to_mcp_tools`).
- Both Claude entry points: typed `say`/`ClaudeBrain` (every call) and the
  voice daemon's `WarmBrain`/`LocalBrain` (first use per session).
- The MCP server as used by other clients (`omarchy-voice mcp` in another
  Claude Code session). A per-tool `_meta` hint would also reach those
  clients. A per-server entry in `_options` would not.
- `ai-mirror` tools, if the whole-session env switch is chosen.
- Not affected: the OpenAI/realtime and local-model backends, which do not
  go through Claude Code.

## Constraints

- The safety gate must not change. `_pre_tool_use` stays the only policy.
  Loading schemas earlier does not give the model any new permission.
- `claude_use_subscription` handling in `_child_env` must stay as it is.
- No reliance on undocumented behaviour without a test that fails when it
  changes. `alwaysLoad` is documented but not in the SDK's typed options.
- Measure with dry runs only (`-n --no-confirm`). The live desktop is not
  driven for this work.
- Keep the prompt within reasonable size. Our 24 schemas are ~19 KB, and they
  must not bring every Claude Code built-in back into the prompt unless that
  is chosen on purpose.

## Open questions

1. Scope: only our `omarchy` tools (`alwaysLoad` per server or per tool), or
   the whole session (`ENABLE_TOOL_SEARCH=false`), which also loads
   `ai-mirror` and Claude Code's deferred built-ins up front?
2. Per server (in `_options`, only when we are the caller) or per tool
   `_meta` (in `mcp_server.py`, so external clients of `omarchy-voice mcp`
   get it too)? And should only the common subset be always loaded, or all
   24?
3. Is the bigger prompt acceptable before it is measured, or must the spec
   first show that time to first token and cost per request do not get worse?
4. Should the `Bash "true"` no-op turn (4 of 6 runs) get its own issue?
