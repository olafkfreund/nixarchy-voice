"""Choosing which brain answers `say`/`ask`.

Two brains exist: Planner (OpenAI Chat Completions, billed per token) and
ClaudeBrain (Claude Code via the Claude Agent SDK, billed against the Claude
subscription). `claude_backend` picks between them; these tests fake the
module claude_backend.check_ready talks to rather than requiring a real CLI,
because CI has neither a subscription nor a terminal to log one into.
"""

import io
import os
import sys
import types
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import omarchy_voice
from omarchy_voice import cli, planner as planner_mod
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


@contextmanager
def fake_network(unreachable=()):
    """Fake the TCP probe. No test here may touch the real network.

    `unreachable` names hosts that refuse; everything else connects. The
    probe is cached for the life of the process, so the cache is cleared on
    the way in and out or one test's offline machine would be the next test's.
    """
    def connect(address, timeout=None):
        host, port = address
        if host in unreachable:
            raise OSError("Network is unreachable")
        return mock.MagicMock()

    planner_mod._connects.cache_clear()
    try:
        with mock.patch.object(planner_mod.socket, "create_connection",
                               side_effect=connect) as probe:
            yield probe
    finally:
        planner_mod._connects.cache_clear()


LOCAL = "http://127.0.0.1:11434/v1"
ANTHROPIC = "api.anthropic.com"
OPENAI = "api.openai.com"


@contextmanager
def claude_installed():
    """A machine with the CLI and a login, so only reachability is in play."""
    from omarchy_voice import claude_backend as cb
    with mock.patch.object(cb, "cli_path", return_value="/usr/bin/claude"), \
            mock.patch.object(cb, "_credentials_present", return_value=True):
        yield


class BackendChoiceTests(unittest.TestCase):
    def setUp(self):
        # Everything reachable and a key present unless a test says otherwise:
        # these tests are about the claude rung, and a CI box with no
        # OPENAI_API_KEY would otherwise fail them for the chat rung instead.
        self.enterContext(fake_network())
        self.enterContext(mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}))

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
        self.calls = []

    def confirm(self):
        self.confirmed = True
        held, self.pending = self.pending, None
        return f"RELEASE {held}" if held else None

    def cancel(self):
        self.cancelled = True
        held, self.pending = self.pending, None
        return held

    def think(self, text, release=False):
        self.calls.append((text, release))
        if self.confirmed:
            return Turn(text=text, reply="Rebooting.")
        return Turn(text=text, reply="")


class ConfirmFlowTests(unittest.TestCase):
    """cmd_say has to reach a hold that lives on the brain, not the executor.

    Nothing exercised this path before: `executor.pending` stayed None the
    whole time and the confirmation prompt for a held Claude Code action
    never printed, so "reboot the machine" silently went nowhere.
    """

    def _run(self, confirm_input, backend=HeldBrain):
        config = Config()
        executor = Executor(config)
        args = types.SimpleNamespace(text=["reboot", "the", "machine"], no_confirm=False)
        buf = io.StringIO()
        with mock.patch.object(cli, "choose_backend", return_value=(backend, "test")), \
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

    def test_confirming_sends_the_release_not_the_utterance(self):
        """The second turn is the brain's release message, marked as one (#76).

        Re-sending the typed text ran everything else in it a second time.
        """
        brains = []

        def build(config, executor):
            brains.append(HeldBrain(config, executor))
            return brains[-1]

        self._run("y", backend=build)
        self.assertEqual(brains[0].calls[1], ("RELEASE reboot the machine", True))

    def test_declining_a_held_brain_action_cancels_it(self):
        self._run("n")
        # A second run proves cancel() actually ran: a fresh HeldBrain always
        # starts pending again, so this only checks the printed wording.
        _, output = self._run("n")
        self.assertIn("cancelled", output)


if __name__ == "__main__":
    unittest.main()


