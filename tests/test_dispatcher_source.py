"""#64: validate against the compositor that is running, not a pinned stub.

`hypr_dispatch` checks every dispatcher name against a list. That list used to
come from a stub file pinned by THIS repo's nixpkgs, which has no relationship
to the Hyprland a user actually runs -- the two agreeing was luck, and nothing
would have noticed when it stopped.

Both directions were bad, and the likelier one is the one that looks safe: a
dispatcher the compositor has but the stub lacks makes hypr_dispatch refuse
something that works, in a tool whose entire purpose is to stop silent
failures.

Run with: nix develop -c python3 -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice import capabilities  # noqa: E402

LIVE = "cursor.move,focus,window.close,workspace.change_id"


def many(count: int) -> str:
    return ",".join(f"window.d{n}" for n in range(count))


class IntrospectionLuaTests(unittest.TestCase):
    def test_the_lua_is_a_plain_literal_in_the_source(self):
        """#22 is about Lua never being assembled from input. Checked against
        the SOURCE rather than the value: a scan of the string cannot tell an
        f-string's result from a literal, and "{}" is an empty Lua table, not
        interpolation -- which is how the first version of this test failed.
        """
        import ast

        tree = ast.parse(Path(capabilities.__file__).read_text())
        assigned = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(getattr(t, "id", None) == "_LIVE_DISPATCHER_LUA"
                    for t in node.targets)
        ]
        self.assertEqual(len(assigned), 1, "expected exactly one assignment")
        self.assertIsInstance(assigned[0], ast.Constant)
        self.assertIsInstance(assigned[0].value, str)
        self.assertIn("hl.dsp", assigned[0].value)


class LiveEnumerationTests(unittest.TestCase):
    def live(self, output: str):
        capabilities._live_namespaces.cache_clear()
        with mock.patch.object(capabilities, "_run", return_value=output):
            return capabilities._live_namespaces()

    def tearDown(self):
        capabilities._live_namespaces.cache_clear()

    def test_dotted_names_become_namespaces(self):
        got = dict(self.live(many(30) + "," + LIVE))
        self.assertIn("cursor", got)
        self.assertIn("move", got["cursor"])
        self.assertIn("focus", got["root"])

    def test_no_compositor_is_empty_not_a_guess(self):
        self.assertEqual(self.live(""), ())

    def test_a_lua_error_is_empty(self):
        self.assertEqual(self.live("error: 3 [string \"x\"]:1: syntax error"), ())

    def test_a_connection_failure_is_empty(self):
        # What hyprctl prints with no compositor: "Couldn't connect to ... (4)"
        self.assertEqual(self.live("Couldn't connect to /run/user/1000/x (4)"), ())

    def test_a_suspiciously_short_list_is_not_believed(self):
        """If hl.dsp ever gains an __index, pairs() returns a fraction of the
        set and every missing dispatcher is silently refused. Measured, the
        bare top level is 20 and the full walk is 51."""
        self.assertEqual(self.live(many(19)), ())
        self.assertNotEqual(self.live(many(51)), ())


class SourcePrecedenceTests(unittest.TestCase):
    """An explicit choice beats the compositor; the packaging's guess loses."""

    def setUp(self):
        capabilities._live_namespaces.cache_clear()
        self.addCleanup(capabilities._live_namespaces.cache_clear)

    def stub(self, tmp: Path, name: str) -> str:
        path = tmp / name
        path.write_text("---@class HL.DspNamespace\n"
                        "---@field fromstub fun(...): HL.Dispatcher\n")
        return str(path)

    def test_an_explicit_stub_beats_the_live_compositor(self):
        with mock.patch.object(capabilities, "_run", return_value=many(51)):
            import tempfile

            with tempfile.TemporaryDirectory() as tmp:
                chosen = self.stub(Path(tmp), "chosen.lua")
                with mock.patch.dict("os.environ",
                                     {"OMARCHY_VOICE_HL_STUB": chosen}):
                    self.assertIn("fromstub", capabilities.dispatchers())

    def test_the_live_compositor_beats_the_packaging_fallback(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            fallback = self.stub(Path(tmp), "fallback.lua")
            env = {"OMARCHY_VOICE_HL_STUB_FALLBACK": fallback}
            with mock.patch.dict("os.environ", env, clear=False), \
                 mock.patch.object(capabilities, "_run", return_value=many(51)):
                import os

                os.environ.pop("OMARCHY_VOICE_HL_STUB", None)
                found = capabilities.dispatchers()
        self.assertIn("window.d0", found)
        self.assertNotIn("fromstub", found)

    def test_nothing_anywhere_refuses_everything(self):
        """#22: falling open here hands back the hole the check exists to
        close, so an empty set is the correct answer, not a bug."""
        with mock.patch.object(capabilities, "_run", return_value=""), \
             mock.patch.object(capabilities, "_stub_path", return_value=None):
            self.assertEqual(capabilities.dispatchers(), frozenset())


if __name__ == "__main__":
    unittest.main()
