"""#67: every tool that sends input is guarded on what it sends input TO.

The sensitive-window guard (#46) covered every path that READS pixels and none
that WRITES input, so read_screen refused to look at a password manager while
type_text typed into it and reported success.

The enumeration test below is the point of this file. click_text was already
protected before #67 -- but only because it OCRs first and the guard rides on
_ocr_words, which is the right outcome reached by accident. Accidents do not
survive refactoring, and nothing would have noticed.

Run with: nix develop -c python3 -m unittest discover -s tests
"""

import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice.config import Config  # noqa: E402
from omarchy_voice.tools import INPUT_TOOLS, TOOL_SCHEMAS, Executor, Result  # noqa: E402

# Anything a handler does that puts input into a window. Deliberately loose:
# over-matching fails the suite until someone lists the tool, under-matching
# ships an unguarded tool in silence.
INPUT_MARKERS = ("send_key_state", "send_shortcut", "cursor.move",
                 "_press_button", "_send_input", "wheel")

VAULT = {"address": "0xvault", "class": "1Password", "title": "1Password",
         "at": [0, 0], "size": [800, 600], "workspace": {"name": "1"},
         "mapped": True, "focusHistoryID": 0}
ORDINARY = {"address": "0xord", "class": "foot", "title": "shell",
            "at": [0, 0], "size": [800, 600], "workspace": {"name": "1"},
            "mapped": True, "focusHistoryID": 0}

# The arguments each input tool needs, beyond its target.
INVOCATIONS = {
    "type_text": ({"text": "secret"}, "window"),
    "send_shortcut": ({"mods": "CTRL", "key": "a"}, "window"),
    "click_text": ({"text": "Continue"}, "target"),
    "scroll": ({"direction": "down"}, "target"),
}


def executor(clients):
    ex = Executor(Config(dry_run=False))
    ex._query_rows = lambda kind: (clients, None)
    ex._query_json = lambda kind: clients
    ex._visible_workspaces = lambda: {"1"}
    ex._screen_unavailable = lambda: None
    ex._kb_layout = lambda: ("gb", "")
    ex.sent = []
    ex._shell = lambda cmd, **kw: (ex.sent.append(cmd), Result(True, "ok"))[1]
    ex._dispatch = lambda *a, **k: (ex.sent.append(a), Result(True, "ok"))[1]
    ex._dispatch_lua = lambda lua: (ex.sent.append(lua), Result(True, "ok"))[1]
    ex._ocr_words = lambda geometry: ([{"text": "Continue", "x": 10, "y": 10,
                                        "w": 60, "h": 14, "conf": 90}], "")
    return ex


class EnumerationTests(unittest.TestCase):
    """The constant and the code cannot drift apart, in either direction."""

    def handlers(self):
        for schema in TOOL_SCHEMAS:
            handler = getattr(Executor, f"_tool_{schema['name']}", None)
            if handler is not None:
                yield schema["name"], inspect.getsource(handler)

    def test_every_tool_that_sends_input_is_listed(self):
        """A tool added later cannot quietly skip the guard: it fails here
        until somebody either guards it or argues it out."""
        found = {name for name, src in self.handlers()
                 if any(m in src for m in INPUT_MARKERS)}
        self.assertEqual(found, set(INPUT_TOOLS),
                         "INPUT_TOOLS disagrees with what the handlers do")

    def test_every_listed_tool_actually_calls_the_guard(self):
        for name, src in self.handlers():
            if name in INPUT_TOOLS:
                with self.subTest(tool=name):
                    self.assertIn("_input_refused", src)


class RefusalTests(unittest.TestCase):
    def test_no_input_tool_will_act_on_a_password_manager(self):
        """Asserted on what was SENT, not on the message. A message-level
        check passes on an implementation that acts first and explains after."""
        for name in sorted(INPUT_TOOLS):
            args, key = INVOCATIONS[name]
            with self.subTest(tool=name):
                ex = executor([VAULT])
                result = getattr(ex, f"_tool_{name}")(**args, **{key: "address:0xvault"})
                self.assertFalse(result.ok, result.output)
                self.assertEqual(ex.sent, [], f"{name} sent something anyway")

    def test_the_refusal_names_the_category_and_never_the_title(self):
        """Titles on this desktop carry inbox counts and email addresses, so a
        refusal that quoted one would leak exactly what it refused (#46)."""
        leaky = dict(VAULT, title="1Password — someone@example.com — Vault (7)")
        ex = executor([leaky])
        result = ex._tool_type_text("x", "address:0xvault")
        self.assertIn("a password manager", result.output)
        self.assertNotIn("someone@example.com", result.output)
        self.assertNotIn("(7)", result.output)

    def test_an_ordinary_window_is_untouched(self):
        for name in sorted(INPUT_TOOLS):
            args, key = INVOCATIONS[name]
            with self.subTest(tool=name):
                ex = executor([ORDINARY])
                result = getattr(ex, f"_tool_{name}")(**args, **{key: "address:0xord"})
                self.assertNotIn("password manager", result.output)

    def test_an_unreadable_window_list_sends_nothing(self):
        ex = executor([ORDINARY])
        ex._query_rows = lambda kind: ([], "hyprctl timed out")
        result = ex._tool_type_text("x", "address:0xord")
        self.assertFalse(result.ok)
        self.assertEqual(ex.sent, [])

    def test_an_empty_desktop_is_allowed_not_refused(self):
        """#24's distinction: nothing open means nothing to protect and the
        input lands nowhere. Only an UNREADABLE list is unknown."""
        ex = executor([])
        ex._target_geometry = lambda target="screen": ("0,0 800x600", None)
        self.assertIsNone(ex._input_refused("activewindow"))


if __name__ == "__main__":
    unittest.main()
