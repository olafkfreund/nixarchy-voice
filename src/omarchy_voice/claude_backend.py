"""The typed path, driven by Claude Code instead of the OpenAI API.

Same persona, same tools, same policy gate as `planner.Planner` — only the
engine underneath changes. Turns run through the `claude` CLI via the Claude
Agent SDK, so they bill against the user's Claude subscription rather than an
API key with a balance on it.

Two things come along with that engine, and both matter here:

  * Claude Code would bring its own toolset; this backend offers only `Read`
    and `ToolSearch` of it (see `BUILTIN_TOOLS`). Ours are offered alongside
    them as an in-process MCP server (the very same `mcp_server.build_server`,
    so there is still one implementation of every tool). Claude Code's own
    tools have never seen our `Policy`, which is what the `PreToolUse` hook
    below is for -- a hook rather than the
    permission callback, because Claude Code never calls the callback for
    anything it approves on its own.
  * The SDK is async-only and `think()` is not, because everything that calls
    it — `say`, the realtime session's typed fallback — is synchronous. One
    `asyncio.run` per turn is the whole of the bridge.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from dataclasses import dataclass

from . import capabilities, mcp_server, planner
from .config import Config
from .planner import PlannerUnavailable, Turn
from .tools import READ_ONLY_TOOLS, Denied, Executor, NeedsConfirmation

try:
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
except ImportError:  # the SDK is an optional dependency; this backend is opt-in
    # Stand-ins so the gate below can be written, read and TESTED without the
    # SDK installed. The SDK isinstance-checks what the callback returns, so
    # these would be rejected -- which is fine, because with no SDK there is
    # nothing to reject them: `_options` raises long before the gate runs.
    @dataclass
    class PermissionResultAllow:  # type: ignore[no-redef]
        behavior: str = "allow"

    @dataclass
    class PermissionResultDeny:  # type: ignore[no-redef]
        behavior: str = "deny"
        message: str = ""
        interrupt: bool = False


# Where to find the Claude Code binary, when it is not on PATH. Same shape as
# listen_local's model override: the package wires it up, and someone running
# the module straight out of the checkout can still point at their own.
CLI_ENV = "OMARCHY_VOICE_CLAUDE_CLI"

# A full model id, never an alias. An alias resolves server-side and quietly
# moves under you — "claude-sonnet-latest" was a different model last month.
DEFAULT_MODEL = "claude-sonnet-5"

NO_CLI = "Claude Code isn't installed."
NOT_LOGGED_IN = "Claude Code isn't logged in."

# What the model is told when something is held (#86). It names the words
# only to forbid them: her saying one is what the engine refuses to take.
HOLD_INSTRUCTION = (
    "needs the user's confirmation, which they give the engine directly. Stop "
    "here. Say it is waiting; do not ask them to confirm and do not say "
    "confirm, go ahead or yes do it.")

# Tools whose whole job is to read. Described rather than run through a
# separate path: the policy gate sees them like anything else, and a deny rule
# aimed at a path should still catch a read of it. Deny rules still apply to
# reads; confirm rules do not (#100).
_PATH_TOOLS = {"Write": "write", "Edit": "edit", "NotebookEdit": "edit",
               "Read": "read", "Glob": "list", "Grep": "search"}

# Claude Code tools a dry run may let through, because they cannot change
# anything. Everything else is refused under dry_run, Bash included.
#
# An allowlist, not a denylist: Claude Code gains tools on its own schedule,
# and a new one must arrive as "not known to be safe" rather than as safe.
# `tools.READ_ONLY_TOOLS` does the same job for our own tools.
#
# Read off the CLI rather than remembered. Claude Code 2.1.274 declares its
# tools in the `init` message of `--output-format stream-json`, and that list
# has no Glob, Grep or TodoWrite -- search goes through Bash now -- so the
# reader names in _PATH_TOOLS above are not reused here: two of them match
# nothing. Tools that only sound read-only (ReadMcpResourceTool, LSP) are left
# out too. Omitting a reader costs a needless refusal; admitting a writer is
# the bug this set exists to prevent.
#
# No WebFetch. It fetches an arbitrary URL the model picked, and a GET can
# spend a one-use link, hit an unsubscribe or webhook URL, or carry data out
# in its query string. HTTP's "GET is safe" is a promise the server makes, not
# one the client can enforce (RFC 9110 9.2.1). WebSearch stays: it sends a
# query to a search engine, which is not a URL anyone chose to act on.
#
# ToolSearch is in because it only loads tool schemas, and it never reached
# the gate until the policy moved into a PreToolUse hook -- it is approved
# without asking, as a dry run creating a worktree showed. Refusing it would
# make a dry run narrate "would have run: ToolSearch" instead of the call the
# model was reaching for.
DRY_RUN_READS = frozenset({"Read", "WebSearch", "ToolSearch"})


def _is_read(tool: str) -> bool:
    """A Claude Code built-in reader, or one of our read-only tools (#100)."""
    return tool in DRY_RUN_READS or (
        tool.startswith("mcp__omarchy__")
        and tool.removeprefix("mcp__omarchy__") in READ_ONLY_TOOLS)

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


def cli_path(config: Config) -> str:
    """The `claude` binary to drive, or "" if there is not one."""
    return (os.environ.get(CLI_ENV, "")
            or getattr(config, "claude_cli", "")
            or shutil.which("claude") or "")


def cli_version(binary: str) -> str:
    """What `claude --version` says, or "" if it will not say.

    The version matters beyond display: the policy gate rests on how this
    CLI treats hooks, measured on one version at a time. `doctor` shows it
    and `verify-gate` reports it beside its verdict.
    """
    try:
        return subprocess.run([binary, "--version"], capture_output=True,
                              text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


# ai-mirror (github:olafkfreund/ai-mirror): real mouse, keyboard, screenshots and
# the accessibility tree, behind its own bar indicator and kill switch. Offered
# whenever it is installed. Its calls are not ours, so they go through the
# policy hook like every other call does -- typing "sudo ..." into a terminal
# is still typing sudo.
AI_MIRROR_ENV = "OMARCHY_VOICE_AI_MIRROR"

AI_MIRROR_PROMPT = """\
# Full desktop control (ai-mirror)

The mcp__ai-mirror__ tools drive the real mouse and keyboard. Reach for them when \
send_shortcut, click_text and launch_app cannot do the job: a button with no \
readable label, a drag, a dialog, a form.

Control belongs to the user. Call control with mode=agent to ASK for it, then \
poll status: owner=pending means a dialog is open on their desktop and you wait; \
owner=agent means they said yes; owner=off after asking means they said no or \
nobody answered. On a deny or a timeout, tell them you could not get control and \
do not ask again in this turn. Prefer a11y_find over a screenshot, and call \
control with mode=off when the job is done. not_owner or stale_generation means \
the user took control back: stop and say so, never retry."""


def ai_mirror_path() -> str:
    """The `ai-mirror` binary, or "" if it is not installed."""
    return os.environ.get(AI_MIRROR_ENV, "") or shutil.which("ai-mirror") or ""


def _credentials_present() -> bool:
    """Whether Claude Code has something to authenticate with.

    A subscription login lands in ~/.claude/.credentials.json; an API key in
    the environment works too. Either is enough — this only rules out the
    case where there is neither, which fails as a wall of CLI output several
    seconds into a turn.
    """
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return True
    return (Path.home() / ".claude" / ".credentials.json").exists()


# Claude Code talks to this and nothing else. Probed rather than assumed
# because `check_ready` used to pass on a laptop with no network at all: the
# CLI was installed, the login was on disk, every check said yes, and then the
# turn died inside the SDK with no fallback.
ANTHROPIC_HOST = "https://api.anthropic.com"


def check_ready(config: Config | None = None) -> list[str]:
    config = config or Config()
    problems = []
    if not planner.reachable(ANTHROPIC_HOST, config):
        problems.append(
            # Worded as a network fact, because the reason line is all the
            # user sees: "not ready" alone sends someone offline in a train
            # hunting for a login that was never missing.
            "cannot reach api.anthropic.com — offline, not misconfigured")
    if not cli_path(config):
        problems.append(
            f"claude is not installed: {CLI_ENV} is unset and there is no "
            "`claude` on PATH. Add pkgs.claude-code to your configuration and "
            "rebuild, or set claude_cli in config.toml.")
    elif not _credentials_present():
        problems.append(
            "claude is installed but not logged in — run `claude` once and "
            "sign in, or set ANTHROPIC_API_KEY")
    return problems


def describe_tool(tool: str, tool_input: dict) -> str:
    """One line for a Claude Code tool call, for the policy gate and the log.

    The gate matches regexes written for shell commands and omarchy routes, so
    what matters is that the dangerous part of the call — the command, the
    path — ends up in the string rather than buried in a JSON blob.
    """
    if tool == "Bash":
        return str(tool_input.get("command", ""))
    if tool in _PATH_TOOLS:
        target = tool_input.get("file_path") or tool_input.get("path") or ""
        return f"{_PATH_TOOLS[tool]} {target}".strip()
    if tool == "WebFetch":
        return f"fetch {tool_input.get('url', '')}"
    try:
        rest = json.dumps(tool_input, default=str)
    except (TypeError, ValueError):
        rest = str(tool_input)
    if tool.startswith("mcp__ai-mirror__"):
        # Uncut: it carries typed text, and a deny rule must see all of it.
        return f"{tool} {rest}"
    return f"{tool} {rest[:400]}"


def _release_message(tool: str, tool_input: dict) -> str:
    """The turn that releases one held call (#76).

    Worded as a fact about the user because it arrives under "# What the user
    said". The input goes in uncut: the gate matches on it exactly, so the
    model must be able to reproduce it exactly.
    """
    return (f"I have just confirmed exactly this one {tool} call, with this "
            f"input: {json.dumps(tool_input, default=str)}. Make that call now, "
            "unchanged, and nothing else. Then say in one sentence whether it ran.")


def _same_call(tool: str, tool_input: dict, approved: tuple[str, dict]) -> bool:
    """Is this the call the user said yes to? Exact, except Bash's `description`.

    That key is the model's own label for the command, not part of what runs,
    and it is rarely written the same way twice. Nothing else is relaxed: a
    Write with other content, or a command with one more flag, is another call.
    """
    held_tool, held_input = approved
    if tool != held_tool:
        return False
    if tool == "Bash":
        drop = lambda d: {k: v for k, v in d.items() if k != "description"}
        return drop(tool_input) == drop(held_input)
    return tool_input == held_input


class ClaudeBrain:
    """`Planner`'s twin, with Claude Code underneath."""

    # verify-gate widens this on one instance (case C).
    builtin_tools = BUILTIN_TOOLS

    def __init__(self, config: Config, executor: Executor):
        self.config = config
        self.executor = executor
        # A description held back for the user to say yes to. Deliberately
        # NOT `executor.pending`: that is a re-executable handle, and
        # `Executor.run_pending` releases it with `getattr(self, "_tool_" +
        # name)`. There is no `_tool_Bash`, so parking a Claude Code call
        # there would turn "yes, do it" into an AttributeError. The release
        # path is `confirm()` below; the model is told to stop and ask.
        self.pending: str | None = None
        # The held call itself, so confirming can name it exactly (#76). An
        # approval exists only inside the release turn: `_approved` is the
        # call the user said yes to, and it is honoured only while
        # `_releasing`, and cleared when that turn ends, used or not.
        self._held_call: tuple[str, dict] | None = None
        self._approved: tuple[str, dict] | None = None
        self._releasing = False
        # What the gate let through this turn, in order. Filled here rather
        # than from the assistant's tool_use blocks because those are written
        # before the gate has spoken: a denied action would be reported as
        # something that happened.
        self._actions: list[str] = []

    def confirm(self) -> str | None:
        """The user said yes: the message for the release turn, or None.

        The next *release turn* (`think`/`ask_stream` with `release=True`) may
        make that exact call once. Replaying the utterance instead ran the rest
        of it a second time, or ran whatever was said last (#76).
        """
        call, self._held_call, self.pending = self._held_call, None, None
        self._approved = call
        return _release_message(*call) if call else None

    def cancel(self) -> str | None:
        """Drop the hold, or the approval if the user already said yes.

        A yes can be waiting for its release turn -- queued behind a turn that
        was running when it was given -- and a cancel in that gap must end it,
        or the release turn runs what the user just cancelled (#76).
        """
        held = self.held_or_approved
        self.pending = self._held_call = self._approved = None
        return held

    @property
    def held_or_approved(self) -> str | None:
        """What a cancel would withdraw: the hold, or an approval not yet spent."""
        return self.pending or (describe_tool(*self._approved) if self._approved else None)

    def _unspent(self) -> str:
        """What to say when a release turn ends with its approval unused."""
        return f"{describe_tool(*self._approved)} was not run." if self._approved else ""

    def think(self, text: str, *, release: bool = False) -> Turn:
        """One turn. `release=True` for the message `confirm()` returned."""
        turn = Turn(text=text)
        started = time.monotonic()
        # Set here, not in confirm(): an approval cannot be spent by a turn
        # that was already running when the user said yes (#76).
        self._releasing = release
        try:
            try:
                asyncio.run(self._ask(text, turn))
            except PlannerUnavailable as exc:
                turn.error = str(exc)
                turn.reply = exc.spoken
            except Exception as exc:  # a voice tool must not die on one bad turn
                turn.error = f"{type(exc).__name__}: {exc}"
                turn.reply = "Something went wrong with that."
            if release and (left := self._unspent()):
                turn.reply = f"{turn.reply} {left}".strip()
        finally:
            # The approval dies with its release turn, used or not. Not with
            # any other turn: see ask_stream.
            self._releasing = False
            if release:
                self._approved = None
        turn.elapsed = time.monotonic() - started
        return turn

    # -- the gate -----------------------------------------------------------
    async def _decide(self, tool: str, tool_input: dict):
        """Our policy's verdict on one Claude Code tool call.

        The decision, not the entry point: `_pre_tool_use` is the only
        caller, and it is what guarantees this runs for every call. Order
        matters and is the whole design -- a release turn first, then ours
        (Executor gates them), then deny and confirm, then dry-run.
        """
        if self._releasing:
            # A release turn (#76) makes the one call the user confirmed and
            # nothing else. First, so not even our own tools slip past it.
            description = describe_tool(tool, tool_input or {})
            if self._approved and _same_call(tool, tool_input or {}, self._approved):
                # Spent on the way through. The user approved this action, not
                # this action forever -- a second attempt in the same turn is
                # refused below like anything else.
                self._approved = None
                # A yes is consent to the action, not an exemption from
                # dry-run: this path skips the policy check (that is what
                # confirming was) but must not skip this one, or a dry run
                # acts the moment the user approves the thing it was only
                # meant to describe.
                if refusal := self._dry_run_refusal(tool, description):
                    return refusal
                # Logged like any other run. CONFIRM is the tag
                # Executor.run_pending writes for the same event.
                self.executor.transcript.append(f"CONFIRM {description}")
                self.executor.on_action(tool, description)
                self._actions.append(description)
                return PermissionResultAllow()
            if not _is_read(tool):
                # Refused, not held: one hold at a time, and a second gated
                # action in the sentence is left for the user to ask again.
                self._note(f"DENIED  {description} (not the confirmed call)")
                return PermissionResultDeny(
                    message=("Only the call the user confirmed may run now. Do "
                             "not retry anything else; tell the user what is "
                             "still undone."),
                    interrupt=False)
            # A read takes the ordinary path below.

        if tool == "mcp__omarchy__confirm_last":
            # Consent is the user's, heard by the engine or typed at the tty,
            # never the model's to give (#86). cancel_last stays: it is safe.
            self._note(f"DENIED  {tool} (only the user can release a hold)")
            return PermissionResultDeny(
                message=("Only the user can release a held action, by voice or "
                         "key; the engine hears it, not you. Do not call this."),
                interrupt=False)

        if tool.startswith("mcp__omarchy__"):
            # Ours. `Executor.call` runs `Policy.check` itself, so checking
            # here as well would hold the same action at two gates and ask the
            # user to confirm it twice.
            self._actions.append(f"{tool.removeprefix('mcp__omarchy__')} "
                                 f"{json.dumps(tool_input or {}, default=str)[:200]}")
            return PermissionResultAllow()

        description = describe_tool(tool, tool_input or {})
        try:
            self.executor.policy.check(description, read=tool in DRY_RUN_READS)
        except Denied as exc:
            self._note(f"DENIED  {description} ({exc})")
            return PermissionResultDeny(
                message=(f"Refused: {exc}. Tell the user you will not do that. "
                         "Do not look for another route around it."),
                interrupt=False)
        except NeedsConfirmation:
            self.pending = description
            self._held_call = (tool, dict(tool_input or {}))
            self._note(f"HOLD    {description}")
            return PermissionResultDeny(
                message=(f"{description!r} {HOLD_INSTRUCTION}"),
                interrupt=False)

        # After deny and confirm, never before: a dry run may refuse more than
        # the policy does, never less, and a denied or gated action must still
        # be reported as that rather than collapsing into "dry run".
        if refusal := self._dry_run_refusal(tool, description):
            return refusal

        self.executor.transcript.append(f"RUN     {description}")
        self.executor.on_action(tool, description)
        self._actions.append(description)
        return PermissionResultAllow()

    def _dry_run_refusal(self, tool: str, description: str):
        """The refusal for an action a dry run must not take, or None.

        Both of `_decide`'s ways of saying yes go through here -- the ordinary
        one, and the release of something the user just confirmed -- so the
        rule exists once and cannot be skipped by taking the other door.

        Refused rather than simulated because refusing is all this callback
        can do: the SDK has no way to hand back a fake result. So this is a
        described refusal where Executor's dry run is a simulation, and the
        wording is what stops the model treating it as an obstacle to solve.
        """
        if not self.config.dry_run or tool in DRY_RUN_READS:
            return None
        self._note(f"DRYRUN  {description}")
        return PermissionResultDeny(
            message=(f"[dry-run] would have run: {description}. Nothing was "
                     "done, because this is a dry run. Do not try another "
                     "route around it — tell the user what you would have "
                     "done."),
            interrupt=False)

    def _note(self, line: str) -> None:
        """Record a call that did not run, in the transcript and in the log.

        Only for what did not run. RUN and CONFIRM stay plain appends, because
        executor.on_action already logs them -- sending them here as well would
        write every allowed call to the log twice. The sink is the Executor's
        (#13), so this brain logs through the same route as every backend.
        """
        self.executor.record(line)

    async def _pre_tool_use(self, hook_input, tool_use_id, context) -> dict:
        """Every tool call Claude Code makes, through our policy.

        A PreToolUse hook, not the permission callback, because the callback is
        Claude Code's *ask* path: it is only consulted when the CLI would
        otherwise prompt. A Read inside the working directory, or an
        EnterWorktree, is approved without asking -- measured on CLI 2.1.274,
        where a deny rule on a path let a Read of it straight through, and a
        dry run created a real git worktree. This hook runs for all of them.

        Always an explicit allow or deny, never no answer. With no answer the
        call carries on to the permission callback and is evaluated a second
        time -- spending a one-shot confirmation here and holding the replay
        there. An explicit answer is final.

        And never an exception. A hook that raises is logged by the CLI and the
        call RUNS: measured, not assumed. For an auto-approved call nothing
        stands behind this, so a bug in a regex or in describe_tool would
        otherwise open every one of them. Anything that goes wrong is a deny.
        """
        tool = hook_input.get("tool_name", "") if isinstance(hook_input, dict) else ""
        try:
            result = await self._decide(tool, hook_input.get("tool_input") or {})
            allowed = result.behavior == "allow"
            reason = getattr(result, "message", "") or "allowed by policy"
        except Exception as exc:
            self._note(f"ERROR   {tool} ({type(exc).__name__}: {exc})")
            allowed, reason = False, ("Refused: the safety check failed on this "
                                      "call, so it was not run. Tell the user.")
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow" if allowed else "deny",
            "permissionDecisionReason": reason,
        }}

    async def _alarm(self, tool: str, tool_input: dict, ctx):
        """The permission callback, which should never be called.

        `_pre_tool_use` answers every call explicitly, and an explicit answer
        means Claude Code does not ask -- so reaching here means the hook did
        not decide. That is a fault, not a question to answer: refuse, and say
        so in the log where it can be found. Registered rather than left out so
        an undecided call meets this refusal instead of whatever the CLI does
        with a prompt nobody answers.
        """
        self._note(f"ALARM   {tool} reached can_use_tool; the hook did not decide")
        return PermissionResultDeny(
            message="Refused: this call skipped the safety check. It was not run.",
            interrupt=False)

    # -- the turn -----------------------------------------------------------
    def _options(self):
        from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

        binary = cli_path(self.config)
        if not binary:
            raise PlannerUnavailable(
                f"no claude CLI: {CLI_ENV} is unset and none is on PATH", NO_CLI)

        # Our tools, in-process, from the one implementation that exists.
        #
        # Caveat worth knowing about: on mcp 1.x an in-process tool is NOT
        # cancelled when Claude Code abandons the call. Several of ours
        # block for seconds — grim and tesseract for a screen read, tmux
        # for a command — and those run to completion regardless.
        # Pass ours. Without it build_server makes a second Executor, and a
        # session then has two: the caller's, which holds the trace and which
        # `say` checks for a pending hold, and this one, which actually runs
        # every tool. One session, one Executor (#43).
        servers = {"omarchy": {"type": "sdk", "name": "omarchy",
                               # Show the model our schemas up front. Without
                               # this, Claude Code defers every MCP tool and the
                               # first use of each costs a ToolSearch round trip
                               # (#84). Verified on CLI 2.1.281; not in the SDK's
                               # typed McpSdkServerConfig, but the SDK passes
                               # every key except `instance` to the CLI.
                               "alwaysLoad": True,
                               "instance": mcp_server.build_server(self.config,
                                                                   self.executor)}}
        # The engine hears consent, not the model (#86): after build_server,
        # which sets the MCP client's wording on this same Executor.
        self.executor.confirm_instruction = f"This action {HOLD_INSTRUCTION}"
        # The desktop is not in here: it goes in front of every turn (#69).
        prompt = planner._system_prompt(live=False)
        # Installed is not wanted: the desktop is handed over only if asked for (#17).
        if self.config.desktop_control and (ai_mirror := ai_mirror_path()):
            servers["ai-mirror"] = {"type": "stdio", "command": ai_mirror, "args": ["mcp"]}
            prompt = f"{prompt}\n\n{AI_MIRROR_PROMPT}"

        return ClaudeAgentOptions(
            system_prompt=prompt,
            mcp_servers=servers,
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
            # The policy. Unfiltered (matcher=None), so a tool Claude Code
            # adds next month is checked the day it arrives. WarmBrain and
            # local_engine's LocalBrain build on this via super(), so every
            # brain gets it.
            hooks={"PreToolUse": [HookMatcher(matcher=None,
                                              hooks=[self._pre_tool_use])]},
            # Not the policy: a tripwire. The hook answers every call, so this
            # only runs if it failed to. See _alarm.
            can_use_tool=self._alarm,
            # Never "bypassPermissions": it shadows can_use_tool entirely (the
            # SDK warns about exactly this), and nothing here should depend on
            # finding out whether it shadows hooks too.
            permission_mode="default",
            model=getattr(self.config, "claude_model", "") or DEFAULT_MODEL,
            cli_path=binary,
            max_turns=self.config.max_turns,
            cwd=getattr(self.config, "claude_cwd", "") or os.path.expanduser("~"),
            env=self._child_env(),
        )

    def _child_env(self) -> dict:
        """Environment overrides for the Claude Code subprocess.

        Blanking ANTHROPIC_API_KEY is the whole point of this backend, not a
        detail. The CLI prefers an API key over the claude.ai login whenever
        one is set, so on a machine that exports ANTHROPIC_API_KEY for anything
        else -- and this one does -- every turn silently billed the API instead
        of the subscription. Measured before this line existed: a single "what
        workspace am I on" cost $0.1255 and printed "claude.ai connectors are
        disabled because ANTHROPIC_API_KEY ... takes precedence over your
        claude.ai login". A feature whose entire purpose is to stop paying per
        token was paying per token.

        Blanked rather than removed because the SDK merges this dict over the
        inherited environment (`{**inherited, **options.env}`), so a key cannot
        be deleted through it. The empty string is treated as absent by the
        CLI -- verified by running it both ways against this machine's login.

        Set claude_use_subscription = false to leave the environment alone,
        which is what an account with an API key and no subscription needs.
        """
        if not getattr(self.config, "claude_use_subscription", True):
            return {}
        return {"ANTHROPIC_API_KEY": ""}

    async def _ask(self, text: str, turn: Turn) -> None:
        from claude_agent_sdk import (AssistantMessage, ClaudeSDKClient,
                                      ResultMessage)

        options = self._options()
        self._actions = []
        reply = ""
        async with ClaudeSDKClient(options=options) as client:
            await client.query(_with_desktop(text))
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if said := (getattr(block, "text", None) or "").strip():
                            reply = said
                elif isinstance(message, ResultMessage):
                    turn.tokens = _usage(message)
                    if message.is_error and not reply:
                        raise PlannerUnavailable(
                            f"claude returned an error: {message.subtype}",
                            "Claude Code had trouble with that.")
        turn.actions = list(self._actions)
        turn.reply = reply or ("That needs confirmation." if self.pending else "Done.")


