"""The desktop snapshot must say when it does not know (#69, #75).

It goes in front of every Claude turn. A failed hyprctl written up as "no
windows" is a fact the model acts on; "unknown" sends it to look.

Run with: python3 -m unittest discover -s tests
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice import capabilities

HEALTHY = {
    "monitors": [{"name": "DP-1", "width": 2560, "height": 1440,
                  "activeWorkspace": {"name": "1"}}],
    "workspaces": [{"id": 1, "name": "1", "windows": 1}],
    "activewindow": {"class": "firefox", "title": "News"},
    "clients": [{"class": "firefox", "title": "News",
                 "workspace": {"name": "1"}, "address": "0xabc"}],
}
EMPTY = {"monitors": [], "workspaces": [], "activewindow": {}, "clients": []}


def snapshot(answers: dict) -> str:
    """live_state with hyprctl answering from `answers`; a str is sent raw."""
    def run(cmd, timeout=10.0):
        answer = answers.get(cmd[-1], "")
        return answer if isinstance(answer, str) else json.dumps(answer)
    with mock.patch.object(capabilities, "_run", side_effect=run):
        return capabilities.live_state()


class LiveStateTests(unittest.TestCase):
    def test_a_healthy_desktop_is_described_in_full(self):
        text = snapshot(HEALTHY)
        self.assertIn("Monitors: DP-1 2560x1440 (workspace 1)", text)
        self.assertIn("Workspaces in use: 1 (1 windows)", text)
        self.assertIn('Focused window: firefox — "News"', text)
        self.assertIn("address 0xabc", text)
        self.assertNotIn("unknown", text)

    def test_one_failed_query_is_unknown_and_the_rest_still_true(self):
        text = snapshot({**HEALTHY, "clients": ""})
        self.assertIn("Open windows: unknown (hyprctl did not answer)", text)
        self.assertIn("Monitors: DP-1 2560x1440 (workspace 1)", text)
        self.assertTrue(text.endswith("call hypr_query before acting on what is missing."))

    def test_an_empty_desktop_says_none_not_unknown(self):
        text = snapshot(EMPTY)
        self.assertIn("Monitors: none", text)
        self.assertIn("Workspaces in use: none", text)
        self.assertIn("Open windows: none", text)
        self.assertNotIn("Focused window", text)  # nothing focused is not a failure
        self.assertNotIn("unknown", text)

    def test_bad_json_is_unknown(self):
        text = snapshot({**HEALTHY, "monitors": "not json"})
        self.assertIn("Monitors: unknown (hyprctl did not answer)", text)
        self.assertIn("call hypr_query", text)


if __name__ == "__main__":
    unittest.main()
