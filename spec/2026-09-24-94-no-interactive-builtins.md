---
status: approved
issue: 94
intent: intent/2026-09-24-94-no-interactive-builtins.md
---

# Spec: offer the voice brain our tools and a named few built-ins, nothing else

## The intent's open questions, answered

The intent was approved without answers to its five questions, so each is
decided here with the reasoning shown. Any of them can be rejected at this
gate.

### The measurement

Run on 2026-09-24 on `fix/94-no-interactive-builtins` (= `main` 40c80fb plus
the intent). Claude Code CLI 2.1.281, claude-agent-sdk 0.2.152 (0.2.153 builds
the flags the same way, `subprocess_cli.py:585-594,693,722-723`), model
`claude-sonnet-5`. Ten dry runs, `nix run .#omarchy-voice -- -n say
--no-confirm "<request>"`. Each variant was a scratch patch to the worktree
copy of `claude_backend._options`, chosen by an environment variable. The
patch also wrote the CLI's `init` message, every `tool_use` block and the
`ResultMessage` usage to a scratch file. It was reverted afterwards with
`git checkout -- src/`, so nothing under `src/` is committed.

- **base**: `main` as it is.
- **iso**: `setting_sources=[]` and `strict_mcp_config=True`.
- **allow**: iso plus `tools=["Read", "ToolSearch"]`.

"Input" is `input + cache_creation + cache_read` summed over the turn's model
requests. "Time" is the turn time `say` prints. "CLAUDE.md" comes from a probe
request that asked the model, with no tools, whether its instructions contain
(a) the "Managed instructions" artifact workflow (only in
`/etc/claude-code/CLAUDE.md`) and (b) the "Expert Reasoning Protocol" (only in
`~/.claude/CLAUDE.md`).

| # | Variant | Request | Built-ins | MCP tools | Skills / agents / plugins | CLAUDE.md managed / user | Input | Time | What happened |
|---|---|---|---|---|---|---|---|---|---|
| 1 | base | CLAUDE.md probe | 32 | 141 from 8 servers | 217 / 45 / 26 | yes / yes | 58,359 | 5.1 s | `a=yes b=yes` |
| 2 | iso | CLAUDE.md probe | 31 | 27, `omarchy` only | 18 / 5 / 2 | **yes** / no | 38,504 | 4.6 s | `a=yes b=no` |
| 3 | allow | CLAUDE.md probe | 5 | 27, `omarchy` only | 18 / 5 / 2 | **yes** / no | 20,237 | 4.5 s | `a=yes b=no` |
| 9 | base | open zed | 32 | 146 | 217 / 45 / 26 | | 125,484 | 9.0 s | `launch_app zed` |
| 10 | iso | open zed | 31 | 27 | 18 / 5 / 2 | | 77,033 | 7.6 s | `launch_app zed` |
| 4 | allow | open zed | 5 | 27 | 18 / 5 / 2 | | 40,646 | 10.7 s | `launch_app zed` |
| 5 | allow | what's on my screen | 5 | 27 | 18 / 5 / 2 | | 20,116 | 8.3 s | answered from the per-turn snapshot (#69), correct, no tool |
| 6 | allow | the intent's run 3 browser picker | 5 | 27 | 18 / 5 / 2 | | 40,797 | 9.8 s | `find_app browser`, then spoken: "You've got Brave, Chromium, BrowserOS, and Firefox — which one?" |
| 8 | base | reboot the machine | 32 | 158 | 217 / 45 / 26 | | 126,143 | 10.6 s | **held `AskUserQuestion {… "Reboot now anyway?" …}`**: the #94 bug, reproduced |
| 7 | allow | reboot the machine | 5 | 27 | 18 / 5 / 2 | | 41,038 | 12.8 s | no picker; held `look up omarchy command 'reboot restart system power'` (see "Found on the way") |

What the table shows:

- **iso removes everything that is not ours on the MCP side.** Base connected
  88 servers (`init.mcp_servers`), including `claude.ai Gmail`, `claude.ai
  Google Drive`, `cfactory`, `mcp-server-git`, `audiobook`, the Backstage
  scaffolder, `ai-mirror` (pending, with `desktop_control` off) and 58 plugin
  servers. Under iso it was `[("omarchy", "connected")]`. The intent's
  unverified question is answered: `--strict-mcp-config` together with
  `--setting-sources=` does stop the claude.ai connectors and the plugin
  servers.
