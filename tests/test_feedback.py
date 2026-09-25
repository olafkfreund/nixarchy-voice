"""Picking a Piper voice and its sample rate.

Both of these used to be absent. `piper --output-raw` was called with no
model, which piper refuses outright, and the playback rate was the constant
22050 -- right for the medium voices and wrong for the rest. A rate that
disagrees with the audio does not fail; it plays back at the wrong pitch and
speed, which is a worse way to be wrong.

Run with: python3 -m unittest discover -s tests
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
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


# -- the resident Piper worker (#136) -----------------------------------------

# A stand-in for the piper-tts package: the worker imports `piper` and nothing
# else, so this is all of piper it can see. `import trace` is the -P check: run
# without -P, the worker's own directory comes first on sys.path and our
# trace.py (which has SYNTH) would shadow the standard library's.
FAKE_PIPER = '''
import time
import trace

if hasattr(trace, "SYNTH"):
    raise ImportError("omarchy_voice's trace.py shadowed the stdlib one")


class AudioChunk:
    def __init__(self, data):
        self.audio_int16_bytes = data


class PiperVoice:
    @classmethod
    def load(cls, path):
        if "broken" in str(path):
            raise RuntimeError("no such voice")
        return cls()

    def synthesize(self, text):
        if text == "hang":
            time.sleep(60)
        if text.startswith("two:"):
            yield AudioChunk(b"first")
            yield AudioChunk(b"second")
            return
        yield AudioChunk(text.encode())
'''


class _Sink(io.BytesIO):
    """A pipe's write end that keeps what was written after it is closed."""

    def close(self):
        self.written = self.getvalue()
        super().close()


class _Recorder:
    """A fake pw-cat or per-sentence piper: records its argv and its input."""

    def __init__(self, argv, **_kwargs):
        self.argv = argv
        self.stdin = _Sink()
        self.stdout = io.BytesIO()

    def wait(self, timeout=None):
        return 0

    @property
    def fed(self) -> bytes:
        return self.stdin.written if self.stdin.closed else self.stdin.getvalue()


