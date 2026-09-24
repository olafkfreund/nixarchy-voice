---
status: draft
issue: 94
author: olafkfreund
---

# Intent: the voice brain should not be offered tools that need a terminal

Closes #94.

## Problem

The Claude Code brain is offered every built-in tool Claude Code has,
including tools that only work with a person at a terminal. When it wants to
ask something, it can call `AskUserQuestion`, which draws a picker in a
terminal UI. A voice session or a one-shot `say` has no terminal UI, so
nobody can answer it. The model should ask in its spoken reply instead.

It also breaks the confirm prompt. In #76's dry run (Step 8,
`plan/2026-09-24-76-confirm-replays-once.md:366-369`) the gate held
`AskUserQuestion {... "Reboot the machine now?" ...}` instead of `reboot`.
The picker's JSON contains the word "reboot", so it matches the confirm
pattern `\breboot\b` (`src/omarchy_voice/config.py:110`). The user is then
asked to confirm a question, not the action.

### Where it comes from in our code

- `ClaudeBrain._options()` (`src/omarchy_voice/claude_backend.py:493-549`)
  builds `ClaudeAgentOptions` (`:528-549`) with no `tools`,
  `allowed_tools`, `disallowed_tools`, `setting_sources` or
  `strict_mcp_config`. So the CLI offers its whole default toolset. It also
  loads the user's own Claude Code settings, plugins and MCP servers (see
  below).
- `WarmBrain` (`:668`, `super()._options()` at `:696`) and `LocalBrain`
  (`src/omarchy_voice/local_engine.py:112-114`) build on the same options.
  Typed `say`, the voice daemon and the local engine all get the same tools.
- A call to a tool we do not know is described as `"<tool> <json>"`, cut to
  400 characters (`describe_tool`, `claude_backend.py:208-215`). That string
  goes through `policy.check` (`:380-397`). So any built-in whose JSON
  carries a confirm word is held under its own name.

### What the session is offered (measured)

Measured on 2026-09-24 against `main` (fc52245): Claude Code CLI 2.1.281,
claude-agent-sdk 0.2.152, model `claude-sonnet-5`. There were four dry runs
of `omarchy-voice -n say --no-confirm "<request>"`, run from the dev shell
through a scratch wrapper. The wrapper records the CLI's `init` system
message and every `tool_use` block. It does not change the options, except in
run 4.

**Built-ins: 32, the same in every run:**

| Class | Tools |
|---|---|
| Needs a person at an interactive terminal | `AskUserQuestion`, `EnterPlanMode`, `ExitPlanMode` (a plan approval prompt) |
| Useful headless, already gated by the hook | `Bash`, `Read`, `Write`, `Edit`, `WebSearch`, `WebFetch`, `ToolSearch` (still needed for deferred tools from other servers) |
| Coding-session or agent-orchestration tools with no use in a voice turn. Risky because they act outside the turn, start other agents or change the filesystem | `Task` (subagents), `SendMessage`, `ListAgents`, `TaskStop`, `Workflow`, `CronCreate`, `CronDelete`, `CronList`, `ScheduleWakeup`, `RemoteTrigger`, `Monitor`, `PushNotification`, `EnterWorktree`, `ExitWorktree` (a dry run once created a real worktree, `claude_backend.py:448-450`), `NotebookEdit`, `LSP`, `DesignSync`, `ReportFindings`, `Skill` |
| Read-only, but they read the user's other MCP servers | `ListMcpResourcesTool`, `ReadMcpResourceTool`, `ReadMcpResourceDirTool` |

`AskUserQuestion` is offered up front, not deferred. In run 3 the model
called it directly, without a `ToolSearch` first.

**MCP tools: 173, 206 and 178 in runs 1-3.** Only 27 of them are ours
(`omarchy`). The count changes from run to run because the CLI also connects
the user's own MCP servers from `~/.claude.json`, the claude.ai connectors
and 26 plugins, and a different number are ready when `init` is sent. Tools
seen in the `init` list include `claude_ai_Gmail` (30, including
`send_message` and `forward`), `claude_ai_Google_Drive` (11, including
`share_file`), `claude_ai_Google_Calendar`, `claude_ai_Spotify`, `cfactory`
(40), `mcp-server-git` (12), `audiobook` (10) and the Backstage scaffolder
(5). The `init` message also lists 217 skills, 301 slash commands and 45
agent types. Every one of these calls still goes through our PreToolUse hook,
but the regex policy was written for shell commands and omarchy routes, not
for email.

