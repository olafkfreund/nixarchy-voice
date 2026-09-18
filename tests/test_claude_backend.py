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
from omarchy_voice.claude_backend import (DRY_RUN_READS, _PATH_TOOLS, ClaudeBrain,
                                          describe_tool)
from omarchy_voice.config import Config
from omarchy_voice.planner import PlannerUnavailable
from omarchy_voice.tools import Executor


def brain(**overrides) -> ClaudeBrain:
    """A brain for gate tests. Dry-run by default, as a harness safety.

    That default used to be free, because dry_run did nothing to the gate --
    which was #5. It now refuses every non-read, so a test asserting what the
    gate lets through in normal operation has to say `dry_run=False`. Safe:
    these tests only read the permission `_gate` returns; nothing executes.
    """
    config = Config(**{"dry_run": True, **overrides})
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
        subject = brain(dry_run=False)
        result = gate(subject, "Bash", {"command": "ls ~/Documents"})
        self.assertEqual(result.behavior, "allow")
        self.assertIsNone(subject.pending)
        self.assertEqual(subject._actions, ["ls ~/Documents"])

    def test_a_held_action_goes_through_once_the_user_says_yes(self):
        subject = brain(dry_run=False)
        self.assertEqual(gate(subject, "Bash", {"command": "reboot"}).behavior, "deny")
        self.assertEqual(subject.confirm(), "reboot")
        self.assertEqual(gate(subject, "Bash", {"command": "reboot"}).behavior, "allow")

    def test_saying_yes_once_does_not_approve_it_forever(self):
        """The approval is spent by the replay it was given for.

        This brain outlives the turn — `LocalSession.run` builds one and keeps
        it for the whole daemon — so an approval that is never consumed is a
        standing permission. One "yes, reboot" at breakfast used to mean the
        model could reboot unprompted all day, with no second question.
        """
        subject = brain(dry_run=False)
        gate(subject, "Bash", {"command": "reboot"})
        subject.confirm()
        self.assertEqual(gate(subject, "Bash", {"command": "reboot"}).behavior, "allow")
        # Same action, later, on nobody's say-so. Held again.
        self.assertEqual(gate(subject, "Bash", {"command": "reboot"}).behavior, "deny")
        self.assertEqual(subject.pending, "reboot")
        self.assertFalse(subject._confirmed)

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

    def test_ai_mirror_typing_is_gated_to_the_last_character(self):
        """ai-mirror types into a real terminal; a deny rule must see all of it."""
        subject = brain(dry_run=False)
        text = "x" * 1000 + " sudo reboot"
        result = gate(subject, "mcp__ai-mirror__input",
                      {"actions": [{"type": "type", "text": text}]})
        self.assertEqual(result.behavior, "deny")
        self.assertEqual(gate(subject, "mcp__ai-mirror__control", {"mode": "agent"}).behavior,
                         "allow")


