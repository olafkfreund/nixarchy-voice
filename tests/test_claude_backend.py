"""Driving the desktop with Claude Code instead of the OpenAI API.

The engine changes; the rules must not. Claude Code's own tools have never
been near our `Policy`, and the few it still offers (#94) -- plus any it adds
by itself -- go through the PreToolUse hook in this backend, the only thing
holding the deny list and the shutdown/reboot confirm patterns up. A hook,
because the permission callback is only consulted for calls Claude Code
would have asked about. Most of what follows is that gate, driven through
the hook the way the CLI drives it.
"""

import asyncio
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice import claude_backend
from omarchy_voice.claude_backend import (DRY_RUN_READS, _PATH_TOOLS, ClaudeBrain,
                                          describe_tool)
from omarchy_voice.config import Config
from omarchy_voice.planner import PlannerUnavailable, Turn
from omarchy_voice.tools import Executor


def brain(**overrides) -> ClaudeBrain:
    """A brain for gate tests. Dry-run by default, as a harness safety.

    That default used to be free, because dry_run did nothing to the gate --
    which was #5. It now refuses every non-read, so a test asserting what the
    gate lets through in normal operation has to say `dry_run=False`. Safe:
    these tests only read the verdict the hook returns; nothing executes.
    """
    config = Config(**{"dry_run": True, **overrides})
    return ClaudeBrain(config, Executor(config))


async def verdict(subject: ClaudeBrain, tool: str, tool_input: dict):
    """One call through the PreToolUse hook, the way the CLI makes it.

    Through the hook rather than `_decide`, so every gate test exercises the
    wiring and not only the rules -- rules that were right but never called
    are exactly what #7 was. The verdict comes back as `.behavior` /
    `.message`, so the assertions read the same as they did before the hook.
    """
    out = await subject._pre_tool_use({"tool_name": tool, "tool_input": tool_input},
                                      None, None)
    decision = out["hookSpecificOutput"]
    return types.SimpleNamespace(behavior=decision["permissionDecision"],
                                 message=decision["permissionDecisionReason"])


def gate(subject: ClaudeBrain, tool: str, tool_input: dict):
    return asyncio.run(verdict(subject, tool, tool_input))


def release(subject: ClaudeBrain) -> str | None:
    """The user says yes, and the release turn starts (#76).

    `think`/`ask_stream` set `_releasing` for the length of that turn; set
    here so a test can put gate calls inside it one at a time.
    """
    message = subject.confirm()
    subject._releasing = True
    return message


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
        self.assertIn('"command": "reboot"', release(subject))
        self.assertEqual(gate(subject, "Bash", {"command": "reboot"}).behavior, "allow")

    def test_saying_yes_once_does_not_approve_it_forever(self):
        """The approval is spent by the release turn it was given for.

        This brain outlives the turn — `LocalSession.run` builds one and keeps
        it for the whole daemon — so an approval that is never consumed is a
        standing permission. One "yes, reboot" at breakfast used to mean the
        model could reboot unprompted all day, with no second question.
        """
        subject = brain(dry_run=False)
        gate(subject, "Bash", {"command": "reboot"})
        release(subject)
        self.assertEqual(gate(subject, "Bash", {"command": "reboot"}).behavior, "allow")
        subject._releasing = False  # the release turn is over
        # Same action, later, on nobody's say-so. Held again.
        self.assertEqual(gate(subject, "Bash", {"command": "reboot"}).behavior, "deny")
        self.assertEqual(subject.pending, "reboot")
        self.assertIsNone(subject._approved)

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


