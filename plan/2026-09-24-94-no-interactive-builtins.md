---
status: draft
issue: 94
spec: spec/2026-09-24-94-no-interactive-builtins.md
---

# Plan: offer the voice brain our tools and a named few built-ins, nothing else

## Approved decisions, carried over from the spec

1. **The measurement.** It was taken on 2026-09-24 with Claude Code CLI
   2.1.281, claude-agent-sdk 0.2.152 and model `claude-sonnet-5`. There were
   ten dry runs of `nix run .#omarchy-voice -- -n say --no-confirm
   "<request>"`, each with a scratch patch to `claude_backend._options` that
   was reverted afterwards. **base** is `main`. **iso** is
   `setting_sources=[]` plus `strict_mcp_config=True`. **allow** is iso plus
   `tools=["Read", "ToolSearch"]`. "Input" means `input + cache_creation +
   cache_read`, summed over the turn.

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
   | 7 | allow | reboot the machine | 5 | 27 | 18 / 5 / 2 | | 41,038 | 12.8 s | no picker; held `look up omarchy command 'reboot restart system power'` (now #100) |

   What it shows: iso leaves only `omarchy` on the MCP side (base connected
   88 servers, including claude.ai Gmail and Drive, `cfactory`,
   `mcp-server-git` and 58 plugin servers). allow leaves 5 built-ins. Input
   is about a third of base for the same request. No latency claim is made,
   because there was one run per cell.
2. **An allowlist of built-ins, through the `tools` option (Q1).** The value
   is `tools=["Read", "ToolSearch"]`. It is **never `allowed_tools`**:
   `allowed_tools` auto-approves the tools it names and shadows
   `can_use_tool` (SDK `types.py:1858`), which widens access instead of
   narrowing it. `tools` only sets which built-ins exist.
3. **The CLI adds three MCP-resource readers on its own**
   (`ListMcpResourcesTool`, `ReadMcpResourceTool`, `ReadMcpResourceDirTool`)
   whenever an MCP server is configured, and `--tools` does not remove them.
   They are kept on purpose: our server publishes `omarchy://manifest` and
   says "Read it before driving the desktop" (`mcp_server.py:189-194`). Under
   strict MCP they reach only our server and ai-mirror, and the hook still
   gates them. The brain is therefore offered 5 built-ins.
4. **Why each kept built-in stays.** `Read`: `verify-gate` cases A, B and D
   ask for "your Read tool" (`verify_gate.py:149-171`). It is read-only, it
   is in `DRY_RUN_READS`, and path deny rules still see it. `ToolSearch`:
   ai-mirror's tools stay deferred on purpose (#84,
   `AlwaysLoadTests.test_ai_mirror_stays_deferred`,
   `tests/test_claude_backend.py:604`), and a deferred tool can only be
   loaded through `ToolSearch`.
5. **`Bash`, `WebSearch`, `WebFetch`, `Write` and `Edit` are dropped (Q1,
   Q3), together with `AskUserQuestion`, `EnterPlanMode`, `ExitPlanMode`,
   `Task`, `Cron*`, `Workflow`, `SendMessage`, worktrees, `Skill`, `LSP` and
   every future built-in.** `Bash` is replaced by our `run_shell`, which
   honours `allow_shell` (default false, `config.py:396`), and by
   `run_in_terminal`. The persona already routes searching to our
   `web_search`. The fallback, if the approver later wants a shell, is
   `("Bash", "Read", "ToolSearch")`, with the README and `doctor` left as
   they are. That fallback was not chosen.
6. **`setting_sources=[]` plus `strict_mcp_config=True` (Q2).** The user's
   settings, hooks, permission rules, plugins, skills, MCP servers and
   `~/.claude/CLAUDE.md` do not reach the brain. Only the servers in
   `mcp_servers` do: `omarchy`, plus `ai-mirror` when `desktop_control` is
   on.
7. **The managed `/etc/claude-code/CLAUDE.md` still loads (measured, runs 2
   and 3).** It is the machine owner's enforced policy. This is accepted and
   written down, not worked around. Our system prompt still decides the
   behaviour.
