"""The desktop, as an MCP server.

The same tools the voice session drives, offered to any MCP client -- Claude
Code, Codex, anything else that speaks the protocol. `omarchy-voice mcp` on
stdio:

    claude --mcp-config '{"mcpServers":{"omarchy":{"command":"omarchy-voice","args":["mcp"]}}}'

Nothing here re-implements a tool. The schemas are the ones the model already
sees and `Executor` is the same object the realtime session calls, so an action
taken through an agent goes through the same policy gate, the same confirm
hold, and the same transcript as one taken by voice. A second implementation
would be a second set of rules to keep in step, and the gate is the one place
that must not drift.

The manifest is offered as a resource rather than pushed into every prompt.
An MCP client has its own context to manage and asks when it wants it, which
is the opposite of the voice session's problem -- there, everything must be in
front of the model on every single turn.
"""

from __future__ import annotations

import asyncio
import base64

import time

from . import capabilities
from .config import Config, load_env_file
from .session import _matches
from .tools import attach_waker, Executor, tools_for

SERVER_NAME = "omarchy-voice"

# How long after a hold a confirmation is believed.
#
# The voice session refuses a confirmation that arrives in the same response as
# the hold, or without a new user turn, because it can see turns. Here there
# are none: two tool calls arrive and nothing says what happened between them.
# Elapsed time is the only part of "the user was actually asked" this server
# can observe. Someone reading a reboot prompt and typing an answer takes
# longer than this; an agent chaining a confirm onto its own hold takes
# milliseconds.
#
# ponytail: a heuristic, and it only catches the careless case. The real fix is
# to ask the user through the client -- MCP elicitation -- at which point this
# constant goes away rather than being tuned.
CONFIRM_DELAY = 2.0