class ReleaseTurnGateTests(unittest.TestCase):
    """Confirming runs the held call, once, and nothing else (#76).

    It used to replay the whole utterance with the held description
    pre-approved, so the rest of the sentence ran twice -- or whatever was
    said last ran instead, and the approval stayed armed for later.
    """

    def held(self, tool="Bash", tool_input=None):
        subject = brain(dry_run=False)
        gate(subject, tool, tool_input or {"command": "reboot"})
        return subject

    def test_the_held_call_runs_once_in_its_release_turn(self):
        subject = self.held()
        release(subject)
        self.assertEqual(gate(subject, "Bash", {"command": "reboot"}).behavior, "allow")
        again = gate(subject, "Bash", {"command": "reboot"})
        self.assertEqual(again.behavior, "deny")
        self.assertIn("Only the call the user confirmed", again.message)

    def test_nothing_else_runs_in_a_release_turn(self):
        """Not a harmless command, not even our own tools. A read may run."""
        subject = self.held()
        release(subject)
        self.assertEqual(gate(subject, "Bash", {"command": "ls"}).behavior, "deny")
        self.assertEqual(gate(subject, "mcp__omarchy__notify", {"text": "hi"}).behavior,
                         "deny")
        self.assertEqual(gate(subject, "ToolSearch", {"query": "x"}).behavior, "allow")

    def test_our_lookup_runs_in_a_release_turn(self):
        """A lookup is not an action, so it may run beside the approval (#100)."""
        approved = ("Bash", {"command": "reboot"})  # only a built-in is held here
        subject = self.held(*approved)
        release(subject)
        self.assertEqual(gate(subject, "mcp__omarchy__omarchy_help",
                              {"query": "reboot"}).behavior, "allow")
        self.assertEqual(gate(subject, "mcp__omarchy__launch_app",
                              {"app": "firefox"}).behavior, "deny")
        self.assertEqual(gate(subject, "Bash", {"command": "ls"}).behavior, "deny")
        self.assertEqual(gate(subject, "Read", {"file_path": "/etc/shadow"}).behavior,
                         "deny")
        self.assertEqual(gate(subject, *approved).behavior, "allow")
        self.assertEqual(gate(subject, *approved).behavior, "deny")

    def test_a_second_gated_action_is_refused_not_held(self):
        """One hold at a time: the model says what is still undone."""
        subject = self.held()
        release(subject)
        result = gate(subject, "Bash", {"command": "poweroff"})
        self.assertEqual(result.behavior, "deny")
        self.assertIn("still undone", result.message)
        self.assertIsNone(subject.pending)

    def test_an_approval_is_not_honoured_outside_a_release_turn(self):
        """A turn already running when the user said yes cannot spend it."""
        subject = self.held()
        subject.confirm()
        self.assertEqual(gate(subject, "Bash", {"command": "reboot"}).behavior, "deny")
        self.assertEqual(subject.pending, "reboot")

    def test_an_unused_approval_dies_with_its_turn(self):
        subject = self.held()
        message = subject.confirm()

        async def ask(text, turn):  # a model that makes no call at all
            turn.reply = "I could not."

        with mock.patch.object(subject, "_ask", ask):
            turn = subject.think(message, release=True)
        self.assertIsNone(subject._approved)
        self.assertFalse(subject._releasing)
        self.assertTrue(turn.reply.endswith("reboot was not run."))

    def test_a_turn_ending_after_the_yes_does_not_throw_it_away(self):
        """The user can confirm while an ordinary turn is still running.

        That turn cannot spend the approval, and its ending must not clear
        it either, or the release turn queued behind it has nothing to run.
        """
        subject = self.held()
        subject.confirm()

        async def ask(text, turn):
            turn.reply = "It is four."

        with mock.patch.object(subject, "_ask", ask):
            subject.think("what time is it")
        self.assertEqual(subject._approved, ("Bash", {"command": "reboot"}))
        subject._releasing = True
        self.assertEqual(gate(subject, "Bash", {"command": "reboot"}).behavior, "allow")

    def test_the_release_turn_through_think(self):
        """`think(release=True)` opens the gate for the held call, then shuts it."""
        subject = self.held()
        message = subject.confirm()
        verdicts = []

        async def ask(text, turn):
            for command in ("reboot", "ls"):
                out = await subject._pre_tool_use(
                    {"tool_name": "Bash", "tool_input": {"command": command}}, None, None)
                verdicts.append(out["hookSpecificOutput"]["permissionDecision"])
            turn.reply = "Rebooting."

        with mock.patch.object(subject, "_ask", ask):
            turn = subject.think(message, release=True)
        self.assertEqual(verdicts, ["allow", "deny"])
        self.assertEqual(turn.reply, "Rebooting.")
        self.assertFalse(subject._releasing)

    def test_a_write_with_other_content_does_not_match(self):
        subject = self.held("Write", {"file_path": "/tmp/reboot.txt", "content": "x"})
        self.assertEqual(subject.pending, "write /tmp/reboot.txt")
        release(subject)
        result = gate(subject, "Write", {"file_path": "/tmp/reboot.txt", "content": "y"})
        self.assertEqual(result.behavior, "deny")

    def test_bash_description_is_not_part_of_the_match(self):
        """It is the model's label for the command, rarely written twice alike."""
        subject = self.held("Bash", {"command": "reboot", "description": "Reboot"})
        release(subject)
        result = gate(subject, "Bash", {"command": "reboot", "description": "Restart"})
        self.assertEqual(result.behavior, "allow")

    def test_the_release_message_names_the_exact_call(self):
        subject = self.held()
        message = subject.confirm()
        self.assertIn("Bash", message)
        self.assertIn(json.dumps({"command": "reboot"}), message)
        self.assertIsNone(subject.confirm())

    def test_cancel_forgets_the_held_call(self):
        subject = self.held()
        subject.cancel()
        self.assertIsNone(subject.confirm())


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
                           ("WebSearch", {"query": "nixos"})):
            with self.subTest(tool=tool):
                self.assertEqual(gate(brain(), tool, args).behavior, "allow")

    def test_fetching_a_url_is_not_a_read(self):
        """A GET can spend a one-use link or fire a webhook. The URL is the act."""
        result = gate(brain(), "WebFetch", {"url": "https://example.org/unsubscribe?t=1"})
        self.assertEqual(result.behavior, "deny")
        self.assertIn("would have run: fetch https://example.org/unsubscribe", result.message)

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
        """The confirmed release skips the policy -- that is what confirming is.

        It must not skip this. Found while implementing: the plan covered the
        ordinary allow and missed that `_decide` has a second one, so a dry run
        held a reboot, the user said yes, and the replay ran it for real.
        """
        subject = brain()
        gate(subject, "Bash", {"command": "reboot"})
        release(subject)
        result = gate(subject, "Bash", {"command": "reboot"})
        self.assertEqual(result.behavior, "deny")
        self.assertIn("would have run", result.message)
        self.assertIsNone(subject._approved)  # the approval is still spent

    def test_a_dry_run_is_in_the_log(self):
        """`omarchy-voice log` must show a refusal, not something that ran.

        This used to assert only the transcript, which nothing reads, so the
        docstring was untrue: a dry-run refusal never reached the log. The
        sink is what the daemon points at its log (#7).
        """
        subject = brain()
        logged: list[str] = []
        subject.executor.on_record = logged.append
        gate(subject, "Bash", {"command": "touch /tmp/x"})
        self.assertIn("DRYRUN  touch /tmp/x", logged)
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


