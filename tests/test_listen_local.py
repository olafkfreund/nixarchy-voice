"""Hearing without the API.

The typed path already ran against a local model, so `say` worked with the
account empty -- but it needed someone to type, which made "offline" mean
"offline, and also use the keyboard". These cover the half that closes: audio
in, on this machine, for both `ask` and the wake word.
"""

import array
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice import listen_local
from omarchy_voice.config import Config


class CleanTests(unittest.TestCase):
    """whisper does not return an empty string for an empty clip.

    It returns its best guess at what a human would have said, confidently,
    and every one of these was passed straight to the planner as an
    instruction someone had given.
    """

    def test_real_speech_survives(self):
        self.assertEqual(listen_local.clean("  Close the browser. "),
                         "Close the browser.")

    def test_its_own_annotations_are_stripped(self):
        self.assertEqual(
            listen_local.clean("(upbeat music) switch to workspace two"),
            "switch to workspace two")

    def test_the_classic_hallucinations_are_not_instructions(self):
        for raw in ("[BLANK_AUDIO]", "[Music]", "Thank you.", "thanks for watching",
                    "you", "", "   ", "(silence)", "..."):
            with self.subTest(raw=raw):
                self.assertEqual(listen_local.clean(raw), "")

    def test_an_unclosed_bracket_is_not_an_instruction(self):
        self.assertEqual(listen_local.clean("[never closed"), "")


class WakeWordTests(unittest.TestCase):
    def test_punctuation_and_case_do_not_stop_it(self):
        """whisper adds both; neither is something the user did."""
        for text in ("Oma, close the browser", "OMA!", "oma.", "Hey oma, hello"):
            with self.subTest(text=text):
                self.assertTrue(listen_local.heard_wake_word(text, "oma"))

    def test_a_word_that_merely_contains_it_does_not_fire(self):
        """Substring matching wakes on 'aroma' and on anyone called Omar."""
        for text in ("the aroma of coffee", "Omar called", "Oklahoma"):
            with self.subTest(text=text):
                self.assertFalse(listen_local.heard_wake_word(text, "oma"))

    def test_any_configured_spelling_counts(self):
        """A misheard name is tuned by adding what whisper returns, not arguing."""
        self.assertTrue(listen_local.heard_wake_word("Ohma, hello", "oma ohma"))

    def test_no_wake_word_never_fires(self):
        self.assertFalse(listen_local.heard_wake_word("oma", ""))


class TranscribeGuardTests(unittest.TestCase):
    def test_a_fragment_is_not_sent_to_whisper(self):
        """A door closing is not an instruction, and whisper would name it one."""
        with mock.patch.object(listen_local.subprocess, "run") as run:
            self.assertEqual(listen_local.transcribe(b"\0" * 100), "")
            run.assert_not_called()

    def test_a_missing_model_says_so_rather_than_failing_at_the_microphone(self):
        long_enough = b"\0" * int(listen_local.SAMPLE_RATE * 2)
        with mock.patch.dict(listen_local.os.environ, {listen_local.MODEL_ENV: ""}):
            with self.assertRaises(listen_local.Unavailable):
                listen_local.transcribe(long_enough, Config())

    def test_check_ready_names_every_missing_piece(self):
        with mock.patch.object(listen_local.shutil, "which", return_value=None), \
             mock.patch.dict(listen_local.os.environ, {listen_local.MODEL_ENV: ""}):
            problems = " ".join(listen_local.check_ready(Config()))
        self.assertIn("whisper-cli", problems)
        self.assertIn("pw-record", problems)
        self.assertIn(listen_local.MODEL_ENV, problems)


class RecordTests(unittest.TestCase):
    """The recorder decides when a sentence has ended."""

    def _frames(self, *levels):
        """Frames at the given amplitudes, then EOF."""
        return [array.array("h", [a, -a] * (listen_local.FRAME_BYTES // 4)).tobytes()
                for a in levels] + [b""]

    def _record(self, frames, **kw):
        reads = list(frames)

        class FakeStdout:
            def read(self, _n):
                return reads.pop(0) if reads else b""

        proc = mock.Mock()
        proc.stdout = FakeStdout()
        proc.poll.return_value = None
        with mock.patch.object(listen_local.subprocess, "Popen", return_value=proc):
            return listen_local.record_utterance(**kw)

    def test_a_silent_room_records_nothing_at_all(self):
        """Not an empty instruction -- nothing was said, and that is different."""
        self.assertEqual(self._record(self._frames(0, 0, 0)), b"")

    def test_speech_is_captured(self):
        pcm = self._record(self._frames(0, 8000, 8000, 0))
        self.assertGreater(len(pcm), 0)

    def test_the_trailing_quiet_is_kept(self):
        """whisper reads a hard cut at the end of a word as a different word."""
        loud_only = self._record(self._frames(8000))
        with_tail = self._record(self._frames(8000, 0))
        self.assertGreater(len(with_tail), len(loud_only))


if __name__ == "__main__":
    unittest.main()