- **iso removes the user's `CLAUDE.md`, but not the managed one.** Answer (b)
  went from yes to no, and answer (a) stayed yes. The 18 skills, 5 agents and 2
  plugins that remain are the CLI's own (`agents-md@builtin`,
  `telemetry@builtin`; skills such as `loop`, `schedule`, `update-config`).
  The model can only reach them through `Skill` or `Task`, and allow removes
  both.
- **allow leaves 5 built-ins, not 2.** `--tools Read,ToolSearch` gave `Read`,
  `ToolSearch`, `ListMcpResourcesTool`, `ReadMcpResourceTool` and
  `ReadMcpResourceDirTool`. The CLI adds the three MCP-resource readers
  whenever an MCP server is configured, and `--tools` does not remove them.
  They are kept on purpose (see Q1).
- **Input is about a third of base on the same request** (40.6 K against
  125.5 K for "open zed"), because the other servers' tool schemas and 217
  skill descriptions are gone. That more than offsets the ~19 KB that #84 added.
- **Time does not improve measurably.** There was one run per cell, and output
  length varied (85 to 384 tokens for the same request). No latency claim is
  made.
- **The three representative requests still work under allow.** Zed was
  launched through our tool. The screen was described correctly. The browser
  request made the model ask out loud, after `find_app`, and it named the same
  four browsers as the intent's run 3.

**Not verified:**
- whether hooks in the user's `~/.claude/settings.json` ran inside the brain
  under base and stop under iso. `setting_sources=[]` should drop them, but
  that was not observed;
- whether auto-memory still loads under iso. `init.memory_paths.auto` is
  still reported (`~/.claude/projects/-home-olafkfreund/memory/`). The
  directory is empty today, and without `Write` the brain cannot fill it;
- a live `desktop_control = true` run under strict MCP. The unit test in
  Verification shows that `ai-mirror` is still passed in `--mcp-config`, and
  `--strict-mcp-config` keeps every server passed there;
- the managed-CLAUDE.md result rests on the model's own report, not on a
  prompt dump. Two probes agreed, and base run 2 of the intent showed the same
  instructions being followed unprompted.

### Q1. Denylist or allowlist? Allowlist.

This is `tools=["Read", "ToolSearch"]`, plus `strict_mcp_config=True` for
the MCP side. It is **not** `allowed_tools`. The lead suggested an allowlist
named `allowed_tools`, but the evidence rules that option out: in the SDK,
`allowed_tools` auto-approves tools without asking and shadows
`can_use_tool` (`types.py:1858,1955`). The intent's constraints forbid it.
`tools` becomes `--tools a,b` (`subprocess_cli.py:582-591`) and sets which
built-ins exist. Run 3 shows it works.

Why an allowlist: a denylist of three names leaves the other 29 built-ins on
offer, including `Task`, `Cron*`, `RemoteTrigger`, `Workflow`,
`SendMessage`, `EnterWorktree`, `Write`, `Edit` and `WebFetch`, and anything
Claude Code ships next month. The code already reasons this way for dry-run
reads (`claude_backend.py:78-80`).

Each candidate built-in, decided from evidence (persona, tool descriptions,
the 2,996-line `~/.local/state/omarchy-voice/session.log`, and the code that
depends on it):

