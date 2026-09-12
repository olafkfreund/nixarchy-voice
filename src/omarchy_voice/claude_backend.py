"""The typed path, driven by Claude Code instead of the OpenAI API.

Same persona, same tools, same policy gate as `planner.Planner` — only the
engine underneath changes. Turns run through the `claude` CLI via the Claude
Agent SDK, so they bill against the user's Claude subscription rather than an
API key with a balance on it.

Two things come along with that engine, and both matter here:

  * Claude Code brings its own toolset — Bash, Write, Edit, Read, WebFetch.
    Ours are offered alongside them as an in-process MCP server (the very
    same `mcp_server.build_server`, so there is still one implementation of
    every tool). Claude Code's own tools have never seen our `Policy`, which
    is what `_gate` below is for.
  * The SDK is async-only and `think()` is not, because everything that calls
    it — `say`, the realtime session's typed fallback — is synchronous. One
    `asyncio.run` per turn is the whole of the bridge.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path

from dataclasses import dataclass

from . import mcp_server, planner
from .config import Config
from .planner import PlannerUnavailable, Turn
from .tools import Denied, Executor, NeedsConfirmation

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

# Tools whose whole job is to read. Described rather than run through a
# separate path: the policy gate sees them like anything else, and a deny rule
# aimed at a path should still catch a read of it.
_PATH_TOOLS = {"Write": "write", "Edit": "edit", "NotebookEdit": "edit",
               "Read": "read", "Glob": "list", "Grep": "search"}


def cli_path(config: Config) -> str:
    """The `claude` binary to drive, or "" if there is not one."""
    return (os.environ.get(CLI_ENV, "")
            or getattr(config, "claude_cli", "")
            or shutil.which("claude") or "")


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
    return f"{tool} {rest[:400]}"


class ClaudeBrain:
    """`Planner`'s twin, with Claude Code underneath."""

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
        self._confirmed: set[str] = set()
        # What the gate let through this turn, in order. Filled here rather
        # than from the assistant's tool_use blocks because those are written
        # before the gate has spoken: a denied action would be reported as
        # something that happened.
        self._actions: list[str] = []

    def confirm(self) -> str | None:
        """The user said yes. The next attempt at that exact action goes through."""
        held, self.pending = self.pending, None
        if held:
            self._confirmed.add(held)
        return held

    def cancel(self) -> str | None:
        held, self.pending = self.pending, None
        return held

    def think(self, text: str) -> Turn:
        turn = Turn(text=text)
        started = time.monotonic()
        try:
            asyncio.run(self._ask(text, turn))
        except PlannerUnavailable as exc:
            turn.error = str(exc)
            turn.reply = exc.spoken
        except Exception as exc:  # a voice tool must not die on one bad turn
            turn.error = f"{type(exc).__name__}: {exc}"
            turn.reply = "Something went wrong with that."
        turn.elapsed = time.monotonic() - started
        return turn

    # -- the gate -----------------------------------------------------------
    async def _gate(self, tool: str, tool_input: dict, ctx):
        """Every tool call Claude Code makes, through our policy.

        This is the only thing standing between a spoken sentence and Bash.
        `allow_shell = false` and the shutdown/reboot confirm patterns mean
        exactly as much as this function does.
        """
        if tool.startswith("mcp__omarchy__"):
            # Ours. `Executor.call` runs `Policy.check` itself, so checking
            # here as well would hold the same action at two gates and ask the
            # user to confirm it twice.
            self._actions.append(f"{tool.removeprefix('mcp__omarchy__')} "
                                 f"{json.dumps(tool_input or {}, default=str)[:200]}")
            return PermissionResultAllow()

        description = describe_tool(tool, tool_input or {})
        if description in self._confirmed:
            return PermissionResultAllow()

        try:
            self.executor.policy.check(description)
        except Denied as exc:
            self.executor.transcript.append(f"DENIED  {description} ({exc})")
            return PermissionResultDeny(
                message=(f"Refused: {exc}. Tell the user you will not do that. "
                         "Do not look for another route around it."),
                interrupt=False)
        except NeedsConfirmation:
            self.pending = description
            self.executor.transcript.append(f"HOLD    {description}")
            return PermissionResultDeny(
                message=(f"{description!r} needs the user's confirmation first. "
                         "Stop here and ask them to confirm it out loud; do not "
                         "try another route around it."),
                interrupt=False)

        self.executor.transcript.append(f"RUN     {description}")
        self.executor.on_action(tool, description)
        self._actions.append(description)
        return PermissionResultAllow()

    # -- the turn -----------------------------------------------------------
    def _options(self):
        from claude_agent_sdk import ClaudeAgentOptions

        binary = cli_path(self.config)
        if not binary:
            raise PlannerUnavailable(
                f"no claude CLI: {CLI_ENV} is unset and none is on PATH", NO_CLI)

        return ClaudeAgentOptions(
            system_prompt=planner._system_prompt(),
            # Our tools, in-process, from the one implementation that exists.
            #
            # Caveat worth knowing about: on mcp 1.x an in-process tool is NOT
            # cancelled when Claude Code abandons the call. Several of ours
            # block for seconds — grim and tesseract for a screen read, tmux
            # for a command — and those run to completion regardless.
            mcp_servers={"omarchy": {"type": "sdk", "name": "omarchy",
                                     "instance": mcp_server.build_server(self.config)}},
            can_use_tool=self._gate,
            # Never "bypassPermissions": it shadows can_use_tool entirely (the
            # SDK warns about exactly this), which would leave Bash ungated.
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
            await client.query(text)
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

    async def ask_stream(self, text: str):
        """Complete sentences, as they are produced.

        Never raises for an ordinary failure -- same discipline as `think()`.
        A voice loop that dies on one bad turn is a deaf one, so a failure
        comes back as something to say.
        """
        if self._client is None:
            yield NO_SESSION
            return
        self._actions = []
        spoke = False
        try:
            async for sentence in self._turn(text):
                spoke = True
                yield sentence
        except PlannerUnavailable as exc:
            yield exc.spoken
        except Exception:
            yield "Something went wrong with that."
        else:
            if not spoke:
                yield "That needs confirmation." if self.pending else "Done."

    async def _turn(self, text: str):
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
