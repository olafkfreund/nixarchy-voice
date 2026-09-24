"""Reach, patience and memory: the tools a multi-step goal needs.

Scrolling to what is not painted, waiting for what has not happened yet,
reading exact text rather than pixels, asking the machine about itself, and
writing something down so it survives the session.

Run with: python3 -m unittest discover -s tests
"""

import contextlib
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice.config import Config
from omarchy_voice.tools import (
    READ_ONLY_TOOLS, SCROLL_MAX_CLICKS, SCROLL_MAX_PAGES, SCROLL_MIN_CLICKS,
    SCROLL_PIXELS_PER_CLICK, SYSTEM_QUERIES, TOOL_SCHEMAS, WAIT_MAX,
    Executor, Result, _matching_lines, _scroll_clicks, tools_for,
)


def stub_queries(executor, fn):
    """Point both query entry points at `fn`.

    #24 made `_query_rows` the primitive and left `_query_json` a wrapper, so a
    test that stubs only the wrapper stops covering the three callers that
    moved -- it reaches the real hyprctl instead, silently. Stubbing both keeps
    a test honest about which path it exercises.
    """
    executor._query_json = fn
    executor._query_rows = lambda kind: (fn(kind), None)

WINDOW = {
    "address": "0x1", "class": "chrome-bbc", "title": "BBC News",
    "at": [100, 40], "size": [800, 600], "focusHistoryID": 0,
    "workspace": {"name": "1"},
}


def executor(**config) -> Executor:
    ex = Executor(Config(**config))
    ex._session_is_locked = lambda: False
    return ex


class ScrollTests(unittest.TestCase):
    def setUp(self):
        self.executor = executor()
        self.executor._visible_workspaces = lambda: {"1"}
        stub_queries(self.executor, lambda kind: [WINDOW] if kind == "clients" else [])

    def run_scroll(self, args, helper=True):
        """#30: the wheel is a line to the Wayland helper, not a ydotool argv.

        `self.calls` keeps the old shape -- a list of argv-ish lists -- so the
        distance assertions below read the same; the wheel entry is now
        ["helper", "S", dx, dy].
        """
        self.calls = []

        def fake_shell(cmd, **kwargs):
            self.calls.append(cmd)
            return Result(True, "ok")

        class FakeHelper:
            def send(_self, lines):
                for line in lines:
                    self.calls.append(["helper"] + line.split())

            def close(_self):
                pass

        self.executor.input_helper = FakeHelper() if helper else None
        with mock.patch.object(Executor, "_shell", staticmethod(fake_shell)), \
             mock.patch("shutil.which", return_value="/usr/bin/x"):
            return self.executor.call("scroll", args)

    def test_the_pointer_is_put_over_the_window_first(self):
        """A wheel event goes to whatever is under the cursor. Without the move,
        'scroll the article' scrolled whichever pane the mouse was resting on."""
        self.assertTrue(self.run_scroll({"direction": "down"}).ok)
        move = next(c for c in self.calls if c[0] == "hyprctl")
        self.assertIn("hl.dsp.cursor.move", move[-1])
        self.assertIn("x = 500", move[-1])   # 100 + 800/2
        self.assertIn("y = 340", move[-1])   # 40 + 600/2

    def test_down_and_up_have_opposite_signs(self):
        self.run_scroll({"direction": "down"})
        down = int(next(c for c in self.calls if c[0] == "helper" and c[1] == "S")[3])
        self.calls = []
        self.run_scroll({"direction": "up"})
        up = int(next(c for c in self.calls if c[0] == "helper" and c[1] == "S")[3])
        self.assertEqual(down, -up)
        self.assertLess(down, 0)  # REL_WHEEL counts up when the page goes up

    def test_horizontal_scrolling_uses_the_other_axis(self):
        """`S <dx> <dy>`: horizontal travels in dx and leaves dy at zero."""
        self.run_scroll({"direction": "right"})
        wheel = next(c for c in self.calls if c[0] == "helper" and c[1] == "S")
        self.assertNotEqual(int(wheel[2]), 0)
        self.assertEqual(int(wheel[3]), 0)

    def test_amount_is_screens_not_notches(self):
        self.run_scroll({"direction": "down", "amount": 3})
        clicks = abs(int(next(c for c in self.calls if c[0] == "helper" and c[1] == "S")[3]))
        self.assertEqual(clicks, _scroll_clicks(WINDOW["size"][1], 3))

    def test_one_screen_is_most_of_the_window_not_a_fixed_ten_notches(self):
        """Ten notches moved 406px of a 1030px window while reporting a screen:
        you read 40% of an article and believe you read all of it."""
        self.run_scroll({"direction": "down"})
        clicks = abs(int(next(c for c in self.calls if c[0] == "helper" and c[1] == "S")[3]))
        travelled = clicks * SCROLL_PIXELS_PER_CLICK
        self.assertGreater(travelled, WINDOW["size"][1] * 0.7)
        self.assertLess(travelled, WINDOW["size"][1])

    def test_horizontal_scrolling_is_measured_against_the_width(self):
        self.run_scroll({"direction": "right"})
        clicks = abs(int(next(c for c in self.calls if c[0] == "helper" and c[1] == "S")[2]))
        self.assertEqual(clicks, _scroll_clicks(WINDOW["size"][0], 1))

    def test_a_silly_amount_is_capped_not_refused(self):
        self.assertTrue(self.run_scroll({"direction": "down", "amount": 999}).ok)
        clicks = abs(int(next(c for c in self.calls if c[0] == "helper" and c[1] == "S")[3]))
        self.assertEqual(clicks, _scroll_clicks(WINDOW["size"][1], SCROLL_MAX_PAGES))

    def test_an_unknown_direction_is_refused(self):
        self.assertFalse(self.run_scroll({"direction": "sideways"}).ok)

    def test_without_a_helper_it_refuses_rather_than_pressing_a_key(self):
        """#30, and this is a deliberate behaviour change.

        Scroll used to fall back to Page_Down when ydotool was missing, and
        report success. That only worked where the page itself had focus, so
        "scrolled" was frequently untrue -- and the reason ydotool was missing
        was a root daemon this no longer needs. A compositor that cannot be
        scrolled should say so once.
        """
        result = self.run_scroll({"direction": "down"}, helper=False)
        self.assertFalse(result.ok)
        self.assertNotIn("Page_Down", result.output)
        self.assertNotIn("ydotool", result.output)

    def test_a_window_on_another_workspace_is_refused(self):
        self.executor._visible_workspaces = lambda: {"7"}
        result = self.run_scroll({"direction": "down"})
        self.assertFalse(result.ok)
        self.assertIn("workspace 1", result.output)

    def test_a_locked_session_is_refused_before_the_pointer_moves(self):
        self.executor._session_is_locked = lambda: True
        result = self.run_scroll({"direction": "down"})
        self.assertFalse(result.ok)
        self.assertIn("locked", result.output)
        self.assertEqual(self.calls, [])


