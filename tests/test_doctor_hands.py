"""Doctor's hands section checks the tools the daemon runs, not wtype (#143).

wtype left the typing path in #60, but doctor still ticked it, and
capabilities.missing_tools() still listed it while leaving out ai-mirror-input,
which clicking needs. Doctor now lists seven tools, each with what it is for,
and missing_tools() is gone.

`which` is faked: nothing is started, and the mic, the compositor and the
manifest are not read.

Run with: python3 -m unittest discover -s tests
"""

import unittest
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice import capabilities, cli, virtual_input

TOOLS = [
    ("hyprctl", "dispatch and typing"),
    ("omarchy", "omarchy commands"),
    ("notify-send", "notifications"),
    ("uwsm-app", "launching apps"),
    ("ai-mirror-input", "clicking and scrolling"),
    ("grim", "screenshots for reading the screen"),
    ("tesseract", "reading text off the screen"),
]


def _lines(found):
    with mock.patch("omarchy_voice.cli.shutil.which", return_value=found):
        return cli._hands_tools()


class HandsTools(unittest.TestCase):
    def test_no_wtype(self):
        self.assertFalse([l for l in _lines(None) if "wtype" in l])

    def test_each_tool_crossed_with_purpose(self):
        lines = _lines(None)
        self.assertEqual(len(lines), 7)
        for tool, why in TOOLS:
            hits = [l for l in lines if tool in l and why in l]
            self.assertEqual(len(hits), 1, (tool, lines))
            self.assertIn("✗", hits[0])

    def test_each_tool_ticked_when_present(self):
        lines = _lines("/usr/bin/x")
        self.assertEqual(len(lines), 7)
        for tool, why in TOOLS:
            hits = [l for l in lines if tool in l and why in l]
            self.assertEqual(len(hits), 1, (tool, lines))
            self.assertIn("✓", hits[0])
            self.assertNotIn("✗", hits[0])

    def test_helper_name_from_virtual_input(self):
        with mock.patch.object(virtual_input, "HELPER", "fake-helper"), \
                mock.patch("omarchy_voice.cli.shutil.which", return_value=None) as which:
            lines = cli._hands_tools()
        asked = [c.args[0] for c in which.call_args_list]
        self.assertIn("fake-helper", asked)
        self.assertNotIn("ai-mirror-input", asked)
        clicking = [l for l in lines if "clicking and scrolling" in l]
        self.assertEqual(len(clicking), 1)
        self.assertIn("fake-helper", clicking[0])

    def test_missing_tools_gone(self):
        self.assertFalse(hasattr(capabilities, "missing_tools"))


if __name__ == "__main__":
    unittest.main()
