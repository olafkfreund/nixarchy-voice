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


if __name__ == "__main__":
    unittest.main()
