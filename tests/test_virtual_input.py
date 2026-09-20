"""#30: clicks and the wheel through Wayland, and the release that follows.

No test here spawns a real helper or needs a compositor — `nix flake check`
has neither, and #28 already demonstrated what happens when a unit test is
allowed to reach the live session.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice import virtual_input as vi
from omarchy_voice.config import Config
from omarchy_voice.tools import Executor


class FakeProc:
    """Stands in for the helper process. Records what was written to it."""

    def __init__(self, ready="READY"):
        self.written: list[str] = []
        self.killed = False
        self.terminated = False
        self._alive = True
        self.stdin = mock.Mock()
        self.stdin.write = self.written.append
        self.stdout = mock.Mock()
        self.stdout.readline = lambda: f"{ready}\n"

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def kill(self):
        self.killed = True
        self._alive = False

    def wait(self, timeout=None):
        self._alive = False


def helper(ready="READY", extent=(1920, 1080)):
    """A Helper wired to a FakeProc, with select() short-circuited."""
    h = vi.Helper(extent=extent)
    proc = FakeProc(ready)
    patches = [mock.patch("subprocess.Popen", return_value=proc),
               mock.patch("select.select", return_value=([proc.stdout], [], []))]
    for p in patches:
        p.start()
    return h, proc, patches


class LineTests(unittest.TestCase):
    def test_a_click_is_a_press_and_a_release(self):
        self.assertEqual(vi.click("left"), ["B 272 1", "B 272 0"])

    def test_a_double_click_is_that_pair_twice(self):
        """Not ydotool's --repeat 2: the helper has no notion of a count."""
        self.assertEqual(vi.click("left", double=True),
                         ["B 272 1", "B 272 0", "B 272 1", "B 272 0"])

    def test_the_other_buttons(self):
        self.assertEqual(vi.click("right")[0], "B 273 1")
        self.assertEqual(vi.click("middle")[0], "B 274 1")

    def test_a_bad_button_is_refused(self):
        with self.assertRaises(ValueError):
            vi.click("elbow")

    def test_scroll_is_two_axes(self):
        self.assertEqual(vi.scroll(0, -3), ["S 0 -3"])
        self.assertEqual(vi.scroll(4, 0), ["S 4 0"])


class LifetimeTests(unittest.TestCase):
    def tearDown(self):
        mock.patch.stopall()

    def test_closing_is_the_release(self):
        """No release command is sent. Closing the pipe destroys the virtual
        keyboard, and a virtual keyboard can only be released by its owner --
        which is the whole reason for taking this helper."""
        h, proc, _ = helper()
        h.send(vi.click("left"))
        h.close()
        self.assertTrue(proc.terminated)
        self.assertFalse(h.open)
        self.assertNotIn("R\n", proc.written)

    def test_the_extent_reaches_the_helper(self):
        """It exits 1 without AI_MIRROR_EXTENT_W/H, which nothing documents
        outside its own main()."""
        h, _, _ = helper(extent=(5120, 2520))
        h.start()
        env = subprocess.Popen.call_args.kwargs["env"]
        self.assertEqual(env["AI_MIRROR_EXTENT_W"], "5120")
        self.assertEqual(env["AI_MIRROR_EXTENT_H"], "2520")

    def test_a_helper_that_does_not_say_READY_is_unavailable(self):
        """control.py compares with != 'READY', not a prefix. A compositor
        without zwlr_virtual_pointer_v1 fails here rather than swallowing
        every click."""
        h, proc, _ = helper(ready="ERR no virtual pointer")
        with self.assertRaises(vi.Unavailable) as caught:
            h.start()
        self.assertIn("zwlr_virtual_pointer", str(caught.exception))
        self.assertTrue(proc.killed)

    def test_a_missing_binary_is_unavailable_not_a_crash(self):
        h = vi.Helper(extent=(800, 600))
        with mock.patch("subprocess.Popen", side_effect=FileNotFoundError("nope")):
            with self.assertRaises(vi.Unavailable):
                h.start()


class RevokeTests(unittest.TestCase):
    """SUPER + SHIFT + ESCAPE, which ai-mirror implements as a generation bump."""

    def tearDown(self):
        mock.patch.stopall()

    def _state(self, tmp, generation):
        d = Path(tmp) / "ai-mirror"
        d.mkdir(parents=True, exist_ok=True)
        (d / "state.json").write_text(json.dumps(
            {"owner": "off", "generation": generation}))

    def test_a_generation_bump_closes_the_helper_and_refuses(self):
        with mock.patch.dict("os.environ", {"XDG_RUNTIME_DIR": "/tmp/vi-test"}):
            self._state("/tmp/vi-test", 1)
            h, proc, _ = helper()
            h.start()
            self._state("/tmp/vi-test", 2)          # the key is pressed
            with self.assertRaises(vi.Unavailable) as caught:
                h.send(vi.click("left"))
        self.assertIn("withdrawn", str(caught.exception))
        self.assertTrue(proc.terminated, "the helper must not outlive the revoke")

    def test_an_unchanged_generation_is_not_a_revoke(self):
        with mock.patch.dict("os.environ", {"XDG_RUNTIME_DIR": "/tmp/vi-test"}):
            self._state("/tmp/vi-test", 7)
            h, proc, _ = helper()
            h.start()
            h.send(vi.click("left"))
        self.assertTrue(h.open)

    def test_no_ai_mirror_is_not_a_revoke(self):
        """The test that stops "honour the panic key" becoming "require
        ai-mirror installed". owner is off most of the time and ai-mirror is
        often not installed at all; neither is a reason to refuse input."""
        with mock.patch.dict("os.environ", {"XDG_RUNTIME_DIR": "/tmp/vi-absent"}):
            h, _, _ = helper()
            h.start()
            h.send(vi.click("left"))
            self.assertFalse(h.revoked())
        self.assertTrue(h.open)


class ExecutorWiringTests(unittest.TestCase):
    def test_an_executor_has_no_helper_by_default(self):
        """As `waker` does since #28: building an Executor in a test must not
        spawn a process."""
        self.assertIsNone(Executor(Config()).input_helper)

    def test_end_turn_closes_the_helper(self):
        ex = Executor(Config())
        closed = []
        ex.input_helper = type("H", (), {"close": lambda *_: closed.append(True)})()
        ex.end_turn()
        self.assertEqual(closed, [True])

    def test_end_turn_is_safe_without_one(self):
        Executor(Config()).end_turn()

    def test_no_helper_refuses_rather_than_pretending(self):
        ex = Executor(Config())
        result = ex._press_button("left", False)
        self.assertFalse(result.ok)
        self.assertNotIn("ydotool", result.output)


if __name__ == "__main__":
    unittest.main()
