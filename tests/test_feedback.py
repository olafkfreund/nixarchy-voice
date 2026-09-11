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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

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


if __name__ == "__main__":
    unittest.main()