| Built-in | Keep? | Evidence |
|---|---|---|
| `Read` | **keep** | `omarchy-voice verify-gate` cases A, B and D ask for "your Read tool" (`verify_gate.py:148-171`). They are the live proof that the hook catches a call the CLI approves by itself. `Read` is read-only, is in `DRY_RUN_READS`, and path deny rules still see it (`describe_tool`, `claude_backend.py:202-204`). The persona does not name it, and the session log has no built-in `Read` call. |
| `ToolSearch` | **keep** | With `desktop_control` on, ai-mirror's tools stay deferred on purpose (`AlwaysLoadTests.test_ai_mirror_stays_deferred`, `tests/test_claude_backend.py:604-608`), and a deferred tool can only be loaded through `ToolSearch`. It only loads schemas and is in `DRY_RUN_READS`. |
| MCP-resource readers (3) | **keep** (the CLI adds them anyway) | Our server publishes resources, `omarchy://manifest` among them. Its description says "Read it before driving the desktop" (`mcp_server.py:182-199`). Under strict MCP they reach only our server and ai-mirror. The hook still gates them. |
| `Bash` | **drop** | Our `run_shell` does this job and honours `allow_shell` (default false, `config.py:396`). The built-in `Bash` skipped that setting. `doctor` and the README warn about the gap (`cli.py:245-270`, `README.md:256-263`). The persona says never to invent a shell command around a refused tool (`persona.py:129-130`). The session log has one built-in `Bash` call (2026-09-13 20:11, `ls -la …/test && …writable check`), made while the model tried to "start a new cloud session", which is a coding request and not a desktop one. `run_in_terminal` still runs commands where the user can watch them. |
| `WebSearch` | drop | The persona says "Searching is web_search, always" (`persona.py:106`), which is our tool. No built-in `WebSearch` call appears in the log. |
| `WebFetch` | drop | It is left out of the dry-run reads because a GET to a URL the model picked can act or leak data (`claude_backend.py:90-95`). There is no use in the log. |
| `Write`, `Edit` | drop | No voice feature writes files, and the log shows no use. |
| `AskUserQuestion`, `EnterPlanMode`, `ExitPlanMode` | drop | The bug. `AskUserQuestion` also appears live in the log (2026-09-13 20:10:58), and run 8 reproduced it. |
| everything else (`Task`, `Cron*`, `Workflow`, `SendMessage`, worktrees, `Skill`, `LSP`, …) | drop | These are coding-session and orchestration tools, with no voice use. |

Dropping `Bash` is the one decision here that changes documented behaviour.
The approver can reject it: the fallback is `("Bash", "Read", "ToolSearch")`,
which keeps the README paragraph and the `doctor` caveat as they are.

### Q2. Is the rest of the user's setup in scope? Yes.

It is a security issue, and the fix is two options in the same function. Base
offered the brain `claude_ai_Gmail` (30 tools, including `send_message` and
`forward`), Drive (`share_file`), `cfactory` (40) and `mcp-server-git` (12),
plus 217 skills and the user's `CLAUDE.md`. Our regex policy was written for
shell commands and omarchy routes, not for email. Runs 2 and 3 show that the
two options remove all of it. What they cannot remove is the **managed**
`/etc/claude-code/CLAUDE.md`, which still loads under `setting_sources=[]`.
That is the machine owner's policy, and it is enforced by design. Our system
prompt still decides the behaviour, and the three request runs acted on it.
This is accepted and written down, not worked around.

### Q3. Should `WebFetch`, `Write` and `Edit` stay? No.

This follows from Q1. Of the built-ins, the brain gets `Read`, `ToolSearch`
and the resource readers the CLI adds.

### Q4. Should the system prompt also say "ask out loud"? No.

With the tool gone, run 6 asked out loud with no extra prompt, and it did so
after checking with `find_app`. An extra rule would guard against nothing we
have seen. Add one if a dry run ever shows the model trying to ask any other
way.

### Q5. The browsers named without `find_app`: in scope? In scope for verification only.

Under allow, run 6 called `find_app` and named Brave, Chromium, BrowserOS and
Firefox, the same set as the intent's run 3. The intent's run 4 was a single
sample under a narrower patch. The live check in Verification repeats the
request and requires a `find_app` call. No code change is planned for it.

### Found on the way (not fixed here)

- **Run 7 held a help lookup, not the reboot.** Our own read-only
  `omarchy_help` describes itself as `look up omarchy command '<query>'`
  (`tools.py:1840-1841`). A query containing "reboot" matches `\breboot\b`, so
  the gate asked the user to confirm a lookup. This is the same class of bug
  as #94, but in our tool. So the intent's third outcome ("the prompt names the
  real action") is only met for the built-ins by this change. Recommended: a
  separate issue, for example to skip the confirm check for `READ_ONLY_TOOLS`,
  which it does not cover here.
- **The real session log holds test output.** Lines 2907-2939 of
  `session.log` (2026-09-24 12:05:01: "commit my notes and reboot", repeated)
  look like a test fixture writing to the user's real log. This is worth an
  issue of its own.

## Design