class DryRunTests(unittest.TestCase):
    """A dry run must not act (#5).

    `Executor` narrates under dry_run; this gate used to ignore it, so on the
    claude-code backend -- the default wherever the CLI is installed -- a dry
    run executed Claude Code's own Bash and Write for real. Now every
    non-read is refused with a message that reads as the narration.
    """

    def test_bash_is_refused_and_described(self):
        """Refused outright, never classified: `cat x > y` is a write spelled as a read."""
        result = gate(brain(), "Bash", {"command": "touch /tmp/x"})
        self.assertEqual(result.behavior, "deny")
        self.assertIn("would have run", result.message)
        self.assertIn("touch /tmp/x", result.message)

    def test_writes_are_refused(self):
        for tool in ("Write", "Edit", "NotebookEdit"):
            with self.subTest(tool=tool):
                result = gate(brain(), tool, {"file_path": "/tmp/x", "content": "y"})
                self.assertEqual(result.behavior, "deny")

    def test_reads_still_go_through(self):
        """Otherwise a dry run is only useful for watching it fail."""
        for tool, args in (("Read", {"file_path": "/tmp/x"}),
                           ("WebFetch", {"url": "https://example.org"}),
                           ("WebSearch", {"query": "nixos"})):
            with self.subTest(tool=tool):
                self.assertEqual(gate(brain(), tool, args).behavior, "allow")

    def test_an_unknown_tool_fails_closed(self):
        """Claude Code gains tools on its own schedule. A new one is not known safe."""
        self.assertEqual(gate(brain(), "SomeNewTool", {"x": 1}).behavior, "deny")

    def test_ai_mirror_is_refused(self):
        """It types into real windows; nothing about that is a read."""
        result = gate(brain(), "mcp__ai-mirror__input",
                      {"actions": [{"type": "type", "text": "hello"}]})
        self.assertEqual(result.behavior, "deny")

    def test_our_own_tools_are_left_to_executor(self):
        """Executor narrates dry-run itself. Refusing here would narrate twice."""
        subject = brain()
        with mock.patch.object(subject.executor.policy, "check") as check:
            result = gate(subject, "mcp__omarchy__run_shell", {"command": "touch /tmp/x"})
        self.assertEqual(result.behavior, "allow")
        check.assert_not_called()

    def test_a_denied_command_is_still_reported_as_denied(self):
        """After the policy, never before: a refusal must stay a refusal."""
        subject = brain()
        result = gate(subject, "Bash", {"command": "sudo rm -rf /"})
        self.assertEqual(result.behavior, "deny")
        self.assertIn("Refused", result.message)
        self.assertNotIn("dry-run", result.message)

    def test_a_gated_command_is_still_held(self):
        """...and a hold must stay a hold, with the question put to the user."""
        subject = brain()
        result = gate(subject, "Bash", {"command": "reboot"})
        self.assertIn("confirmation", result.message)
        self.assertNotIn("dry-run", result.message)
        self.assertEqual(subject.pending, "reboot")

    def test_saying_yes_does_not_make_a_dry_run_act(self):
        """The confirmed replay skips the policy -- that is what confirming is.

        It must not skip this. Found while implementing: the plan covered the
        ordinary allow and missed that `_gate` has a second one, so a dry run
        held a reboot, the user said yes, and the replay ran it for real.
        """
        subject = brain()
        gate(subject, "Bash", {"command": "reboot"})
        subject.confirm()
        result = gate(subject, "Bash", {"command": "reboot"})
        self.assertEqual(result.behavior, "deny")
        self.assertIn("would have run", result.message)
        self.assertFalse(subject._confirmed)  # the approval is still spent

    def test_a_dry_run_is_in_the_log(self):
        """`omarchy-voice log` must show a refusal, not something that ran."""
        subject = brain()
        gate(subject, "Bash", {"command": "touch /tmp/x"})
        self.assertIn("DRYRUN  touch /tmp/x", subject.executor.transcript)
        self.assertEqual(subject._actions, [])

    def test_without_dry_run_nothing_changes(self):
        """The shipped default takes exactly today's path."""
        subject = brain(dry_run=False)
        for tool, args in (("Bash", {"command": "ls"}),
                           ("Write", {"file_path": "/tmp/x", "content": "y"}),
                           ("SomeNewTool", {"x": 1}),
                           ("mcp__ai-mirror__control", {"mode": "agent"})):
            with self.subTest(tool=tool):
                self.assertEqual(gate(subject, tool, args).behavior, "allow")

    def test_the_read_set_admits_no_writer(self):
        """_PATH_TOOLS and DRY_RUN_READS must never disagree on what writes."""
        writers = {t for t, verb in _PATH_TOOLS.items() if verb in ("write", "edit")}
        self.assertFalse(DRY_RUN_READS & writers)
        self.assertNotIn("Bash", DRY_RUN_READS)