def options_of(subject):
    """`_options()` with a stand-in SDK, so it can be read without the CLI."""
    sdk = types.SimpleNamespace(ClaudeAgentOptions=lambda **kw: types.SimpleNamespace(**kw),
                                HookMatcher=lambda **kw: types.SimpleNamespace(**kw))
    with mock.patch.dict("sys.modules", {"claude_agent_sdk": sdk}), \
         mock.patch.dict("os.environ", {claude_backend.CLI_ENV: "/bin/claude",
                                        claude_backend.AI_MIRROR_ENV: ""}), \
         mock.patch("shutil.which", return_value=None), \
         mock.patch.object(claude_backend.mcp_server, "build_server"), \
         mock.patch.object(claude_backend.planner, "_system_prompt", return_value="base"):
        return subject._options()


class HookTests(unittest.TestCase):
    """The policy runs in a PreToolUse hook, because the callback cannot see everything (#7).

    Claude Code only consults `can_use_tool` for calls it would have asked
    about. A Read inside the working directory ran past a deny rule on its
    path; a dry run created a real git worktree. A hook runs for every call --
    but only if it always answers, never raises, and is registered on every
    brain. Those are the three things tested here.
    """

    def test_every_call_gets_an_explicit_answer(self):
        """No answer sends the call on to the callback, to be judged twice."""
        for tool, args in (("Read", {"file_path": "/tmp/x"}), ("Bash", {"command": "ls"}),
                           ("Bash", {"command": "sudo rm -rf /"}), ("EnterWorktree", {}),
                           ("SomeNewTool", {"x": 1}), ("mcp__omarchy__hypr_query", {})):
            with self.subTest(tool=tool, args=args):
                out = asyncio.run(brain()._pre_tool_use(
                    {"tool_name": tool, "tool_input": args}, None, None))
                decision = out["hookSpecificOutput"]
                self.assertEqual(decision["hookEventName"], "PreToolUse")
                self.assertIn(decision["permissionDecision"], ("allow", "deny"))
                self.assertTrue(decision["permissionDecisionReason"])

    def test_a_deny_rule_on_a_path_stops_a_read(self):
        """The reproduced bypass: this Read never reached the callback."""
        subject = brain(dry_run=False, deny_patterns=[r"grocery-list"])
        result = gate(subject, "Read", {"file_path": "/home/u/docs/grocery-list.txt"})
        self.assertEqual(result.behavior, "deny")
        self.assertIn("Refused", result.message)

    def test_an_ordinary_read_runs_and_is_logged(self):
        """Auto-approved reads used to leave no record at all."""
        subject = brain(dry_run=False)
        acted: list[str] = []
        subject.executor.on_action = lambda name, desc: acted.append(desc)
        self.assertEqual(gate(subject, "Read", {"file_path": "/tmp/notes"}).behavior, "allow")
        self.assertIn("RUN     read /tmp/notes", subject.executor.transcript)
        self.assertEqual(acted, ["read /tmp/notes"])  # on_action is what the daemon logs

    def test_a_crash_in_the_policy_refuses_the_call(self):
        """A hook that raises lets the call run -- measured. So this one never raises."""
        subject = brain(dry_run=False)
        logged: list[str] = []
        subject.executor.on_record = logged.append
        with mock.patch.object(subject, "_decide", side_effect=RuntimeError("regex blew up")):
            result = gate(subject, "Read", {"file_path": "/tmp/x"})
        self.assertEqual(result.behavior, "deny")
        self.assertIn("not run", result.message)
        self.assertTrue(any(line.startswith("ERROR   Read (RuntimeError: regex blew up")
                            for line in logged))

    def test_a_malformed_hook_input_is_refused(self):
        """Whatever the CLI hands over, the answer is still a decision."""
        subject = brain(dry_run=False)
        for bad in (None, "Read", {"tool_name": "Read", "tool_input": "not a dict"}):
            with self.subTest(bad=bad):
                out = asyncio.run(subject._pre_tool_use(bad, None, None))
                self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_reaching_the_callback_is_an_alarm(self):
        """The hook always answers, so the callback running means it did not."""
        subject = brain(dry_run=False)
        logged: list[str] = []
        subject.executor.on_record = logged.append
        result = asyncio.run(subject._alarm("Read", {"file_path": "/tmp/x"}, None))
        self.assertEqual(result.behavior, "deny")
        self.assertEqual(logged, ["ALARM   Read reached can_use_tool; the hook did not decide"])

    def test_refusals_reach_the_log_and_runs_are_not_logged_twice(self):
        """on_action logs what ran; on_record logs what did not. Never both."""
        subject = brain(dry_run=False)
        logged: list[str] = []
        subject.executor.on_record = logged.append
        gate(subject, "Bash", {"command": "sudo rm -rf /"})   # denied
        gate(subject, "Bash", {"command": "reboot"})          # held
        gate(subject, "Bash", {"command": "ls"})              # ran
        self.assertEqual([line.split()[0] for line in logged], ["DENIED", "HOLD"])

    def test_a_confirmed_run_is_logged_like_any_other(self):
        """This branch used to return before the transcript, on_action and _actions."""
        subject = brain(dry_run=False)
        acted: list[str] = []
        subject.executor.on_action = lambda name, desc: acted.append(desc)
        gate(subject, "Bash", {"command": "reboot"})
        release(subject)
        self.assertEqual(gate(subject, "Bash", {"command": "reboot"}).behavior, "allow")
        self.assertIn("CONFIRM reboot", subject.executor.transcript)
        self.assertEqual(acted, ["reboot"])
        self.assertEqual(subject._actions, ["reboot"])
        self.assertEqual(gate(subject, "Bash", {"command": "reboot"}).behavior, "deny")

    def test_a_dry_run_refuses_what_used_to_be_auto_approved(self):
        """A dry run created a real git worktree, because this never asked."""
        result = gate(brain(), "EnterWorktree", {})
        self.assertEqual(result.behavior, "deny")
        self.assertIn("would have run", result.message)

    def test_a_dry_run_can_still_look_up_tools(self):
        """ToolSearch loads schemas. Refusing it narrates the wrong call."""
        self.assertEqual(gate(brain(), "ToolSearch", {"query": "worktree"}).behavior, "allow")

    def test_every_brain_carries_the_hook(self):
        """Say, the warm brain and the daemon's brain all build on _options()."""
        from omarchy_voice.local_engine import brain_for

        config = Config(dry_run=True)
        for subject in (ClaudeBrain(config, Executor(config)),
                        WarmBrain(config, Executor(config)),
                        brain_for(config, Executor(config))):
            with self.subTest(brain=type(subject).__name__):
                options = options_of(subject)
                (matcher,) = options.hooks["PreToolUse"]
                self.assertIsNone(matcher.matcher)  # every tool, including new ones
                self.assertEqual(matcher.hooks, [subject._pre_tool_use])
                self.assertEqual(options.can_use_tool, subject._alarm)
                self.assertEqual(options.permission_mode, "default")

    def test_the_daemon_brain_logs_through_its_executor(self):
        """One route to the log (#13): the brain's refusals use its Executor's sink."""
        from omarchy_voice.local_engine import brain_for

        logged: list[str] = []
        config = Config(dry_run=False)
        subject = brain_for(config, Executor(config, on_record=logged.append))
        gate(subject, "Bash", {"command": "sudo rm -rf /"})
        self.assertEqual(len(logged), 1)
        self.assertTrue(logged[0].startswith("DENIED  sudo rm -rf /"))

    def test_a_read_that_mentions_a_confirm_word_runs(self):
        """A lookup is not the thing it looks up (#100)."""
        subject = brain(dry_run=False)
        self.assertEqual(gate(subject, "Read",
                              {"file_path": "/home/u/notes/reboot-checklist.md"}).behavior,
                         "allow")
        # _decide itself: #94 no longer offers WebSearch, but the gate judges
        # whatever the CLI calls.
        self.assertEqual(gate(subject, "WebSearch",
                              {"query": "how do I reboot hyprland"}).behavior, "allow")
        self.assertIsNone(subject.pending)

    def test_deny_rules_still_stop_reads(self):
        subject = brain(dry_run=False)
        self.assertEqual(gate(subject, "WebSearch",
                              {"query": "sudo password prompt"}).behavior, "deny")

    def test_secret_paths_are_refused_to_read(self):
        from test_policy import SECRET_PATHS
        subject = brain(dry_run=False)
        for path in SECRET_PATHS:
            with self.subTest(path=path):
                result = gate(subject, "Read", {"file_path": path})
                self.assertEqual(result.behavior, "deny")
                self.assertIn("Refused", result.message)

    def test_a_user_path_rule_still_refuses_a_read(self):
        from omarchy_voice.config import DEFAULT_DENY
        subject = brain(dry_run=False, deny_patterns=[*DEFAULT_DENY, "/home/u/private/"])
        self.assertEqual(gate(subject, "Read",
                              {"file_path": "/home/u/private/diary.md"}).behavior, "deny")

    def test_an_action_with_a_confirm_word_is_still_held(self):
        subject = brain(dry_run=False)
        self.assertEqual(gate(subject, "Bash", {"command": "systemctl reboot"}).behavior,
                         "deny")
        self.assertEqual(subject.pending, "systemctl reboot")

    def test_is_read(self):
        from omarchy_voice.claude_backend import _is_read
        for tool in ("screenshot", "mcp__omarchy__notify"):
            with self.subTest(tool=tool):
                self.assertFalse(_is_read(tool))
        for tool in ("mcp__omarchy__screenshot", "Read", "ToolSearch"):
            with self.subTest(tool=tool):
                self.assertTrue(_is_read(tool))


