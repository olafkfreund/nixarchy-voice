"""The cloud voice, and the promise that it can never take the voice away.

ElevenLabs sounds enormously better than piper and is the only part of the
mouth that can fail: no network, expired key, spent quota, missing ffmpeg. The
headline test here is the one that says a failure is heard as a plainer voice
rather than as silence -- an assistant that stops answering because an API had
a bad afternoon is a worse bug than one that sounds like 1998.

Nothing here touches the network: urllib and subprocess are both faked.

Run with: python3 -m unittest discover -s tests
"""

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice import elevenlabs, feedback
from omarchy_voice.config import Config


def _config(**overrides) -> Config:
    settings = dict(elevenlabs_enabled=True, elevenlabs_voice_id="abc123voice",
                    speak=True)
    settings.update(overrides)
    return Config(**settings)


class _Done:
    """What subprocess.run gives back, with only the bits we read."""

    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class KeyLookupTests(unittest.TestCase):
    def setUp(self):
        elevenlabs._key_for_slot.cache_clear()

    def tearDown(self):
        elevenlabs._key_for_slot.cache_clear()

    def test_the_keyring_is_preferred_over_the_environment(self):
        """An export in a shell profile is a plaintext key on disk.

        It stays supported as a last resort, but if the keyring has an answer
        that is the one to use -- otherwise someone who did the secure thing
        keeps silently using the insecure copy they left behind.
        """
        with mock.patch.dict("os.environ", {"ELEVENLABS_API_KEY": "from-env"}), \
                mock.patch("shutil.which", return_value="/run/secret-tool"), \
                mock.patch("subprocess.run",
                           return_value=_Done(0, stdout="from-keyring\n")):
            self.assertEqual(elevenlabs.api_key(_config()), "from-keyring")

    def test_the_environment_is_the_fallback_when_the_slot_is_empty(self):
        with mock.patch.dict("os.environ", {"ELEVENLABS_API_KEY": "from-env"}), \
                mock.patch("shutil.which", return_value="/run/secret-tool"), \
                mock.patch("subprocess.run", return_value=_Done(1, stdout="")):
            self.assertEqual(elevenlabs.api_key(_config()), "from-env")

    def test_no_key_anywhere_is_an_empty_string_not_a_crash(self):
        """`doctor` asks for this on machines with neither. It must answer."""
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch("shutil.which", return_value=None):
            self.assertEqual(elevenlabs.api_key(_config()), "")


class ReadyTests(unittest.TestCase):
    """All three, or we do not call out to the network at all.

    A missing one used to mean an HTTP request built around an empty voice id,
    which comes back 404 a second later instead of never being sent.
    """

    def setUp(self):
        elevenlabs._key_for_slot.cache_clear()

    def _with_key(self, key="k"):
        return mock.patch.object(elevenlabs, "api_key", return_value=key)

    def test_ready_when_enabled_and_configured_and_keyed(self):
        with self._with_key():
            self.assertTrue(elevenlabs.ready(_config()))

    def test_not_ready_when_disabled(self):
        with self._with_key():
            self.assertFalse(elevenlabs.ready(_config(elevenlabs_enabled=False)))

    def test_not_ready_without_a_voice_id(self):
        with self._with_key():
            self.assertFalse(elevenlabs.ready(_config(elevenlabs_voice_id="")))

    def test_not_ready_without_a_key(self):
        with self._with_key(""):
            self.assertFalse(elevenlabs.ready(_config()))


class CheckReadyTests(unittest.TestCase):
    """Each missing piece named separately.

    One "elevenlabs is not configured" line for four different causes sends
    you to read config files that were right all along.
    """

    def setUp(self):
        elevenlabs._key_for_slot.cache_clear()

    def test_every_missing_piece_is_named(self):
        with mock.patch.object(elevenlabs, "api_key", return_value=""), \
                mock.patch("shutil.which", return_value=None):
            problems = " ".join(elevenlabs.check_ready(
                _config(elevenlabs_enabled=False, elevenlabs_voice_id="")))
        self.assertIn("elevenlabs_enabled", problems)
        self.assertIn("elevenlabs_voice_id", problems)
        self.assertIn("ffmpeg", problems)
        self.assertIn("ELEVENLABS_API_KEY", problems)

    def test_the_key_slot_is_quoted_so_the_hint_can_be_pasted(self):
        with mock.patch.object(elevenlabs, "api_key", return_value=""), \
                mock.patch("shutil.which", return_value="/bin/ffmpeg"):
            problems = " ".join(elevenlabs.check_ready(
                _config(elevenlabs_key_slot="my-own-slot")))
        self.assertIn("my-own-slot", problems)

    def test_a_fully_configured_setup_reports_nothing(self):
        with mock.patch.object(elevenlabs, "api_key", return_value="k"), \
                mock.patch("shutil.which", return_value="/bin/ffmpeg"), \
                mock.patch.object(elevenlabs, "voices", return_value=[]):
            self.assertEqual(elevenlabs.check_ready(_config()), [])

    def test_an_unreachable_api_is_reported_rather_than_thrown(self):
        """doctor prints a list of problems; it does not catch exceptions."""
        with mock.patch.object(elevenlabs, "api_key", return_value="k"), \
                mock.patch("shutil.which", return_value="/bin/ffmpeg"), \
                mock.patch.object(elevenlabs, "voices",
                                  side_effect=elevenlabs.Unavailable("no route to host")):
            problems = elevenlabs.check_ready(_config())
        self.assertEqual(len(problems), 1)
        self.assertIn("no route to host", problems[0])


