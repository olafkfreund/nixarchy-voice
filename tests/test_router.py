"""The router: the measured fixed commands, and nothing else (#71).

The bar has two numbers and both are here. The 11 command lines the session
log actually holds are routed against one fixture of windows, and every
sentence below that is not exactly one of those commands -- including the
logged ones that merely contain a command word -- goes to the model.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)
sys.path.insert(0, str(ROOT / "tools"))

from eval_router import FIXTURE_CLIENTS  # noqa: E402
from omarchy_voice.router import route  # noqa: E402


def fixture():
    return FIXTURE_CLIENTS, None


def focus(address):
    return ("hypr_dispatch", {"dispatcher": "focus", "args": {"window": f"address:{address}"}})


TERMINAL = ("omarchy_cli", {"command": "launch terminal"})

# The logged command lines, each to the call it must make (the spec's Q6).
COMMANDS = {
    "Move Discord back to workspace 3, please.": (
        "hypr_dispatch", {"dispatcher": "window.move",
                          "args": {"workspace": "3", "follow": False, "window": "address:0x1"}}),
    "Open up a new terminal for me, please.": TERMINAL,
    "Can you open up a new terminal?": TERMINAL,
    "Open up a new terminal, please.": TERMINAL,
    "Can you bring up my Teams window?": focus("0x2"),
    "Can you switch to workspace one, please?": (
        "hypr_dispatch", {"dispatcher": "focus", "args": {"workspace": "1"}}),
    "Okay, close the weather window, please.": (
        "hypr_dispatch", {"dispatcher": "window.close", "args": {"window": "address:0x3"}}),
}
LISTS = ["What windows do I have open?", "What windows are opened on this desktop?",
         "What windows do I have open on this desktop?"]

# Must go to the model: the adversarial table, then the logged non-commands.
NOT_COMMANDS = [
    "Don't close it.", "Do not switch to workspace two.", "Turn it down.",
    "Close it.", "Close the window.", "Move it to workspace 2.",
    "Close the terminal.", "Focus the terminal.", "Close chrome.",
    "Close all windows.", "Switch to workspace 3 and open firefox.",
    "Close Discord and Teams.", "Close weather, no, discord.",
    "What is the weather for today?", "Can you open up today's weather?",
    "Plus terminal.", "I will turn the light.", "Opened it.", "Perfect. Close it.",
    "Oh, that's fine. Close the window.", "Go back to works, bit one.",
    "Can you show me today's weather please?",
    "That's fine. You can stop it. You can close the terminal. This was a test.",
]


class CommandTests(unittest.TestCase):
    def test_the_logged_commands_are_routed(self):
        for text, (tool, args) in COMMANDS.items():
            with self.subTest(text=text):
                hit = route(text, fixture)
                self.assertIsNotNone(hit)
                self.assertEqual((hit.tool, hit.args), (tool, args))

    def test_the_window_list_is_answered_from_the_clients(self):
        for text in LISTS:
            with self.subTest(text=text):
                hit = route(text, fixture)
                self.assertIsNone(hit.tool)
                for name in ("Discord", "Microsoft Teams", "Weather", "two Alacritty"):
                    self.assertIn(name, hit.answer)

    def test_the_spoken_lines(self):
        self.assertEqual(route("Move Discord back to workspace 3, please.", fixture).said,
                         "Moved Discord to workspace three.")
        self.assertEqual(route("Can you switch to workspace one, please?", fixture).said,
                         "Workspace one.")
        self.assertEqual(route("Okay, close the weather window, please.", fixture).said,
                         "Closed Weather.")

    def test_eleven_lines_route_and_nothing_else_does(self):
        routed = [t for t in [*COMMANDS, *LISTS, LISTS[0], *NOT_COMMANDS]
                  if route(t, fixture)]
        self.assertEqual(len(routed), 11)

    def test_the_wake_word_is_not_part_of_the_command(self):
        hit = route("Oma, switch to workspace two", fixture, wake="oma")
        self.assertEqual(hit.args, {"dispatcher": "focus", "args": {"workspace": "2"}})


class RefusalTests(unittest.TestCase):
    def test_anything_but_exactly_one_command_goes_to_the_model(self):
        for text in NOT_COMMANDS:
            with self.subTest(text=text):
                self.assertIsNone(route(text, fixture))

    def test_one_matching_window_is_routed(self):
        self.assertEqual((route("Bring up Teams.", fixture).tool,
                          route("Bring up Teams.", fixture).args), focus("0x2"))
        self.assertIsNotNone(route("Close the weather window.", fixture))

    def test_two_matching_windows_are_not_a_choice_to_make(self):
        self.assertIsNone(route("close alacritty", fixture))

    def test_a_second_weak_match_is_enough_to_refuse(self):
        """Exactly one window at any score, not a unique top score."""
        tab = {"class": "google-chrome", "title": "Weather forecast - Google Chrome",
               "address": "0x7"}
        self.assertIsNone(route("Close the weather window",
                                lambda: ([*FIXTURE_CLIENTS, tab], None)))

    def test_close_needs_a_class_match(self):
        """A page sets its window title: "close notes" must not close Chrome."""
        chrome = [{"class": "google-chrome",
                   "title": "Release notes - Google Chrome", "address": "0x6"}]
        self.assertIsNone(route("close notes", lambda: (chrome, None)))
        self.assertEqual(route("close chrome", lambda: (chrome, None)).args,
                         {"dispatcher": "window.close",
                          "args": {"window": "address:0x6"}})
        self.assertEqual((route("focus notes", lambda: (chrome, None)).tool,
                          route("focus notes", lambda: (chrome, None)).args),
                         focus("0x6"))

    def test_an_unanswered_query_is_not_an_empty_desktop(self):
        self.assertIsNone(route("What windows are open",
                                lambda: ([], "hyprctl clients failed: timeout")))
        self.assertEqual(route("What windows are open", lambda: ([], None)).answer,
                         "Nothing is open.")

    def test_a_miss_never_asks_hyprctl(self):
        calls = []
        self.assertIsNone(route("turn it down", lambda: calls.append(1) or fixture()))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
