"""Actions and routines (#157): recipes on disk, the gate, the runner, the menu
and the timers. Everything runs in the throwaway HOME from _isolated.

Run with: python3 -m unittest discover -s tests
"""

import json
import os
import re
import shutil
import tomllib
import unittest

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice import actions as act
from omarchy_voice.config import Config
from omarchy_voice.tools import Executor, tools_for


# Nothing in this file may reach the real user manager. Set again in
# TestTimers.setUp: another test module may have replaced it since import.
SYSTEMCTL: list[tuple] = []


def record_systemctl(*args):
    SYSTEMCTL.append(args)
    return True, ""


act._systemctl = record_systemctl


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


class TestRunner(Clean):
    """Plan step 3."""

    def setUp(self):
        super().setUp()
        act.APPROVALS_FILE.unlink(missing_ok=True)
        write("inner", '[[step]]\ntool = "media_control"\nargs = { action = "next" }\n')
        write("outer", '[[step]]\ntool = "media_control"\nargs = { action = "pause" }\n'
                       '[[step]]\naction = "inner"\n[[step]]\nask = "summarise"\n')
        self.known, broken = act.load_all()
        self.assertEqual(broken, {})

    def run_outer(self, ex, ask=lambda text, n: (True, "sure"), start=1):
        return act.run(self.known["outer"], self.known, ex, ask=ask, start=start)

    def test_all_steps_run(self):
        r = self.run_outer(executor())
        self.assertTrue(r.ok, r.summary())
        self.assertEqual(len(r.lines), 3)

    def test_unapproved_hold_stops_there(self):
        ex = executor(confirm_patterns=[r"media next"])
        r = self.run_outer(ex)
        self.assertFalse(r.ok)
        self.assertEqual(r.held, ("inner", 2, "media next"))
        self.assertIn("step 2 of 3", r.stopped)
        self.assertEqual(len(r.lines), 1)  # the ask after it did not run

    def test_approval_belongs_to_the_owning_action(self):
        ex = executor(confirm_patterns=[r"media next"])
        act.grant("outer", ["media next"])  # wrong owner: still held
        self.assertFalse(self.run_outer(ex).ok)
        ex.pending = None
        act.grant("inner", ["media next"])
        self.assertTrue(self.run_outer(ex).ok)

    def test_edited_step_approval_lapses(self):
        act.grant("inner", ["media next"])
        write("inner", '[[step]]\ntool = "media_control"\nargs = { action = "previous" }\n')
        known, _ = act.load_all()
        r = act.run(known["outer"], known, executor(confirm_patterns=[r"media"]),
                    ask=lambda t, n: (True, ""))
        self.assertEqual(r.held[1], 1)

    def test_in_turn_ask_yields_with_where_to_resume(self):
        r = self.run_outer(executor(), ask=lambda text, n: None)
        self.assertTrue(r.ok)
        self.assertIn("Step 3 of 3 is yours to do now: summarise", r.yielded)
        write("outer2", '[[step]]\nask = "first"\n[[step]]\nask = "second"\n')
        known, _ = act.load_all()
        r = act.run(known["outer2"], known, executor(), ask=lambda t, n: None)
        self.assertIn("name=outer2, start=2", r.yielded)
        r = act.run(known["outer2"], known, executor(), ask=lambda t, n: None, start=2)
        self.assertIn("Step 2 of 2 is yours to do now: second", r.yielded)
        self.assertNotIn("start=", r.yielded)

    def test_failed_ask_stops(self):
        r = self.run_outer(executor(), ask=lambda text, n: (False, "no model"))
        self.assertFalse(r.ok)
        self.assertIn("step 3 of 3: no model", r.stopped)

    def test_held_steps_asks_the_real_gate(self):
        cfg = Config(confirm_patterns=[r"media next"])
        self.assertEqual(act.held_steps(self.known["outer"], self.known, cfg),
                         [("inner", 2, "media next")])
        cfg = Config(confirm_patterns=[r"media next"], deny_patterns=[r"media next"])
        self.assertEqual(act.held_steps(self.known["outer"], self.known, cfg), [])