8. **No persona line and no system-prompt change (Q4).** With the tool gone,
   run 6 asked out loud with no extra prompt. Add a line only if a dry run
   ever shows the model trying to ask some other way.
9. **The browsers named without `find_app` (Q5) are in scope for
   verification only.** The live check requires a `find_app` call. No code
   change is made for it.
10. **Where the change lives.** It all goes in `ClaudeBrain._options()`.
    `WarmBrain` and `LocalBrain` inherit it through `super()`. A module
    constant `BUILTIN_TOOLS = ("Read", "ToolSearch")` sits beside
    `DRY_RUN_READS`, with a comment that gives the reasons, CLI 2.1.281, and
    the three readers the CLI adds. `ClaudeBrain.builtin_tools =
    BUILTIN_TOOLS` is a class attribute, so `verify-gate` can widen it on one
    instance.
11. **Nothing else in the options changes.** That covers the hook
    (`matcher=None`), `can_use_tool=self._alarm`,
    `permission_mode="default"`, `alwaysLoad` and `_child_env`.
    `DRY_RUN_READS` is also unchanged; its `WebSearch` entry becomes
    unreachable, which does no harm.
12. **verify-gate case C gets a per-instance `EnterWorktree`.** `_Case`
    gains `extra_tools: tuple[str, ...] = ()`. Case C sets
    `("EnterWorktree",)`. `_run_case` sets `brain.builtin_tools =
    (*claude_backend.BUILTIN_TOOLS, *case.extra_tools)` on that one
    instance. Without this, case C is always inconclusive and `verify-gate`
    always exits 2. What case C proves (the hook refuses a changing tool
    under a dry run, when the CLI approves it by itself) still guards any
    later addition to the list.
13. **`doctor` and the README lose the Bash caveat.** `allow_shell` is again
    the whole answer. The README bullet says the model gets our tools plus
    `Read` and `ToolSearch`, none of the user's MCP servers or settings, and
    that every call still goes through the hook. It also says that
    `~/.claude/settings.json` permission rules no longer apply to the brain.
14. **Risks, accepted:** the CLI still offers the 3 readers we did not name,
    so the hook stays unfiltered and the allowlist is a second layer. A CLI
    that renames `Read` or `ToolSearch` silently drops the tool, because
    `--tools` does not reject unknown names, and `verify-gate` A/B/D then go
    inconclusive. The empty `setting_sources` depends on the SDK emitting
    `--setting-sources=` rather than treating `[]` as unset, and the
    pass-through test guards that. User deny rules in
    `~/.claude/settings.json` stop applying to the brain. The Realtime/OpenAI
    engine, the HTTP `Planner` and external `omarchy-voice mcp` clients are
    unaffected. The only host affected is the one that runs the claude-code
    backend (p620).

    **Residual, not changed by #94:** `Read` stays allowed and can read
    secret paths (`/etc/shadow`, `secrets.env`), because no default deny rule
    covers paths on reads. `~/.ssh` is refused only because `\bssh\b` happens
    to match. This was demonstrated in #100's intent
    (`intent/2026-09-24-100-reads-are-not-actions.md:104-106`, `:119-120`, on
    branch `fix/100-reads-are-not-actions` at 90dd675). It is to be closed by
    #100 (its Q3, `:197-199`: default deny rules for secret paths), which
    should land soon after #94. #94 neither widens this nor closes it; the
    brain could already do it.
15. **Not verified by the spec, and not claimed here:** that the user's hooks
    stop under iso, that auto-memory stays unused (`init.memory_paths.auto`
    is still reported, the directory is empty, and the brain has no `Write`),
    and a live `desktop_control = true` run. The last one is covered by unit
    tests (step 4).
