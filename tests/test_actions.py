"""Actions and routines (#157): recipes on disk, the gate, the runner, the menu
and the timers. Everything runs in the throwaway HOME from _isolated.

Run with: python3 -m unittest discover -s tests
"""

import os
import shutil
import tomllib
import unittest

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice import actions as act
from omarchy_voice.config import Config
from omarchy_voice.tools import Executor


def write(name: str, text: str) -> None:
    act.ACTIONS_DIR.mkdir(parents=True, exist_ok=True)
    act.path_for(name).write_text(text)


class Clean(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(act.ACTIONS_DIR, ignore_errors=True)

    tearDown = setUp


class TestFiles(Clean):
    def test_round_trip(self):
        a = act.Action(
            name="dev-setup", description='Say "hi"\nthen go', phrases=["dev setup"],
            steps=[act.Step(tool="hypr_dispatch",
                            args={"dispatcher": "focus", "args": {"workspace": "4"}}),
                   act.Step(ask="summarise the repo"),
                   act.Step(tool="launch_app", args={"app": "chromium", "url": "https://x"})],
            when="Mon..Fri 08:00", enabled=True)
        write("other", '[[step]]\nask = "x"\n')
        act.save(a)
        self.assertEqual(act.load("dev-setup"), a)

    def test_errors_name_the_step(self):
        cases = {
            '[[step]]\ntool = "nope"\n': "step 1: unknown tool",
            '[[step]]\nask = "a"\n[[step]]\nask = "b"\ntool = "hypr_query"\n': "step 2: needs exactly one",
            '[[step]]\ntool = "launch_app"\n': "step 1: launch_app needs app",
            '[[step]]\ntool = "launch_app"\nargs = { app = "x", bogus = 1 }\n': "does not take bogus",
            '[[step]]\ntool = "run_shell"\nargs = { command = "ls" }\n': "step 1: run_shell needs allow_shell",
            '[[step]]\naction = "ghost"\n': "step 1: no action called 'ghost'",
            'descripton = "typo"\n[[step]]\nask = "a"\n': "unknown key(s): descripton",
            'description = "empty"\n': "at least one [[step]]",
        }
        for text, expected in cases.items():
            with self.subTest(expected=expected):
                write("bad", text)
                _, broken = act.load_all()
                self.assertIn(expected, broken["bad"])

    def test_run_shell_allowed_with_the_shell_on(self):
        write("sh", '[[step]]\ntool = "run_shell"\nargs = { command = "ls" }\n')
        self.assertIn("sh", act.load_all(allow_shell=True)[0])

    def test_cycle_refused(self):
        write("a", '[[step]]\naction = "b"\n')
        write("b", '[[step]]\naction = "a"\n')
        _, broken = act.load_all()
        self.assertIn("cycle", broken["a"])

    def test_depth(self):
        write("a", '[[step]]\naction = "b"\n')
        write("b", '[[step]]\naction = "c"\n')
        write("c", '[[step]]\nask = "x"\n')
        self.assertIn("a", act.load_all()[0])  # three levels is the limit
        write("c", '[[step]]\naction = "d"\n')
        write("d", '[[step]]\nask = "x"\n')
        self.assertIn("nested deeper than 3", act.load_all()[1]["a"])

    def test_bad_name(self):
        with self.assertRaises(act.ActionError):
            act.save(act.Action(name="../escape", steps=[act.Step(ask="x")]))

    def test_comments_are_not_silently_dropped(self):
        write("c", '# mine\n# a note I care about\n[[step]]\nask = "x"\n')
        a = act.load("c")
        with self.assertRaisesRegex(act.ActionError, "comments"):
            act.save(a)
        act.save(a, force=True)
        self.assertNotIn("a note I care about", act.path_for("c").read_text())

    def test_symlink_is_home_managers(self):
        target = act.ACTIONS_DIR.parent / "declared.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('[[step]]\nask = "x"\n')
        act.ACTIONS_DIR.mkdir(parents=True, exist_ok=True)
        os.symlink(target, act.path_for("hm"))
        a = act.load("hm")
        with self.assertRaisesRegex(act.ActionError, "Home Manager"):
            act.save(a, force=True)
        with self.assertRaisesRegex(act.ActionError, "Home Manager"):
            act.delete("hm")

    def test_delete_goes_to_trash(self):
        write("gone", '[[step]]\nask = "x"\n')
        dest = act.delete("gone")
        self.assertFalse(act.path_for("gone").exists())
        self.assertEqual(tomllib.loads(dest.read_text())["step"][0]["ask"], "x")
        self.assertNotIn("gone", act.load_all()[0])  # .trash is not scanned


def executor(**kw) -> Executor:
    kw.setdefault("dry_run", True)
    return Executor(Config(**kw))


class TestGate(unittest.TestCase):
    """The approved set skips a hold and nothing else (plan step 2)."""

    NEXT = act.approval_key("media next")

    def test_unapproved_holds(self):
        ex = executor(confirm_patterns=[r"media next"])
        r = ex._call_locked("media_control", {"action": "next"})
        self.assertFalse(r.ok)
        self.assertEqual(ex.pending, ("media_control", {"action": "next"}))

    def test_approved_runs(self):
        ex = executor(confirm_patterns=[r"media next"])
        r = ex._call_locked("media_control", {"action": "next"}, approved=frozenset({self.NEXT}))
        self.assertTrue(r.ok, r.output)
        self.assertIsNone(ex.pending)
        self.assertIn("APPROVED media next", ex.transcript)

    def test_deny_is_never_approved(self):
        ex = executor(deny_patterns=[r"media next"], confirm_patterns=[r"media next"])
        r = ex._call_locked("media_control", {"action": "next"}, approved=frozenset({self.NEXT}))
        self.assertFalse(r.ok)
        self.assertIn("refused", r.output)

    def test_edited_step_lapses(self):
        ex = executor(confirm_patterns=[r"media"])
        r = ex._call_locked("media_control", {"action": "previous"}, approved=frozenset({self.NEXT}))
        self.assertFalse(r.ok)
        self.assertIsNotNone(ex.pending)

    def test_shell_off_command_approved(self):
        ex = executor()  # allow_shell off: a command is held (#112)
        args = {"command": "herdr"}
        self.assertFalse(ex._call_locked("run_in_terminal", args).ok)
        ex.pending = None
        key = act.approval_key("run in terminal: herdr")
        self.assertTrue(ex._call_locked("run_in_terminal", args, approved=frozenset({key})).ok)
        self.assertFalse(ex._releasing)


if __name__ == "__main__":
    unittest.main()