class TestTool(Clean):
    """The `action` tool: plan step 4."""

    STEPS = [{"tool": "media_control", "args": {"action": "next"}}, {"ask": "summarise"}]

    def setUp(self):
        super().setUp()
        act.APPROVALS_FILE.unlink(missing_ok=True)

    def test_save_holds_then_writes_and_approves(self):
        ex = Executor(Config(confirm_patterns=[r"media next"]))
        r = ex.call("action", {"do": "save", "name": "tidy", "steps": self.STEPS,
                               "schedule": {"when": "every 2h", "enabled": False}})
        self.assertFalse(r.ok)
        self.assertFalse(act.path_for("tidy").exists())
        self.assertIn("save action tidy: 1. media next; 2. ask: summarise; runs every 2h",
                      ex.describe(*ex.pending))
        r = ex.run_pending()
        self.assertTrue(r.ok, r.output)
        self.assertIn("1 step(s) approved", r.output)
        self.assertEqual(act.load("tidy").when, "every 2h")
        self.assertIn(act.approval_key("media next"), act.approvals()["tidy"])

    def test_bad_recipe_does_not_spend_the_yes(self):
        ex = Executor(Config())
        r = ex.call("action", {"do": "save", "name": "x", "steps": [{"tool": "nope"}]})
        self.assertFalse(r.ok)
        self.assertIn("unknown tool", r.output)
        self.assertIsNone(ex.pending)

    def test_delete_holds(self):
        write("old", '[[step]]\nask = "x"\n')
        ex = Executor(Config())
        self.assertFalse(ex.call("action", {"do": "delete", "name": "old"}).ok)
        self.assertTrue(act.path_for("old").exists())
        self.assertTrue(ex.run_pending().ok)
        self.assertFalse(act.path_for("old").exists())

    def test_list_and_show_are_reads(self):
        write("a", 'description = "A thing"\n[[step]]\nask = "x"\n')
        write("b", '[[step]]\ntool = "nope"\n')
        ex = executor(confirm_patterns=[r"action"])  # would hold anything not a read
        r = ex.call("action", {"do": "list"})
        self.assertTrue(r.ok)
        self.assertIn("a: A thing", r.output)
        self.assertIn("b: BROKEN", r.output)
        self.assertIn('ask = "x"', ex.call("action", {"do": "show", "name": "a"}).output)

    def test_run_hold_resumes_the_action_on_confirm(self):
        write("t", '[[step]]\ntool = "media_control"\nargs = { action = "pause" }\n'
                   '[[step]]\ntool = "media_control"\nargs = { action = "next" }\n'
                   '[[step]]\ntool = "media_control"\nargs = { action = "previous" }\n')
        ex = executor(confirm_patterns=[r"media next"])
        r = ex.call("action", {"do": "run", "name": "t"})
        self.assertFalse(r.ok)
        self.assertIn("stopped at step 2 of 3", r.output)
        self.assertEqual(ex.pending[0], "action")
        self.assertEqual(ex.pending[1]["start"], 2)
        r = ex.run_pending()
        self.assertTrue(r.ok, r.output)
        self.assertIn("3. ", r.output)
        self.assertFalse(ex._releasing)
        # approved for good: the next run does not ask
        self.assertTrue(ex.call("action", {"do": "run", "name": "t"}).ok)

    def test_model_cannot_approve_its_own_step(self):
        write("t", '[[step]]\ntool = "media_control"\nargs = { action = "next" }\n')
        ex = executor(confirm_patterns=[r"media next"])
        r = ex.call("action", {"do": "run", "name": "t", "_approve": "t\tmedia next"})
        self.assertFalse(r.ok)
        self.assertNotIn("t", act.approvals())

    def test_run_yields_ask_to_the_model(self):
        write("t", '[[step]]\nask = "check my email"\n[[step]]\nask = "then this"\n')
        r = executor().call("action", {"do": "run", "name": "t"})
        self.assertTrue(r.ok)
        self.assertIn("yours to do now: check my email", r.output)
        self.assertIn("start=2", r.output)

    def test_schema_names_saved_actions(self):
        write("dev-setup", '[[step]]\nask = "x"\n')
        schema = next(s for s in tools_for(Config()) if s["name"] == "action")
        self.assertIn("Saved: dev-setup.", schema["description"])


