"""The desktop offered over MCP.

The point of these is that there is no second implementation. The schemas are
the ones the model already sees and the executor is the one the voice session
calls, so what needs checking is that nothing was copied: a tool list that
drifts, or a policy gate that only holds on one of the two paths, is the
failure this design exists to prevent.

Run with: python3 -m unittest discover -s tests
"""

import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice import mcp_server
from omarchy_voice.config import Config
from omarchy_voice.tools import Executor, tools_for

try:
    import mcp  # noqa: F401
except ImportError:  # pragma: no cover - depends on the machine
    mcp = None


@unittest.skipIf(mcp is None, "the mcp package is not installed")
class ToolExposureTests(unittest.TestCase):
    def test_every_tool_is_offered_and_none_invented(self):
        config = Config()
        ours = {t["name"] for t in tools_for(config)}
        theirs = {t.name for t in mcp_server._to_mcp_tools(tools_for(config))}
        self.assertEqual(ours, theirs)

    def test_the_schema_survives_the_rename(self):
        # input_schema here, inputSchema there. A tool whose schema is dropped
        # still appears in the list and fails on every call.
        schemas = tools_for(Config())
        converted = {t.name: t for t in mcp_server._to_mcp_tools(schemas)}
        for schema in schemas:
            with self.subTest(tool=schema["name"]):
                self.assertEqual(converted[schema["name"]].inputSchema,
                                 schema["input_schema"])

    def test_the_shell_tool_stays_behind_its_setting(self):
        # allow_shell gates a tool that runs arbitrary commands. Offering it
        # over MCP regardless would be a way around the setting.
        off = {t.name for t in mcp_server._to_mcp_tools(tools_for(Config(allow_shell=False)))}
        on = {t.name for t in mcp_server._to_mcp_tools(tools_for(Config(allow_shell=True)))}
        self.assertNotIn("run_shell", off)
        self.assertIn("run_shell", on)


class ConfirmWordingTests(unittest.TestCase):
    """A held action has to ask in terms its caller can act on."""

    def test_the_voice_default_asks_for_speech(self):
        self.assertIn("out loud", Executor(Config()).confirm_instruction)

    def test_the_gate_still_holds_and_says_why(self):
        executor = Executor(Config(dry_run=True))
        result = executor.call("omarchy_cli", {"command": "reboot"})
        self.assertFalse(result.ok)
        self.assertIsNotNone(executor.pending)
        self.assertIn(executor.confirm_instruction, result.output)

    @unittest.skipIf(mcp is None, "the mcp package is not installed")
    def test_over_mcp_it_asks_in_the_conversation_instead(self):
        # Telling a text agent to wait for speech leaves it either stuck or
        # hunting for a way around the gate.
        mcp_server.build_server(Config())
        # build_server sets it on its own executor; check the sentence itself
        # rather than reaching into the closure.
        executor = Executor(Config())
        executor.confirm_instruction = (
            "This action needs the user's confirmation. Stop here, ask them in "
            "this conversation, and call confirm_last once they agree. Do not "
            "try another route around it.")
        self.assertNotIn("out loud", executor.confirm_instruction)
        self.assertIn("confirm_last", executor.confirm_instruction)
        # The clause that closed the shell workaround: an agent offered a
        # refusal without it reached for `systemctl reboot` through a terminal.
        self.assertIn("another route around it", executor.confirm_instruction)


@unittest.skipIf(mcp is None, "the mcp package is not installed")
class GateReleaseTests(unittest.TestCase):
    """A held action can be carried to a decision without leaving the client.

    Driven over the real protocol rather than by calling the handlers, because
    the bug this fixes was a tool that the server talked about and never
    offered -- exactly what a direct call would have hidden.
    """

    def setUp(self):
        # dry_run so a confirmed action reports itself instead of rebooting
        # the machine running the tests.
        self.executor = Executor(Config(dry_run=True))
        self.server = mcp_server.build_server(Config(dry_run=True), self.executor)

    def _run(self, coro):
        import asyncio

        return asyncio.run(coro)

    async def _session(self, body):
        from mcp.shared.memory import create_connected_server_and_client_session

        async with create_connected_server_and_client_session(self.server) as client:
            return await body(client)

    def call(self, name, arguments=None):
        async def body(client):
            result = await client.call_tool(name, arguments or {})
            return result.content[0].text

        return self._run(self._session(body))

    def hold(self):
        """Put a gated action in the gate and return what it said."""
        return self.call("omarchy_cli", {"command": "omarchy update"})

    def answered(self):
        """Pretend the user has been asked and has answered."""
        self.executor.pending_since -= mcp_server.CONFIRM_DELAY + 1

    def test_both_gate_tools_are_offered(self):
        async def body(client):
            return {t.name for t in (await client.list_tools()).tools}

        offered = self._run(self._session(body))
        self.assertIn("confirm_last", offered)
        self.assertIn("cancel_last", offered)

    def test_the_hold_names_a_way_out(self):
        held = self.hold()
        self.assertIsNotNone(self.executor.pending)
        self.assertIn("confirm_last", held)
        self.assertIn("cancel_last", held)

    def test_the_users_word_releases_it(self):
        self.hold()
        self.answered()
        out = self.call("confirm_last", {"phrase": "confirm"})
        self.assertIn("[dry-run]", out)
        self.assertIn("omarchy update", out)
        self.assertIsNone(self.executor.pending)
        self.assertIsNone(self.executor.pending_since)

    def test_a_phrase_that_is_not_agreement_keeps_it_held(self):
        self.hold()
        self.answered()
        out = self.call("confirm_last", {"phrase": "maybe later"})
        self.assertIn("not a confirmation phrase", out)
        self.assertIn('"confirm"', out)
        self.assertIsNotNone(self.executor.pending)

    def test_confirming_its_own_hold_is_refused(self):
        # The agent that never asked. Milliseconds after the hold, so no user
        # can have answered.
        self.hold()
        out = self.call("confirm_last", {"phrase": "confirm"})
        self.assertIn("has not been put to the user", out)
        self.assertIsNotNone(self.executor.pending)
        # ...and it is still releasable once they have actually answered.
        self.answered()
        self.assertIn("[dry-run]", self.call("confirm_last", {"phrase": "confirm"}))

    def test_confirming_nothing_is_refused(self):
        out = self.call("confirm_last", {"phrase": "confirm"})
        self.assertIn("nothing is waiting", out)

    def test_cancelling_frees_the_gate_for_the_next_action(self):
        self.hold()
        self.assertIn("It was not run", self.call("cancel_last"))
        self.assertIsNone(self.executor.pending)
        # The bug behind the bug: a stuck hold refuses every later gated
        # action, so cancelling has to leave the gate usable.
        self.assertIn("confirm_last", self.hold())
        self.call("cancel_last")
        self.assertEqual("Nothing was being held.", self.call("cancel_last"))

    def test_a_denied_action_is_never_confirmable(self):
        out = self.call("omarchy_cli", {"command": "rm -rf /home/someone"})
        self.assertIn("refused", out)
        self.assertIsNone(self.executor.pending)
        self.assertIn("nothing is waiting", self.call("confirm_last", {"phrase": "confirm"}))


class SilentLogTests(unittest.TestCase):
    def test_the_mcp_server_never_logs_refusals_to_stdout(self):
        """stdout is the protocol here; a stray line is a parse error at the client."""
        with mock.patch.object(mcp_server, "Executor", wraps=Executor) as built:
            mcp_server.build_server(Config(dry_run=True))
        self.assertIsNone(built.call_args.kwargs.get("on_record"))

if __name__ == "__main__":
    unittest.main()