All in `ClaudeBrain._options()`, so `WarmBrain` (`claude_backend.py:706-711`)
and `LocalBrain` (`local_engine.py:112-116`) inherit it through `super()`, as
they already inherit the hook and `alwaysLoad`.

1. **`src/omarchy_voice/claude_backend.py`, beside `DRY_RUN_READS` (`:101`):**
   add `BUILTIN_TOOLS = ("Read", "ToolSearch")`, with a comment that records
   the reasons from Q1 in short, the CLI version it was checked on (2.1.281),
   and the fact that the CLI adds the three MCP-resource readers on its own.
   On `ClaudeBrain` (`:246`), add a class attribute
   `builtin_tools = BUILTIN_TOOLS`, so `verify-gate` can widen it on one
   instance, the same way it already replaces `_decide` on one instance
   (`verify_gate.py:194-203`).
2. **`ClaudeAgentOptions(...)` (`:528-549`):** add three arguments:
   - `tools=list(self.builtin_tools)`: an allowlist of built-ins. Comment that
     this is `tools`, never `allowed_tools`, and why;
   - `setting_sources=[]`: none of the user's settings, plugins, skills or
     `CLAUDE.md`. The managed `CLAUDE.md` still loads (measured);
   - `strict_mcp_config=True`: only the servers in `mcp_servers`, which are
     `omarchy` and `ai-mirror` when `desktop_control` is on (`:524-526`).

   Nothing else changes. The hook (`:535-536`, `matcher=None`),
   `can_use_tool=self._alarm`, `permission_mode="default"`, `alwaysLoad`
   (`:518`) and `_child_env` all stay as they are.
3. **`src/omarchy_voice/verify_gate.py`:** case C asks for `EnterWorktree`
   (`:160-164`), which is no longer offered, so the case would stay
   inconclusive and `verify-gate` would always exit 2. Give `_Case` an
   `extra_tools: tuple[str, ...] = ()` field. Set it to `("EnterWorktree",)`
   for case C. In `_run_case`, set
   `brain.builtin_tools = (*claude_backend.BUILTIN_TOOLS, *case.extra_tools)`.
   What case C proves is that the hook refuses, under a dry run, a changing
   tool that the CLI approves by itself. That is still worth proving, because
   it guards any later addition to the list. Cases A, B and D need no change,
   because `Read` stays.
4. **`src/omarchy_voice/cli.py` `shell_status` (`:245-270`) and
   `README.md:256-263`:** the claude-code backend no longer hands the model
   `Bash`, so `allow_shell` is again the whole answer. Remove the three-line
   caveat and its docstring paragraph, and rewrite the README bullet: the
   model gets our tools plus `Read` and `ToolSearch`, none of the user's MCP
   servers or settings, and every call still goes through the hook. Update
   `tests/test_backend_choice.py:348-359` to match. (If the approver keeps
   `Bash`, this step is dropped.)

`DRY_RUN_READS` is unchanged. Its `WebSearch` entry becomes unreachable, and
it does no harm. The persona and the system prompt do not change (Q4).

## Alternatives rejected

- **`disallowed_tools=["AskUserQuestion", "EnterPlanMode", "ExitPlanMode"]`**:
  the intent's run 4 proved it works, but it leaves 29 built-ins and every
  future one on offer (Q1).
- **`allowed_tools=[…]`**: this auto-approves tools and shadows `can_use_tool`
  (`types.py:1858`). It widens access instead of narrowing it, and the intent
  forbids it.
- **`tools=[]`, with no built-ins at all**: this breaks `verify-gate` A, B and
  D, and it removes `ToolSearch`, which ai-mirror's deferred tools need.
- **`alwaysLoad` on ai-mirror, so that `ToolSearch` could go too**: #84 decided
  to keep other servers deferred. Revisiting that is out of scope, and
  `ToolSearch` is harmless.
- **Removing the managed `CLAUDE.md`**: it is the machine owner's enforced
  policy. No option we should use turns it off.
- **A persona line telling the model to ask out loud**: not needed, per run 6
  (Q4).
- **A second ruleset for MCP tools from other servers**: with strict MCP they
  are never offered, so there is nothing to gate.

## Risks