class AiMirrorTests(unittest.TestCase):
    """ai-mirror is offered to the brain whenever it is installed, and only then."""

    def options(self, ai_mirror: str, desktop_control: bool = True):
        sdk = types.SimpleNamespace(ClaudeAgentOptions=lambda **kw: types.SimpleNamespace(**kw),
                                    HookMatcher=lambda **kw: types.SimpleNamespace(**kw))
        config = Config(dry_run=True, desktop_control=desktop_control)
        with mock.patch.dict("sys.modules", {"claude_agent_sdk": sdk}), \
             mock.patch.dict("os.environ", {claude_backend.CLI_ENV: "/bin/claude",
                                            claude_backend.AI_MIRROR_ENV: ai_mirror}), \
             mock.patch("shutil.which", return_value=None), \
             mock.patch.object(claude_backend.mcp_server, "build_server"), \
             mock.patch.object(claude_backend.planner, "_system_prompt", return_value="base"):
            return ClaudeBrain(config, Executor(config))._options()

    def test_installed_ai_mirror_is_a_second_server_once_asked_for(self):
        """Rewritten for #17: it always described the opted-in case."""
        options = self.options("/bin/ai-mirror")
        self.assertEqual(options.mcp_servers["ai-mirror"],
                         {"type": "stdio", "command": "/bin/ai-mirror", "args": ["mcp"]})
        self.assertIn("mcp__ai-mirror__", options.system_prompt)

    def test_installed_is_not_the_same_as_wanted(self):
        """#17: the binary being on PATH is not a decision to hand over the desktop."""
        options = self.options("/bin/ai-mirror", desktop_control=False)
        self.assertEqual(set(options.mcp_servers), {"omarchy"})
        self.assertNotIn("mcp__ai-mirror__", options.system_prompt)

    def test_the_prompt_tells_the_brain_to_ask_and_wait(self):
        """ai-mirror#10: mode=agent asks a human, so "first" is no longer the instruction."""
        prompt = self.options("/bin/ai-mirror").system_prompt
        self.assertNotIn("mode=agent first", prompt)
        self.assertIn("status", prompt)
        self.assertIn("pending", prompt)

    def test_no_ai_mirror_no_server(self):
        options = self.options("")
        self.assertEqual(set(options.mcp_servers), {"omarchy"})
        self.assertEqual(options.system_prompt, "base")


