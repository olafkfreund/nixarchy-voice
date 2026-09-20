"""#46: do not read the screen when the screen is a password.

read_screen hands the OCR'd *text* to a model — already extracted, already
quotable, and logged. Nothing in the capture path declined based on what was
on screen.

The fixture is the twelve windows actually open on the development desktop
when this was written, so test 8 can assert that an ordinary machine is not
refused. Tightening the lists until everything is "sensitive" would pass every
other test in this file.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice.config import Config
from omarchy_voice.tools import Executor

MONITOR = "0,0 2560x1440"


def win(klass, title, at=(0, 0), size=(800, 600), ws="1"):
    return {"address": f"0x{abs(hash(klass + title)) % 10**8:x}", "class": klass,
            "title": title, "initialTitle": title, "at": list(at),
            "size": list(size), "workspace": {"name": ws}, "focusHistoryID": 1}


# The real desktop, at rest. None of these may be refused.
ORDINARY = [
    win("google-chrome", "Issues · olafkfreund/Factory - Google Chrome"),
    win("google-chrome", "Workflow runs · olafkfreund/nixarchy"),
    win("foot", "p620: Factory"),
    win("chrome-ejhkdoiecgkmdpomoahkdihbcldkgjci-Default", "Element | agents"),
    win("chrome-fmgjjmmmlfnkbppncabfkddbjimcfncm-Profile_4", "Gmail - Inbox (9)"),
    win("spotifast", "Spotifast"),
    win("chrome-dkfoldflcfkbhibhiajfgobmfkifgbdl-Default", "Sonarr - Missing"),
    win("chrome-gccfknblijdagkibookfihhdkjdcpabh-Default", "Geek - Dashboard"),
    win("chrome-agimnkijcaahngcdmfeangaknmldooml-Default", "YouTube - Moonstone Desert"),
    win("qemu", "QEMU (nixarchy)"),
    win("org.omarchy.herdr", "p620: nixarchy-ai-mirror"),
    win("vesktop", "(65) Discord | #omarchy-kernel"),
]
VAULT = win("1Password", "Personal Vault — 1Password", at=(900, 0))
BANK = win("google-chrome", "Chase — Online Banking", at=(900, 0))


def executor(clients, failed=None):
    ex = Executor(Config())
    ex._query_rows = lambda kind: (clients, failed)
    ex._query_json = lambda kind: clients
    ex._visible_workspaces = lambda: {"1"}
    ex._session_is_locked = lambda: False
    ex._screen_asleep = lambda: False
    return ex


class InFrameTests(unittest.TestCase):
    def test_a_vault_in_frame_refuses(self):
        why = executor(ORDINARY + [VAULT])._sensitive_in_frame(MONITOR)
        self.assertIsNotNone(why)
        self.assertIn("password manager", why)

    def test_a_vault_on_another_workspace_does_not(self):
        """The guard is about what is in the frame, not what exists."""
        elsewhere = {**VAULT, "workspace": {"name": "9"}}
        self.assertIsNone(executor(ORDINARY + [elsewhere])._sensitive_in_frame(MONITOR))

    def test_a_vault_beside_the_focused_window_refuses(self):
        """The case the prior art misses, and the reason this is not
        focused-only: hermes checks the focused window, and on the development
        desktop three windows shared one frame."""
        focused = {**ORDINARY[0], "focusHistoryID": 0}
        why = executor([focused, VAULT])._sensitive_in_frame(MONITOR)
        self.assertIsNotNone(why, "a vault beside the focused window is still in shot")

    def test_a_window_scoped_capture_narrows_with_it(self):
        """Reading one window while a vault is open elsewhere on the monitor
        must still work, or the guard makes the tool useless."""
        browser = ORDINARY[0]
        rect = f'{browser["at"][0]},{browser["at"][1]} ' \
               f'{browser["size"][0]}x{browser["size"][1]}'
        self.assertIsNone(executor([browser, VAULT])._sensitive_in_frame(rect))

    def test_a_bank_tab_refuses_on_its_title(self):
        """Every bank shares one window class with every other web page."""
        why = executor([BANK])._sensitive_in_frame(MONITOR)
        self.assertIsNotNone(why)
        self.assertIn("financial", why)

    def test_an_ordinary_desktop_is_not_refused(self):
        """Twelve real windows. Without this, tightening the lists until
        everything matches would pass every other test here."""
        self.assertIsNone(executor(ORDINARY)._sensitive_in_frame(MONITOR))

    def test_it_fails_closed_when_the_window_list_cannot_be_read(self):
        """Against the rule the two guards beside it follow, deliberately."""
        why = executor([], failed="hyprctl clients failed")._sensitive_in_frame(MONITOR)
        self.assertIsNotNone(why)
        self.assertIn("unknown", why)

    def test_a_window_it_cannot_place_is_assumed_to_be_in_shot(self):
        broken = {**VAULT}
        del broken["at"]
        self.assertIsNotNone(executor([broken])._sensitive_in_frame(MONITOR))


class CapturePathTests(unittest.TestCase):
    def test_nothing_is_captured_when_a_vault_is_in_frame(self):
        ex = executor(ORDINARY + [VAULT])
        with mock.patch("subprocess.run") as run:
            result = ex._ocr_region(MONITOR)
        self.assertFalse(result.ok)
        run.assert_not_called()

    def test_the_refusal_never_names_the_window(self):
        """The title is the protected thing. It must not reach the result, the
        transcript or the log."""
        secret = "Personal Vault — mother's maiden name is Rosencrantz"
        logged = []
        ex = executor([win("1Password", secret)])
        ex.on_record = logged.append
        with mock.patch("subprocess.run"):
            result = ex._ocr_region(MONITOR)
        self.assertFalse(result.ok)
        self.assertNotIn("Rosencrantz", result.output)
        self.assertNotIn("Rosencrantz", " ".join(ex.transcript))
        self.assertNotIn("Rosencrantz", " ".join(logged))

    def test_scroll_is_unaffected(self):
        """It moves the pointer and turns the wheel. Scrolling a password
        manager sends nothing to a model, and refusing would be its own bug."""
        ex = executor(ORDINARY + [VAULT])
        self.assertIsNone(ex._screen_unavailable(),
                          "no geometry means no sensitive check")

    def test_the_lock_guard_still_fails_open(self):
        """This change edits the docstring that governs the other two, so the
        rule they follow is asserted rather than assumed."""
        ex = executor(ORDINARY)
        ex._session_is_locked = lambda: False
        self.assertIsNone(ex._screen_unavailable(MONITOR))


if __name__ == "__main__":
    unittest.main()
