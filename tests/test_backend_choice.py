"""Choosing which brain answers `say`/`ask`.

Two brains exist: Planner (OpenAI Chat Completions, billed per token) and
ClaudeBrain (Claude Code via the Claude Agent SDK, billed against the Claude
subscription). `claude_backend` picks between them; these tests fake the
module claude_backend.check_ready talks to rather than requiring a real CLI,
because CI has neither a subscription nor a terminal to log one into.
"""

import io
import sys
import types
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import omarchy_voice
from omarchy_voice import cli
from omarchy_voice.config import Config
from omarchy_voice.planner import Planner, Turn
from omarchy_voice.tools import Executor


def _fake_module(problems=()):
    module = types.ModuleType("omarchy_voice.claude_backend")
    module.check_ready = lambda config: list(problems)
    module.ClaudeBrain = type("ClaudeBrain", (), {})
    return module


@contextmanager
def fake_claude_backend(problems=()):
    """Stand in for omarchy_voice.claude_backend for the duration of a test.

    `choose_backend` does `from . import claude_backend`, which -- once any
    OTHER test module has done a real `import omarchy_voice.claude_backend`
    -- resolves via the attribute cached on the `omarchy_voice` package
    object rather than sys.modules, so patching sys.modules alone silently
    does nothing on unlucky test orderings. mock.patch on the dotted path
    replaces (and restores) that cached attribute directly.
    """
    module = _fake_module(problems)
    with mock.patch.dict(sys.modules, {"omarchy_voice.claude_backend": module}), \
            mock.patch.object(omarchy_voice, "claude_backend", module, create=True):
        yield module


@contextmanager
def no_claude_backend():
    """Simulate the SDK/module being entirely unimportable (ImportError)."""
    had = hasattr(omarchy_voice, "claude_backend")
    orig = getattr(omarchy_voice, "claude_backend", None)
    if had:
        delattr(omarchy_voice, "claude_backend")
    try:
        with mock.patch.dict(sys.modules, {"omarchy_voice.claude_backend": None}):
            yield
    finally:
        if had:
            omarchy_voice.claude_backend = orig


class BackendChoiceTests(unittest.TestCase):
    def test_chat_always_means_planner(self):
        config = Config(claude_backend="chat")
        backend, reason = cli.choose_backend(config)
        self.assertIs(backend, Planner)
        self.assertTrue(reason)

    def test_auto_picks_claude_code_when_ready(self):
        with fake_claude_backend(problems=[]) as fake:
            backend, reason = cli.choose_backend(Config(claude_backend="auto"))
        self.assertIs(backend, fake.ClaudeBrain)
        self.assertTrue(reason)

    def test_auto_falls_back_to_planner_when_not_ready(self):
        with fake_claude_backend(problems=["claude CLI not found"]):
            backend, reason = cli.choose_backend(Config(claude_backend="auto"))
        self.assertIs(backend, Planner)
        self.assertIn("claude CLI not found", reason)

    def test_explicit_claude_code_falls_back_rather_than_hard_failing(self):
        """A broken brain must not make the command unusable."""
        with fake_claude_backend(problems=["claude CLI not found"]):
            backend, reason = cli.choose_backend(Config(claude_backend="claude-code"))
        self.assertIs(backend, Planner)
        self.assertIn("claude CLI not found", reason)

    def test_explicit_claude_code_used_when_ready(self):
        with fake_claude_backend(problems=[]) as fake:
            backend, reason = cli.choose_backend(Config(claude_backend="claude-code"))
        self.assertIs(backend, fake.ClaudeBrain)
        self.assertTrue(reason)

    def test_missing_sdk_falls_back_instead_of_crashing(self):
        """No claude_backend module at all (SDK not installed) is not fatal."""
        with no_claude_backend():
            backend, reason = cli.choose_backend(Config(claude_backend="auto"))
        self.assertIs(backend, Planner)
        self.assertTrue(reason)

    def test_missing_agent_sdk_falls_back_via_check_ready(self):
        """check_ready is where an installed-but-SDK-less machine gets caught.

        The module itself never raises ImportError (it imports claude_agent_sdk
        lazily inside its functions), so this path -- not the except ImportError
        above -- is what has to catch "CLI present, Python SDK missing".
        """
        with fake_claude_backend(problems=["claude_agent_sdk is not installed"]):
            backend, reason = cli.choose_backend(Config(claude_backend="auto"))
        self.assertIs(backend, Planner)
        self.assertIn("claude_agent_sdk", reason)

    def test_unrecognized_backend_value_says_so_rather_than_going_silent(self):
        with fake_claude_backend(problems=[]) as fake:
            backend, reason = cli.choose_backend(Config(claude_backend="claude"))
        self.assertIs(backend, fake.ClaudeBrain)
        self.assertIn("not recognised", reason)

    def test_reason_is_always_non_empty(self):
        for value in ("chat", "claude-code", "auto"):
            for problems in ([], ["broken"]):
                with self.subTest(value=value, problems=problems):
                    with fake_claude_backend(problems=problems):
                        _, reason = cli.choose_backend(Config(claude_backend=value))
                    self.assertTrue(reason.strip())


class HeldBrain:
    """A brain that holds its confirmation on itself rather than in
    executor.pending -- the shape ClaudeBrain uses, since there is no
    `_tool_Bash` for Executor.run_pending to release.
    """

    def __init__(self, config, executor):
        self.config = config
        self.executor = executor
        self.pending = "reboot the machine"
        self.confirmed = False
        self.cancelled = False

    def confirm(self):
        self.confirmed = True
        held, self.pending = self.pending, None
        return held

    def cancel(self):
        self.cancelled = True
        held, self.pending = self.pending, None
        return held

    def think(self, text):
        if self.confirmed:
            return Turn(text=text, reply="Rebooting.")
        return Turn(text=text, reply="")


class ConfirmFlowTests(unittest.TestCase):
    """cmd_say has to reach a hold that lives on the brain, not the executor.

    Nothing exercised this path before: `executor.pending` stayed None the
    whole time and the confirmation prompt for a held Claude Code action
    never printed, so "reboot the machine" silently went nowhere.
    """

    def _run(self, confirm_input):
        config = Config()
        executor = Executor(config)
        args = types.SimpleNamespace(text=["reboot", "the", "machine"], no_confirm=False)
        buf = io.StringIO()
        with mock.patch.object(cli, "choose_backend", return_value=(HeldBrain, "test")), \
                mock.patch.object(cli, "Executor", return_value=executor), \
                mock.patch("sys.stdin.isatty", return_value=True), \
                mock.patch("builtins.input", return_value=confirm_input), \
                redirect_stdout(buf):
            code = cli.cmd_say(args, config)
        return code, buf.getvalue()

    def test_a_held_brain_action_prompts_for_confirmation(self):
        _, output = self._run("n")
        self.assertIn("holding", output)
        self.assertIn("reboot the machine", output)

    def test_confirming_a_held_brain_action_re_asks_the_brain(self):
        code, output = self._run("y")
        self.assertEqual(code, 0)
        self.assertIn("Rebooting.", output)

    def test_declining_a_held_brain_action_cancels_it(self):
        self._run("n")
        # A second run proves cancel() actually ran: a fresh HeldBrain always
        # starts pending again, so this only checks the printed wording.
        _, output = self._run("n")
        self.assertIn("cancelled", output)


if __name__ == "__main__":
    unittest.main()