`ai-mirror` appears in `init.mcp_servers` (status `pending`) even though
`desktop_control` defaults to false (`config.py:411`) and `_options` adds it
only when that setting is on (`claude_backend.py:524-526`). It comes from the
user-scope entry in `~/.claude.json`. Its tools were not in the `tools` list
in any of the four runs, so it was not shown to be callable. See the open
questions.

The user's instructions reach the brain as well. Run 2's reply began: "This
is a system reboot request — a single destructive action, not a multi-file
code task, so the intent/spec/plan artifact workflow doesn't apply here."
Run 4's began: "This is a quick desktop action, not a coding/artifact task —
no plan/spec needed." That workflow comes from the machine's managed
`/etc/claude-code/CLAUDE.md` and the user's `CLAUDE.md`, not from our prompt. Our system prompt
replaces Claude Code's own, but those memory files are still loaded.

### Reproduced

Run 3, request "open a new browser, but first ask me which of my browsers I
want and give me the options to pick from":

```
TOOL_USE mcp__omarchy__find_app {"query": "browser"}
TOOL_USE AskUserQuestion {"questions": [{"question": "Which browser should I open?",
  "header": "Browser", "options": [{"label": "Brave", ...}, {"label": "Chromium", ...},
  {"label": "Firefox", ...}, {"label": "BrowserOS", ...}], "multiSelect": false}]}
reply   I'd ask you to pick between Brave, Chromium, Firefox, or BrowserOS to open.
```

The dry run refused it: `AskUserQuestion` is not in `DRY_RUN_READS`
(`claude_backend.py:101`, `_dry_run_refusal` at `:410-430`). Without a dry
run the hook would allow it, because its JSON matches no policy pattern.
**Not verified:** what the CLI does with an allowed `AskUserQuestion` under
the SDK. It may wait on stdin, fail, or route it to `can_use_tool` (our
`_alarm`, `:477-490`). Finding out would take a non-dry run, which this
intent does not do.

Run 4 used the same request, with `disallowed_tools=["AskUserQuestion",
"EnterPlanMode", "ExitPlanMode"]` patched onto the options (scratch code,
not committed). The three tools were missing from `init.tools`, and the
model asked in speech: "Which one do you want — Chromium, or Google Chrome?"
It answered without calling `find_app`, though, and named browsers that do
not match run 3's list. That is a separate quality issue, but a spec should
check for it.

Run 1 ("delete some of the old screenshots in my Pictures folder") and run 2
("reboot the machine") did not call `AskUserQuestion`. They asked in speech.
The behaviour depends on the request.

### What the SDK offers

From the installed SDK source
(`claude_agent_sdk/types.py`, `_internal/transport/subprocess_cli.py`, 0.2.152)
and `claude --help` (2.1.281):

- `disallowed_tools: list[str]` (`types.py:2028`) becomes `--disallowedTools`
  (`subprocess_cli.py:606-607`). The docstring says these tools "are removed
  from the model's context and cannot be used". Run 4 confirms that for the
  three built-ins.
- `tools: list[str] | ToolsPreset` (`types.py:1944`) becomes `--tools a,b,c`
  (`subprocess_cli.py:582-591`). It sets the *base set* of built-ins, as an
  allowlist. `[]` removes all of them.
- `allowed_tools` (`types.py:1955`) is **not** a restriction. It auto-approves
  tools without prompting, and the SDK warns that it shadows `can_use_tool`
  (`types.py:1858`). It is the wrong option for this.
- `setting_sources` (`types.py:2218`): when it is unset, the SDK passes no
  `--setting-sources` flag (`subprocess_cli.py:536-544`, `:719-720`). Runs
  1-4 show that the CLI then loads the user's settings, plugins and skills.
- `strict_mcp_config` (`types.py:1984`) becomes `--strict-mcp-config`: "only
  use MCP servers passed via `mcp_servers`".