def _usage(message) -> dict:
    """Tokens and cost off a ResultMessage.

    `cost` is NOT a charge. The CLI reports what the turn would have cost
    through the API, whether or not that is what happens, so on a subscription
    it reads as a real number against a bill nobody is paying -- around eleven
    cents a turn here. An earlier version of this docstring claimed it should
    read ~0 on a plan, which is simply false, and it cost somebody an
    investigation into a regression that was not there.

    That the plan is what pays was established by running a turn with a
    deliberately invalid ANTHROPIC_API_KEY: it still answered, which is only
    possible on the claude.ai login. See `_child_env`, which blanks the key
    for the subprocess precisely so that stays true. Callers that show this
    number should say whose money it is -- `cmd_say` prints "~$X on your plan".
    """
    usage = getattr(message, "usage", None) or {}
    return {
        "in": usage.get("input_tokens", 0),
        "out": usage.get("output_tokens", 0),
        "cached": usage.get("cache_read_input_tokens", 0),
        "cost": getattr(message, "total_cost_usd", None) or 0.0,
    }


# -- the warm brain ---------------------------------------------------------
#
# The approach below -- one client held open for the session, partial-message
# streaming, and the interrupt-then-drain fix for the shared message pipe --
# was learned from backtalk by Jared Rhodenizer (AGPL-3.0),
# https://github.com/jaredrhod/backtalk. Written fresh here; the debt is his.