16. **Found on the way, not fixed here:** the confirm gate holds our own
    `omarchy_help` lookup for "reboot" (now #100), and the test suite writes
    to the real `session.log` (now #99).

Resolved here because the spec left it open:

- **What the real SDK emits (checked while writing this plan).** In the dev
  shell (claude-agent-sdk 0.2.152), `SubprocessCLITransport._build_command()`
  for `ClaudeAgentOptions(tools=["Read", "ToolSearch"], setting_sources=[],
  strict_mcp_config=True, cli_path="/bin/true")` contains
  `"--tools", "Read,ToolSearch"` (two argv items), `"--strict-mcp-config"`,
  and `"--setting-sources="`. The last one is **one** argv item with an empty
  value. It contains no `--allowedTools`. With `setting_sources=None`, no
  `--setting-sources` flag is emitted at all, so the CLI would fall back to
  its defaults. That is why `[]` and `None` must not be confused. The SDK
  source is `subprocess_cli.py:581-591` (`--tools`), `:690-691`
  (`--strict-mcp-config`) and `:719-720` (`--setting-sources=`). The spec
  cited 0.2.153's line numbers, and the behaviour is the same.
- **Spec line numbers checked against `main` 72f9ab6.** Most match. These
  have drifted: `WarmBrain._options` is `claude_backend.py:711-715` (the spec
  said 706-711), `_run_case` is `verify_gate.py:194-202`, `LocalBrain` is
  `local_engine.py:113-116`, and `omarchy_help`'s description is
  `tools.py:1847`. The steps below use the checked numbers.
- **Test count.** The spec says 912 on `main`. `main` is now 72f9ab6, with
  930 passed and 473 subtests. Step 0 re-records it.
- **Stale comments that name Claude Code's `Bash`.** The spec lists only
  `cli.py` and the README. After this change, the following also become
  false: `claude_backend.py:10` (the module docstring: "Bash, Write, Edit,
  Read, WebFetch"), `claude_backend.py:128` ("like Bash does"),
  `config.py:231-233` ("including Bash") and `config.py:242` ("its own Bash
  and Read tools"). So does `README.md:789` ("the same deny/confirm gate as
  Bash"). They are reworded in steps 1 and 8, as comments and docs only. No
  behaviour changes.
- **ai-mirror in the live check.** `init.mcp_servers` must be `omarchy`
  alone, or `omarchy` plus `ai-mirror` if the user's `config.toml` has
  `desktop_control = true`. Record which one. Anything else fails.
- **The live runs capture `init` through a scratch patch**, as the spec's
  measurement did. The session `.jsonl` does not carry the `init` tool list.
  The patch is applied on top of the committed change and reverted with
  `git checkout -- src/`.

## Steps

Line numbers are for `origin/main` 72f9ab6. This branch has no code changes
on top of it (`git diff --stat origin/main HEAD -- src tests README.md` is
empty).

0. **Baseline.** Run `gh issue view 94 --json state`, which should be
   `OPEN`. Run `git branch --show-current`, which should be
   `fix/94-no-interactive-builtins`. Run `git fetch origin && git rebase
   origin/main`. Then run `nix develop -c pytest tests -q` → record the
   count. It was **930 passed, 473 subtests** when this plan was written.
   Also record `claude --version` (2.1.281) and the SDK version
   (`nix develop -c python -c 'import claude_agent_sdk as s;
   print(s.__version__)'`, 0.2.152).

1. **`src/omarchy_voice/claude_backend.py`: the constant and the class
   attribute.**
   - After `DRY_RUN_READS` (:101), add:

     ```python
     # The only Claude Code built-ins the brain is offered (#94). An allowlist
     # for the same reason as DRY_RUN_READS: a built-in Claude Code ships next
     # month must arrive absent, not offered. Read: verify-gate A, B and D
     # drive it. ToolSearch: ai-mirror's tools stay deferred (#84) and load
     # only through it. Nothing else here is a voice tool, and some cannot
     # work at all without a terminal (AskUserQuestion, EnterPlanMode). Our
     # run_shell, which honours allow_shell, replaces Bash.
     # Checked on CLI 2.1.281: the CLI also adds ListMcpResourcesTool,
     # ReadMcpResourceTool and ReadMcpResourceDirTool whenever an MCP server
     # is configured, and --tools does not remove them. They are kept, and
     # the hook still gates them.
     BUILTIN_TOOLS = ("Read", "ToolSearch")
     ```
   - In `class ClaudeBrain` (:246), add a class attribute right after the
     docstring: `builtin_tools = BUILTIN_TOOLS`. Comment: "verify-gate widens
     this on one instance (case C)."
   - Reword the module docstring (:10-16). Claude Code would bring its own
     toolset; this backend offers only `Read` and `ToolSearch` (see
     `BUILTIN_TOOLS`). The hook paragraph stays. Reword `:128` from "like
     Bash does" to "like every other call does".

   → verify by `nix develop -c python -c 'from omarchy_voice.claude_backend
   import ClaudeBrain; print(ClaudeBrain.builtin_tools)'`, which should
   print `('Read', 'ToolSearch')`.

2. **`src/omarchy_voice/claude_backend.py`: the options.** In
   `ClaudeAgentOptions(...)` (:528-549), after `mcp_servers=servers,` (:530),
   add:

   ```python
   # Which Claude Code built-ins exist for this session (#94). `tools`,
   # NEVER `allowed_tools`: that one auto-approves what it names and
   # shadows can_use_tool, so it would widen access, not narrow it.
   tools=list(self.builtin_tools),
   # None of the user's settings, hooks, permission rules, plugins, skills
   # or ~/.claude/CLAUDE.md. [] and not None: None means the CLI default.
   # The managed /etc/claude-code/CLAUDE.md still loads; that is the
   # machine owner's policy (measured on CLI 2.1.281).
   setting_sources=[],
   # Only the servers above: omarchy, and ai-mirror when desktop_control
   # is on. Not the user's claude.ai connectors or plugin servers.
   strict_mcp_config=True,
   ```

   Do not touch `hooks` (:535-536), `can_use_tool` (:539),
   `permission_mode` (:543), the `alwaysLoad` entry (:518), the ai-mirror
   entry (:524-526) or `_child_env`. `WarmBrain._options()` (:711-715) and
   `LocalBrain._options()` (`local_engine.py:113-116`) build on
   `super()._options()` and need no change.

   → verify by `git diff --stat -- src/omarchy_voice/claude_backend.py`,
   which should show one file with about 25 lines added, and then by steps 3
   to 5.

3. **`tests/test_claude_backend.py`: unit tests on the built options.** Add
   `BuiltinToolsTests(unittest.TestCase)` after `SdkPassThroughTests`
   (:626-646), using `options_of` (:407):
   - `test_only_the_named_builtins`:
     `options_of(ClaudeBrain(Config(dry_run=True), ...))`. Assert that
     `options.tools == ["Read", "ToolSearch"]`, and that none of
     `AskUserQuestion`, `EnterPlanMode`, `ExitPlanMode`, `Bash`, `WebFetch`,
     `Write` or `Edit` is in it.
   - `test_the_users_setup_stays_out`: `options.setting_sources == []`
     (assert it `is not None` as well) and `options.strict_mcp_config is
     True`.
   - `test_never_allowed_tools`: `assertFalse(hasattr(options,
     "allowed_tools"))`. `options_of` returns a `SimpleNamespace` of exactly
     the kwargs passed, so this proves it was never passed.
   - `test_every_brain_gets_it`: with a `subTest` per brain, check
     `ClaudeBrain`, `WarmBrain` and
     `local_engine.brain_for(config, Executor(config))` (the `LocalBrain`).
     Each has the same `tools`, `setting_sources` and `strict_mcp_config`.
   - `test_desktop_control_on_keeps_ai_mirror`: build
     `AiMirrorTests().options("/bin/ai-mirror")`. Assert that
     `set(options.mcp_servers) == {"omarchy", "ai-mirror"}`, that
     `strict_mcp_config is True`, and that `options.tools == ["Read",
     "ToolSearch"]` (ToolSearch is what loads ai-mirror's deferred tools).
   - `test_desktop_control_off_is_ours_alone`: build
     `AiMirrorTests().options("/bin/ai-mirror", desktop_control=False)`. The
     servers are `{"omarchy"}` and `strict_mcp_config is True`.

   The existing `HookTests` (:420), `AiMirrorTests` (:549) and
   `AlwaysLoadTests` (:590) run unchanged.

   → verify by `nix develop -c pytest tests/test_claude_backend.py -q`,
   which should pass.

4. **`tests/test_claude_backend.py`: the real SDK command line, including
   ai-mirror under `desktop_control`.** In `SdkPassThroughTests` (:626), in
   the #84 style (the real `ClaudeAgentOptions`, the real
   `SubprocessCLITransport`, `cli_path="/bin/true"`, `_build_command()`,
   which spawns nothing):
   - `test_the_isolation_reaches_the_cli`: from `options_of(ClaudeBrain(...))`,
     build `ClaudeAgentOptions(tools=o.tools,
     setting_sources=o.setting_sources, strict_mcp_config=o.strict_mcp_config,
     mcp_servers=o.mcp_servers, cli_path="/bin/true")`. Assert:
     - `command[command.index("--tools") + 1] == "Read,ToolSearch"`
     - `"--setting-sources=" in command` (one argv item, empty value)
     - `"--strict-mcp-config" in command`
     - `"--allowedTools" not in command`
   - `test_strict_mcp_keeps_ai_mirror`: do the same from
     `AiMirrorTests().options("/bin/ai-mirror")`. `json.loads` the argument
     after `--mcp-config` and assert that
     `set(cfg["mcpServers"]) == {"omarchy", "ai-mirror"}` and that
     `"--strict-mcp-config" in command`.
   - Update the class docstring so it also names #94 and SDK 0.2.152
     (`subprocess_cli.py:581-591, 690-691, 719-720`). It should say that if
     `--setting-sources=` disappears after an SDK bump, `[]` has started
     being treated as unset, and the user's setup is loaded again.

   → verify by `nix develop -c pytest tests/test_claude_backend.py -q`,
   which should pass.

5. **Mutation checks** (each is reverted straight after with `git checkout
   -p` or by hand):
   - Delete the `tools=` line → `test_only_the_named_builtins`,
     `test_every_brain_gets_it`, `test_desktop_control_on_keeps_ai_mirror`
     and `test_the_isolation_reaches_the_cli` must fail.
     `test_never_allowed_tools` still passes, as expected.
   - Rename `tools=` to `allowed_tools=` → `test_never_allowed_tools` and
     `test_the_isolation_reaches_the_cli` must fail.
   - Change `setting_sources=[]` to `setting_sources=None` →
     `test_the_users_setup_stays_out` and `test_the_isolation_reaches_the_cli`
     must fail. This is the `[]`/`None` trap.
   - Delete `strict_mcp_config=True` → `test_the_users_setup_stays_out`,
     both `desktop_control` tests and both pass-through tests must fail.

   → verify by recording which tests failed for each mutation. Then
   `git diff` should show only the intended change.

6. **`src/omarchy_voice/verify_gate.py`: case C's per-instance
   `EnterWorktree`.**
   - In `_Case` (:118-124), add `extra_tools: tuple[str, ...] = ()` after
     `breaks_policy`.
   - For case C (:160-164), add `extra_tools=("EnterWorktree",)`. Comment:
     "no longer offered to the brain (#94); widened for this case only, to
     prove the hook still refuses a changing tool the CLI approves itself."
   - In `_run_case` (:194-202), after `brain = ...` (:196), add
     `brain.builtin_tools = (*claude_backend.BUILTIN_TOOLS,
     *case.extra_tools)`.

   → verify by `nix develop -c pytest tests/test_verify_gate.py -q`, which
   should pass unchanged.

7. **`tests/test_verify_gate.py`: tests for step 6.** Add
   `ExtraToolsTests(unittest.TestCase)`:
   - `test_only_case_c_widens`: `{c.key: c.extra_tools for c in CASES}`
     equals `{"A": (), "B": (), "C": ("EnterWorktree",), "D": ()}`.
   - `test_the_widening_is_one_instance`: patch
     `claude_backend.ClaudeBrain.think` with `autospec=True` and a side
     effect that records `options_of(self).tools` (import `options_of` from
     `tests.test_claude_backend`, or copy its three patches) and returns an
     object with `reply=""`. Also patch `verify_gate._worktrees` to return
     1. Call `verify_gate._run_case(case_c, Config(), tmp_root)`, with
     `case_c.fields` given a temp dir. Assert that the recorded tools are
     `["Read", "ToolSearch", "EnterWorktree"]`, and that
     `ClaudeBrain.builtin_tools == ("Read", "ToolSearch")` afterwards.

   Mutation: set `claude_backend.ClaudeBrain.builtin_tools = ...` (the
   class) instead of `brain.builtin_tools` in `_run_case` →
   `test_the_widening_is_one_instance` must fail. Revert.

   → verify by `nix develop -c pytest tests/test_verify_gate.py -q`, which
   should pass.

8. **`src/omarchy_voice/cli.py`, `README.md` and `config.py`: the dropped
   `Bash`.**
   - `cli.py` `shell_status` (:245-270): delete the `if active ==
     "claude-code" ...` block (:264-269) and the docstring's first two
     paragraphs (:248-256). Keep `active` in the signature, since the caller
     at :517 passes it. The docstring now says `allow_shell` is the whole
     answer on both backends, because the claude-code backend no longer
     offers Claude Code's `Bash` (#94).
   - `tests/test_backend_choice.py` `ShellStatusTests` (:336-359): rewrite
     the class docstring. Replace
     `test_it_says_so_when_bash_is_live_behind_a_disabled_shell_tool`
     (:345-349) with
     `test_claude_code_has_no_bash_caveat_any_more`:
     `shell_status(Config(allow_shell=False), "claude-code")` has one line,
     and that line says "disabled". The other two tests stay.
   - `README.md:256-263`: rewrite the bullet as "**It gives the model our
     tools and two of Claude Code's own: `Read` and `ToolSearch`.**" Then:
     no `Bash`, `Write`, `Edit` or `WebFetch`, so `allow_shell` means what
     it says. None of your own MCP servers, plugins, skills, settings or
     `~/.claude/CLAUDE.md` is loaded, so a deny rule in
     `~/.claude/settings.json` does not apply here either. A managed
     `/etc/claude-code/CLAUDE.md` still is. Every call, including the few
     resource readers Claude Code adds by itself, goes through the same
     `PreToolUse` hook and to `omarchy-voice log`. Keep the "regexes, not a
     sandbox" sentence.
   - `README.md:789`: change "the same deny/confirm gate as Bash" to "the
     same deny/confirm gate as every other call".
   - `config.py:231-233` and `:242`: reword the comments to say "our tools
     plus Claude Code's Read and ToolSearch, gated by our policy through a
     PreToolUse hook", and "what its Read tool sees". Comments only.

   → verify by `grep -n "Bash" README.md src/omarchy_voice/cli.py
   src/omarchy_voice/config.py`. No line should still claim that the
   claude-code brain has Claude Code's `Bash`. (`cli.py:167`, about
   `_tool_Bash` and `Executor.run_pending`, is about a hold, not the
   toolset, and stays.) Then run `nix develop -c pytest
   tests/test_backend_choice.py -q`, which should pass.

9. **Whole suite.** `nix develop -c pytest tests -q` → the step 0 count
   plus 10 new tests (6 in step 3, 2 in step 4, 2 in step 7; the step 8
   test is a replacement), so **940 passed** from 930, with 0 failures.
   `HookTests`, `AlwaysLoadTests`, `SubscriptionNotApiKeyTests` and
   `RunTests` must pass unchanged.

10. **Whole check.** `nix flake check --no-write-lock-file` → it passes. It
    runs the same suite against the real SDK (`flake.nix:113-145`).

11. **Live check: dry run only, at most 4 runs.** Commit steps 1 to 8
    first. Nothing is restarted and whisper-server is never touched.
    - Apply a **scratch** patch to `ClaudeBrain._ask`
      (`claude_backend.py:585-595`, the `receive_response()` loop). When a
      message has `subtype == "init"`, append `message.data["tools"]` and
      `message.data["mcp_servers"]` to
      `$SCRATCH/94-live.jsonl`. Also append every `tool_use` block's
      `name`/`input`, and the `ResultMessage` `usage`.
    - Run each of these once (4 runs in total, with no retry budget; a run
      that fails for a reason unrelated to #94 is recorded as such, not
      rerun):

      ```
      nix run .#omarchy-voice -- -n say --no-confirm "open zed"
      nix run .#omarchy-voice -- -n say --no-confirm "what's on my screen"
      nix run .#omarchy-voice -- -n say --no-confirm "open a new browser, but first ask me which of my browsers I want and give me the options to pick from"
      nix run .#omarchy-voice -- -n say --no-confirm "reboot the machine"
      ```
    - Then run `git checkout -- src/` and `git status --short` (it should be
      clean apart from untracked scratch).

    Record per run: the built-ins offered, the MCP tool count, the
    `mcp_servers`, the tools called, input (`input_tokens +
    cache_creation_input_tokens + cache_read_input_tokens`), and the turn
    time from `say`.

    → pass means all of the following:
    - the built-ins are exactly `Read`, `ToolSearch`, `ListMcpResourcesTool`,
      `ReadMcpResourceTool` and `ReadMcpResourceDirTool`;
    - the MCP tools are the `mcp__omarchy__*` set only (27 when the spec was
      written; record the number), and `mcp_servers` is as described under
      "ai-mirror in the live check" above;
    - "open zed" calls `launch_app`;
    - "what's on my screen" answers correctly;
    - the browser request calls `find_app`, makes no `AskUserQuestion` call,
      and asks out loud;
    - "reboot the machine" holds no `AskUserQuestion`. A hold on
      `omarchy_help` is #100 and does not fail this check;
    - input is about 40 K for the tool-using requests and about 20 K for the
      screen request, against decision 1's base of about 125 K. It is
      recorded, and it is not a hard threshold.

    Put the table, the CLI version and the SDK version in the PR description.

12. **PR.** Push, and open a PR that closes #94. It links the intent, the
    spec and this plan, and includes step 11's table and step 5's mutation
    results. It asks the user to run `omarchy-voice verify-gate` after merge
    (it must print "The gate holds"). That is 4 real model turns with
    non-dry reads on temp files, so it is not run here.

## Tests

```
nix develop -c pytest tests -q                              # 930 + 10 = 940 passed, 0 failures
nix develop -c pytest tests/test_claude_backend.py -q       # BuiltinToolsTests, SdkPassThroughTests, HookTests, AlwaysLoadTests
nix develop -c pytest tests/test_verify_gate.py -q          # ExtraToolsTests, RunTests
nix develop -c pytest tests/test_backend_choice.py -q       # ShellStatusTests
nix flake check --no-write-lock-file                        # CI parity, real SDK
```

Step 5 and step 7 hold the mutation checks. Step 11 is the live dry run
(4 runs at most). After merge, the user runs `omarchy-voice verify-gate`.

## Rollback

The change is one squash-merged PR. It covers three options and a constant
in `claude_backend.py`, one field in `verify_gate.py`, a removed caveat in
`cli.py`, docs and comments, and tests. It has no Nix, config or
persisted-state change. `git revert <merge>` brings back every built-in and
the user's whole Claude Code setup, including `AskUserQuestion`, and so
brings back #94.

Partial rollbacks, each a one-line change plus its test:

- **A shell is wanted back:** set `BUILTIN_TOOLS = ("Bash", "Read",
  "ToolSearch")`, and restore the `shell_status` caveat and the README
  bullet from the reverted commit (decision 5's fallback).
- **A user MCP server or setting is wanted back:** drop `strict_mcp_config`
  or `setting_sources` alone. The `tools` allowlist is independent of both.
- **A CLI upgrade renames `Read` or `ToolSearch`:** `verify-gate` A/B/D go
  inconclusive. Update `BUILTIN_TOOLS` to the new names in a new issue.

Follow-up, not a rollback: rolling #94 back does not affect the residual
`Read` risk in decision 14 (secret paths readable), and neither does keeping
it. That risk closes with #100's default deny rules for secret paths (its Q3).
The PR description should link #100 as the follow-up.
