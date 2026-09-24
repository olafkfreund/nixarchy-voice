"""Hearing without the API.

The typed path already ran against a local model, so `say` worked with the
account empty -- but it needed someone to type, which made "offline" mean
"offline, and also use the keyboard". These cover the half that closes: audio
in, on this machine, for both `ask` and the wake word.
"""

import array
import http.server
import threading
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



SPEECH = b"\1\2" * listen_local.SAMPLE_RATE  # one second, past the fragment guard


class VocabularyTests(unittest.TestCase):
    """What whisper is told to expect, so "Claude" stops coming back "clone" (#72)."""

    def test_it_names_this_desktop(self):
        line = listen_local.vocabulary(Config(wake_word="oma", whisper_vocabulary="Vesktop, herdr"))
        for word in ("oma", "Claude", "Hyprland", "codex", "Vesktop", "herdr"):
            self.assertIn(word, line)

    def test_it_stays_short(self):
        line = listen_local.vocabulary(Config(whisper_vocabulary=", ".join(["word"] * 100)))
        self.assertLessEqual(len(line), 200)

    def test_whisper_cli_is_given_it(self):
        done = mock.Mock(returncode=0, stdout="hello there", stderr="")
        with mock.patch.dict(listen_local.os.environ, {listen_local.MODEL_ENV: "/m.bin"}), \
             mock.patch.object(listen_local.shutil, "which", return_value="/bin/whisper-cli"), \
             mock.patch.object(listen_local.subprocess, "run", return_value=done) as run:
            listen_local.transcribe(SPEECH, Config(wake_word="oma"))
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index("--prompt") + 1],
                         listen_local.vocabulary(Config(wake_word="oma")))


class AfterWakeWordTests(unittest.TestCase):
    """Only a sentence that OPENS with the wake word is an instruction (#72)."""

    def test_what_follows_the_wake_word(self):
        for said, rest in (("Oma, close it.", "close it."),
                           ("Hey Oma, close it", "close it"),
                           ("OMA turn the volume down", "turn the volume down")):
            with self.subTest(said=said):
                self.assertEqual(listen_local.after_wake_word(said, "oma"), rest)

    def test_talking_about_her_is_not_telling_her(self):
        for said in ("I told Oma to close it", "Oma?", "Omaha is nice", "Oma, thank you."):
            with self.subTest(said=said):
                self.assertEqual(listen_local.after_wake_word(said, "oma"), "")


class _Whisper(http.server.BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).seen.append((self.path, body))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b" start a new Claude session\n")

    def log_message(self, *args):
        pass


class ServerTests(unittest.TestCase):
    """A resident whisper, and whisper-cli the moment it is not there (#72)."""

    def setUp(self):
        _Whisper.seen = []
        self.http = http.server.HTTPServer(("127.0.0.1", 0), _Whisper)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        running = mock.Mock()
        running.poll.return_value = None
        self.server = listen_local.Server(running, self.http.server_address[1], "s3cret")
        self.cli = mock.Mock(returncode=0, stdout="from whisper cli", stderr="")
        for patch in (mock.patch.dict(listen_local.os.environ, {listen_local.MODEL_ENV: "/m.bin"}),
                      mock.patch.object(listen_local.shutil, "which", return_value="/bin/x")):
            patch.start()
            self.addCleanup(patch.stop)

    def test_it_asks_behind_the_secret_path_with_the_prompt(self):
        text = listen_local.transcribe(SPEECH, Config(wake_word="oma"), server=self.server)
        self.assertEqual(text, "start a new Claude session")
        [(path, body)] = _Whisper.seen
        self.assertEqual(path, "/s3cret/inference")
        self.assertIn(b'name="prompt"', body)
        self.assertIn(b"oma, Claude", body)
        self.assertIn(b"RIFF", body)  # the audio went as a WAV

    def test_a_dead_server_falls_back_and_stays_fallen_back(self):
        self.http.shutdown()
        self.http.server_close()
        with mock.patch.object(listen_local.subprocess, "run", return_value=self.cli) as run:
            first = listen_local.transcribe(SPEECH, Config(), server=self.server)
            second = listen_local.transcribe(SPEECH, Config(), server=self.server)
        self.assertEqual((first, second), ("from whisper cli", "from whisper cli"))
        self.assertTrue(self.server.failed)
        self.assertFalse(self.server.alive)
        self.assertEqual(run.call_count, 2)

    def test_no_whisper_server_means_no_server(self):
        with mock.patch.object(listen_local.shutil, "which", return_value=None):
            self.assertIsNone(listen_local.Server.start(Config()))


if __name__ == "__main__":
    unittest.main()
