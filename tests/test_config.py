"""Config loading: unknown keys, additive policy lists."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice import capabilities, config as cfg
from omarchy_voice.config import Config, DEFAULT_CONFIRM, DEFAULT_DENY


class ConfigLoadTests(unittest.TestCase):
    def write(self, text: str) -> Path:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / "config.toml"
        path.write_text(text)
        return path

    def test_unknown_keys_are_kept_for_doctor(self):
        path = self.write('[ears]\nenginee = "realtime"\n')
        loaded = cfg.load(path)
        self.assertIn("enginee", loaded.unknown_keys)

    def test_a_retired_key_is_not_reported_as_a_typo(self):
        # Listening is toggle-only now. Every config written before that says
        # `mode = "push"`, and none of them should make doctor shout about it.
        path = self.write('[ears]\nmode = "push"\n')
        loaded = cfg.load(path)
        self.assertEqual(loaded.unknown_keys, [])
        self.assertIn("mode", loaded.retired_keys)

    def test_a_retired_key_still_gets_explained(self):
        self.assertIn("toggle", cfg.RETIRED_KEYS["mode"])

    def test_there_is_no_always_on_setting(self):
        path = self.write('[ears]\nmode = "always"\n')
        loaded = cfg.load(path)
        self.assertFalse(hasattr(loaded, "mode"))

    def test_confirm_patterns_union_with_defaults(self):
        path = self.write('[hands]\nconfirm_patterns = ["\\\\bformat\\\\b"]\n')
        loaded = cfg.load(path)
        self.assertIn(r"\bformat\b", loaded.confirm_patterns)
        for builtin in DEFAULT_CONFIRM:
            self.assertIn(builtin, loaded.confirm_patterns)

    def test_confirm_patterns_replace_drops_defaults(self):
        path = self.write(
            '[hands]\n'
            'confirm_patterns = ["\\\\bformat\\\\b"]\n'
            'confirm_patterns_replace = true\n'
        )
        loaded = cfg.load(path)
        self.assertEqual(loaded.confirm_patterns, [r"\bformat\b"])
        self.assertNotIn(r"\breboot\b", loaded.confirm_patterns)

    def test_deny_patterns_union_with_defaults(self):
        path = self.write('[hands]\ndeny_patterns = ["\\\\bwipe\\\\b"]\n')
        loaded = cfg.load(path)
        self.assertIn(r"\bwipe\b", loaded.deny_patterns)
        self.assertIn(DEFAULT_DENY[0], loaded.deny_patterns)



class UnreadableSourceTests(unittest.TestCase):
    """The manifest degrades silently by design; doctor must not.

    Every source here drops a section and lets the manifest build anyway,
    which is right at runtime and wrong to keep quiet about — the symptom is
    Oma not knowing how to do something, three steps from the cause.
    """

    def test_a_missing_omarchy_path_is_named(self):
        with mock.patch.object(capabilities, "OMARCHY_PATH",
                               Path("/nonexistent/omarchy")):
            problems = capabilities.unreadable_sources()
        self.assertTrue(any("OMARCHY_PATH" in p for p in problems), problems)

    def test_no_dispatcher_source_at_all_is_named(self):
        """#64: the compositor is asked first, so this is only a problem when
        BOTH it and every stub are unavailable -- and the message now says so
        rather than naming one file that was never the whole story."""
        capabilities._live_namespaces.cache_clear()
        self.addCleanup(capabilities._live_namespaces.cache_clear)
        with mock.patch.object(capabilities, "_stub_path", return_value=None), \
             mock.patch.object(capabilities, "_run", return_value=""):
            problems = capabilities.unreadable_sources()
        self.assertTrue(any("dispatcher tree" in p for p in problems), problems)

    def test_a_readable_machine_reports_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "default/hypr/bindings").mkdir(parents=True)
            stub = root / "hl.meta.lua"
            # A field line, not just the class: #64 made the check "are there
            # any dispatchers" rather than "does a file exist", because a stub
            # that parses to nothing is no more useful than a missing one.
            stub.write_text("---@class HL.DspNamespace\n"
                            "---@field focus fun(...): HL.Dispatcher\n")
            capabilities._live_namespaces.cache_clear()
            self.addCleanup(capabilities._live_namespaces.cache_clear)
            with mock.patch.object(capabilities, "OMARCHY_PATH", root), \
                 mock.patch.object(capabilities, "_stub_path", lambda: stub), \
                 mock.patch.object(capabilities, "_run", return_value=""), \
                 mock.patch.object(capabilities, "_omarchy_routes",
                                   lambda: {"omarchy theme set"}):
                self.assertEqual(capabilities.unreadable_sources(), [])


class CodingAgentTests(unittest.TestCase):
    """What the manifest says about the agents on this machine.

    The tools to drive them already existed -- run_in_terminal starts one and
    watch_terminal reports back. What was missing is that nothing said they
    are CLIs, so asked to have Claude review a diff she searched the desktop
    application list, found Claude-Desktop, and failed to launch it twice.
    """

    def test_only_what_is_installed_is_offered(self):
        # Naming an absent binary buys a failed command and a confused turn.
        with mock.patch.object(capabilities.shutil, "which",
                               side_effect=lambda b: "/bin/claude" if b == "claude" else None):
            section = capabilities.coding_agents()
        self.assertIn("claude -p", section)
        self.assertNotIn("codex", section)
        self.assertNotIn("ollama", section)

    def test_nothing_installed_means_no_section_at_all(self):
        # Not an empty heading: that is tokens spent every turn to say nothing.
        with mock.patch.object(capabilities.shutil, "which", return_value=None):
            self.assertEqual(capabilities.coding_agents(), "")

    def test_it_says_these_are_not_desktop_apps(self):
        with mock.patch.object(capabilities.shutil, "which", return_value="/bin/x"):
            section = capabilities.coding_agents()
        self.assertIn("not desktop apps", section)
        self.assertIn("run_in_terminal", section)
        self.assertIn("watch_terminal", section)


if __name__ == "__main__":
    unittest.main()


class ConsentDefaults(unittest.TestCase):
    """#17: capabilities that reach the desktop or the disk start off."""

    def test_both_start_off(self):
        self.assertFalse(Config().desktop_control)
        self.assertFalse(Config().allow_notifications)

    def test_load_remembers_whether_the_user_said_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            quiet = Path(tmp) / "quiet.toml"
            quiet.write_text("[mouth]\nnotify = true\n")
            self.assertFalse(cfg.load(quiet).allow_notifications_explicit)
            asked = Path(tmp) / "asked.toml"
            asked.write_text("[hands]\nallow_notifications = false\n")
            self.assertTrue(cfg.load(asked).allow_notifications_explicit)

    def test_desktop_control_is_read_from_the_hands_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.toml"
            path.write_text("[hands]\ndesktop_control = true\n")
            self.assertTrue(cfg.load(path).desktop_control)


class HoldTests(unittest.TestCase):
    def test_the_local_hold_is_its_own_and_the_realtime_one_is_untouched(self):
        """Lowering silence_hold_seconds would end every OpenAI turn early:
        server-side turn detection needs the pause to hear it (#72)."""
        self.assertEqual(Config().silence_hold_seconds, 1.5)
        self.assertEqual(Config().end_of_speech_seconds, 0.8)

    def test_the_new_keys_are_known(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.toml"
            path.write_text('end_of_speech_seconds = 1.0\nwhisper_vocabulary = "herdr"\n')
            loaded = cfg.load(path)
        self.assertEqual((loaded.end_of_speech_seconds, loaded.whisper_vocabulary), (1.0, "herdr"))
        self.assertEqual(loaded.unknown_keys, [])