class AlwaysLoadTests(unittest.TestCase):
    """#84: our tools are loaded up front, and only ours.

    Deferred, the first use of each of our tools cost a ToolSearch round trip.
    The fix is per server, so ai-mirror and Claude Code's built-ins stay
    deferred and an external `omarchy-voice mcp` client keeps its default.
    """

    def test_our_tools_are_loaded_up_front(self):
        """Without the key every first use of a tool pays a ToolSearch turn."""
        config = Config(dry_run=True)
        options = options_of(ClaudeBrain(config, Executor(config)))
        self.assertIs(options.mcp_servers["omarchy"]["alwaysLoad"], True)

    def test_ai_mirror_stays_deferred(self):
        """The scope is ours: someone else's tools keep Claude Code's default."""
        options = AiMirrorTests().options("/bin/ai-mirror")
        self.assertNotIn("alwaysLoad", options.mcp_servers["ai-mirror"])
        self.assertIs(options.mcp_servers["omarchy"]["alwaysLoad"], True)

    def test_scope_is_per_server_not_global(self):
        """ENABLE_TOOL_SEARCH=false would load every server's tools: 181 K tokens, not 58 K."""
        for subscription in (True, False):
            with self.subTest(claude_use_subscription=subscription):
                config = Config(dry_run=True, claude_use_subscription=subscription)
                options = options_of(ClaudeBrain(config, Executor(config)))
                self.assertNotIn("ENABLE_TOOL_SEARCH", options.env)

    def test_every_claude_brain_gets_it(self):
        """The voice path is WarmBrain; it builds on ClaudeBrain._options() and must keep the key."""
        from omarchy_voice.claude_backend import WarmBrain
        config = Config(dry_run=True)
        options = options_of(WarmBrain(config, Executor(config)))
        self.assertIs(options.mcp_servers["omarchy"]["alwaysLoad"], True)