class ScrollDistanceTests(unittest.TestCase):
    def test_a_tiny_pane_still_moves(self):
        self.assertGreaterEqual(_scroll_clicks(120, 1), SCROLL_MIN_CLICKS)

    def test_a_huge_window_does_not_fire_a_runaway_burst(self):
        """Applications apply their own momentum to a fast run of notches."""
        self.assertLessEqual(_scroll_clicks(4320, 1), SCROLL_MAX_CLICKS)

    def test_pages_multiply(self):
        self.assertEqual(_scroll_clicks(1030, 3), 3 * _scroll_clicks(1030, 1))

    def test_a_screen_leaves_some_overlap_to_read_across(self):
        travelled = _scroll_clicks(1030, 1) * SCROLL_PIXELS_PER_CLICK
        self.assertLess(travelled, 1030)


class WaitForTests(unittest.TestCase):
    def setUp(self):
        self.executor = executor()

    def test_a_window_that_is_already_there_returns_at_once(self):
        stub_queries(self.executor, lambda kind: [WINDOW])
        result = self.executor.call("wait_for", {"what": "window", "value": "bbc"})
        self.assertTrue(result.ok)
        self.assertIn("opened", result.output)

    def test_a_window_that_appears_late_is_waited_for(self):
        seen = {"n": 0}

        def clients(kind):
            seen["n"] += 1
            return [WINDOW] if seen["n"] >= 3 else []

        stub_queries(self.executor, clients)
        with mock.patch("time.sleep"):
            result = self.executor.call("wait_for", {"what": "window", "value": "bbc"})
        self.assertTrue(result.ok)
        self.assertGreaterEqual(seen["n"], 3)

    def test_window_gone_is_the_inverse(self):
        stub_queries(self.executor, lambda kind: [WINDOW])
        with mock.patch("time.sleep"):
            result = self.executor.call("wait_for",
                                        {"what": "window_gone", "value": "bbc", "timeout": 1})
        self.assertIn("still open", result.output)

    def test_a_timeout_is_a_finding_not_an_error(self):
        """The model must say what happened, not treat this as a crash."""
        stub_queries(self.executor, lambda kind: [])
        with mock.patch("time.sleep"):
            result = self.executor.call("wait_for",
                                        {"what": "window", "value": "ghost", "timeout": 1})
        self.assertTrue(result.ok)
        self.assertIn("has opened", result.output)

    def test_waiting_is_bounded_however_long_it_is_asked_for(self):
        """The assistant is mute while it waits, so the cap is not negotiable.
        Driven by a fake clock: the deadline is wall-clock, so a mocked sleep
        that does not advance time would spin here for the full 25 seconds."""
        stub_queries(self.executor, lambda kind: [])
        clock = [0.0]
        with mock.patch("time.sleep", side_effect=lambda s: clock.__setitem__(0, clock[0] + s)), \
             mock.patch("time.monotonic", side_effect=lambda: clock[0]):
            result = self.executor.call(
                "wait_for", {"what": "window", "value": "ghost", "timeout": 9999})
        self.assertTrue(result.ok)
        self.assertLessEqual(clock[0], WAIT_MAX)

    def test_waiting_for_text_reads_the_screen(self):
        self.executor._query_json = lambda kind: [
            {"focused": True, "x": 0, "y": 0, "width": 100, "height": 100}]
        self.executor._ocr_words = lambda geometry: (
            [{"text": "Ready", "x": 1, "y": 1, "w": 10, "h": 5, "conf": 99.0}], "")
        result = self.executor.call("wait_for", {"what": "text", "value": "Ready"})
        self.assertTrue(result.ok)
        self.assertIn("appeared", result.output)

    def test_a_screen_that_cannot_be_read_stops_the_wait(self):
        self.executor._query_json = lambda kind: [
            {"focused": True, "x": 0, "y": 0, "width": 100, "height": 100}]
        self.executor._ocr_words = lambda geometry: ([], "the session is locked")
        result = self.executor.call("wait_for", {"what": "text", "value": "Ready"})
        self.assertFalse(result.ok)
        self.assertIn("locked", result.output)

    def test_an_unknown_condition_is_refused(self):
        self.assertFalse(self.executor.call("wait_for",
                                            {"what": "vibes", "value": "x"}).ok)