class ReachabilityTests(unittest.TestCase):
    """Offline is a network problem, and the ladder has to say so.

    Before this, `check_ready` asked whether the CLI existed and whether a
    login was on disk — never whether there was a network. On a train that
    all passed, ClaudeBrain was chosen, and the turn died inside the SDK with
    no fallback and nothing usable to say.
    """

    def setUp(self):
        self.enterContext(mock.patch.dict(os.environ, {}, clear=False))
        os.environ.pop("OPENAI_API_KEY", None)

    def test_claude_code_unreachable_falls_back_to_the_local_planner(self):
        config = Config(claude_backend="auto", base_url=LOCAL)
        with fake_network(unreachable=[ANTHROPIC]), claude_installed():
            backend, reason = cli.choose_backend(config)
        self.assertIs(backend, Planner)
        self.assertIn("api.anthropic.com", reason)

    def test_both_brains_unreachable_says_neither_rather_than_using_chat(self):
        """The fallback being equally dead must not read as a working fallback."""
        config = Config(claude_backend="auto", base_url=LOCAL)
        with fake_network(unreachable=[ANTHROPIC, "127.0.0.1"]), claude_installed():
            backend, reason = cli.choose_backend(config)
        self.assertIs(backend, Planner)  # nothing else to return; think() speaks it
        self.assertIn("neither", reason)
        self.assertIn("127.0.0.1", reason)

    def test_explicit_chat_is_never_second_guessed(self):
        """An explicit choice must not be probed, let alone overridden."""
        config = Config(claude_backend="chat", base_url=LOCAL)
        with fake_network(unreachable=[ANTHROPIC, "127.0.0.1"]) as probe:
            backend, reason = cli.choose_backend(config)
        self.assertIs(backend, Planner)
        self.assertEqual(probe.call_count, 0)
        self.assertNotIn("neither", reason)

    def test_a_local_base_url_needs_no_api_key(self):
        """The whole offline path: a local model, no account, no problems."""
        with fake_network():
            self.assertEqual(planner_mod.check_ready(Config(base_url=LOCAL)), [])

    def test_a_remote_base_url_without_a_key_is_a_problem(self):
        with fake_network():
            problems = planner_mod.check_ready(Config())
        self.assertTrue(any("OPENAI_API_KEY" in p for p in problems))

    def test_an_unreachable_endpoint_is_reported_as_a_network_problem(self):
        with fake_network(unreachable=[OPENAI]):
            problems = planner_mod.check_ready(Config())
        self.assertIn("cannot reach", problems[0])

    def test_claude_check_ready_blames_the_network_not_the_setup(self):
        from omarchy_voice import claude_backend as cb
        with fake_network(unreachable=[ANTHROPIC]), claude_installed():
            problems = cb.check_ready(Config())
        self.assertIn("offline", problems[0])
        self.assertNotIn("not logged in", problems[0])

    def test_the_probe_is_cached_rather_than_run_per_call(self):
        """choose_backend and doctor both ask, and say asks every turn.

        One connect per endpoint per process, or an offline machine gets
        slower the more it is used.
        """
        config = Config(claude_backend="auto", base_url=LOCAL)
        with fake_network(unreachable=[ANTHROPIC]) as probe, claude_installed():
            for _ in range(5):
                cli.choose_backend(config)
        self.assertEqual(probe.call_count, 2)  # anthropic once, localhost once


class ShellStatusTests(unittest.TestCase):
    """What doctor says about whether this machine can run commands.

    `allow_shell = false` gates our `run_shell`. The claude-code backend used
    to hand the model Claude Code's own Bash as well, which the setting never
    reached, so doctor carried a caveat. #94 took Bash away from the brain,
    and "disabled" now means what it says on both backends.
    """

    def test_claude_code_has_no_bash_caveat_any_more(self):
        lines = cli.shell_status(Config(allow_shell=False), "claude-code")
        self.assertEqual(len(lines), 1)
        self.assertIn("disabled", lines[0])

    def test_the_chat_backend_has_no_such_caveat(self):
        """There the setting means what it says: no shell tool is even sent."""
        lines = cli.shell_status(Config(allow_shell=False), "chat")
        self.assertEqual(len(lines), 1)

    def test_an_enabled_shell_tool_does_not_warn_about_what_it_enabled(self):
        lines = cli.shell_status(Config(allow_shell=True), "claude-code")
        self.assertIn("enabled", lines[0])
        self.assertEqual(len(lines), 1)


class ConsentStatusTests(unittest.TestCase):
    """#17: doctor says what is handed over, and what it takes to hand it over."""

    def test_the_off_lines_name_the_way_to_turn_each_on(self):
        lines = cli.consent_status(Config())
        joined = "\n".join(lines)
        self.assertIn("desktop control: disabled", joined)
        self.assertIn("desktop_control = true", joined)
        self.assertIn("programs.omarchy-voice.desktopControl", joined)
        self.assertIn("notification log: disabled", joined)

    def test_enabled_desktop_control_says_whether_ai_mirror_is_there(self):
        with mock.patch("omarchy_voice.claude_backend.ai_mirror_path", return_value="/bin/ai-mirror"):
            self.assertIn("/bin/ai-mirror", "\n".join(cli.consent_status(Config(desktop_control=True))))
        with mock.patch("omarchy_voice.claude_backend.ai_mirror_path", return_value=""):
            self.assertIn("not installed", "\n".join(cli.consent_status(Config(desktop_control=True))))


class VoiceCreditTests(unittest.TestCase):
    """#18: the shipped voice's dataset asks to be credited wherever it speaks."""

    def test_the_credit_is_shown_when_the_package_sets_it(self):
        with mock.patch.dict(os.environ, {"OMARCHY_VOICE_ATTRIBUTION": "Jenny (Dioco)"}):
            self.assertIn("Jenny (Dioco)", "\n".join(cli.voice_credit()))

    def test_nothing_is_claimed_for_a_voice_that_needs_no_credit(self):
        with mock.patch.dict(os.environ, {"OMARCHY_VOICE_ATTRIBUTION": ""}):
            self.assertEqual(cli.voice_credit(), [])


class GateHintTests(unittest.TestCase):
    """doctor names verify-gate on the backend it exists for, and never runs it."""

    def test_the_claude_code_backend_is_pointed_at_the_check(self):
        lines = cli.gate_hint("claude-code")
        self.assertEqual(len(lines), 1)
        self.assertIn("omarchy-voice verify-gate", lines[0])

    def test_the_chat_backend_has_no_gate_to_verify(self):
        self.assertEqual(cli.gate_hint("chat"), [])
