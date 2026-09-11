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

from . import capabilities
from .config import Config, load_env_file
from .tools import Executor, tools_for

SERVER_NAME = "omarchy-voice"


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


def build_server(config: Config):
    from mcp.server import Server
    from mcp.types import TextContent

    executor = Executor(config)
    # The gate is the same one the voice session uses; only the sentence it
    # hands back changes, because this caller has a conversation rather than a
    # microphone.
    executor.confirm_instruction = (
        "This action needs the user's confirmation. Stop here, ask them in "
        "this conversation, and call confirm_last once they agree. Do not try "
        "another route around it.")
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools():
        return _to_mcp_tools(tools_for(config))

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list:
        # Executor is synchronous and some tools block for seconds -- grim and
        # tesseract for a screen read, tmux for a command. Off the event loop,
        # or the server stops answering while one runs.
        result = await asyncio.to_thread(executor.call, name, arguments or {})
        return [TextContent(type="text", text=result.as_tool_result())]

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