MENU = """{
  // Extend the Quickshell Omarchy menu with JSONC.
  // "personal": {"icon":"","label":"Personal"},
  "help": {"icon": "\U000f0674", "label": "Help", "action": "nixi", "aliases": ["how"]},
  "system.pkgs": {
    "icon": "\\u2744",
    "label": "packages"
  }
  // trailing comment, no trailing comma above
}
"""


def parse_jsonc(text: str) -> dict:
    text = "\n".join(l for l in text.splitlines() if not l.strip().startswith("//"))
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", text))


class TestMenu(Clean):
    """Plan step 5."""

    def setUp(self):
        super().setUp()
        act.MENU_FILE.unlink(missing_ok=True)
        act.MENU_FILE.parent.mkdir(parents=True, exist_ok=True)
        write("dev-setup", 'description = "Dev"\n[[step]]\nask = "x"\n')
        write("morning", '[[step]]\nask = "x"\n[schedule]\nwhen = "08:00"\nenabled = true\n')
        self.known, _ = act.load_all()

    def outside(self, text: str) -> str:
        b, e = text.find(act.MENU_BEGIN), text.find(act.MENU_END)
        return text[:b] + text[e + len(act.MENU_END):]

    def test_insert_then_rewrite_touches_only_the_block(self):
        act.MENU_FILE.write_text(MENU)
        self.assertIsNone(act.write_menu_rows(self.known))
        first = act.MENU_FILE.read_text()
        self.assertEqual((act.MENU_FILE.parent / "omarchy-menu.jsonc.bak-omarchy-voice").read_text(), MENU)
        rows = parse_jsonc(first)
        self.assertEqual(rows["help"]["action"], "nixi")
        self.assertEqual(rows["voice.actions.dev-setup.run"]["action"],
                         "omarchy-voice action run dev-setup")
        self.assertIn("omarchy-voice-routine-morning.timer",
                      rows["voice.actions.morning.routine"]["checked"])
        self.assertNotIn("voice.actions.dev-setup.routine", rows)
        # every byte the user wrote is still there, in order
        # every byte the user wrote is still there, plus the one comma their
        # last entry needed
        self.assertEqual(self.outside(first).replace("\n\n\n", "\n"),
                         MENU.replace("  }\n  // trailing", "  },\n  // trailing"))
        act.path_for("dev-setup").unlink()
        known, _ = act.load_all()
        self.assertIsNone(act.write_menu_rows(known))
        second = act.MENU_FILE.read_text()
        self.assertEqual(self.outside(second), self.outside(first))
        self.assertNotIn("voice.actions.dev-setup", parse_jsonc(second))

    def test_no_change_no_write(self):
        act.MENU_FILE.write_text(MENU)
        act.write_menu_rows(self.known)
        bak = act.MENU_FILE.parent / "omarchy-menu.jsonc.bak-omarchy-voice"
        bak.unlink()
        act.write_menu_rows(self.known)
        self.assertFalse(bak.exists())

    def test_missing_file_is_created(self):
        self.assertIsNone(act.write_menu_rows(self.known))
        self.assertIn("voice.new", parse_jsonc(act.MENU_FILE.read_text()))

    def test_symlink_is_left_alone(self):
        target = act.MENU_FILE.parent / "managed.jsonc"
        target.write_text(MENU)
        os.symlink(target, act.MENU_FILE)
        self.assertIn("Home Manager", act.write_menu_rows(self.known))
        self.assertEqual(target.read_text(), MENU)

    def test_one_marker_is_refused(self):
        act.MENU_FILE.write_text(MENU.replace("}\n", "\n" + act.MENU_BEGIN + "\n}\n", 1))
        before = act.MENU_FILE.read_text()
        self.assertIn("only one", act.write_menu_rows(self.known))
        self.assertEqual(act.MENU_FILE.read_text(), before)


