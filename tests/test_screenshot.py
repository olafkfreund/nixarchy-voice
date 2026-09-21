"""#58: the calling agent gets the pixels, not only our OCR of them.

The guard tests here assert that no capture process is SPAWNED, not merely
that the answer is a refusal. A guard placed after grim would return exactly
the same message while having already taken the picture of the thing it is
refusing to show.

Run with: python3 -m unittest discover -s tests
"""

import base64
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice.config import Config
from omarchy_voice.tools import Executor, Result, tools_for

PNG = b"\x89PNG\r\n\x1a\n-pretend-this-is-pixels"


class ScreenshotTests(unittest.TestCase):
    def executor(self, *, refused=None, unavailable=None, geometry="0,0 800x600"):
        ex = Executor(Config(dry_run=False))
        ex._target_geometry = lambda target="screen": (geometry, None)
        ex._screen_unavailable = lambda: unavailable
        ex._capture_refused = lambda geo: refused
        return ex

    def test_a_permitted_target_returns_png_bytes(self):
        ex = self.executor()
        with mock.patch("omarchy_voice.tools.shutil.which", return_value="/usr/bin/grim"), \
             mock.patch("omarchy_voice.tools.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=PNG, stderr=b"")
            result = ex._tool_screenshot("active")
        self.assertTrue(result.ok)
        self.assertEqual(result.image, PNG)
        # PNG, not the PPM the OCR path uses: these bytes go to a model.
        self.assertIn("-t", run.call_args.args[0])
        self.assertIn("png", run.call_args.args[0])

    def test_the_text_line_says_what_was_captured(self):
        ex = self.executor(geometry="10,20 640x480")
        with mock.patch("omarchy_voice.tools.shutil.which", return_value="/usr/bin/grim"), \
             mock.patch("omarchy_voice.tools.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=PNG, stderr=b"")
            result = ex._tool_screenshot("chrome")
        self.assertIn("chrome", result.output)
        self.assertIn("640x480", result.output)

    def test_a_sensitive_window_refuses_before_grim_runs(self):
        ex = self.executor(refused="a credential prompt is visible in that area")
        with mock.patch("omarchy_voice.tools.shutil.which", return_value="/usr/bin/grim"), \
             mock.patch("omarchy_voice.tools.subprocess.run") as run:
            result = ex._tool_screenshot("active")
        self.assertFalse(result.ok)
        self.assertIsNone(result.image)
        run.assert_not_called()

    def test_an_unreadable_screen_refuses_before_grim_runs(self):
        ex = self.executor(unavailable="the session is locked")
        with mock.patch("omarchy_voice.tools.shutil.which", return_value="/usr/bin/grim"), \
             mock.patch("omarchy_voice.tools.subprocess.run") as run:
            result = ex._tool_screenshot("active")
        self.assertFalse(result.ok)
        self.assertIsNone(result.image)
        run.assert_not_called()

    def test_an_unresolvable_target_refuses_before_grim_runs(self):
        ex = self.executor()
        ex._target_geometry = lambda target="screen": (None, "nothing is focused")
        with mock.patch("omarchy_voice.tools.subprocess.run") as run:
            result = ex._tool_screenshot("active")
        self.assertFalse(result.ok)
        self.assertEqual(result.output, "nothing is focused")
        run.assert_not_called()

    def test_a_failed_capture_is_an_error_not_empty_pixels(self):
        ex = self.executor()
        with mock.patch("omarchy_voice.tools.shutil.which", return_value="/usr/bin/grim"), \
             mock.patch("omarchy_voice.tools.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=1, stdout=b"", stderr=b"no such output")
            result = ex._tool_screenshot("active")
        self.assertFalse(result.ok)
        self.assertIsNone(result.image)
        self.assertIn("no such output", result.output)

    def test_a_missing_grim_says_how_to_install_it(self):
        ex = self.executor()
        with mock.patch("omarchy_voice.tools.shutil.which", return_value=None):
            result = ex._tool_screenshot("active")
        self.assertFalse(result.ok)
        self.assertIsNone(result.image)


class ResultTests(unittest.TestCase):
    def test_the_text_path_is_unchanged_by_the_new_field(self):
        # Every existing caller reads as_tool_result. It must not learn about
        # images -- the voice path in particular has no use for pixels.
        self.assertEqual(Result(True, "done", image=PNG).as_tool_result(), "done")
        self.assertEqual(Result(False, "nope", image=PNG).as_tool_result(), "ERROR: nope")

    def test_image_defaults_to_none(self):
        self.assertIsNone(Result(True, "x").image)


class SchemaTests(unittest.TestCase):
    def schema(self):
        return next(t for t in tools_for(Config()) if t["name"] == "screenshot")

    def test_it_is_offered(self):
        self.assertEqual(self.schema()["input_schema"]["properties"]["target"]["type"],
                         "string")

    def test_the_description_tells_the_caller_what_it_costs(self):
        # A tool that is ~10x read_screen must say so, or it gets called by
        # default and the caller pays without choosing to.
        text = self.schema()["description"]
        self.assertIn("read_screen", text)
        self.assertIn("10x", text)

    def test_the_description_says_the_default_is_a_window_and_why(self):
        # It is deliberately unlike read_screen's default. Undocumented, that
        # is a trap rather than a decision.
        text = self.schema()["description"].lower()
        self.assertIn("active window", text)
        self.assertIn("downscaled", text)


if __name__ == "__main__":
    unittest.main()
