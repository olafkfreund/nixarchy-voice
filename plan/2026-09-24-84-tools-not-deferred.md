---
status: approved
issue: 84
spec: spec/2026-09-24-84-tools-not-deferred.md
---

# Plan: load our own tools up front, and only ours

## Approved decisions, carried over from the spec

1. **The measurement.** Taken on 2026-09-24 with Claude Code CLI 2.1.281,
   claude-agent-sdk 0.2.152 and model `claude-sonnet-5`. Every run was a dry
   run, `nix run .#omarchy-voice -- -n say --no-confirm "<request>"`. Each
   variant was a temporary patch, reverted afterwards.

   | Variant | Request | `ToolSearch` | Model requests | Time | Prompt (1st req) | Cost |
   |---|---|---|---|---|---|---|
   | baseline (`main`) | open zed | 1 | 4 | 14.9 s | 50,974 | $0.2152 |
   | baseline | switch to workspace 3 | 1 | 4 | 14.8 s | 50,975 | $0.2167 |
   | baseline | what is on my screen | 1 | 3 | 15.7 s | 50,944 | $0.2104 |
   | **A. `alwaysLoad` on the server entry** | open zed | 0 | 2 | 9.1 s | 57,735 | $0.2788 (cold cache) |
   | A | switch to workspace 3 | 0 | 2 | 7.4 s | 58,200 | $0.1859 |
   | A | what is on my screen | 0 | 2 | 10.2 s | 57,866 | $0.1561 (called `Read`, not `read_screen`) |
   | B. `ENABLE_TOOL_SEARCH=false` | open zed | 0 | 2 | 17.9 s | **180,939** | **$1.4734** (cold cache) |
   | B | switch to workspace 3 | 0 | 2 | 12.4 s | **180,944** | $0.2658 |
   | C. `_meta["anthropic/alwaysLoad"]` per tool | open zed | 0 | 2 | 7.3 s | 57,734 | $0.1851 |
   | C | switch to workspace 3 | 0 | 2 | 7.2 s | 58,099 | $0.1535 |
   | C | what is on my screen | 0 | 2 | 16.0 s (includes real OCR) | 58,192 | $0.1637 |

   All three mechanisms work for an `sdk` server on CLI 2.1.281. Our schemas
   cost about 6,800 prompt tokens (+13 %). Requests that need a tool still get
   cheaper and faster, because they take 2 model requests instead of 3–4. B
   also loads every deferred tool from every MCP server in the user's Claude
   Code setup.
2. **Scope: only our `omarchy` tools (Q1).** B is rejected. `ai-mirror` and
   Claude Code's own deferred built-ins stay deferred.
3. **Mechanism: A, `"alwaysLoad": True` on the `omarchy` server entry in
   `ClaudeBrain._options()` (Q2).** It only takes effect when we are the
   caller, so external `omarchy-voice mcp` clients keep Claude Code's
   default. C (per tool `_meta` in `mcp_server._to_mcp_tools`) is rejected
   because it would add 6.8 K tokens to every external client. C stays the
   fallback if a future CLI drops the per-server key but keeps the per-tool
   one.
4. **All tools, not a subset (Q2).** That is all 24 on `main`, plus #85's
   `find_app` with no further change. A subset brings back search-on-guess.
5. **The larger prompt is accepted (Q3).** It is about +6.8 K tokens per
   request. A cold cache pays roughly $0.03 once. Requests that need no tool
   pay the tokens without saving a round trip. They were not measured.
6. **The `Bash "true"` no-op gets no issue yet (Q4).** It appeared in 2 of 3
   baseline runs and 0 of 8 fixed runs. The live check counts it, and an
   issue is opened only if it still appears.
7. **Not touched:** `_child_env()` (no `ENABLE_TOOL_SEARCH`), the `ai-mirror`
   entry, `mcp_server._to_mcp_tools()`, the policy (the `PreToolUse` hook and
   the `can_use_tool` tripwire), and `DRY_RUN_READS`, which keeps `ToolSearch`
   because `ai-mirror` and the built-ins still use it.
8. **A code comment records why the key is there and what it was verified
   on.** The CLI version is 2.1.281. The key is not in the SDK's typed
   `McpSdkServerConfig`, but the SDK passes every key except `instance`.
9. **Risks:**
   - A CLI upgrade that drops or renames `alwaysLoad` breaks nothing. Requests
     go back to paying the `ToolSearch` turn. Only the live check catches
     this.
   - An SDK upgrade that stops passing unknown keys through (for example by
     validating against `McpSdkServerConfig`) is caught by the pass-through
     test.
   - Requests that need no tool pay about 6.8 K more tokens.
   - Tool choice may shift now that the model sees every schema (the one `Read`
     run, n=1).
   - The policy is not affected, and `HookTests` must pass unchanged.
   - Every voice host (p620, razer) is affected in the same way.