- **The hook must still answer every call.** The change only removes tools.
  `hooks`, `matcher=None`, `can_use_tool=_alarm` and `permission_mode` are not
  touched, and the existing `HookTests` keep asserting them. The CLI still
  offers three tools we did not name (the resource readers). That is the
  reason the hook stays unfiltered, and the reason the allowlist is a second
  layer.
- **#84's `alwaysLoad` keeps working.** Runs 3-7 all showed the 27 `omarchy`
  tools in `init.tools` with no `ToolSearch` call before first use. The
  `AlwaysLoadTests` and `SdkPassThroughTests` stay green, and the new
  pass-through test builds the same command line.
- **Realtime/OpenAI engine: unaffected.** It does not use `ClaudeBrain`
  (`README.md:246-249`). The HTTP `Planner` and external `omarchy-voice mcp`
  clients do not build `ClaudeAgentOptions` either.
- **Dropping `Bash` removes a capability some requests used** (one call in the
  log). A request that needs a shell now gets `run_shell` (refused while
  `allow_shell` is false) or `run_in_terminal`. That is what `allow_shell`
  always claimed to mean. It is reversible by adding one name.
- **User `permissions` rules in `~/.claude/settings.json` stop applying to the
  brain.** Our hook is the policy, and it has never relied on them. But anyone
  who added a deny rule there for this backend loses it. The README bullet
  says so.
- **A CLI upgrade that renames `Read` or `ToolSearch`** would silently drop the
  tool, because `--tools` did not reject names in these runs. `verify-gate`
  A/B/D would then go inconclusive and exit 2. That is the existing
  after-upgrade check (`README.md:264-269`).
- **The `setting_sources=[]` behaviour depends on the SDK.** It relies on the
  SDK emitting `--setting-sources=` for an empty list
  (`subprocess_cli.py:536-540,719-720`) instead of treating it as unset. The
  pass-through test below fails if a later SDK changes that.
- **Hosts:** this machine only (p620, where the daemon runs). The change is
  the same on any host that runs the claude-code backend.

## Verification

1. **Unit, options** (`tests/test_claude_backend.py`, using `options_of`,
   `:407`): for `ClaudeBrain`, `WarmBrain` and `LocalBrain`, `options.tools ==
   ["Read", "ToolSearch"]`, `options.setting_sources == []`,
   `options.strict_mcp_config is True`, `"AskUserQuestion" not in
   options.tools`, and `allowed_tools` is not passed. With `desktop_control`
   on (`AiMirrorTests`), `ai-mirror` is still in `mcp_servers`.
2. **Unit, the real SDK command line** (the #84 `SdkPassThroughTests`
   pattern, `:626-646`): build a real `ClaudeAgentOptions` from `options_of`'s
   `tools`, `setting_sources`, `strict_mcp_config` and `mcp_servers`, and call
   `SubprocessCLITransport._build_command()`. Assert that the command has
   `--tools` followed by `Read,ToolSearch`, `--setting-sources=` (the empty
   value), and `--strict-mcp-config`. This fails if the options are dropped or
   a later SDK renames them.
3. **Unit, verify-gate:** case C carries `extra_tools == ("EnterWorktree",)`,
   and `_run_case` widens only that instance (patch `ClaudeBrain.think`,
   assert the built options' `tools`).
4. **`tests/test_backend_choice.py`:** `shell_status` has no Bash caveat.
5. **Full suite plus `nix flake check --no-write-lock-file`:** 912 passed on
   `main` before this change, and all must pass after, plus the new tests.
6. **Live, dry run only** (`nix run .#omarchy-voice -- -n say --no-confirm`),
   with the `init` capture from the measurement above, used as scratch and
   not committed:
   - `init.tools` built-ins are exactly `Read`, `ToolSearch` and the three
     MCP-resource readers, and the MCP tools are the 27 `mcp__omarchy__*`;
     `init.mcp_servers == [omarchy]`;
   - "open zed" calls `launch_app`;
   - "what's on my screen" answers correctly;
   - the run 3 browser request calls `find_app`, makes no `AskUserQuestion`
     call, and asks out loud;
   - "reboot the machine" holds no `AskUserQuestion`.

   Record the CLI version beside the results.
7. **After merge, by the user:** `omarchy-voice verify-gate` must print
   `The gate holds`. It spends four model turns and runs real (non-dry) reads
   on temporary files, which is why it is not in this spec's measurement.
