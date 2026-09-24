"""Picking a Piper voice and its sample rate.

Both of these used to be absent. `piper --output-raw` was called with no
model, which piper refuses outright, and the playback rate was the constant
22050 -- right for the medium voices and wrong for the rest. A rate that
disagrees with the audio does not fail; it plays back at the wrong pitch and
speed, which is a worse way to be wrong.

Run with: python3 -m unittest discover -s tests
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice import feedback


def _voice(directory: Path, name: str = "test-voice", rate: int | None = 22050) -> Path:
    """A voice on disk: the model, and the config beside it as piper expects."""
    model = directory / f"{name}.onnx"
    model.write_bytes(b"not really an onnx model")
    if rate is not None:
        (directory / f"{name}.onnx.json").write_text(
            json.dumps({"audio": {"sample_rate": rate}}))
    return model


class VoiceSelectionTests(unittest.TestCase):
    def setUp(self):
        # Both helpers are lru_cached, which is right in a daemon and wrong
        # across tests that each set a different environment.
        feedback.piper_model.cache_clear()
        feedback.piper_rate.cache_clear()

    def test_no_environment_means_no_voice(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(feedback.piper_model())

    def test_a_voice_that_is_not_there_is_not_offered(self):
        # An override pointing at a deleted file must fall through to the
        # espeak branch rather than handing piper a path it will reject.
        with mock.patch.dict(os.environ,
                             {"OMARCHY_VOICE_PIPER_MODEL": "/nonexistent/x.onnx"}):
            self.assertIsNone(feedback.piper_model())

    def test_the_environment_picks_the_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = _voice(Path(tmp))
            with mock.patch.dict(os.environ,
                                 {"OMARCHY_VOICE_PIPER_MODEL": str(model)}):
                self.assertEqual(feedback.piper_model(), model)


class SampleRateTests(unittest.TestCase):
    def setUp(self):
        feedback.piper_rate.cache_clear()

    def test_the_rate_comes_from_the_voice_not_a_constant(self):
        for rate in (16000, 22050, 48000):
            with self.subTest(rate=rate):
                feedback.piper_rate.cache_clear()
                with tempfile.TemporaryDirectory() as tmp:
                    model = _voice(Path(tmp), rate=rate)
                    self.assertEqual(feedback.piper_rate(model), rate)

    def test_an_unreadable_config_falls_back_rather_than_raising(self):
        # Speaking at the wrong rate beats a traceback out of a daemon thread.
        with tempfile.TemporaryDirectory() as tmp:
            model = _voice(Path(tmp), rate=None)
            self.assertEqual(feedback.piper_rate(model), 22050)



class SpeakingIntoAnOpenMicTests(unittest.TestCase):
    """The local voice must never be audible while the microphone is live.

    The realtime audio is held while she replies; piper's is not, so it was
    the one voice that could talk into an open mic. It did: an error was
    spoken, the mic heard it, the server read it as a new user turn and
    cancelled her reply, which produced another error to speak.
    """

    def _feedback(self, speak=True):
        from omarchy_voice.config import Config
        return feedback.Feedback(Config(speak=speak, notify=False))

    def test_a_closed_mic_speaks(self):
        fb = self._feedback()
        fb.mic_open = False
        with mock.patch.object(feedback.threading, "Thread") as thread:
            fb.speak("workspace three")
        thread.assert_called_once()

    def test_an_open_mic_holds_it_back(self):
        fb = self._feedback()
        fb.mic_open = True
        with mock.patch.object(feedback.threading, "Thread") as thread:
            fb.speak("that did not go through")
        thread.assert_not_called()

    def test_nothing_speaks_when_speak_is_off(self):
        fb = self._feedback(speak=False)
        fb.mic_open = False
        with mock.patch.object(feedback.threading, "Thread") as thread:
            fb.speak("workspace three")
        thread.assert_not_called()

    def test_the_default_is_a_closed_mic(self):
        # Everything without a microphone -- CLI, planner, tests -- must still
        # be able to speak.
        self.assertFalse(self._feedback().mic_open)


if __name__ == "__main__":
    unittest.main()