class SdkPassThroughTests(unittest.TestCase):
    """#84 and #94: the real SDK hands our options to the CLI.

    #84: `alwaysLoad` is not in the SDK's typed McpSdkServerConfig; on 0.2.152
    the SDK passes every sdk-server key except `instance` into --mcp-config.
    If that fails after an SDK bump, the fallback is per-tool
    `_meta["anthropic/alwaysLoad"]` (spec decision 3, option C).

    #94: on 0.2.152 `tools`, `strict_mcp_config` and `setting_sources` become
    --tools, --strict-mcp-config and --setting-sources= (subprocess_cli.py
    581-591, 690-691, 719-720). If --setting-sources= disappears after an SDK
    bump, `[]` has started being treated as unset, and the user's setup is
    loaded again.
    """

    def command(self, options):
        from claude_agent_sdk import ClaudeAgentOptions
        from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
        real = ClaudeAgentOptions(tools=getattr(options, "tools", None),
                                  setting_sources=getattr(options, "setting_sources", None),
                                  strict_mcp_config=getattr(options, "strict_mcp_config", False),
                                  mcp_servers=options.mcp_servers, cli_path="/bin/true")
        return SubprocessCLITransport(prompt="x", options=real)._build_command()

    def test_the_isolation_reaches_the_cli(self):
        config = Config(dry_run=True)
        command = self.command(options_of(ClaudeBrain(config, Executor(config))))
        self.assertIn("--tools", command)
        self.assertEqual(command[command.index("--tools") + 1], "Read,ToolSearch")
        self.assertIn("--setting-sources=", command)  # one argv item, empty value
        self.assertIn("--strict-mcp-config", command)
        self.assertNotIn("--allowedTools", command)

    def test_strict_mcp_keeps_ai_mirror(self):
        command = self.command(AiMirrorTests().options("/bin/ai-mirror"))
        cfg = json.loads(command[command.index("--mcp-config") + 1])
        self.assertEqual(set(cfg["mcpServers"]), {"omarchy", "ai-mirror"})
        self.assertIn("--strict-mcp-config", command)

    def test_the_key_reaches_the_cli(self):
        import json
        from claude_agent_sdk import ClaudeAgentOptions
        from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
        config = Config(dry_run=True)
        servers = options_of(ClaudeBrain(config, Executor(config))).mcp_servers
        transport = SubprocessCLITransport(
            prompt="x", options=ClaudeAgentOptions(mcp_servers=servers, cli_path="/bin/true"))
        command = transport._build_command()
        cfg = json.loads(command[command.index("--mcp-config") + 1])
        self.assertIs(cfg["mcpServers"]["omarchy"]["alwaysLoad"], True)
        self.assertNotIn("instance", cfg["mcpServers"]["omarchy"])