class TestTimers(Clean):
    """Plan step 6."""

    def setUp(self):
        super().setUp()
        shutil.rmtree(act.UNIT_DIR, ignore_errors=True)
        SYSTEMCTL.clear()
        act._systemctl = record_systemctl

    def action(self, when, enabled=True, name="r"):
        return act.Action(name=name, steps=[act.Step(ask="x")], when=when, enabled=enabled)

    def test_calendar(self):
        units = act.unit_texts(self.action("Mon..Fri 08:00"), "/bin/ov")
        self.assertIn("ExecStart=/bin/ov action run r --unattended",
                      units["omarchy-voice-routine-r.service"])
        timer = units["omarchy-voice-routine-r.timer"]
        self.assertIn("OnCalendar=Mon..Fri 08:00\nPersistent=true", timer)
        self.assertIn("WantedBy=graphical-session.target", timer)

    def test_every(self):
        timer = act.unit_texts(self.action("every 2h"), "x")["omarchy-voice-routine-r.timer"]
        self.assertIn("OnBootSec=2m\nOnUnitActiveSec=2h", timer)

    def test_login_is_a_service_only(self):
        units = act.unit_texts(self.action("login"), "x")
        self.assertEqual(list(units), ["omarchy-voice-routine-r.service"])
        self.assertIn("[Install]\nWantedBy=graphical-session.target", units["omarchy-voice-routine-r.service"])
        act.write_timers({"r": self.action("login")}, "x")
        self.assertIn(("enable", "omarchy-voice-routine-r.service"), SYSTEMCTL)

    def test_lifecycle(self):
        act.write_timers({"r": self.action("every 1h")}, "x")
        self.assertTrue((act.UNIT_DIR / "omarchy-voice-routine-r.timer").exists())
        self.assertEqual(SYSTEMCTL, [("daemon-reload",), ("enable", "--now", "omarchy-voice-routine-r.timer")])
        SYSTEMCTL.clear()
        act.write_timers({"r": self.action("every 1h")}, "x")
        self.assertEqual(SYSTEMCTL, [])  # nothing changed, systemd is left alone
        act.write_timers({"r": self.action("every 1h", enabled=False)}, "x")
        self.assertIn(("disable", "--now", "omarchy-voice-routine-r.timer"), SYSTEMCTL)
        self.assertEqual(list(act.UNIT_DIR.glob("omarchy-voice-routine-*")), [])

    def test_home_manager_units_are_left_alone(self):
        act.UNIT_DIR.mkdir(parents=True)
        target = act.UNIT_DIR.parent / "store.timer"
        target.write_text("declared")
        os.symlink(target, act.UNIT_DIR / "omarchy-voice-routine-hm.timer")
        act.write_timers({}, "x")
        self.assertTrue((act.UNIT_DIR / "omarchy-voice-routine-hm.timer").is_symlink())
        self.assertEqual(SYSTEMCTL, [])

    @unittest.skipUnless(shutil.which("systemd-analyze"), "needs systemd-analyze")
    def test_bad_calendar_is_reported_not_scheduled(self):
        notes = act.write_timers({"r": self.action("every tuesday-ish")}, "x")
        self.assertIn("routine r not scheduled", notes[0])
        self.assertFalse((act.UNIT_DIR / "omarchy-voice-routine-r.timer").exists())


if __name__ == "__main__":
    unittest.main()
