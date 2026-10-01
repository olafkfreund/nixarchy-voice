"""Doctor's cloud-voice lines and `voices` (#178).

check_ready and voices are faked: nothing here reaches the network.

Run with: python3 -m unittest discover -s tests
"""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice import cli, elevenlabs
from omarchy_voice.config import Config


def _lines(**settings):
    # _tick adds colour under a TTY; the glyphs are what is asserted.
    with mock.patch("omarchy_voice.cli.sys.stdout.isatty", return_value=False):
        return _join(settings)


def _join(settings):
    return "\n".join(cli._cloud_voice_lines(Config(**settings)))


class CloudVoiceDoctorTests(unittest.TestCase):
    def test_off_lists_the_four_steps_and_the_key_slot(self):
        text = _lines(elevenlabs_enabled=False, elevenlabs_key_slot="my-slot")
        for step in ("1.", "2.", "3.", "4."):
            self.assertIn(step, text)
        self.assertIn("my-slot", text)
        self.assertIn("enabled = true", text)

    def test_off_never_reaches_check_ready(self):
        with mock.patch.object(elevenlabs, "check_ready") as check:
            _lines(elevenlabs_enabled=False)
        check.assert_not_called()

    def test_on_and_clean_is_a_ticked_header_naming_the_model(self):
        with mock.patch.object(elevenlabs, "check_ready", return_value=[]):
            text = _lines(elevenlabs_enabled=True)
        self.assertIn("✓ cloud voice: ElevenLabs eleven_v4_turbo", text)

    def test_on_with_a_problem_shows_it(self):
        with mock.patch.object(elevenlabs, "check_ready",
                               return_value=["no ElevenLabs API key"]):
            text = _lines(elevenlabs_enabled=True)
        self.assertIn("✗ cloud voice", text)
        self.assertIn("no ElevenLabs API key", text)

    def test_the_old_default_is_flagged_and_no_other_model_is(self):
        with mock.patch.object(elevenlabs, "check_ready", return_value=[]):
            old = _lines(elevenlabs_enabled=True,
                         elevenlabs_model="eleven_turbo_v2_5")
            flash = _lines(elevenlabs_enabled=True,
                           elevenlabs_model="eleven_flash_v2_5")
        self.assertIn("pins eleven_turbo_v2_5", old)
        self.assertNotIn("pins", flash)


class VoicesCommandTests(unittest.TestCase):
    def test_voices_is_a_registered_subcommand(self):
        args = cli.build_parser().parse_args(["voices"])
        self.assertIs(args.func, cli.cmd_voices)

    VOICES = [
        {"name": "Rachel", "voice_id": "id-rachel", "category": "premade",
         "accent": "american", "gender": "female", "description": ""},
        {"name": "Mine", "voice_id": "id-mine", "category": "",
         "accent": "", "gender": "", "description": ""},
    ]

    def _run(self, **mocked):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(elevenlabs, "voices", **mocked), \
                redirect_stdout(out), redirect_stderr(err):
            code = cli.cmd_voices(None, Config(elevenlabs_voice_id="id-mine"))
        return code, out.getvalue(), err.getvalue()

    def test_lines_carry_id_and_name_and_the_configured_one_is_starred(self):
        code, out, _ = self._run(return_value=self.VOICES)
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("  id-rachel  Rachel"))
        self.assertIn("(premade; american, female)", lines[0])
        self.assertTrue(lines[1].startswith("* id-mine  Mine"))
        self.assertNotIn("(", lines[1])
        self.assertIn("voice_id under [elevenlabs]", lines[-1])

    def test_no_voices_says_so(self):
        code, out, _ = self._run(return_value=[])
        self.assertEqual((code, out.strip()), (0, "no voices on this account"))

    def test_unavailable_is_an_error_on_stderr(self):
        code, out, err = self._run(side_effect=elevenlabs.Unavailable("no key"))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("error: no key", err)


if __name__ == "__main__":
    unittest.main()