10. **Landing order.** Rebase onto #78 (`fix/69-snapshot-per-turn`) if it has
    merged. It edits the lines around the `servers` dict, which is a trivial
    conflict. #85 does not touch `claude_backend.py` or `mcp_server.py`, and
    its `find_app` gets loaded up front automatically. #81 touches neither
    file.

Resolved here because the spec left it open:

- **Where the `ai-mirror` assertion lives.** The spec names `options_of()`
  (`tests/test_claude_backend.py:271`). That helper forces `AI_MIRROR_ENV`
  to `""`, so it can never produce an `ai-mirror` entry. The omarchy
  assertion uses `options_of()`. The ai-mirror assertion uses
  `AiMirrorTests.options()` (`:416`), which already takes an ai-mirror path.
- **What "0 ToolSearch" means in the live check.** Pass means no `ToolSearch`
  call in any of the three runs. If one appears whose query names only
  `ai-mirror` or built-in tools, it is recorded with its query and does not
  fail the check (decision 2). One whose query names an `omarchy` tool fails
  the check.
- **"`_child_env()` returns exactly what it returns today"** is covered by
  the existing `SubscriptionNotApiKeyTests` (`:516-547`), which run
  unchanged. No new test duplicates them.

## Steps

Line numbers below are for `fix/69-snapshot-per-turn` (#78), which is what
`main` becomes once #78 merges. They were checked with
`git show fix/69-snapshot-per-turn:<file>`.

0. **Baseline.** `nix develop -c pytest tests -q` on this branch before the
   rebase → record the count. It is 792 passed (plus 414 subtests) on
   `perf/84-tools-not-deferred` at 114014f.

1. **Precondition: #78 has merged.** Run `gh pr view 78 --json state,mergeCommit`.
   - `MERGED`: run `git fetch origin && git rebase origin/main`. The branch
     holds only docs commits, so it rebases cleanly.
   - Anything else: **stop and report** that #84 waits on #78. Do not write
     the change against pre-#78 `main`.

   → verify by `grep -n "_with_desktop\|servers = {" src/omarchy_voice/claude_backend.py`.
   It should show `servers = {"omarchy": ...` at :433 and `_with_desktop` in
   `_ask`. Then run `nix develop -c pytest tests -q` again and record the new
   count as the baseline for step 5. That count is 792 plus #78's tests, and
   plus #85's if #85 merged first.

2. **`src/omarchy_voice/claude_backend.py`: the key.** In
   `ClaudeBrain._options()` (#78 :415), in the `servers` dict (#78 :433-435),
   add `"alwaysLoad": True` to the `omarchy` entry, between `"name"` and
   `"instance"`, with the spec's comment:

   ```python
   servers = {"omarchy": {"type": "sdk", "name": "omarchy",
                          # Show the model our schemas up front. Without this,
                          # Claude Code defers every MCP tool and the first use
                          # of each costs a ToolSearch round trip (#84).
                          # Verified on CLI 2.1.281; not in the SDK's typed
                          # McpSdkServerConfig, but the SDK passes every key
                          # except `instance` to the CLI.
                          "alwaysLoad": True,
                          "instance": mcp_server.build_server(self.config,
                                                              self.executor)}}
   ```

   Do not touch the `ai-mirror` entry (#78 :439-440), `_child_env()`
   (#78 :466-489), the hooks and `can_use_tool` (#78 :450-454), or
   `DRY_RUN_READS` (#78 :101). `WarmBrain._options()` (#78 :610-615) and
   `LocalBrain._options()` (`local_engine.py:113-116`) build on
   `super()._options()` and need no change.

   → verify with `git diff --stat`, which should show only this file with
   about 7 added lines, and then by steps 3 and 4.

3. **`tests/test_claude_backend.py`: what we send.**
   - Add `AlwaysLoadTests(unittest.TestCase)` after `AiMirrorTests`
     (#78 :413-451):
     - `test_our_tools_are_loaded_up_front`: build
       `options_of(ClaudeBrain(config, Executor(config)))` and assert that
       `options.mcp_servers["omarchy"]["alwaysLoad"] is True`.
     - `test_ai_mirror_stays_deferred`: build
       `AiMirrorTests().options("/bin/ai-mirror")` (desktop control on) and
       assert that `"alwaysLoad" not in options.mcp_servers["ai-mirror"]`,
       while the `omarchy` entry still has it.
     - `test_scope_is_per_server_not_global`: assert
       `"ENABLE_TOOL_SEARCH" not in options.env`, both with the default
       config and with `claude_use_subscription=False`.
     - `test_every_claude_brain_gets_it`: build
       `options_of(WarmBrain(config, Executor(config)))` and assert that the
       `omarchy` entry has `alwaysLoad`. This covers the voice path by
       construction, which the spec could not measure live.
   - `AiMirrorTests`' exact-equality assertion on the ai-mirror entry
     (#78 :431-432) stays as it is. It already proves that entry gets no
     extra key.

   → verify by `nix develop -c pytest tests/test_claude_backend.py -q`.
   It should pass.

4. **`tests/test_claude_backend.py`: the SDK passes the key through.** Add
   `SdkPassThroughTests(unittest.TestCase)`, which uses the **real** SDK.
   It is in both the dev shell and the flake check (`flake.nix:61`, `:121`).
   - Build `options = options_of(ClaudeBrain(...))`, keeping only its
     `mcp_servers`. `build_server` is mocked there, so `instance` is a
     `MagicMock`, and the SDK strips it anyway.
   - Build
     `SubprocessCLITransport(prompt="x", options=ClaudeAgentOptions(mcp_servers=options.mcp_servers, cli_path="/bin/true"))`
     and call `_build_command()`. Take the argument after `--mcp-config` and
     `json.loads` it.
   - Assert that `cfg["mcpServers"]["omarchy"]["alwaysLoad"] is True` and
     that `"instance" not in cfg["mcpServers"]["omarchy"]`.

   This was checked offline while writing the plan. On SDK 0.2.152,
   `_build_command()` with `cli_path="/bin/true"` spawns nothing and emits
   `{"mcpServers": {"omarchy": {"type": "sdk", "name": "omarchy", "alwaysLoad": true}, ...}}`.
   The code it relies on is `subprocess_cli.py:657-667`. The spec's
   read-the-source fallback is therefore not needed. The docstring names the
   SDK version, and says that if the test fails after an SDK bump, the
   fallback is decision 3's C.

   → verify:
   - `nix develop -c pytest tests/test_claude_backend.py -q` passes.
   - **Mutation check:** delete the `"alwaysLoad": True` line. Then
     `test_our_tools_are_loaded_up_front`, `test_every_claude_brain_gets_it`
     and the pass-through test must fail. Restore the line.

5. **Whole suite.** `nix develop -c pytest tests -q` → it should equal the
   step 1 count plus the 5 new tests, with 0 failures. `HookTests` (#78 :284)
   and `SubscriptionNotApiKeyTests` (#78 :516) must pass unchanged.

6. **Whole check.** `nix flake check --no-write-lock-file` → passes.

7. **Live check.** This is the spec's measurement repeated, as a dry run
   only, with no service restarts. It never touches whisper-server. Record
   `claude --version` first (2.1.281 when this plan was written). Then run
   these three commands, one each, which is at most 4 runs including one
   retry for a failure unrelated to this change:

   ```
   nix run .#omarchy-voice -- -n say --no-confirm "open zed"
   nix run .#omarchy-voice -- -n say --no-confirm "switch to workspace 3"
   nix run .#omarchy-voice -- -n say --no-confirm "what is on my screen"
   ```

   For each run, read the newest
   `~/.claude/projects/-home-olafkfreund/<session>.jsonl` and record:
   - the `ToolSearch` count, with its query;
   - the model requests (distinct assistant `message.id`s);
   - the tools called;
   - the `Bash` no-op calls;
   - the first request's prompt tokens (`input + cache_creation + cache_read`);
   - the turn time from `say`'s `reply` line.

   → pass means all of the following:
   - 0 `ToolSearch` (see "what 0 means" above);
   - 2 model requests for each run;
   - a prompt of about 58 K tokens, not about 181 K;
   - times recorded next to decision 1's baseline (14.8–15.7 s).

   Put the table in the PR description.

8. **Q4 follow-up and PR.**
   - If any run in step 7 shows `Bash {"command": "true"}`, open an issue for
     the no-op that cites this PR and the counts (decision 6). Otherwise do
     not open one.
   - Push, and open a PR that closes #84, links the intent, spec and plan,
     and includes step 7's table and CLI version.

## Tests

```
nix develop -c pytest tests -q                              # step 1 count + 5, 0 failures
nix develop -c pytest tests/test_claude_backend.py -q       # new + HookTests + SubscriptionNotApiKeyTests
nix flake check --no-write-lock-file                        # CI parity, real SDK
```

The mutation check in step 4 removes the key, expects 3 failures, and then
restores the key. Step 7 is the live dry run.

## Rollback

The change is one squash-merged PR: one dict key, a comment, and tests. It
has no Nix, config or persisted-state change. `git revert <merge>` restores
deferred loading, and each request pays the `ToolSearch` turn again. If a
future CLI ignores the key, nothing breaks and no rollback is needed. Step 7,
repeated, shows the `ToolSearch` coming back, and the fix then is decision 3's
fallback C in a new issue.
