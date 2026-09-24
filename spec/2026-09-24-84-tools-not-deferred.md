---
status: approved
issue: 84
intent: intent/2026-09-24-84-tools-not-deferred.md
---

# Spec: load our own tools up front, and only ours

## The intent's open questions, answered

The intent was approved without answers to its four questions, so each is
decided here with the reasoning shown. Any of them can be rejected at this
gate.

The intent left one thing unverified: whether any of the three mechanisms
actually works for our in-process (`"type": "sdk"`) server. That is now
measured, not assumed.

### The measurement

Run on 2026-09-24 on `perf/84-tools-not-deferred` (= `main` 60a8be6 plus the
intent), Claude Code CLI 2.1.281, claude-agent-sdk 0.2.152, model
`claude-sonnet-5`. Each variant was a temporary one-line patch to the worktree
copy of `src/`, reverted afterwards (`git checkout -- src/`). Every run was a
dry run, `nix run .#omarchy-voice -- -n say --no-confirm "<request>"`. Tool
calls and per-request token usage come from the CLI's session log
(`~/.claude/projects/-home-olafkfreund/<session>.jsonl`). "Time" is the turn
time `say` prints on its `reply` line. "Prompt" is the first model request's
input tokens (`input + cache_creation + cache_read`). "Cost" is the
`~$… on your plan` figure: what the API would have charged. On the
subscription nothing is billed.

| Variant | Request | `ToolSearch` | Model requests | Tools called | Time | Prompt (1st req) | Cost |
|---|---|---|---|---|---|---|---|
| baseline (`main`) | open zed | 1 | 4 | Bash, ToolSearch, launch_app | 14.9 s | 50,974 | $0.2152 |
| baseline | switch to workspace 3 | 1 | 4 | Bash, ToolSearch, hypr_dispatch | 14.8 s | 50,975 | $0.2167 |
| baseline | what is on my screen | 1 | 3 | ToolSearch, read_screen | 15.7 s | 50,944 | $0.2104 |
| **A. `alwaysLoad` on server entry** | open zed | 0 | 2 | launch_app | 9.1 s | 57,735 | $0.2788 ¹ |
| A | switch to workspace 3 | 0 | 2 | hypr_dispatch | 7.4 s | 58,200 | $0.1859 |
| A | what is on my screen | 0 | 2 | Read ² | 10.2 s | 57,866 | $0.1561 |
| B. `ENABLE_TOOL_SEARCH=false` | open zed | 0 | 2 | launch_app | 17.9 s | **180,939** | **$1.4734** ¹ |
| B | switch to workspace 3 | 0 | 2 | hypr_dispatch | 12.4 s | **180,944** | $0.2658 |
| C. `_meta["anthropic/alwaysLoad"]` per tool | open zed | 0 | 2 | launch_app | 7.3 s | 57,734 | $0.1851 |
| C | switch to workspace 3 | 0 | 2 | hypr_dispatch | 7.2 s | 58,099 | $0.1535 |
| C | what is on my screen | 0 | 2 | read_screen | 16.0 s ³ | 58,192 | $0.1637 |

¹ First run after a rebuild, prompt cache cold (`cache_read = 0`), so the
whole prompt is a cache write. ² The model chose `Read` instead of
`read_screen` and answered from the focused window's title. One run, not
repeated; `read_screen` was plainly visible to it. ³ Includes the real
`read_screen` work (grim + OCR), which dry run allows as a read.

11 runs. B's third request was skipped once its cost was clear.

What this shows:

- **All three mechanisms work for an `sdk` server on CLI 2.1.281.** Every
  variant removed `ToolSearch` completely. The intent's "not verified" is now
  verified for this CLI version.
- **Our 24 schemas cost about 6,800 prompt tokens** (57.7–58.2 K against
  50.9–51.0 K), 13 % more per request. But each request that needs a tool now
  takes 2 model requests instead of 3–4. So with a warm cache the whole request
  costs *less* ($0.15–0.19 against $0.21–0.22), and it takes 7–10 s instead of
  15–16 s.
- **B loads far more than our tools.** It also loads the schema of every
  deferred tool from every MCP server this machine's Claude Code has configured
  (claude.ai connectors, GitHub, the factories and more). That makes the prompt
  181 K tokens, 3.5 times the baseline. The cold run cost $1.47. The warm run
  was still slower than A or C (12.4 s). What gets loaded depends on the user's
  own Claude Code setup, which we do not control.
- The `Bash {"command": "true"}` no-op turn from the intent appeared in 2 of
  3 baseline runs and in **0 of 8** runs with the fix. See Q4.

### Q1: scope. Only our `omarchy` tools.