**Not verified:** whether `--setting-sources` with an empty list, or
`--strict-mcp-config`, also stops the claude.ai connectors and plugin MCP
servers. Also whether either option stops the managed `CLAUDE.md` from
loading (the CLI help for `--restricted` says managed settings "still
apply"). And whether a subagent started by `Task` goes through our
PreToolUse hook. None of these was tested.

### How it interacts with #84 and the hook

- `alwaysLoad: True` on the `omarchy` server entry (`claude_backend.py:518`)
  controls *when* our schemas are shown. Restricting built-ins controls
  *which* tools exist at all. The two are independent. Removing unused
  built-ins and other servers' tools also makes the prompt smaller, which
  offsets the ~19 KB #84 added.
- `ToolSearch` must stay while any server's tools are deferred. It is also in
  `DRY_RUN_READS`.
- The PreToolUse hook (`:535-536`, `matcher=None`) must still answer every
  call that is made, and `_alarm` stays as the tripwire. A restriction is a
  second layer, not a replacement: a tool Claude Code adds in a later version
  would still be offered under a denylist, and the hook is what catches it.
  The code already argues for an allowlist for the same reason
  (`claude_backend.py:78-80`).
- `describe_tool`'s JSON fallback (`:208-215`) will still hold any remaining
  tool whose input contains a confirm word under that tool's own name. Taking
  away the tools that carry *questions* removes the main case, but not every
  case.

## Proposed outcome

- The voice brain is never offered a tool that needs an interactive terminal.
  `init.tools` from a dry run does not contain `AskUserQuestion`,
  `EnterPlanMode` or `ExitPlanMode`.
- When the model needs a choice, it asks in the spoken reply. A dry run of the
  run 3 request shows no `AskUserQuestion` call and a spoken question.
- When a gated action is held, the prompt names the real action (`reboot`),
  not a picker.
- A unit test fails if the options stop carrying the restriction, on
  `ClaudeBrain`, `WarmBrain` and `LocalBrain`.
- The chosen tool set is recorded with the CLI version it was checked on.

## Affected users and systems

- `src/omarchy_voice/claude_backend.py` (`_options`, possibly
  `DRY_RUN_READS` and its comment), and `tests/test_claude_backend.py`.
- All Claude Code entry points: typed `say` (`ClaudeBrain`), the voice daemon
  (`WarmBrain`) and the local engine (`LocalBrain`).
- The user's other Claude Code setup (MCP servers, plugins, skills,
  `CLAUDE.md`), if the scope includes the setting sources.
- Not affected: the OpenAI/realtime and local-model backends, and external
  clients of `omarchy-voice mcp`.

## Constraints

- The PreToolUse hook remains the policy. It must still answer every call
  explicitly, and `_alarm` stays. A tool restriction may only take tools away.
- Do not use `allowed_tools` for this: it auto-approves tools and shadows
  `can_use_tool`.
- Do not use `permission_mode="bypassPermissions"` (`claude_backend.py:540-543`).
- `alwaysLoad` (#84) and the `_child_env` subscription handling stay as they
  are.
- Verify with dry runs only (`-n --no-confirm`). The live desktop is not
  driven, and whisper-server is not touched.
- A test must fail if the restriction is dropped or a later SDK renames the
  option.

## Open questions

1. Denylist or allowlist? `disallowed_tools=[AskUserQuestion, EnterPlanMode,
   ExitPlanMode]` is the narrow fix that the issue suggests and that run 4
   proved works. `tools=[Bash, Read, Write, Edit, WebSearch, WebFetch,
   ToolSearch]` matches the allowlist reasoning at `claude_backend.py:78-80`,
   and also drops the orchestration tools (`Task`, `Cron*`, `RemoteTrigger`,
   `Workflow`, `SendMessage`, worktrees). Which one?
2. Is the rest of the user's Claude Code setup in scope here, or does it need
   its own issue? The brain is offered Gmail, Drive, Calendar, cfactory and
   other MCP tools, 217 skills, and the user's `CLAUDE.md` instructions, and
   `ai-mirror` connects with `desktop_control` off. The options would be
   `setting_sources=[]` and `strict_mcp_config=True`. Neither was tested.
3. Should `WebFetch`, `Write` and `Edit` stay available to the voice brain at
   all, or does it only need `Bash`, `Read` and our tools?
4. Should the system prompt also tell the model to ask choices out loud, as a
   second layer, or is taking the tool away enough?
5. Run 4's reply named browsers without calling `find_app`, and they did not
   match run 3's. Is that in scope for this issue's verification, or a
   separate issue?