# Offered over MCP only. The voice session has its own pair in
# realtime.GATE_TOOLS, worded for someone who is speaking; these are for a
# caller who has the user in a conversation. The logic behind both is the same
# Executor, which is the part that must not be written twice.
GATE_SCHEMAS = [
    {
        "name": "confirm_last",
        "description": (
            "Release the action the safety gate is holding, after the user has "
            "agreed to it. Ask them first, in this conversation, and wait for their "
            "answer -- do not call this in the same reply that was held, and never "
            "on your own judgement that they would agree. Pass their words as they "
            "wrote them; this machine checks them against its own confirmation "
            "phrases and refuses if they do not match."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phrase": {
                    "type": "string",
                    "description": "Verbatim what the user just said, e.g. \"confirm\".",
                },
            },
            "required": ["phrase"],
            "additionalProperties": False,
        },
    },
    {
        "name": "cancel_last",
        "description": (
            "Drop the action the safety gate is holding, because the user declined "
            "it or asked for something else instead. Nothing runs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]


def _to_mcp_tools(schemas: list[dict]):
    """Our tool list in MCP's shape.

    The only difference is the key holding the schema: `input_schema` here,
    `inputSchema` there.
    """
    from mcp.types import Tool

    return [
        Tool(
            name=schema["name"],
            description=schema["description"],
            inputSchema=schema["input_schema"],
        )
        for schema in schemas
    ]


def build_server(config: Config, executor: Executor | None = None):
    """The server. `executor` is for tests, which need to see the hold it holds."""
    from mcp.server import Server
    from mcp.types import TextContent

    executor = executor if executor is not None else attach_waker(Executor(config))
    # The gate is the same one the voice session uses; only the sentence it
    # hands back changes, because this caller has a conversation rather than a
    # microphone.
    executor.confirm_instruction = (
        "This action needs the user's confirmation. Stop here and ask them in "
        "this conversation. When they answer, call confirm_last with their own "
        "words, or cancel_last if they decline. Do not try another route around "
        "it.")
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools():
        return _to_mcp_tools(tools_for(config) + GATE_SCHEMAS)

    def _confirm(phrase: str) -> str | None:
        """Why this confirmation is refused, or None to go ahead and run it.

        Each refusal leaves `pending` alone. A bad phrase or an over-eager
        agent costs a round trip; losing the action the user is in the middle
        of approving would cost the whole exchange.
        """
        if not executor.pending:
            return "ERROR: nothing is waiting for confirmation. Do not call this again."
        held = executor.describe(*executor.pending)
        waited = time.monotonic() - (executor.pending_since or 0.0)
        if waited < CONFIRM_DELAY:
            return (f"ERROR: {held} was held a moment ago and has not been put to the "
                    "user yet. Ask them, wait for their answer, then call this again "
                    "with what they said.")
        if not _matches(phrase, config.confirm_words, allow_negation=False):
            phrases = ", ".join(f'"{w}"' for w in config.confirm_words)
            return (f"ERROR: {phrase!r} is not a confirmation phrase, so {held} is "
                    f"still held. Ask the user to say one of: {phrases}.")
        return None  # caller runs it; see call_tool

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list:
        arguments = arguments or {}
        if name == "cancel_last":
            dropped = executor.drop_pending()
            text = ("Nothing was being held." if dropped is None
                    else f"Cancelled: {dropped}. It was not run.")
            return [TextContent(type="text", text=text)]
        if name == "confirm_last":
            refusal = _confirm(str(arguments.get("phrase", "")))
            if refusal is not None:
                return [TextContent(type="text", text=refusal)]
            result = await asyncio.to_thread(executor.run_pending)
            return [TextContent(type="text", text=result.as_tool_result())]
        # Executor is synchronous and some tools block for seconds -- grim and
        # tesseract for a screen read, tmux for a command. Off the event loop,
        # or the server stops answering while one runs.
        result = await asyncio.to_thread(executor.call, name, arguments)
        text = TextContent(type="text", text=result.as_tool_result())
        if result.image is None:
            return [text]
        # The only path that returns pixels (screenshot, #58). The text line
        # still goes first, so a client that cannot render an image is told
        # what was captured rather than handed nothing.
        from mcp.types import ImageContent

        return [text, ImageContent(type="image",
                                   data=base64.b64encode(result.image).decode(),
                                   mimeType="image/png")]

    @server.list_resources()
    async def list_resources():
        from mcp.types import Resource
        from pydantic import AnyUrl

        return [
            Resource(
                uri=AnyUrl("omarchy://manifest"),
                name="This machine",
                description=(
                    "What Omarchy and Hyprland can do here, read off the running "
                    "system: the CLI surface, the dispatcher API for the installed "
                    "Hyprland, and the apps that exist. Read it before driving the "
                    "desktop -- the syntax changes between versions and this is the "
                    "version that is running."
                ),
                mimeType="text/markdown",
            ),
            Resource(
                uri=AnyUrl("omarchy://state"),
                name="The desktop right now",
                description=(
                    "Monitors, workspaces, the focused window and every open window "
                    "with its address. Re-read it rather than remembering: windows "
                    "move and close, and only the newest snapshot is true."
                ),
                mimeType="text/markdown",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri) -> str:
        match str(uri):
            case "omarchy://manifest":
                return await asyncio.to_thread(capabilities.manifest)
            case "omarchy://state":
                return await asyncio.to_thread(capabilities.live_state)
        raise ValueError(f"no such resource: {uri}")

    return server


async def _serve(config: Config) -> None:
    from mcp.server.stdio import stdio_server

    server = build_server(config)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def run(config: Config) -> int:
    """Entry point for `omarchy-voice mcp`.

    stdio is the transport, so nothing may be printed to stdout that is not a
    protocol message -- a stray print is a parse error at the client, which
    reports it as the server failing to start.
    """
    try:
        import mcp  # noqa: F401
    except ImportError:
        import sys

        print("the mcp package is not installed — this is packaged with "
              "python3Packages.mcp; running the module directly is not enough",
              file=sys.stderr)
        return 1

    load_env_file()
    try:
        asyncio.run(_serve(config))
    except KeyboardInterrupt:
        pass
    return 0