class ClipboardTests(unittest.TestCase):
    def setUp(self):
        self.executor = executor()

    def test_writing_holds_no_pipe_open(self):
        """wl-copy forks a process that outlives us and inherits our file
        descriptors. On a pipe, reading to EOF waited for it: a copy that had
        already worked was reported as a ten-second timeout."""
        with mock.patch("subprocess.run") as run, \
             mock.patch("shutil.which", return_value="/usr/bin/wl-copy"):
            run.return_value = mock.Mock(returncode=0)
            result = self.executor.call("clipboard", {"action": "write", "text": "hi"})
        self.assertTrue(result.ok)
        kwargs = run.call_args.kwargs
        self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
        self.assertIsNot(kwargs["stderr"], subprocess.PIPE)

    def test_the_text_goes_on_the_argv_after_a_double_dash(self):
        with mock.patch("subprocess.run") as run, \
             mock.patch("shutil.which", return_value="/usr/bin/wl-copy"):
            run.return_value = mock.Mock(returncode=0)
            self.executor.call("clipboard", {"action": "write", "text": "--not-a-flag"})
        self.assertEqual(run.call_args.args[0], ["wl-copy", "--", "--not-a-flag"])

    def test_writing_nothing_is_refused(self):
        self.assertFalse(self.executor.call("clipboard", {"action": "write"}).ok)

    def test_reading_returns_the_text(self):
        with mock.patch.object(Executor, "_shell",
                               staticmethod(lambda cmd, **kw: Result(True, "https://x.test"))), \
             mock.patch("shutil.which", return_value="/usr/bin/wl-paste"):
            result = self.executor.call("clipboard", {"action": "read"})
        self.assertEqual(result.output, "https://x.test")

    def test_an_empty_clipboard_is_said_plainly(self):
        for wl_paste_says in ("Nothing is copied", "clipboard is empty"):
            with self.subTest(says=wl_paste_says):
                with mock.patch.object(Executor, "_shell", staticmethod(
                        lambda cmd, **kw: Result(False, wl_paste_says))), \
                     mock.patch("shutil.which", return_value="/usr/bin/wl-paste"):
                    result = self.executor.call("clipboard", {"action": "read"})
                self.assertTrue(result.ok)
                self.assertIn("empty", result.output)

    def test_a_clipboard_holding_an_image_is_not_an_error(self):
        with mock.patch.object(Executor, "_shell", staticmethod(
                lambda cmd, **kw: Result(False, "No suitable type of content copied"))), \
             mock.patch("shutil.which", return_value="/usr/bin/wl-paste"):
            result = self.executor.call("clipboard", {"action": "read"})
        self.assertTrue(result.ok)
        self.assertIn("does not hold any text", result.output)

    def test_the_gate_can_see_what_is_being_copied(self):
        self.assertIn("sudo rm", Executor.describe(
            "clipboard", {"action": "write", "text": "sudo rm -rf /"}))