import re

_SENTENCE_END = re.compile(r"(?<=[.!?])\s")

NO_SESSION = "Claude Code isn't running."

# Answerable in one word, with nothing in it that touches this machine and
# nothing a later turn could mistake for an instruction.
WARM_UP = "Reply with one word: ready."


def _with_desktop(text: str, from_user: bool = True) -> str:
    """What the user said, behind the desktop as it is at this turn (#69).

    The system prompt is built once and a warm session lives for days, so a
    snapshot kept there was days old. Here it is fresh every turn, and the
    cached prefix in front of it never changes.

    `from_user=False` is a turn nobody spoke, a finished watch (#74): it
    carries its own heading, and "What the user said" would put words in
    their mouth.
    """
    # ponytail: old snapshots stay in the session history, ~560 tokens a turn,
    # left to Claude Code's compaction. If `usage` shows uncached input growing
    # turn over turn, restart the warm session when idle-stop fires.
    said = "# What the user said\n\n" if from_user else ""
    return ("# The desktop right now (current as of this turn; "
            "supersedes every earlier snapshot)\n\n"
            f"{capabilities.live_state()}\n\n{said}{text}")


def _sentences(buffer: str) -> tuple[list[str], str]:
    """Complete sentences out of a growing buffer, and what is left over."""
    done = []
    while match := _SENTENCE_END.search(buffer):
        sentence, buffer = buffer[:match.end()].strip(), buffer[match.end():]
        if sentence:
            done.append(sentence)
    return done, buffer


