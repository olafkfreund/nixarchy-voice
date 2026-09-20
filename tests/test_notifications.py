"""Reading back what the desktop popped up.

The toast is gone by the time anyone asks about it and it was never a window,
so the only route used to be `read_screen` -- a picture of the whole desktop
sent to OpenAI to recover four words. These cover the cheap route.
"""

import json
import tempfile
import unittest.mock
import time
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice import notifications
from omarchy_voice.config import Config
from omarchy_voice.tools import Executor, tools_for


# A real line off this machine's session bus, kept verbatim. A hand-written
# fixture would only prove the parser matches the fixture.
REAL_LINE = json.dumps({
    "type": "method_call", "endian": "l", "flags": 0, "version": 1, "cookie": 9,
    "timestamp-realtime": 1789199821514372,
    "sender": ":1.19818", "destination": ":1.7087",
    "path": "/org/freedesktop/Notifications",
    "interface": "org.freedesktop.Notifications", "member": "Notify",
    "payload": {"type": "susssasa{sv}i",
                "data": ["TestApp", 0, "", "Build finished", "3 warnings, 0 errors",
                         [], {"urgency": {"type": "y", "data": 1}}, -1]},
})


class ParseTests(unittest.TestCase):
    def test_a_real_notify_call_off_the_bus(self):
        entry = notifications.parse(REAL_LINE)
        self.assertEqual(entry["app"], "TestApp")
        self.assertEqual(entry["summary"], "Build finished")
        self.assertEqual(entry["body"], "3 warnings, 0 errors")
        self.assertAlmostEqual(entry["at"], 1789199821.514372, places=3)

    def test_everything_else_on_the_bus_is_not_a_notification(self):
        """The monitor emits every message; parse is called on all of them."""
        for line in ("", "not json at all", "[]", "null",
                     json.dumps({"member": "NameAcquired", "type": "signal"}),
                     json.dumps({"member": "Notify", "type": "method_return"}),
                     json.dumps({"member": "Notify", "type": "method_call",
                                 "payload": {"data": ["app", 0]}}),
                     json.dumps({"member": "Notify", "type": "method_call",
                                 "payload": {"data": ["app", 0, "", "", ""]}})):
            with self.subTest(line=line[:40]):
                self.assertIsNone(notifications.parse(line))


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "notifications.jsonl"

    def test_newest_first_even_when_written_out_of_order(self):
        notifications.record({"at": 100, "app": "Old", "summary": "first"}, self.path)
        notifications.record({"at": 900, "app": "New", "summary": "second"}, self.path)
        notifications.record({"at": 500, "app": "Mid", "summary": "third"}, self.path)
        self.assertEqual([e["app"] for e in notifications.recent(9, self.path)],
                         ["New", "Mid", "Old"])

    def test_the_file_does_not_grow_without_end(self):
        for n in range(notifications.TRIM_AT + 20):
            notifications.record({"at": n, "app": "spam", "summary": str(n)}, self.path)
        kept = self.path.read_text().splitlines()
        self.assertLessEqual(len(kept), notifications.TRIM_AT)
        # Trimming must drop the oldest, not the newest.
        self.assertEqual(notifications.recent(1, self.path)[0]["summary"],
                         str(notifications.TRIM_AT + 19))

    def test_a_corrupt_line_does_not_lose_the_rest(self):
        notifications.record({"at": 1, "app": "Good", "summary": "kept"}, self.path)
        with self.path.open("a") as fh:
            fh.write("half a line, no newline or json\n")
        notifications.record({"at": 2, "app": "Also", "summary": "kept"}, self.path)
        self.assertEqual(len(notifications.recent(9, self.path)), 2)

    def test_no_file_reads_as_nothing_rather_than_raising(self):
        self.assertEqual(notifications.recent(5, self.path), [])


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "notifications.jsonl"
        self.real, notifications.HISTORY_FILE = notifications.HISTORY_FILE, self.path
        self.addCleanup(setattr, notifications, "HISTORY_FILE", self.real)
        self.executor = Executor(Config())

    def _call(self, **args):
        return self.executor.call("read_notifications", args)

    def test_it_says_what_arrived_and_how_long_ago(self):
        notifications.record({"at": time.time() - 5, "app": "TestApp",
                              "summary": "Build finished", "body": "3 warnings"})
        out = self._call().output
        self.assertIn("TestApp", out)
        self.assertIn("Build finished", out)
        self.assertIn("ago", out)

    def test_a_query_reaches_past_the_default_window(self):
        """The one the user means may be well behind a pile of chat messages."""
        notifications.record({"at": 1, "app": "Mail", "summary": "the one"})
        for n in range(20):
            notifications.record({"at": 100 + n, "app": "Chat", "summary": f"msg {n}"})
        self.assertIn("the one", self._call(query="mail").output)

    def test_nothing_recorded_says_why_rather_than_no(self):
        """'No notifications' and 'nobody was watching' are different answers."""
        result = self._call()
        self.assertFalse(result.ok)
        self.assertIn("daemon", result.output)

    def test_a_silly_limit_does_not_get_through(self):
        for n in range(60):
            notifications.record({"at": n, "app": "a", "summary": str(n)})
        self.assertLessEqual(len(self._call(limit=10_000).output.splitlines()), 25)
        self.assertEqual(len(self._call(limit="nonsense").output.splitlines()), 5)


class GateTests(unittest.TestCase):
    def test_the_tool_is_not_offered_when_recording_is_off(self):
        names = [t["name"] for t in tools_for(Config(allow_notifications=False))]
        self.assertNotIn("read_notifications", names)

    def test_and_is_offered_when_it_is_on(self):
        names = [t["name"] for t in tools_for(Config(allow_notifications=True))]
        self.assertIn("read_notifications", names)


if __name__ == "__main__":
    unittest.main()


class ConsentNoticeTests(unittest.TestCase):
    """#17: the notification log is off now, and a user who had it on is told once."""

    def setUp(self):
        from omarchy_voice import cli, config as cfg
        self.cli, self.cfg = cli, cfg
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state = Path(tmp.name)
        for name, value in (("STATE_DIR", state), ("LOG_FILE", state / "session.log")):
            for module in (cfg, cli):
                patch = unittest.mock.patch.object(module, name, value, create=True)
                patch.start()
                self.addCleanup(patch.stop)
        self.log = state / "session.log"
        self.marker = state / "notifications-off-noticed"

    def test_it_is_said_once_and_only_once(self):
        config = Config(allow_notifications=False, notify=False)
        self.cli.consent_notice(config)
        self.assertTrue(self.marker.exists())
        first = self.log.read_text()
        self.assertIn("notification", first.lower())
        self.cli.consent_notice(config)
        self.assertEqual(self.log.read_text(), first)

    def test_a_user_who_chose_is_not_told_what_they_chose(self):
        config = Config(allow_notifications=False, notify=False)
        config.allow_notifications_explicit = True
        self.cli.consent_notice(config)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.log.exists())