class SystemQueryTests(unittest.TestCase):
    def setUp(self):
        self.executor = executor()

    def test_every_topic_in_the_schema_has_a_recipe(self):
        schema = next(t for t in TOOL_SCHEMAS if t["name"] == "system_query")
        listed = schema["input_schema"]["properties"]["topic"]["enum"]
        self.assertEqual(sorted(listed), sorted(SYSTEM_QUERIES))

    def test_no_recipe_uses_a_shell(self):
        """This is a reference table, not a command builder: a misheard sentence
        must not be able to steer it anywhere."""
        for topic, recipe in SYSTEM_QUERIES.items():
            if callable(recipe):
                continue
            for _, argv, *_ in recipe:
                with self.subTest(topic=topic):
                    self.assertNotIn(argv[0], ("sh", "bash", "zsh", "eval"))

    def test_it_is_read_only_so_it_works_under_dry_run(self):
        self.assertIn("system_query", READ_ONLY_TOOLS)

    def test_an_unknown_topic_lists_the_real_ones(self):
        result = self.executor.call("system_query", {"topic": "horoscope"})
        self.assertFalse(result.ok)
        self.assertIn("disk", result.output)

    def test_a_missing_command_is_skipped_not_reported_as_broken(self):
        with mock.patch("shutil.which", return_value=None):
            result = self.executor.call("system_query", {"topic": "disk"})
        self.assertFalse(result.ok)
        self.assertIn("nothing on this machine", result.output)

    def test_a_long_answer_is_cut_to_the_top_of_the_list(self):
        with mock.patch("shutil.which", return_value="/usr/bin/ps"), \
             mock.patch.object(Executor, "_shell", staticmethod(
                 lambda cmd, **kw: Result(True, "\n".join(f"row {i}" for i in range(50))))):
            result = self.executor.call("system_query", {"topic": "processes"})
        self.assertLessEqual(len(result.output.splitlines()), 11)

    def test_no_battery_is_an_answer_not_a_failure(self):
        with mock.patch("pathlib.Path.iterdir", side_effect=OSError):
            result = self.executor.call("system_query", {"topic": "battery"})
        self.assertTrue(result.ok)
        self.assertIn("no battery", result.output)

    def test_bluetooth_does_not_hang_when_there_is_no_adapter(self):
        """bluetoothctl waits forever for a controller that is not there, and on
        a voice channel that is five seconds of silence."""
        with mock.patch("pathlib.Path.exists", return_value=False):
            result = self.executor.call("system_query", {"topic": "bluetooth"})
        self.assertTrue(result.ok)
        self.assertIn("no bluetooth adapter", result.output)

    def test_bluetoothctl_is_always_given_a_timeout(self):
        seen = []
        with mock.patch("pathlib.Path.exists", return_value=True), \
             mock.patch.object(Executor, "_shell", staticmethod(
                 lambda cmd, **kw: (seen.append(cmd), Result(True, "x"))[1])):
            self.executor.call("system_query", {"topic": "bluetooth"})
        self.assertTrue(seen)
        for cmd in seen:
            self.assertIn("--timeout", cmd)


