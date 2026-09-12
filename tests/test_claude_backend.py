"""Driving the desktop with Claude Code instead of the OpenAI API.

The engine changes; the rules must not. Claude Code arrives with Bash, Write
and Edit — tools that have never been near our `Policy` — so the permission
gate in this backend is the only thing left holding `allow_shell = false` and
the shutdown/reboot confirm patterns up. Most of what follows is that gate.
"""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice import claude_backend
from omarchy_voice.claude_backend import ClaudeBrain, describe_tool
from omarchy_voice.config import Config
from omarchy_voice.planner import PlannerUnavailable
from omarchy_voice.tools import Executor


def brain(**overrides) -> ClaudeBrain:
    config = Config(dry_run=True, **overrides)
    return ClaudeBrain(config, Executor(config))


def gate(subject: ClaudeBrain, tool: str, tool_input: dict):
    return asyncio.run(subject._gate(tool, tool_input, None))


class GateTests(unittest.TestCase):
    def test_our_own_tools_are_not_gated_twice(self):
        """`Executor.call` runs `Policy.check` itself.

        Checking again here would hold one action at two gates: the user is
        asked to confirm, says yes, and is asked the identical question again.
        """
        subject = brain()
        with mock.patch.object(subject.executor.policy, "check") as check:
            result = gate(subject, "mcp__omarchy__run_shell", {"command": "sudo reboot"})
        self.assertEqual(result.behavior, "allow")
        check.assert_not_called()

    def test_a_denied_shell_command_is_refused(self):
        """Against the shipped deny list, not a pattern invented for the test."""
        subject = brain()
        result = gate(subject, "Bash", {"command": "sudo rm -rf /"})
        self.assertEqual(result.behavior, "deny")
        self.assertIn("another route", result.message)

    def test_a_shutdown_is_held_for_the_user_to_confirm(self):
        """This is what `allow_shell = false` is worth once Bash is on the table.

        Nothing in Claude Code's own Bash tool knows about our confirm
        patterns, so without this the microphone can reboot the machine.
        """
        for command in ("poweroff", "reboot", "nixos-rebuild switch"):
            with self.subTest(command=command):
                subject = brain()
                result = gate(subject, "Bash", {"command": command})
                self.assertEqual(result.behavior, "deny")
                self.assertIn("confirm", result.message.lower())
                self.assertEqual(subject.pending, command)

    def test_a_harmless_command_still_runs(self):
        """A gate that refuses everything is a gate nobody keeps switched on."""
        subject = brain()
        result = gate(subject, "Bash", {"command": "ls ~/Documents"})
        self.assertEqual(result.behavior, "allow")
        self.assertIsNone(subject.pending)
        self.assertEqual(subject._actions, ["ls ~/Documents"])

    def test_a_held_action_goes_through_once_the_user_says_yes(self):
        subject = brain()
        self.assertEqual(gate(subject, "Bash", {"command": "reboot"}).behavior, "deny")
        self.assertEqual(subject.confirm(), "reboot")
        self.assertEqual(gate(subject, "Bash", {"command": "reboot"}).behavior, "allow")

    def test_a_write_is_described_by_its_path(self):
        """A deny rule aimed at a path has to see the path.

        `Write` hides it in `file_path`, and a rule matching the raw JSON blob
        would be a rule written against the SDK's wire format.
        """
        self.assertEqual(describe_tool("Write", {"file_path": "/etc/passwd"}),
                         "write /etc/passwd")
        self.assertEqual(describe_tool("Bash", {"command": "ls"}), "ls")
        self.assertEqual(describe_tool("WebFetch", {"url": "http://x"}),
                         "fetch http://x")

    def test_an_unknown_tool_is_still_described_and_checked(self):
        """Claude Code gains tools we have never heard of; they get gated too."""
        subject = brain(deny_patterns=[r"secret"])
        result = gate(subject, "SomeNewTool", {"target": "the secret file"})
        self.assertEqual(result.behavior, "deny")