class BuiltinToolsTests(unittest.TestCase):
    """#94: our tools, a named few built-ins, and none of the user's setup.

    AskUserQuestion reached a voice turn that nobody could answer. The fix is
    an allowlist (`tools`), never `allowed_tools`, plus isolation from the
    user's Claude Code settings and MCP servers.
    """

    def options(self):
        config = Config(dry_run=True)
        return options_of(ClaudeBrain(config, Executor(config)))

    def test_only_the_named_builtins(self):
        options = self.options()
        self.assertEqual(options.tools, ["Read", "ToolSearch"])
        for tool in ("AskUserQuestion", "EnterPlanMode", "ExitPlanMode", "Bash",
                     "WebFetch", "Write", "Edit"):
            self.assertNotIn(tool, options.tools)

    def test_the_users_setup_stays_out(self):
        options = self.options()
        self.assertIsNotNone(options.setting_sources)  # None is the CLI default
        self.assertEqual(options.setting_sources, [])
        self.assertIs(options.strict_mcp_config, True)

    def test_never_allowed_tools(self):
        """allowed_tools auto-approves what it names and shadows can_use_tool."""
        self.assertFalse(hasattr(self.options(), "allowed_tools"))

    def test_every_brain_gets_it(self):
        from omarchy_voice import local_engine
        from omarchy_voice.claude_backend import WarmBrain
        config = Config(dry_run=True)
        for subject in (ClaudeBrain(config, Executor(config)),
                        WarmBrain(config, Executor(config)),
                        local_engine.brain_for(config, Executor(config))):
            with self.subTest(brain=type(subject).__name__):
                options = options_of(subject)
                self.assertEqual(options.tools, ["Read", "ToolSearch"])
                self.assertEqual(options.setting_sources, [])
                self.assertIs(options.strict_mcp_config, True)

    def test_desktop_control_on_keeps_ai_mirror(self):
        """ToolSearch is what loads ai-mirror's deferred tools (#84)."""
        options = AiMirrorTests().options("/bin/ai-mirror")
        self.assertEqual(set(options.mcp_servers), {"omarchy", "ai-mirror"})
        self.assertIs(options.strict_mcp_config, True)
        self.assertEqual(options.tools, ["Read", "ToolSearch"])

    def test_desktop_control_off_is_ours_alone(self):
        options = AiMirrorTests().options("/bin/ai-mirror", desktop_control=False)
        self.assertEqual(set(options.mcp_servers), {"omarchy"})
        self.assertIs(options.strict_mcp_config, True)


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
        # FakeClient answers by the exact text it is sent, and the real helper
        # would run hyprctl. The snapshot tests below put it back (#69).
        self.enterContext(mock.patch.object(claude_backend, "_with_desktop",
                                            side_effect=lambda text, *a, **k: text))

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

    async def test_an_unused_release_says_so_instead_of_done(self):
        """A release turn that made no call must not report "Done." (#76)."""
        message = claude_backend._release_message("Bash", {"command": "reboot"})
        subject = await self.warm({message: [result()]})
        await verdict(subject, "Bash", {"command": "reboot"})
        self.assertEqual(subject.confirm(), message)
        spoken = [s async for s in subject.ask_stream(message, release=True)]
        self.assertEqual(spoken, ["reboot was not run."])
        self.assertIsNone(subject._approved)
        self.assertFalse(subject._releasing)

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

    async def test_what_ran_without_the_model_is_told_once(self):
        """The router ran it (#71); the next turn must know, and only it."""
        subject = await self.warm()
        subject.note("User said \"close weather\" → dispatch window.close window='address:0x3' → ok")
        await self.collect(subject, "hi")
        await self.collect(subject, "again")
        first, second = self.client.asked[1:]
        self.assertTrue(first.startswith("# Done without you since your last turn"))
        self.assertIn("- User said \"close weather\" → dispatch window.close window='address:0x3' → ok", first)
        self.assertTrue(first.endswith("hi"))
        self.assertEqual(second, "again")

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
        outcome = await verdict(subject, "Bash", {"command": "sudo rm -rf /"})
        self.assertEqual(outcome.behavior, "deny")
        held = await verdict(subject, "Bash", {"command": "reboot"})
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