class RememberTests(unittest.TestCase):
    def setUp(self):
        self.executor = executor()
        self.tmp = Path(__file__).resolve().parent / "_notes_test.json"
        self.executor._notes_path = lambda: self.tmp
        self.addCleanup(lambda: self.tmp.unlink(missing_ok=True))

    def note(self, text):
        return self.executor.call("remember", {"action": "note", "text": text})

    def test_an_empty_notebook_says_so(self):
        self.assertIn("empty", self.executor.call("remember", {"action": "list"}).output)

    def test_a_note_survives_a_new_executor(self):
        """The whole point: the conversation ends when listening is toggled off."""
        self.note("goal: get the PR merged")
        fresh = executor()
        fresh._notes_path = lambda: self.tmp
        self.assertIn("get the PR merged",
                      fresh.call("remember", {"action": "list"}).output)

    def test_notes_are_dated(self):
        self.note("something")
        listed = self.executor.call("remember", {"action": "list"}).output
        self.assertRegex(listed, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")

    def test_forget_takes_the_note_that_matches_every_word(self):
        self.note("opened PR 9319 for the portal")
        self.note("opened PR 9320 for the service")
        self.executor.call("remember", {"action": "forget", "text": "9319 portal"})
        listed = self.executor.call("remember", {"action": "list"}).output
        self.assertNotIn("9319", listed)
        self.assertIn("9320", listed)

    def test_forgetting_nothing_says_nothing_was_forgotten(self):
        self.note("a note")
        result = self.executor.call("remember", {"action": "forget", "text": "unrelated"})
        self.assertFalse(result.ok)
        self.assertIn("a note", self.executor.call("remember", {"action": "list"}).output)

    def test_forget_all_empties_it(self):
        self.note("one")
        self.note("two")
        self.executor.call("remember", {"action": "forget", "text": "all"})
        self.assertIn("empty", self.executor.call("remember", {"action": "list"}).output)

    def test_the_notebook_does_not_grow_without_bound(self):
        from omarchy_voice.tools import NOTES_LIMIT
        for i in range(NOTES_LIMIT + 15):
            self.note(f"note {i}")
        self.assertEqual(len(json.loads(self.tmp.read_text())), NOTES_LIMIT)

    def test_a_note_is_one_line(self):
        self.note("first line\nsecond line")
        self.assertNotIn("\n", json.loads(self.tmp.read_text())[0]["text"])

    def test_a_corrupt_notebook_reads_as_empty_rather_than_crashing(self):
        self.tmp.write_text("{ not json")
        self.assertIn("empty", self.executor.call("remember", {"action": "list"}).output)

    def test_notes_are_private_to_the_user(self):
        self.note("something personal")
        self.assertEqual(self.tmp.stat().st_mode & 0o077, 0)

    def test_a_note_with_no_text_is_refused(self):
        self.assertFalse(self.executor.call("remember", {"action": "note"}).ok)


class ReadScreenQueryTests(unittest.TestCase):
    SCREEN = "\n".join([
        "Settings", "Night light: on", "Volume 40%",
        "Battery health good", "About this machine",
    ])

    def setUp(self):
        self.executor = executor()
        self.executor._read_screen_text = lambda target="screen": Result(True, self.SCREEN)

    def test_without_a_query_the_whole_screen_comes_back(self):
        self.assertEqual(self.executor.call("read_screen", {}).output, self.SCREEN)

    def test_a_query_returns_the_matching_lines_and_their_neighbours(self):
        result = self.executor.call("read_screen", {"query": "night light"})
        self.assertIn("Night light: on", result.output)
        self.assertNotIn("About this machine", result.output)

    def test_a_query_that_matches_nothing_says_where_else_to_look(self):
        result = self.executor.call("read_screen", {"query": "wifi password"})
        self.assertTrue(result.ok)
        self.assertIn("scroll", result.output)

    def test_a_failed_read_is_still_a_failed_read(self):
        self.executor._read_screen_text = lambda target="screen": Result(False, "asleep")
        self.assertFalse(self.executor.call("read_screen", {"query": "anything"}).ok)

    def test_the_gate_records_what_was_being_looked_for(self):
        self.assertIn("night light",
                      Executor.describe("read_screen", {"query": "night light"}))


class MatchingLinesTests(unittest.TestCase):
    def test_gaps_between_matches_are_marked(self):
        text = "\n".join(["hit one", *[f"filler {i}" for i in range(8)], "hit two"])
        self.assertIn("…", _matching_lines(text, "hit"))

    def test_a_short_query_needs_all_of_its_words(self):
        text = "files changed\nchanged the files elsewhere"
        self.assertEqual(_matching_lines("only changed here", "files changed"), "")
        self.assertIn("files changed", _matching_lines(text, "files changed"))

    def test_an_empty_query_matches_nothing(self):
        self.assertEqual(_matching_lines("anything at all", ""), "")


class OfferedToolsTests(unittest.TestCase):
    def test_the_shell_tool_is_not_offered_when_it_is_off(self):
        """A tool that will only ever be refused costs its schema every turn and
        a whole round trip when the model reaches for it."""
        names = {s["name"] for s in tools_for(Config())}
        self.assertNotIn("run_shell", names)

    def test_it_is_offered_when_the_user_turned_it_on(self):
        names = {s["name"] for s in tools_for(Config(allow_shell=True))}
        self.assertIn("run_shell", names)

    def test_everything_else_is_always_offered(self):
        """Two tools are now gated, not one: the notification log is off by
        default since #17, so it is offered the way run_shell is."""
        offered = {s["name"] for s in tools_for(Config())}
        self.assertEqual(offered, {s["name"] for s in TOOL_SCHEMAS} - {"run_shell", "read_notifications"})
        with_both = {s["name"] for s in tools_for(Config(allow_shell=True, allow_notifications=True))}
        self.assertEqual(with_both, {s["name"] for s in TOOL_SCHEMAS})

    def test_every_offered_tool_has_a_handler(self):
        executor = Executor(Config(allow_shell=True))
        for schema in TOOL_SCHEMAS:
            with self.subTest(tool=schema["name"]):
                self.assertTrue(hasattr(executor, f'_tool_{schema["name"]}'))

    def test_every_tool_has_its_own_line_in_describe(self):
        """The policy gate matches on the description, so a tool that falls
        through to the `name {args}` default is a tool the deny and confirm
        rules cannot see properly."""
        for schema in TOOL_SCHEMAS:
            name = schema["name"]
            with self.subTest(tool=name):
                self.assertNotEqual(Executor.describe(name, {}), f"{name} {{}}")


class LockedSessionTests(unittest.TestCase):
    """A locked session is the one way to capture pixels that are not the
    desktop and have grim report success."""

    def setUp(self):
        self.executor = Executor(Config())

    def test_the_probe_is_not_run_in_quiet_mode(self):
        """`-q` suppresses the answer along with the errors, which reads as
        'not locked' on a session that is locked."""
        seen = []
        with mock.patch("shutil.which", return_value="/usr/bin/omarchy-shell"), \
             mock.patch.object(Executor, "_shell", staticmethod(
                 lambda cmd, **kw: (seen.append(cmd), Result(True, "true"))[1])):
            self.assertTrue(self.executor._session_is_locked())
        self.assertNotIn("-q", seen[0])

    def test_a_locked_session_refuses_before_anything_is_captured(self):
        self.executor._session_is_locked = lambda: True
        with mock.patch("subprocess.run") as run:
            result = self.executor._ocr_region("0,0 100x100")
        self.assertFalse(result.ok)
        self.assertIn("locked", result.output)
        run.assert_not_called()

    def test_without_omarchy_shell_nothing_is_claimed(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(self.executor._session_is_locked())

    def test_a_probe_that_does_not_answer_is_not_taken_as_locked(self):
        with mock.patch("shutil.which", return_value="/usr/bin/omarchy-shell"), \
             mock.patch.object(Executor, "_shell",
                               staticmethod(lambda cmd, **kw: Result(False, "no shell"))):
            self.assertFalse(self.executor._session_is_locked())


class QueryFailureTests(unittest.TestCase):
    """#24: a failed hyprctl query must not read as an answer.

    _query_json returned [] for a timed-out hyprctl, a compositor mid-reload
    and a genuinely empty desktop alike. Three callers read emptiness as a
    positive fact. Demonstrated while scoping: with the baseline query failing,
    _await_new_window returned a window that had been open all along as the one
    just launched, so compose_windows would tile a window the user was in.
    """

    WINDOW = {"address": "0xa", "class": "foot", "title": "bbc news",
              "initialTitle": "bbc.com", "focusHistoryID": 0}

    def _executor(self, rows, error):
        ex = executor()
        self.queries = 0

        def rows_or_error(kind):
            self.queries += 1
            return (rows, error)

        ex._query_rows = rows_or_error
        ex._query_json = lambda kind: rows_or_error(kind)[0]
        return ex

    def test_a_failed_query_is_not_the_window_having_closed(self):
        """The bug in the issue. `not exists` on [] meant "it closed"."""
        ex = self._executor([], "hyprctl clients failed: no such instance")
        result = ex.call("wait_for", {"what": "window_gone",
                                      "value": "bbc", "timeout": 2})
        # Today's code returns ok=True here, reporting the wait as satisfied.
        # That is the bug: the model proceeds on a precondition that never
        # held. The word "closed" still appears, in "whether ... closed is
        # unknown", which is the honest sentence.
        self.assertFalse(result.ok)
        self.assertIn("failed", result.output)
        self.assertIn("unknown", result.output)

    def test_it_does_not_guess_the_other_way_either(self):
        """"Still open" would be the same guess with the sign flipped."""
        ex = self._executor([], "hyprctl clients failed: no such instance")
        result = ex.call("wait_for", {"what": "window", "value": "bbc",
                                      "timeout": 2})
        self.assertIn("unknown", result.output)

    def test_a_failed_wait_stops_rather_than_spinning(self):
        """If hyprctl is not answering, the next fifteen polls will not
        either, and burning the budget in silence is worse than saying so."""
        ex = self._executor([], "hyprctl clients failed: boom")
        ex.call("wait_for", {"what": "window", "value": "bbc", "timeout": 8})
        self.assertEqual(self.queries, 1)

    def test_an_honestly_empty_desktop_still_reads_as_empty(self):
        """The test that stops this fix becoming "treat empty as broken",
        which is the same bug with the sign flipped."""
        ex = self._executor([], None)
        result = ex.call("wait_for", {"what": "window_gone",
                                      "value": "bbc", "timeout": 2})
        self.assertTrue(result.ok)
        self.assertIn("closed", result.output)

    def test_a_successful_query_still_finds_the_window(self):
        ex = self._executor([self.WINDOW], None)
        result = ex.call("wait_for", {"what": "window", "value": "bbc",
                                      "timeout": 2})
        self.assertTrue(result.ok)
        self.assertIn("opened", result.output)

    def test_a_failure_inside_the_wait_loop_is_a_retry(self):
        """Different from wait_for on purpose: the window may still be coming,
        and the deadline already bounds the loop."""
        ex = executor()
        calls = {"n": 0}

        def rows(kind):
            calls["n"] += 1
            if calls["n"] == 1:
                return [], "hyprctl clients failed: transient"
            return [self.WINDOW], None

        ex._query_rows = rows
        with mock.patch("time.sleep"):
            address = ex._await_new_window(set(), timeout=2.0, hint="foot")
        self.assertEqual(address, "0xa", "a transient failure ended the wait")
        self.assertGreater(calls["n"], 1)


class CaptureFormatTests(unittest.TestCase):
    """#26: the capture is piped into tesseract and then thrown away.

    Encoding it as PNG first cost 0.940s on a 2560x1440 framebuffer against
    0.032s for PPM -- means of five, both measured through the pipe. tesseract
    reads PNM natively, so the compression was work nobody read.
    """

    def setUp(self):
        self.executor = executor()

    @staticmethod
    def _capture_argv(run):
        return next(c.args[0] for c in run.call_args_list
                    if c.args and c.args[0] and c.args[0][0] == "grim")

    @contextlib.contextmanager
    def _run_ok(self):
        """subprocess.run stubbed so both grim and tesseract look successful.

        _query_rows too: since #51 a capture refuses when the window list
        cannot be read, and it reaches hyprctl through Popen rather than
        subprocess.run -- so without this the guard refuses, grim never runs,
        and these tests fail looking for a capture that was correctly not made.
        """
        done = mock.Mock(returncode=0, stdout=b"P6 1 1 255 xxx", stderr=b"")
        with mock.patch.object(self.executor, "_query_rows", return_value=([], None)), \
             mock.patch("subprocess.run", return_value=done) as run:
            yield run

    def test_read_screen_captures_ppm(self):
        with self._run_ok() as run:
            self.executor._ocr_region("0,0 100x100")
        argv = self._capture_argv(run)
        self.assertIn("-t", argv)
        self.assertEqual("ppm", argv[argv.index("-t") + 1])

    def test_click_text_captures_ppm(self):
        with self._run_ok() as run:
            self.executor._ocr_words("0,0 100x100")
        argv = self._capture_argv(run)
        self.assertIn("-t", argv)
        self.assertEqual("ppm", argv[argv.index("-t") + 1])

    def test_the_resolution_is_stated_not_inferred_from_the_file(self):
        """Why the format change cannot alter what is read.

        A PNG can carry a pHYs chunk saying what resolution it is, and a PPM
        cannot -- that is the one way the container could have reached
        tesseract's output. It does not apply here twice over: grim's PNG has
        no pHYs chunk (checked: IHDR, IDAT..., IEND and nothing else), and both
        calls pass --dpi explicitly anyway.

        Measured on one real frame converted losslessly to both formats: the
        OCR text was byte-identical, 6029 characters each. That comparison
        needs a live desktop, so what is pinned here is the mechanism -- if
        --dpi ever stops being passed, the format would start to matter and
        this fails.
        """
        for helper in (self.executor._ocr_region, self.executor._ocr_words):
            with self.subTest(helper=helper.__name__), self._run_ok() as run:
                helper("0,0 100x100")
            argv = next(c.args[0] for c in run.call_args_list
                        if c.args and c.args[0] and c.args[0][0] == "tesseract")
            self.assertIn("--dpi", argv)


class ScopedCaptureTests(unittest.TestCase):
    """#26: click_text and wait_for can look at one window, not the monitor.

    read_screen could already; these two each worked out the monitor rect for
    themselves, which is how they drifted. The default stays the monitor: the
    thing being clicked is often not in the focused window -- a bar widget, a
    dialog that has not taken focus -- and narrowing silently would turn "not
    found" into the common case, which costs a model turn to recover from.
    """

    WINDOW = {"address": "0xa", "workspace": {"name": "1"},
              "at": [100, 200], "size": [800, 600]}
    MONITOR = {"x": 0, "y": 0, "width": 2560, "height": 1440,
               "focused": True, "activeWorkspace": {"name": "1"}}

    def setUp(self):
        self.executor = executor()
        self.seen = []
        self.executor._query_json = lambda kind: {
            "clients": [self.WINDOW], "monitors": [self.MONITOR]}[kind]
        # #67's input guard asks _query_rows, which distinguishes "no windows"
        # from "could not read the windows" (#24). Without this these passed
        # locally by reaching the developer's real hyprctl and failed in the
        # hermetic sandbox, which is the whole reason nix flake check is the
        # authority here.
        self.executor._query_rows = lambda kind: ([self.WINDOW], None)
        self.executor._ocr_words = lambda geometry: (self.seen.append(geometry), ([], ""))[1]

    def test_the_default_still_reads_the_whole_monitor(self):
        self.executor.call("click_text", {"text": "Continue"})
        self.assertEqual(self.seen, ["0,0 2560x1440"])

    def test_an_address_reads_only_that_window(self):
        self.executor.call("click_text", {"text": "Continue", "target": "address:0xa"})
        self.assertEqual(self.seen, ["100,200 800x600"])

    def test_wait_for_text_scopes_too(self):
        self.executor.call("wait_for", {"what": "text", "value": "Done",
                                        "timeout": 0.5, "target": "address:0xa"})
        # Polled, so more than one capture -- every one at the window's rect.
        self.assertTrue(self.seen)
        self.assertEqual(set(self.seen), {"100,200 800x600"})

    def test_a_target_that_is_not_visible_is_refused_before_any_capture(self):
        self.executor._query_json = lambda kind: {
            "clients": [{**self.WINDOW, "workspace": {"name": "7"}}],
            "monitors": [self.MONITOR]}[kind]
        result = self.executor.call("click_text",
                                    {"text": "Continue", "target": "address:0xa"})
        self.assertFalse(result.ok)
        self.assertIn("workspace 7", result.output)
        self.assertEqual(self.seen, [])


if __name__ == "__main__":
    unittest.main()