class WarmBrain(ClaudeBrain):
    """One Claude Code session, held open, answering a sentence at a time.

    `ClaudeBrain.think()` spawns a CLI per turn: 6.4-9.2s on this machine.
    Fine for `say`, fatal for a conversation, where the whole latency budget
    is about two seconds and whisper has already spent 1.5 of it. So the
    process is started once and the reply is cut into sentences as it
    streams, which means the mouth can open before the thought is finished.

    Everything safety-critical is inherited, deliberately: the policy gate,
    the options, the blanked API key. There is one gate in this file and this
    class must not become a second one.
    """

    def __init__(self, config: Config, executor: Executor):
        super().__init__(config, executor)
        self._client = None
        # True from the moment a query goes out until its ResultMessage is
        # consumed -- i.e. while the shared pipe may still hold that turn's
        # leftovers. `reset_turn` is a no-op unless this is set.
        self._dirty = False
        self._usage = {"in": 0, "out": 0, "cost": 0.0, "turns": 0}
        # What the router ran without asking (#71), for the next turn.
        self._notes: list[str] = []

    def note(self, line: str) -> None:
        """Tell the model, on its next turn, about a command run without it.

        Not `ClaudeBrain._note`, which records a refusal to the log. Without
        this the session never hears that "close the weather window" happened,
        and "open it again" has nothing to refer to.
        """
        self._notes.append(line)

    @property
    def usage(self) -> dict:
        return dict(self._usage)

    def _options(self):
        options = super()._options()
        # The only difference from a cold turn. Without it the SDK delivers
        # one finished AssistantMessage and there is nothing to stream.
        options.include_partial_messages = True
        return options

    async def start(self, warm_up: bool = True) -> None:
        try:
            client = _new_client(self._options())
            await client.connect()
        except PlannerUnavailable:
            raise  # already has a spoken line -- no CLI, no login
        except Exception as exc:
            raise PlannerUnavailable(
                f"claude would not start: {type(exc).__name__}: {exc}", NO_SESSION)
        self._client = client
        self._dirty = False
        if warm_up:
            await self._warm_up()

    async def _warm_up(self) -> None:
        """One throwaway turn, so nobody waits through the first real one.

        Measured: 3.97s to the first sentence on the opening turn against
        1.52s once the session is running. The daemon connects at login and
        then sits idle until somebody speaks, so that 2.4s is free to spend
        here and expensive to spend in front of a person waiting for an
        answer.

        Deliberately a question with no desktop in it: it must not reach a
        tool, must not land in `turn.actions`, and must not leave anything
        behind that a later turn could read as an instruction. A failure is
        written to the transcript and dropped -- what that costs is a slower
        first turn, and it must never be a daemon that would not start.
        """
        try:
            await asyncio.wait_for(self._drain_query(WARM_UP), 30)
        except Exception as exc:
            self.executor.transcript.append(f"WARMUP  failed ({exc!r})")
            # The pipe may be holding the warm-up's leftovers, and a turn that
            # inherits those answers the warm-up instead of the user. A fresh
            # client cannot have leftovers. No second warm-up: a CLI failing
            # this way would otherwise retry for ever.
            await self.stop()
            await self.start(warm_up=False)
        finally:
            # Nothing here happened on the user's behalf.
            self._actions = []

    async def _drain_query(self, text: str) -> None:
        """Ask, and read to the end without keeping any of it.

        No `_tally`: a warm-up is not a turn the user took, and counting it
        would put a question nobody asked into the usage report.
        """
        self._dirty = True
        await self._client.query(text)
        async for message in self._client.receive_response():
            if type(message).__name__ == "ResultMessage":
                break
        self._dirty = False

    async def stop(self) -> None:
        client, self._client = self._client, None
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass  # shutting down; nothing left to salvage

    async def reset_turn(self, timeout: float = 8.0) -> None:
        """Re-align the pipe after a turn that was cut off mid-stream.

        The SDK client has ONE message stream and `receive_response()` stops
        at the first ResultMessage it sees -- there is no pairing between a
        query and its answer. Abandon a turn mid-stream and its ResultMessage
        stays buffered; the next turn then ends on that stale result and
        yields the *previous* answer, and every turn after it is one behind,
        for the rest of the session. Nothing errors. It just quietly lies.

        So: interrupt the dead turn, drain the pipe through its ResultMessage,
        and if that cannot be done, throw the session away and build a new
        one. A rebuild costs this session's conversation memory, which is far
        cheaper than answering yesterday's question all day.
        """
        if not self._client or not self._dirty:
            return
        try:
            await asyncio.wait_for(self._client.interrupt(), 5)
        except Exception:
            pass  # the turn may already be over; the drain is the point

        async def drain():
            async for message in self._client.receive_response():
                if type(message).__name__ == "ResultMessage":
                    return

        try:
            await asyncio.wait_for(drain(), timeout)
            self._dirty = False
        except Exception:
            await self.stop()
            await self.start(warm_up=False)

    async def ask_stream(self, text: str, *, release: bool = False,
                         from_user: bool = True):
        """Complete sentences, as they are produced.

        Never raises for an ordinary failure -- same discipline as `think()`.
        A voice loop that dies on one bad turn is a deaf one, so a failure
        comes back as something to say.

        `release=True` for the message `confirm()` returned: the one turn in
        which that approval may be spent (#76).
        """
        self._releasing = release
        try:
            if self._client is None:
                yield NO_SESSION
                return
            self._actions = []
            spoke = False
            try:
                async for sentence in self._turn(text, from_user=from_user):
                    spoke = True
                    yield sentence
            except PlannerUnavailable as exc:
                yield exc.spoken
            except Exception:
                yield "Something went wrong with that."
            else:
                # Not "Done." when the one thing asked for did not happen.
                if not spoke and not (release and self._approved):
                    # Not "confirmation": she must never say the word (#86).
                    yield "That is waiting for you." if self.pending else "Done."
            if release and (left := self._unspent()):
                yield left
        finally:
            # The approval dies with its release turn, used or not --
            # including one cancelled by a barge-in, which lands here and
            # yields nothing. Only a release turn clears it: the user can
            # confirm while an ordinary turn is still running, and that
            # turn ending must not throw away the yes queued behind it.
            self._releasing = False
            if release:
                self._approved = None

    async def _turn(self, text: str, *, from_user: bool = True):
        # Before _dirty: a turn cancelled while this runs has sent nothing, so
        # there is nothing in the pipe for reset_turn to drain.
        text = await asyncio.to_thread(_with_desktop, text, from_user)
        if self._notes:
            done = "\n".join(f"- {n}" for n in self._notes)
            text = ("# Done without you since your last turn "
                    f"(already run, do not repeat)\n\n{done}\n\n{text}")
            self._notes = []
        self._dirty = True
        await self._client.query(text)
        buffer = ""
        streamed = spoke = False
        async for message in self._client.receive_response():
            kind = type(message).__name__
            if kind == "StreamEvent":
                event = getattr(message, "event", None) or {}
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        streamed = True
                        buffer += delta.get("text", "")
                        done, buffer = _sentences(buffer)
                        for sentence in done:
                            spoke = True
                            yield sentence
                elif event.get("type") == "content_block_stop":
                    # End of a block of speech -- usually right before a tool
                    # call. Flush now, or "let me go and look" sits mute in
                    # the buffer for the whole tool run and then plays glued
                    # to the answer: dead air, then two thoughts at once.
                    tail, buffer = buffer.strip(), ""
                    if tail:
                        spoke = True
                        yield tail
            elif kind == "AssistantMessage":
                # The finished blocks. Only spoken if no deltas arrived at
                # all -- otherwise this is the same text a second time.
                if streamed:
                    continue
                for block in message.content:
                    buffer += getattr(block, "text", None) or ""
                done, buffer = _sentences(buffer)
                for sentence in done:
                    spoke = True
                    yield sentence
            elif kind == "ResultMessage":
                self._dirty = False  # consumed through the end; pipe aligned
                self._tally(message)
                if getattr(message, "is_error", False) and not spoke:
                    raise PlannerUnavailable(
                        f"claude returned an error: {message.subtype}",
                        "Claude Code had trouble with that.")
                break
        tail = buffer.strip()
        if tail:
            yield tail

    def _tally(self, message) -> None:
        """Running session usage. Must never cost a turn."""
        try:
            turn = _usage(message)
            self._usage["in"] += turn["in"] + turn["cached"]
            self._usage["out"] += turn["out"]
            self._usage["cost"] += turn["cost"]
            self._usage["turns"] += 1
        except Exception:
            pass


def _new_client(options):
    """The SDK client, behind a seam the tests can stand in for."""
    from claude_agent_sdk import ClaudeSDKClient

    return ClaudeSDKClient(options=options)