class _PiperCase(unittest.TestCase):
    """A voice on disk, the worker's interpreter set, and a private log."""

    def setUp(self):
        feedback.piper_model.cache_clear()
        feedback.piper_rate.cache_clear()
        self.addCleanup(feedback.piper_model.cache_clear)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.model = _voice(self.dir)
        (self.dir / "piper").mkdir()
        (self.dir / "piper" / "__init__.py").write_text(FAKE_PIPER)
        self.env = {"OMARCHY_VOICE_PIPER_MODEL": str(self.model),
                    "OMARCHY_VOICE_PIPER_PYTHON": sys.executable,
                    "PYTHONPATH": str(self.dir)}
        env = mock.patch.dict(os.environ, self.env)
        env.start()
        self.addCleanup(env.stop)
        self.log = self.dir / "session.log"
        for patcher in (mock.patch.object(feedback, "LOG_FILE", self.log),
                        mock.patch.object(feedback.shutil, "which",
                                          lambda name: f"/bin/{name}")):
            patcher.start()
            self.addCleanup(patcher.stop)
        # Every process these tests start; each must be gone at the end.
        self.procs, self.recorders = [], []
        real = subprocess.Popen

        def popen(argv, **kwargs):
            if argv[0] == os.environ.get("OMARCHY_VOICE_PIPER_PYTHON"):
                proc = real(argv, **kwargs)
                self.procs.append(proc)
                return proc
            recorder = _Recorder(argv, **kwargs)
            self.recorders.append(recorder)
            return recorder

        popen_patch = mock.patch.object(feedback.subprocess, "Popen", side_effect=popen)
        self.popen = popen_patch.start()
        self.addCleanup(popen_patch.stop)
        self.addCleanup(self._all_exited)

    def _all_exited(self):
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
                self.fail("a piper worker was left running")

    def feedback(self):
        from omarchy_voice.config import Config
        return feedback.Feedback(Config(speak=True, notify=False))

    def ran(self, name):
        return [r for r in self.recorders if r.argv[0] == name]

    def lines(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def worker(self, model=None):
        worker = feedback.PiperWorker.start(model or self.model)
        self.assertIsNotNone(worker, "the worker did not start")
        self.addCleanup(worker.stop)
        return worker


def _frames(worker, ends: int) -> list[bytes]:
    """Read frames until `ends` end markers; the markers read as b"".

    Through the worker's own reads, which have a deadline, so a bad frame is a
    failure rather than a read that waits for bytes that never come.
    """
    out = []
    with mock.patch.object(feedback, "PIPER_READ_TIMEOUT", 2.0):
        while ends:
            size = worker._length()
            out.append(worker._read(size) if size else b"")
            ends -= not size
    return out


class PiperWorkerProtocolTests(_PiperCase):
    """The real worker, on this interpreter, with the fake piper above."""

    def test_worker_frames_each_sentence(self):
        worker = self.worker()  # has read the ready marker
        worker.proc.stdin.write(b"hello there\ntwo: chunks\n")
        self.assertEqual(_frames(worker, 2),
                         [b"hello there", b"", b"first", b"second", b""])

    def test_worker_exits_on_eof(self):
        worker = self.worker()
        worker.proc.stdin.close()
        self.assertEqual(worker.proc.wait(timeout=2), 0)

    def test_hung_worker_times_out(self):
        fb = self.feedback()
        with mock.patch.object(feedback, "PIPER_READ_TIMEOUT", 0.2):
            started = time.monotonic()
            fb._speak_piper("hang")
        self.assertLess(time.monotonic() - started, 5)
        [piper] = self.ran("piper")
        self.assertEqual(piper.fed, b"hang")
        self.assertTrue(fb._piper_failed)
        self.assertIsNone(fb.piper)
        [proc] = self.procs
        self.assertIsNotNone(proc.poll())

    def test_a_newline_does_not_split_a_sentence(self):
        worker = self.worker()
        worker.speak("one\ntwo", 22050)
        worker.speak("three", 22050)
        first, second = self.ran("pw-cat")
        self.assertEqual(first.fed, b"one two")
        self.assertEqual(second.fed, b"three")
        self.assertIn("22050", first.argv)


class _FakeWorker:
    """`PiperWorker`'s shape. `how` says what the next speak does."""

    def __init__(self, how="ok"):
        self.how = how
        self.spoken = []
        self.played = False
        self.inside = False
        self.stopped = 0

    def speak(self, text, rate):
        if self.inside:
            raise AssertionError("two sentences shared the pipe")
        self.inside = True
        try:
            self.played = False
            if self.how == "eof":
                raise EOFError
            if self.how == "timeout":
                raise TimeoutError
            if self.how == "partial":
                self.played = True
                raise EOFError
            time.sleep(0.05)
            self.spoken.append(text)
        finally:
            self.inside = False

    def stop(self):
        self.stopped += 1


class ResidentPiperTests(_PiperCase):
    def start_with(self, worker):
        start = mock.patch.object(feedback.PiperWorker, "start", return_value=worker)
        self.addCleanup(start.stop)
        return start.start()

    def warns(self):
        return [line for line in self.lines() if "warn" in line]

    def test_resident_piper_is_used_for_the_second_sentence(self):
        worker = _FakeWorker()
        start = self.start_with(worker)
        fb = self.feedback()
        fb._speak_piper("one")
        fb._speak_piper("two")
        start.assert_called_once()
        self.assertEqual(worker.spoken, ["one", "two"])
        self.assertEqual(self.ran("piper"), [])

    def test_no_python_means_no_worker(self):
        start = self.start_with(_FakeWorker())
        with mock.patch.dict(os.environ, {"OMARCHY_VOICE_PIPER_PYTHON": ""}):
            self.feedback()._speak_piper("hello")
        start.assert_not_called()
        [piper] = self.ran("piper")
        self.assertEqual(piper.fed, b"hello")

    def test_dead_worker_falls_back_before_any_audio(self):
        worker = _FakeWorker("eof")
        start = self.start_with(worker)
        fb = self.feedback()
        fb._speak_piper("first sentence")
        [piper] = self.ran("piper")
        self.assertEqual(piper.fed, b"first sentence")
        self.assertTrue(fb._piper_failed)
        self.assertEqual(worker.stopped, 1)
        self.assertEqual(len(self.warns()), 1)
        self.assertIn("piper resident failed (EOFError)", self.warns()[0])
        fb._speak_piper("second sentence")
        start.assert_called_once()
        self.assertEqual(len(self.ran("piper")), 2)
        self.assertEqual(len(self.warns()), 1)

    def test_partly_heard_sentence_is_not_repeated(self):
        self.start_with(_FakeWorker("partial"))
        fb = self.feedback()
        fb._speak_piper("heard in part")
        self.assertEqual(self.ran("piper"), [])
        self.assertTrue(fb._piper_failed)
        self.assertEqual(len(self.warns()), 1)

    def test_sentence_never_logged(self):
        for how in ("eof", "partial", "timeout"):
            with self.subTest(how=how):
                self.log.unlink(missing_ok=True)
                start = mock.patch.object(feedback.PiperWorker, "start",
                                          return_value=_FakeWorker(how))
                with start:
                    self.feedback()._speak_piper("my secret sentence")
                self.assertEqual(len(self.warns()), 1)
                self.assertNotIn("secret", self.log.read_text())
        self.assertIn("(timeout)", self.log.read_text())

    def test_resident_piper_adds_no_synth_span(self):
        from omarchy_voice import trace
        self.start_with(_FakeWorker())
        fb = self.feedback()
        fb.trace = trace.Trace("turn")
        fb._speak_piper("first words")
        fb._speak_piper("second words")
        self.assertEqual(fb.trace.spans, [])

    def test_two_sentences_never_share_the_pipe(self):
        worker = _FakeWorker()
        self.start_with(worker)
        fb = self.feedback()
        with mock.patch.object(feedback.elevenlabs, "ready", return_value=False):
            threads = [threading.Thread(target=fb._speak_now, args=(text,))
                       for text in ("left", "right")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
        self.assertEqual(sorted(worker.spoken), ["left", "right"])
        self.assertFalse(fb._piper_failed)

    def test_no_model_means_espeak_and_no_worker(self):
        start = self.start_with(_FakeWorker())
        feedback.piper_model.cache_clear()
        with mock.patch.dict(os.environ, {"OMARCHY_VOICE_PIPER_MODEL": ""}), \
                mock.patch.object(feedback.subprocess, "run") as run:
            self.feedback()._speak_piper("hello")
        start.assert_not_called()
        self.assertEqual(run.call_args.args[0][0], "espeak-ng")

    def test_building_a_feedback_starts_nothing(self):
        from omarchy_voice.config import Config
        start = self.start_with(_FakeWorker())
        with mock.patch.object(feedback.subprocess, "run"):
            fb = feedback.Feedback(Config(speak=True, notify=True))
            fb.state("idle")
            fb.notify("title", "body")
        start.assert_not_called()
        self.assertIsNone(fb.piper)

    def test_worker_that_fails_to_start_is_not_retried(self):
        with mock.patch.object(feedback.PiperWorker, "start", return_value=None) as start:
            fb = self.feedback()
            fb._speak_piper("one")
            fb._speak_piper("two")
        start.assert_called_once()
        self.assertEqual([p.fed for p in self.ran("piper")], [b"one", b"two"])
        self.assertEqual(len(self.warns()), 1)
        # And the real worker, with a voice whose load raises.
        broken = _voice(self.dir, name="broken")
        self.assertIsNone(feedback.PiperWorker.start(broken))
        [proc] = self.procs
        self.assertIsNotNone(proc.poll())


class MakeAheadTests(unittest.TestCase):
    """The seam the engine makes the next line's clip through (#137).

    #135's fakes for urlopen and Popen: no network, and nothing plays.
    """

    def setUp(self):
        from omarchy_voice import elevenlabs
        import test_elevenlabs as fakes

        self.elevenlabs, self.fakes = elevenlabs, fakes
        elevenlabs._key_for_slot.cache_clear()
        self.addCleanup(elevenlabs._key_for_slot.cache_clear)

    def mouth(self, **overrides):
        from omarchy_voice import trace as trace_mod

        mouth = feedback.Feedback(self.fakes._config(**overrides))
        self.logged = []
        mouth.log = self.logged.append
        # A turn is being timed: a make-ahead must still not add to it.
        mouth.trace = trace_mod.Trace()
        return mouth

    def test_only_the_cloud_voice_is_made_ahead(self):
        cases = {"ready": (True, {}, True),
                 "not ready": (False, {}, False),
                 "tts_command set": (True, {"tts_command": "/bin/my-tts"}, False)}
        for name, (ready, overrides, expected) in cases.items():
            with self.subTest(name), \
                    mock.patch.object(self.elevenlabs, "ready", return_value=ready):
                self.assertIs(self.mouth(**overrides).can_make_ahead(), expected)

    def test_a_clip_is_collected_untimed_through_the_one_pipeline(self):
        fakes = self.fakes
        mouth = self.mouth()
        with fakes._harness(fakes._Body([b"mp3-1", b"mp3-2"])) as (spoken, _):
            self.elevenlabs.speak("hello", mouth.config)
        with fakes._harness(fakes._Body([b"mp3-1", b"mp3-2"])) as (made, seen):
            clip = mouth.make_ahead("hello")
        self.assertEqual(clip, (fakes._pcm(b"mp3-1") + fakes._pcm(b"mp3-2"),
                                self.elevenlabs.RATE))
        self.assertEqual(seen["body"]["text"], "hello")
        # #135's argv and mastering, and nothing played.
        self.assertEqual(made.ffmpeg.argv, spoken.ffmpeg.argv)
        self.assertEqual(made.players, [])
        # Its spans would land inside the line playing now (#79).
        self.assertEqual(mouth.trace.spans, [])

    def test_a_clip_made_ahead_plays_and_returns_when_pw_cat_does(self):
        fakes = self.fakes
        released = threading.Event()
        children = fakes._Children(pw_gate=released)
        mouth = self.mouth()
        with mock.patch.object(feedback.Feedback, "_speak_piper") as piper, \
                fakes._harness(fakes._Body([]), children) as (_, seen):
            speaking = threading.Thread(
                target=mouth._speak_now, args=("hello", (b"pcm-1", 44100)),
                daemon=True)
            speaking.start()
            try:
                self.assertTrue(children.wait_entered.wait(fakes.DEADLINE),
                                "pw-cat was never waited on")
                self.assertTrue(speaking.is_alive())
            finally:
                released.set()
                speaking.join(fakes.DEADLINE)
        self.assertFalse(speaking.is_alive())
        [player] = children.players
        self.assertEqual(player.argv, self.elevenlabs.PLAYER)
        self.assertEqual(player.written, [b"pcm-1"])
        self.assertTrue(player.stdin_closed)
        self.assertEqual(children.ffmpegs, [])
        seen["urlopen"].assert_not_called()
        piper.assert_not_called()
        self.assertEqual(mouth.trace.spans, [])

    def test_a_failed_make_ahead_is_piper_saying_it_all(self):
        fakes = self.fakes
        mouth = self.mouth()
        failed = self.elevenlabs.Unavailable("HTTP 401")
        with mock.patch.object(feedback.Feedback, "_speak_piper") as piper, \
                fakes._harness(fakes._Body([])) as (children, seen):
            mouth._speak_now("the browser is closed", failed)
        piper.assert_called_once_with("the browser is closed")
        self.assertIn("tts     elevenlabs failed (HTTP 401) — using piper",
                      self.logged)
        seen["urlopen"].assert_not_called()
        self.assertEqual(children.players, [])

    def test_nothing_made_ahead_is_todays_mouth(self):
        mouth = self.mouth()
        with mock.patch.object(self.elevenlabs, "ready", return_value=True), \
                mock.patch.object(self.elevenlabs, "speak") as speak, \
                mock.patch.object(feedback.Feedback, "_speak_piper") as piper:
            mouth._speak_now("hello")
            mouth._speak_now("again", None)
        self.assertEqual(speak.call_args_list, [
            mock.call("hello", mouth.config, trace=mouth.trace),
            mock.call("again", mouth.config, trace=mouth.trace)])
        piper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