class ReadyTests(unittest.TestCase):
    def setUp(self):
        # check_ready probes the network now. Faked, not called: these tests
        # are about the CLI and the login, and a real connect would make them
        # fail on the offline machine the probe was added for.
        self.enterContext(mock.patch.object(claude_backend.planner, "reachable",
                                            return_value=True))

    def test_a_missing_cli_is_named(self):
        with mock.patch.dict("os.environ", {claude_backend.CLI_ENV: ""}, clear=False), \
             mock.patch("shutil.which", return_value=None):
            problems = claude_backend.check_ready(Config())
        self.assertEqual(len(problems), 1)
        self.assertIn("claude", problems[0])

    def test_an_installed_and_logged_in_cli_has_nothing_to_say(self):
        with mock.patch.dict("os.environ",
                             {claude_backend.CLI_ENV: "/bin/claude",
                              "ANTHROPIC_API_KEY": "sk-test"}, clear=False):
            self.assertEqual(claude_backend.check_ready(Config()), [])


class UsageTests(unittest.TestCase):
    def test_tokens_and_cost_come_off_the_result(self):
        """Cost reads ~0 on a subscription. That is the point, so it is reported."""
        message = mock.Mock(usage={"input_tokens": 120, "output_tokens": 40,
                                   "cache_read_input_tokens": 9000},
                            total_cost_usd=0.0)
        self.assertEqual(claude_backend._usage(message),
                         {"in": 120, "out": 40, "cached": 9000, "cost": 0.0})

    def test_a_result_with_no_usage_at_all_does_not_explode(self):
        self.assertEqual(claude_backend._usage(mock.Mock(usage=None,
                                                         total_cost_usd=None)),
                         {"in": 0, "out": 0, "cached": 0, "cost": 0.0})


class ThinkTests(unittest.TestCase):
    """`think` must never raise. A voice tool that dies on one bad turn is deaf."""

    def _think(self, exc):
        subject = brain()
        with mock.patch.object(ClaudeBrain, "_ask", side_effect=exc):
            return subject.think("open my browser")

    def test_a_missing_cli_is_spoken_rather_than_raised(self):
        turn = self._think(PlannerUnavailable("no claude CLI",
                                              claude_backend.NO_CLI))
        self.assertEqual(turn.reply, claude_backend.NO_CLI)
        self.assertIn("no claude CLI", turn.error)

    def test_anything_else_going_wrong_is_spoken_too(self):
        turn = self._think(RuntimeError("the CLI fell over"))
        self.assertEqual(turn.reply, "Something went wrong with that.")
        self.assertIn("RuntimeError", turn.error)
        self.assertGreaterEqual(turn.elapsed, 0.0)


if __name__ == "__main__":
    unittest.main()


class SubscriptionNotApiKeyTests(unittest.TestCase):
    """The subprocess must use the claude.ai login, not an API key.

    This backend exists to stop paying per token, and for a while it did the
    opposite. The CLI prefers ANTHROPIC_API_KEY over the claude.ai login
    whenever one is set, and this machine exports one for other reasons, so
    every turn went to metered billing: one "what workspace am I on" cost
    $0.1255 and the CLI said so out loud -- "claude.ai connectors are disabled
    because ANTHROPIC_API_KEY ... takes precedence over your claude.ai login".
    Nothing failed. It just quietly charged.
    """

    def _brain(self, **kw):
        config = Config(**kw)
        return claude_backend.ClaudeBrain(config, Executor(config))

    def test_the_api_key_is_blanked_for_the_subprocess(self):
        self.assertEqual(self._brain()._child_env(), {"ANTHROPIC_API_KEY": ""})

    def test_blanked_rather_than_absent(self):
        """The SDK merges this over the inherited env, so a key cannot be
        deleted through it -- only overwritten. An empty value reads as
        unset to the CLI; omitting the entry would leave the real key in
        place, which is the bug this class is about."""
        env = self._brain()._child_env()
        self.assertIn("ANTHROPIC_API_KEY", env)
        self.assertFalse(env["ANTHROPIC_API_KEY"])

    def test_opting_out_leaves_the_environment_alone(self):
        """An account with an API key and no subscription needs the key."""
        self.assertEqual(
            self._brain(claude_use_subscription=False)._child_env(), {})