B is rejected on the measurement above. It more than triples the prompt with
tools we never use, and its size depends on whatever else the user has
installed. A and C both cover exactly our 24 tools. `ai-mirror` and Claude
Code's own deferred built-ins stay deferred.

### Q2: per server (A) or per tool (C)? Per server, in `_options`, for all 24 tools.

A and C measured the same, so the choice comes down to who else it affects:

- **A only takes effect when we are the caller.** The entry is written by
  `ClaudeBrain._options()`, which typed `say`, `WarmBrain` and `LocalBrain`
  all share. A person who adds `omarchy-voice mcp` to their own coding session
  keeps Claude Code's default there. If they want it, they can put
  `alwaysLoad` on their own server entry.
- **C would push 6,800 tokens into every external client** of `omarchy-voice
  mcp`, on every request, whether or not that session drives the desktop. That
  is their prompt budget, and it should be their decision.
- A is one key on a dict we already build. C changes the MCP tool contract in
  `mcp_server.py`, which is shared by both paths.

All 24 tools, not a "common subset". A subset brings back the failure the
intent's worst row showed: the model guesses a tool it cannot see and has to
search for it. The whole set is only about 6.8 K tokens. With #85's `find_app`
it grows by one tool, and it stays loaded without any further change.

### Q3: is the bigger prompt acceptable? Yes. It was measured, not assumed.

The per-request prompt grows by about 6,800 tokens. The request as a whole
gets cheaper and faster anyway, because it makes one or two fewer model round
trips, and each of those re-sends the whole prompt. The one case that costs
more is a cold cache: the first request after a new CLI or a new prompt pays
about 6,800 extra tokens of cache write, roughly $0.03 at API prices. After
that it is cheaper.

Requests that need no tool ("what workspace am I on") still pay the 6,800
tokens without saving a round trip. They were not re-measured. In the intent,
2 of 6 requests were like this.

### Q4: separate issue for the `Bash "true"` no-op? Not yet.

It appeared in 2 of 3 baseline runs and in 0 of 8 runs with the fix. The
likely cause is that the model probes the tool machinery when it cannot see
the schemas, but 11 runs do not prove that. The plan's live check counts
`Bash` calls as well. An issue is opened only if the no-op still appears once
this change is in.

## Design

One key on our server entry, plus a comment recording what it was verified on.

`src/omarchy_voice/claude_backend.py`, `ClaudeBrain._options()`
(`:415`), the `servers` dict (`:433-435` on `main`):

```python
servers = {"omarchy": {"type": "sdk", "name": "omarchy",
                       # Show the model our schemas up front. Without this,
                       # Claude Code defers every MCP tool and the first use
                       # of each costs a ToolSearch round trip (#84).
                       # Verified on CLI 2.1.281; not in the SDK's typed
                       # McpSdkServerConfig, but the SDK passes every key
                       # except `instance` to the CLI.
                       "alwaysLoad": True,
                       "instance": mcp_server.build_server(...)}}
```

Why this works, end to end:

1. The SDK copies every key of an `sdk` server entry except `instance` into
   `--mcp-config` (`claude_agent_sdk/_internal/transport/subprocess_cli.py:657-667`,
   SDK 0.2.152).
2. The CLI treats a tool whose server config has `alwaysLoad: true` as never
   deferred. This is documented at https://code.claude.com/docs/en/mcp and was
   measured in variant A.
3. `WarmBrain` (`:594`) and `local_engine`'s `LocalBrain` build on
   `super()._options()`, as the comment at `:446-448` says (`:595`, `local_engine.py:114`), so every Claude
   brain gets it.

Not touched:

- `_child_env()` (`:465-488`): no `ENABLE_TOOL_SEARCH`. The subscription
  handling stays as it is.
- The `ai-mirror` entry (`:439`): it stays deferred.
- `mcp_server._to_mcp_tools()` (`mcp_server.py:93-107`): external clients see
  no change.
- The policy. `hooks={"PreToolUse": [HookMatcher(matcher=None, ...)]}` and
  `can_use_tool=self._alarm` (`:449-453`) are unchanged. `alwaysLoad` only
  changes when the model sees a schema, not which calls reach the hook. Every
  call to `mcp__omarchy__*` still goes through `_pre_tool_use`, and in
  variants A and C every such call was still gated as a dry run.
- `DRY_RUN_READS` keeps `ToolSearch` (`:101`). Nothing calls it for our tools
  any more, but it is still used for `ai-mirror` and for deferred built-ins.

### Overlap with open PRs

- **#78 `fix/69-snapshot-per-turn`** edits `claude_backend.py`: the `_options`
  prompt lines, `_ask`/`_turn`, and a new `_with_desktop`. On that branch the
  `servers` dict is at the same `:433`, and `_child_env` moves to `:466`. The
  plan rebases onto #78 if it has merged first. The only overlap is the
  context lines around `servers`, which is a trivial conflict.