class VoiceListingTests(unittest.TestCase):
    """Voice ids are opaque per-account strings, so listing them is the only
    way to get one. A guessed id is somebody else's voice."""

    PAYLOAD = {
        "voices": [
            {
                "voice_id": "21m00Tcm4TlvDq8ikWAM",
                "name": "Rachel",
                "category": "premade",
                "labels": {"accent": "american", "description": "calm",
                           "age": "young", "gender": "female"},
            },
            {
                "voice_id": "onwK4e9ZLuTAKqWW03F9",
                "name": "Daniel",
                "labels": {"accent": "british", "gender": "male"},
            },
        ]
    }

    def setUp(self):
        elevenlabs._key_for_slot.cache_clear()

    def _urlopen(self, payload):
        response = mock.MagicMock()
        response.read.return_value = json.dumps(payload).encode()
        response.__enter__.return_value = response
        return mock.patch("urllib.request.urlopen", return_value=response)

    def test_a_realistic_payload_becomes_name_id_and_labels(self):
        with mock.patch.object(elevenlabs, "api_key", return_value="k"), \
                self._urlopen(self.PAYLOAD):
            listed = elevenlabs.voices(_config())
        self.assertEqual(len(listed), 2)
        self.assertEqual(listed[0], {
            "name": "Rachel", "voice_id": "21m00Tcm4TlvDq8ikWAM",
            "accent": "american", "gender": "female", "description": "calm"})
        # A voice with no description is still listable; half the account's
        # voices have partial labels and dropping them would hide them.
        self.assertEqual(listed[1]["name"], "Daniel")
        self.assertEqual(listed[1]["description"], "")

    def test_the_key_travels_in_the_xi_api_key_header(self):
        """Not Authorization: Bearer. ElevenLabs answers 401 to that."""
        seen = {}

        def _capture(request, timeout=None):
            seen["headers"] = dict(request.headers)
            response = mock.MagicMock()
            response.read.return_value = b'{"voices": []}'
            response.__enter__.return_value = response
            return response

        with mock.patch.object(elevenlabs, "api_key", return_value="secret-key"), \
                mock.patch("urllib.request.urlopen", side_effect=_capture):
            elevenlabs.voices(_config())
        # urllib title-cases header names on the way in.
        self.assertEqual(seen["headers"].get("Xi-api-key"), "secret-key")

    def test_an_http_error_is_raised_as_unavailable(self):
        error = urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b""))
        with mock.patch.object(elevenlabs, "api_key", return_value="k"), \
                mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(elevenlabs.Unavailable):
                elevenlabs.voices(_config())


class SynthTests(unittest.TestCase):
    def setUp(self):
        elevenlabs._key_for_slot.cache_clear()

    def test_mp3_is_requested_and_decoded_locally(self):
        """Raw PCM at 44.1k is a Pro-tier output format.

        Asking for it on a normal account returns an error, not audio, so the
        request must stay mp3 and the decode must stay local.
        """
        seen = {}

        def _capture(request, timeout=None):
            seen["url"] = request.full_url
            seen["body"] = json.loads(request.data)
            response = mock.MagicMock()
            response.read.return_value = b"ID3fake-mp3"
            response.__enter__.return_value = response
            return response

        with mock.patch.object(elevenlabs, "api_key", return_value="k"), \
                mock.patch("shutil.which", return_value="/bin/ffmpeg"), \
                mock.patch("urllib.request.urlopen", side_effect=_capture), \
                mock.patch("subprocess.run",
                           return_value=_Done(0, stdout=b"\x00\x01" * 100)) as run:
            pcm, rate = elevenlabs.synth("hello", _config())

        self.assertIn("mp3_44100_128", seen["url"])
        self.assertIn("abc123voice", seen["url"])
        self.assertEqual(seen["body"]["model_id"], "eleven_turbo_v2_5")
        # style is left out entirely: above 0 it makes delivery slow and dull.
        self.assertNotIn("style", seen["body"]["voice_settings"])
        self.assertEqual(rate, elevenlabs.RATE)
        self.assertEqual(pcm, b"\x00\x01" * 100)
        self.assertEqual(run.call_args.kwargs["input"], b"ID3fake-mp3")

    def test_the_mastering_chain_reaches_ffmpeg(self):
        """Their website previews are mastered demo clips.

        Raw API output is quieter and flatter than what you auditioned, so
        skipping loudnorm means the voice you picked is not the voice you get.
        """
        response = mock.MagicMock()
        response.read.return_value = b"mp3"
        response.__enter__.return_value = response
        with mock.patch.object(elevenlabs, "api_key", return_value="k"), \
                mock.patch("shutil.which", return_value="/bin/ffmpeg"), \
                mock.patch("urllib.request.urlopen", return_value=response), \
                mock.patch("subprocess.run",
                           return_value=_Done(0, stdout=b"pcm")) as run:
            elevenlabs.synth("hello", _config(elevenlabs_master="volume=2.0"))
        self.assertIn("volume=2.0", run.call_args.args[0])

    def test_ffmpeg_producing_nothing_is_a_failure_not_silent_audio(self):
        """Empty PCM plays as silence, which is indistinguishable from working."""
        response = mock.MagicMock()
        response.read.return_value = b"mp3"
        response.__enter__.return_value = response
        with mock.patch.object(elevenlabs, "api_key", return_value="k"), \
                mock.patch("shutil.which", return_value="/bin/ffmpeg"), \
                mock.patch("urllib.request.urlopen", return_value=response), \
                mock.patch("subprocess.run", return_value=_Done(0, stdout=b"")):
            with self.assertRaises(elevenlabs.Unavailable):
                elevenlabs.synth("hello", _config())

    def test_missing_ffmpeg_is_named_before_the_network_is_touched(self):
        with mock.patch.object(elevenlabs, "api_key", return_value="k"), \
                mock.patch("shutil.which", return_value=None), \
                mock.patch("urllib.request.urlopen") as urlopen:
            with self.assertRaises(elevenlabs.Unavailable) as caught:
                elevenlabs.synth("hello", _config())
        self.assertIn("ffmpeg", str(caught.exception))
        urlopen.assert_not_called()