class AiMirrorTests(unittest.TestCase):
    """ai-mirror is offered to the brain whenever it is installed, and only then."""

    def options(self, ai_mirror: str):
        sdk = types.SimpleNamespace(ClaudeAgentOptions=lambda **kw: types.SimpleNamespace(**kw))
        config = Config(dry_run=True)
        with mock.patch.dict("sys.modules", {"claude_agent_sdk": sdk}), \
             mock.patch.dict("os.environ", {claude_backend.CLI_ENV: "/bin/claude",
                                            claude_backend.AI_MIRROR_ENV: ai_mirror}), \
             mock.patch("shutil.which", return_value=None), \
             mock.patch.object(claude_backend.mcp_server, "build_server"), \
             mock.patch.object(claude_backend.planner, "_system_prompt", return_value="base"):
            return ClaudeBrain(config, Executor(config))._options()

    def test_installed_ai_mirror_is_a_second_server(self):
        options = self.options("/bin/ai-mirror")
        self.assertEqual(options.mcp_servers["ai-mirror"],
                         {"type": "stdio", "command": "/bin/ai-mirror", "args": ["mcp"]})
        self.assertIn("mcp__ai-mirror__", options.system_prompt)

    def test_no_ai_mirror_no_server(self):
        options = self.options("")
        self.assertEqual(set(options.mcp_servers), {"omarchy"})
        self.assertEqual(options.system_prompt, "base")


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


# -- the warm brain ---------------------------------------------------------

import types
from collections import deque

from omarchy_voice.claude_backend import WarmBrain


class Message(types.SimpleNamespace):
    """A fake SDK message. The class NAME is what the brain dispatches on."""


def _named(name, **fields):
    return type(name, (Message,), {})(**fields)


def delta(text):
    return _named("StreamEvent",
                  event={"type": "content_block_delta",
                         "delta": {"type": "text_delta", "text": text}})


def block_stop():
    return _named("StreamEvent", event={"type": "content_block_stop"})


def result(**fields):
    fields.setdefault("usage", {})
    fields.setdefault("total_cost_usd", 0.0)
    fields.setdefault("is_error", False)
    return _named("ResultMessage", **fields)


def said(text):
    return _named("AssistantMessage", content=[types.SimpleNamespace(text=text)])


class Boom:
    """A scripted mid-stream failure."""


class FakeClient:
    """One shared message pipe, exactly as the SDK has.

    The sharing is the point: `receive_response()` stops at the first
    ResultMessage it finds, whoever's turn it belonged to. Anything a turn
    leaves behind is waiting for the next one.
    """

    def __init__(self, script):
        self.script = script          # {utterance: [messages]}
        self.pipe = deque()
        self.asked = []
        self.interrupts = 0
        self.connected = False
        self.pause = None             # an asyncio.Event the stream waits on

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def interrupt(self):
        self.interrupts += 1

    async def query(self, text):
        self.asked.append(text)
        self.pipe.extend(self.script.get(text, [result()]))

    async def receive_response(self):
        while self.pipe:
            message = self.pipe.popleft()
            if isinstance(message, Boom):
                raise RuntimeError("the CLI fell over mid-stream")
            if message == "pause":
                await self.pause.wait()
                continue
            yield message
            if type(message).__name__ == "ResultMessage":
                return


class WarmBrainTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = None
        # `_options` needs the real SDK to build ClaudeAgentOptions; the parent
        # is faked so these run with or without it installed. The override
        # under test -- include_partial_messages -- still runs.
        self.enterContext(mock.patch.object(ClaudeBrain, "_options",
                                            lambda self: types.SimpleNamespace()))

    async def warm(self, script=None):
        self.client = FakeClient(script or {})
        with mock.patch.object(claude_backend, "_new_client",
                               return_value=self.client):
            subject = brain_warm()
            await subject.start()
        return subject

    async def collect(self, subject, text):
        return [sentence async for sentence in subject.ask_stream(text)]

    async def test_partial_messages_are_switched_on(self):
        """Without it the SDK delivers one finished message and there is
        nothing to stream -- the warm brain would be no faster to speak."""
        self.assertTrue((await self.warm())._options().include_partial_messages)

    async def test_the_first_real_turn_does_not_pay_to_start_the_session(self):
        """Measured 3.97s on the opening turn against 1.52s once running.

        The daemon connects at login and then waits, so that difference is
        free here and is two and a half seconds of silence in front of
        somebody who has just spoken.
        """
        subject = await self.warm()
        self.assertEqual(self.client.asked, [claude_backend.WARM_UP])
        self.assertFalse(subject._dirty)   # drained; the next turn is aligned
        self.assertEqual(subject._actions, [])

    async def test_the_warm_up_is_not_a_turn_the_user_took(self):
        """Counting it would put a question nobody asked in the usage report
        -- and bill the user's curiosity about their own spending."""
        subject = await self.warm()
        self.assertEqual(subject.usage["turns"], 0)
        self.assertEqual(subject.usage["cost"], 0.0)

    async def test_a_warm_up_that_fails_still_leaves_a_working_session(self):
        """The worst this may cost is a slow first turn.

        A daemon that will not start because a throwaway query went wrong is
        a voice assistant that is silent all day to save two seconds.
        """
        broken = FakeClient({claude_backend.WARM_UP: [Boom()]})
        fresh = FakeClient({"hello": [delta("Hello."), result()]})
        with mock.patch.object(claude_backend, "_new_client",
                               side_effect=[broken, fresh]):
            subject = brain_warm()
            await subject.start()
        self.assertIs(subject._client, fresh)
        self.assertFalse(broken.connected)     # its pipe could not be trusted
        self.assertFalse(subject._dirty)
        self.assertEqual(fresh.asked, [])      # not warmed a second time
        self.assertEqual(await self.collect(subject, "hello"), ["Hello."])
        self.assertTrue(any("WARMUP" in line
                            for line in subject.executor.transcript))

    async def test_a_sentence_is_spoken_before_the_rest_has_arrived(self):
        """The whole reason this class exists.

        Waiting for the full reply puts the model's slowest token in front of
        the first word out of the speaker. Here the stream is held open after
        the first sentence: if that sentence does not come out, streaming is
        decorative.
        """
        subject = await self.warm({"hello": [delta("Hello there. "), "pause",
                                             delta("And how are you?"), result()]})
        self.client.pause = asyncio.Event()
        stream = subject.ask_stream("hello")
        first = await anext(stream)
        self.assertEqual(first, "Hello there.")
        self.assertFalse(self.client.pause.is_set())  # rest still unsent
        self.client.pause.set()
        self.assertEqual([s async for s in stream], ["And how are you?"])

    async def test_a_trailing_fragment_with_no_full_stop_is_still_spoken(self):
        """Models end on "42" or a bare list item often enough that holding
        text back until it is punctuated loses whole answers."""
        subject = await self.warm({"count": [delta("The answer is 42"), result()]})
        self.assertEqual(await self.collect(subject, "count"), ["The answer is 42"])

    async def test_text_before_a_tool_call_is_spoken_rather_than_held(self):
        """"Let me look that up." sitting in the buffer through a ten-second
        tool run is ten seconds of silence, then two thoughts at once."""
        subject = await self.warm({"look": [delta("Let me look"), block_stop(),
                                            delta("It is sunny."), result()]})
        self.assertEqual(await self.collect(subject, "look"),
                         ["Let me look", "It is sunny."])

    async def test_a_reply_that_never_streamed_is_still_spoken(self):
        """Some replies arrive only as finished blocks. Silence is not an
        acceptable rendering of an answer that was given."""
        subject = await self.warm({"hi": [said("Fine. Thanks."), result()]})
        self.assertEqual(await self.collect(subject, "hi"), ["Fine.", "Thanks."])

    async def test_an_interrupted_turn_does_not_answer_the_next_question(self):
        """The off-by-one that makes every answer stale.

        `receive_response()` stops at the first ResultMessage in a SHARED
        pipe; nothing pairs a query with its reply. Walk away from a turn
        mid-stream and its ResultMessage stays buffered, so the next question
        ends on the dead turn's result and speaks the dead turn's words --
        and so does every question after it, for the rest of the session.
        Nothing raises. It just answers the previous question all day.
        """
        subject = await self.warm({
            "what time is it": [delta("It is ten past four. "),
                                delta("Nearly quarter past."), result()],
            "what day is it": [delta("It is Thursday."), result()],
        })
        stream = subject.ask_stream("what time is it")
        self.assertEqual(await anext(stream), "It is ten past four.")
        await stream.aclose()                      # the user cut in
        await subject.reset_turn()

        self.assertEqual(await self.collect(subject, "what day is it"),
                         ["It is Thursday."])
        self.assertEqual(self.client.interrupts, 1)

    async def test_a_pipe_that_cannot_be_drained_is_rebuilt(self):
        """Better to lose this session's memory than to run desynced."""
        subject = await self.warm({"hi": [delta("One. "), "pause", result()]})
        self.client.pause = asyncio.Event()        # the result never arrives
        stream = subject.ask_stream("hi")
        await anext(stream)
        await stream.aclose()
        with mock.patch.object(claude_backend, "_new_client",
                               return_value=FakeClient({})) as fresh:
            await subject.reset_turn(timeout=0.05)
        fresh.assert_called_once()
        self.assertFalse(self.client.connected)
        self.assertFalse(subject._dirty)

    async def test_a_clean_turn_needs_no_draining(self):
        """A drain on an aligned pipe would eat the NEXT turn's answer."""
        subject = await self.warm({"hi": [delta("Hello."), result()]})
        await self.collect(subject, "hi")
        await subject.reset_turn()
        self.assertEqual(self.client.interrupts, 0)

    async def test_usage_adds_up_across_the_session(self):
        """Per-turn numbers are useless on a warm brain: the whole point is
        that the session is one long conversation."""
        one = result(usage={"input_tokens": 100, "output_tokens": 10,
                            "cache_read_input_tokens": 900}, total_cost_usd=0.01)
        two = result(usage={"input_tokens": 50, "output_tokens": 5},
                     total_cost_usd=0.02)
        subject = await self.warm({"a": [delta("One."), one],
                                   "b": [delta("Two."), two]})
        await self.collect(subject, "a")
        await self.collect(subject, "b")
        self.assertEqual(subject.usage,
                         {"in": 1050, "out": 15, "cost": 0.03, "turns": 2})

    async def test_a_failure_mid_stream_is_spoken_not_raised(self):
        """Same discipline as `think()`: a voice loop that dies on one bad
        turn is a deaf one for the rest of the day."""
        subject = await self.warm({"hi": [delta("Starting. "), Boom(), result()]})
        self.assertEqual(await self.collect(subject, "hi"),
                         ["Starting.", "Something went wrong with that."])

    async def test_an_errored_result_is_spoken(self):
        subject = await self.warm({"hi": [result(is_error=True, subtype="max_turns")]})
        self.assertEqual(await self.collect(subject, "hi"),
                         ["Claude Code had trouble with that."])

    async def test_a_turn_with_nothing_to_say_still_says_something(self):
        subject = await self.warm({"tidy up": [result()]})
        self.assertEqual(await self.collect(subject, "tidy up"), ["Done."])

    async def test_the_gate_still_holds_a_dangerous_command(self):
        """The warm brain inherits the gate rather than owning a second copy.

        There is exactly one thing between a spoken sentence and Bash, and it
        must not be possible to get a brain that skipped it.
        """
        subject = await self.warm()
        outcome = await subject._gate("Bash", {"command": "sudo rm -rf /"}, None)
        self.assertEqual(outcome.behavior, "deny")
        held = await subject._gate("Bash", {"command": "reboot"}, None)
        self.assertEqual(held.behavior, "deny")
        self.assertEqual(subject.pending, "reboot")

    async def test_asking_before_starting_says_so_rather_than_crashing(self):
        subject = brain_warm()
        self.assertEqual([s async for s in subject.ask_stream("hi")],
                         [claude_backend.NO_SESSION])

    async def test_a_session_that_will_not_connect_is_unavailable(self):
        """`start` is the one place that may raise: there is no session to
        speak through yet, so the caller has to fall back."""
        broken = FakeClient({})
        broken.connect = mock.AsyncMock(side_effect=OSError("no such binary"))
        with mock.patch.object(claude_backend, "_new_client", return_value=broken):
            with self.assertRaises(PlannerUnavailable) as raised:
                await brain_warm().start()
        self.assertEqual(raised.exception.spoken, claude_backend.NO_SESSION)


def brain_warm(**overrides) -> WarmBrain:
    config = Config(dry_run=True, **overrides)
    return WarmBrain(config, Executor(config))