- **#85 `feat/70-find-what-is-installed`** adds `find_app` to `tools_for`.
  Neither `claude_backend.py` nor `mcp_server.py` changes on that branch, so
  there is no conflict. Its tool is loaded up front automatically. This also
  fixes the intent's worst row, where the model guessed `find_app` before it
  existed.
- #81 `feat/72-listen-faster` touches neither file.

## Alternatives rejected

- **B. `ENABLE_TOOL_SEARCH=false` in `_child_env`.** It works, but the first
  request's prompt reached 181 K tokens ($1.47 cold, 12.4 s warm), because it
  also loads every deferred tool from every MCP server in the user's Claude
  Code setup. Its size depends on things outside this repo.
- **`ENABLE_TOOL_SEARCH=auto:N`.** It decides by the total size of all
  deferrable tools, so on this machine it would behave like the default or
  like B, depending on N and whatever else is installed. It is not scoped to
  our tools. Not measured, given B.
- **C. `_meta["anthropic/alwaysLoad"]` per tool in `_to_mcp_tools`.** It
  measured as well as A, but it adds 6.8 K tokens to every external
  `omarchy-voice mcp` client (see Q2). It stays the fallback if a future CLI
  stops honouring the per-server key but still honours the per-tool one.
- **A subset of tools.** This brings back search-on-guess (Q2), and saves
  little.
- **Warming each tool inside `WarmBrain`'s warm-up.** It covers only voice,
  and only until the next session rebuild. It also spends model turns to save
  model turns.

## Risks

- **A CLI upgrade drops or renames `alwaysLoad`.** Nothing breaks. Requests
  just go back to paying the `ToolSearch` turn. The unit test cannot catch
  this, because it only checks what we send. The live check below does catch
  it, and the code comment names the CLI version the key was verified on. All
  hosts running the voice daemon are affected alike (p620, razer).
- **An SDK upgrade stops passing unknown keys through** (for example, if it
  starts validating against the typed `McpSdkServerConfig`). The SDK
  pass-through test below fails when that happens.
- **Requests that need no tool pay about 6.8 K more prompt tokens.** On the
  subscription this counts against usage limits, not money. Q3 bounds it.
- **Tool choice changes because the model now sees every schema.** One A run
  used `Read` where `read_screen` fit better (footnote ² above). This is n=1
  and does not show up in C, but the live check lists tools called, so a
  pattern would be visible.
- **The policy is unaffected**, as argued in Design. The hook tests in
  `tests/test_claude_backend.py` (`HookTests`) run unchanged and must pass.

## Verification

1. **Unit test on the options built**, in `tests/test_claude_backend.py`,
   using the existing `options_of()` helper (`:271`):
   - `options.mcp_servers["omarchy"]["alwaysLoad"] is True`;
   - with `desktop_control` on and an `ai-mirror` path, the `ai-mirror` entry
     has no `alwaysLoad`;
   - `"ENABLE_TOOL_SEARCH" not in options.env` (scope stays per server);
   - `_child_env()` returns exactly what it returns today, and the existing
     tests at `:533-547` pass unchanged.
2. **SDK pass-through test.** Build the real SDK transport's command line
   (`SubprocessCLITransport` with `ClaudeAgentOptions(mcp_servers=...,
   cli_path="/bin/true")`) and assert that the `--mcp-config` JSON carries
   `"alwaysLoad": true` for `omarchy` and has no `instance`. This is the test
   that fails if the SDK changes the behaviour we rely on. If the transport
   cannot be built offline, the plan says so and falls back to checking
   `subprocess_cli.py`'s behaviour by reading it.
3. `nix flake check --no-write-lock-file` passes. It runs the whole pytest
   suite.
4. **Live check** (dry run only, read-only desktop): the same three requests
   as the table, `nix run .#omarchy-voice -- -n say --no-confirm "<request>"`.
   Pass means `ToolSearch = 0` for `omarchy` tools and 2 model requests each
   in the session log, a prompt of about 58 K rather than 181 K, and a turn
   time recorded next to the baseline above. `Bash` no-op calls are counted
   for Q4. The CLI version is recorded with the result. `trace_timings` was
   not used for these numbers, because turning it on means editing the
   user's config. The `reply` line measures the same turn span.

**Could not verify:** the voice path (`WarmBrain` over a session with several
requests). This is reasoned from the shared `_options`, not measured. Also
not verified: requests that need no tool under the change, whether
`alwaysLoad` survives CLI versions after 2.1.281, and whether tool choice
(footnote ²) shifts across more than one run.