class FallbackTests(unittest.TestCase):
    """Degrade, never mute.

    This is the whole reason the cloud voice is allowed in at all. Every way
    ElevenLabs can fail -- and it is the one part of the mouth that can --
    must come out of the speakers as piper, not as nothing.
    """

    def setUp(self):
        elevenlabs._key_for_slot.cache_clear()
        feedback.piper_model.cache_clear()

    def tearDown(self):
        feedback.piper_model.cache_clear()

    def _speak(self, synth_error):
        """Say one line with ElevenLabs failing; report what piper was asked."""
        logged: list[str] = []
        mouth = feedback.Feedback(_config())
        mouth.log = logged.append
        with mock.patch.object(elevenlabs, "ready", return_value=True), \
                mock.patch.object(elevenlabs, "synth", side_effect=synth_error), \
                mock.patch.object(feedback.Feedback, "_speak_piper") as piper:
            mouth._speak_now("the browser is closed")
        return piper, logged

    def test_a_cloud_failure_still_speaks_through_piper(self):
        for failure in (elevenlabs.Unavailable("HTTP 401"),
                        OSError("network is unreachable"),
                        RuntimeError("something nobody predicted")):
            with self.subTest(failure=type(failure).__name__):
                piper, _ = self._speak(failure)
                piper.assert_called_once_with("the browser is closed")

    def test_the_fallback_says_in_the_log_which_voice_you_are_hearing(self):
        """Otherwise the only symptom is that she sounds worse today."""
        _, logged = self._speak(elevenlabs.Unavailable("quota exceeded"))
        joined = " ".join(logged)
        self.assertIn("elevenlabs", joined)
        self.assertIn("quota exceeded", joined)
        self.assertIn("piper", joined)

    def test_the_cloud_voice_is_used_when_it_works(self):
        mouth = feedback.Feedback(_config())
        with mock.patch.object(elevenlabs, "ready", return_value=True), \
                mock.patch.object(elevenlabs, "synth",
                                  return_value=(b"pcm-bytes", 44100)), \
                mock.patch.object(feedback.Feedback, "_play") as play, \
                mock.patch.object(feedback.Feedback, "_speak_piper") as piper:
            mouth._speak_now("hello")
        play.assert_called_once_with(b"pcm-bytes", 44100)
        piper.assert_not_called()

    def test_piper_is_used_directly_when_the_cloud_is_not_configured(self):
        """The default install has no key and must not pay a round trip."""
        mouth = feedback.Feedback(_config(elevenlabs_enabled=False))
        with mock.patch.object(elevenlabs, "synth") as synth, \
                mock.patch.object(elevenlabs, "api_key", return_value=""), \
                mock.patch.object(feedback.Feedback, "_speak_piper") as piper:
            mouth._speak_now("hello")
        synth.assert_not_called()
        piper.assert_called_once()

    def test_tts_command_still_wins_over_everything(self):
        """The escape hatch is the escape hatch; a cloud voice does not take it."""
        mouth = feedback.Feedback(_config(tts_command="/usr/bin/my-tts"))
        with mock.patch.object(elevenlabs, "ready", return_value=True), \
                mock.patch.object(elevenlabs, "synth") as synth, \
                mock.patch("subprocess.run") as run, \
                mock.patch.object(feedback.Feedback, "_speak_piper") as piper:
            mouth._speak_now("hello")
        synth.assert_not_called()
        piper.assert_not_called()
        self.assertEqual(run.call_args.args[0],
                         ["/usr/bin/my-tts", "--", "hello"])


if __name__ == "__main__":
    unittest.main()