class SnapshotPerTurnTests(unittest.IsolatedAsyncioTestCase):
    """The desktop goes in front of every turn, not into a prompt built once (#69).

    A warm session lives for days. With the snapshot in its system prompt, the
    model was told a 34-hour-old window list was "the desktop right now".
    """

    def setUp(self):
        self.enterContext(mock.patch.object(ClaudeBrain, "_options",
                                            lambda self: types.SimpleNamespace()))
        self.live = self.enterContext(mock.patch.object(
            claude_backend.capabilities, "live_state", side_effect=["A", "B"]))

    async def warm(self):
        self.client = FakeClient({})
        with mock.patch.object(claude_backend, "_new_client", return_value=self.client):
            subject = brain_warm()
            await subject.start()
        return subject

    async def test_every_warm_turn_carries_its_own_snapshot(self):
        subject = await self.warm()
        for text in ("what's open", "and now"):
            [_ async for _ in subject.ask_stream(text)]
        first, second = self.client.asked[1:]
        self.assertTrue(first.startswith("# The desktop right now"))
        self.assertIn("\n\nA\n\n", first)
        self.assertTrue(first.endswith("what's open"))
        self.assertIn("\n\nB\n\n", second)
        self.assertTrue(second.endswith("and now"))

    async def test_the_notes_go_in_front_of_the_snapshot(self):
        subject = await self.warm()
        subject.note("User said \"close weather\" → dispatch window.close window='address:0x3' → ok")
        [_ async for _ in subject.ask_stream("what's open")]
        sent = self.client.asked[1]
        self.assertTrue(sent.startswith("# Done without you since your last turn"))
        self.assertLess(sent.index("# Done without you"),
                        sent.index("# The desktop right now"))
        self.assertTrue(sent.endswith("# What the user said\n\nwhat's open"))

    def test_a_turn_nobody_spoke_is_not_headed_as_the_users(self):
        """A finished watch brings its own heading (#74)."""
        announced = claude_backend._with_desktop("# A watched command finished",
                                                 from_user=False)
        said = claude_backend._with_desktop("hi")
        self.assertTrue(announced.startswith("# The desktop right now"))
        self.assertNotIn("What the user said", announced)
        self.assertTrue(announced.endswith("\n\nA\n\n# A watched command finished"))
        self.assertIn("# What the user said\n\nhi", said)

    async def test_ask_stream_passes_it_through(self):
        subject = await self.warm()
        [_ async for _ in subject.ask_stream("# A watched command finished",
                                             from_user=False)]
        [sent] = self.client.asked[1:]
        self.assertNotIn("What the user said", sent)
        self.assertTrue(sent.endswith("# A watched command finished"))

    async def test_the_warm_up_carries_no_desktop(self):
        await self.warm()
        self.assertEqual(self.client.asked, [claude_backend.WARM_UP])
        self.live.assert_not_called()

    async def test_the_cold_brain_carries_one_too(self):
        client = FakeClient({})

        class Session:
            def __init__(self, options):
                pass

            async def __aenter__(self):
                return client

            async def __aexit__(self, *exc):
                return False

        sdk = types.SimpleNamespace(AssistantMessage=type("AssistantMessage", (), {}),
                                    ResultMessage=type("ResultMessage", (), {}),
                                    ClaudeSDKClient=Session)
        with mock.patch.dict("sys.modules", {"claude_agent_sdk": sdk}):
            await brain()._ask("hi", Turn("hi"))
        [sent] = client.asked
        self.assertTrue(sent.startswith("# The desktop right now"))
        self.assertIn("\n\nA\n\n", sent)
        self.assertTrue(sent.endswith("hi"))


class SystemPromptTests(unittest.TestCase):
    def test_the_claude_system_prompt_leaves_the_desktop_out(self):
        """It is sent per turn instead (#69); a copy here would be stale by morning."""
        sdk = types.SimpleNamespace(ClaudeAgentOptions=lambda **kw: types.SimpleNamespace(**kw),
                                    HookMatcher=lambda **kw: types.SimpleNamespace(**kw))
        with mock.patch.dict("sys.modules", {"claude_agent_sdk": sdk}), \
             mock.patch.dict("os.environ", {claude_backend.CLI_ENV: "/bin/claude",
                                            claude_backend.AI_MIRROR_ENV: ""}), \
             mock.patch("shutil.which", return_value=None), \
             mock.patch.object(claude_backend.mcp_server, "build_server"), \
             mock.patch.object(claude_backend.planner, "_system_prompt",
                               return_value="base") as prompt:
            brain()._options()
        prompt.assert_called_once_with(live=False)
