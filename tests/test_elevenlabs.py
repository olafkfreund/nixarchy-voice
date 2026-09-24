"""The cloud voice, and the promise that it can never take the voice away.

ElevenLabs sounds enormously better than piper and is the only part of the
mouth that can fail: no network, expired key, spent quota, missing ffmpeg. The
headline test here is the one that says a failure is heard as a plainer voice
rather than as silence -- an assistant that stops answering because an API had
a bad afternoon is a worse bug than one that sounds like 1998.

Nothing here touches the network: urllib and subprocess are both faked.
RealFfmpegTests run a local ffmpeg on a generated sine, and nothing else.

Run with: python3 -m unittest discover -s tests
"""

import contextlib
import io
import json
import os
import queue
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice import elevenlabs, feedback
from omarchy_voice import trace as trace_mod
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


# -- the streaming harness (#135) -------------------------------------------
# urlopen, ffmpeg and pw-cat, faked. subprocess.run raises, so nothing here can
# start a real process, and every wait is on an event with a deadline.

DEADLINE = 5.0


class _Body:
    """An urlopen response: scripted chunks, an optional gate, a failure.

    `gate` blocks the read that would serve chunk number `gate_at`, until set.
    `fail_at` raises `error` from the read that would serve that chunk.
    """

    def __init__(self, chunks, gate=None, gate_at=None, fail_at=None,
                 error=None):
        self.chunks = list(chunks)
        self.gate, self.gate_at = gate, gate_at
        self.fail_at = fail_at
        self.error = error or OSError("connection reset")
        self.served = 0
        self.closed = False

    def read(self, n=-1):
        if self.gate is not None and self.served == self.gate_at:
            self.gate.wait(DEADLINE)
        if self.fail_at is not None and self.served >= self.fail_at:
            raise self.error
        if self.served >= len(self.chunks):
            return b""
        chunk = self.chunks[self.served]
        self.served += 1
        return chunk

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def _pcm(mp3: bytes) -> bytes:
    """What the fake ffmpeg makes of one mp3 chunk."""
    return b"pcm:" + mp3


class _FfmpegOut:
    def __init__(self, owner):
        self.owner = owner

    def read1(self, n=-1):
        return self.owner._next()

    def close(self):
        pass


class _FakeFfmpeg:
    """One PCM chunk per mp3 chunk written, unless scripted silent."""

    def __init__(self, argv, silent=False, returncode=0, on_first_pcm=None,
                 on_later_pcm=None):
        self.argv = list(argv)
        self.silent = silent
        self.exit_code = returncode
        self.on_first_pcm = on_first_pcm
        self.on_later_pcm = on_later_pcm
        self.returncode = None
        self.events: list[str] = []   # "close" and "kill", in order
        self.waited = False
        self._out: queue.Queue = queue.Queue()
        self._ended = False
        self._given = False
        self.stdin = self
        self.stdout = _FfmpegOut(self)

    @property
    def killed(self):
        return "kill" in self.events

    @property
    def stdin_closed(self):
        return "close" in self.events

    # stdin
    def write(self, chunk):
        if self.killed:
            raise BrokenPipeError("ffmpeg was killed")
        if not self.silent:
            self._out.put(_pcm(chunk))
        return len(chunk)

    def flush(self):
        pass

    def close(self):
        if not self.stdin_closed:
            self.events.append("close")
            self._out.put(None)

    # stdout
    def _next(self):
        if self._ended:
            return b""
        try:
            chunk = self._out.get(timeout=DEADLINE)
        except queue.Empty:
            raise AssertionError("fake ffmpeg: nothing to read") from None
        if chunk is None:
            self._ended = True
            return b""
        if not self._given:
            self._given = True
            if self.on_first_pcm:
                self.on_first_pcm()
        elif self.on_later_pcm:
            self.on_later_pcm()
        return chunk

    # the process
    def kill(self):
        self.events.append("kill")
        self._out.put(None)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        # Like Popen: once reaped, the exit code is kept, and a later kill()
        # does not change it.
        self.waited = True
        if self.returncode is None:
            self.returncode = -9 if self.killed else self.exit_code
        return self.returncode


class _FakePwCat:
    def __init__(self, argv, gate=None, on_wait=None, wrote=None,
                 wait_entered=None):
        self.argv = list(argv)
        self.written: list[bytes] = []
        self.waited = False
        self.killed = False
        self.stdin_closed = False
        self.returncode = None
        self.gate = gate
        self.on_wait = on_wait
        self.wrote = wrote or threading.Event()
        self.wait_entered = wait_entered or threading.Event()
        self.stdin = self

    def write(self, chunk):
        self.written.append(chunk)
        self.wrote.set()
        return len(chunk)

    def flush(self):
        pass

    def close(self):
        self.stdin_closed = True

    def kill(self):
        self.killed = True

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_entered.set()
        if self.gate is not None:
            self.gate.wait(DEADLINE)
        if self.on_wait and not self.waited:
            self.on_wait()
        self.waited = True
        self.returncode = 0
        return 0


class _Children:
    """The Popen stand-in: picks the fake by argv[0], and keeps them all."""

    def __init__(self, silent=False, returncode=0, on_first_pcm=None,
                 on_later_pcm=None, pw_gate=None, on_pw_wait=None):
        self.ffmpeg_kw = dict(silent=silent, returncode=returncode,
                              on_first_pcm=on_first_pcm,
                              on_later_pcm=on_later_pcm)
        self.pw_kw = dict(gate=pw_gate, on_wait=on_pw_wait)
        self.ffmpegs: list[_FakeFfmpeg] = []
        self.players: list[_FakePwCat] = []
        self.wrote = threading.Event()
        self.wait_entered = threading.Event()

    def __call__(self, argv, **_kw):
        if argv[0] == "ffmpeg":
            child = _FakeFfmpeg(argv, **self.ffmpeg_kw)
            self.ffmpegs.append(child)
        elif argv[0] == "pw-cat":
            child = _FakePwCat(argv, wrote=self.wrote,
                               wait_entered=self.wait_entered, **self.pw_kw)
            self.players.append(child)
        else:
            raise AssertionError(f"unexpected child: {argv!r}")
        return child

    @property
    def ffmpeg(self):
        return self.ffmpegs[0]


@contextlib.contextmanager
def _harness(body, children=None, missing=(), on_open=None):
    """Everything `speak` and `synth` touch, faked. Yields (children, seen)."""
    children = children or _Children()
    seen: dict = {}

    def _urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data)
        if on_open:
            on_open()
        return body

    def _which(name):
        return None if name in missing else f"/run/{name}"

    def _no_run(*args, **kwargs):
        raise AssertionError(f"subprocess.run called: {args!r}")

    with mock.patch.object(elevenlabs, "api_key", return_value="k"), \
            mock.patch("shutil.which", side_effect=_which), \
            mock.patch("urllib.request.urlopen", side_effect=_urlopen) as opened, \
            mock.patch("subprocess.run", side_effect=_no_run), \
            mock.patch("subprocess.Popen", side_effect=children):
        seen["urlopen"] = opened
        yield children, seen


def _pairs(argv):
    return set(zip(argv, argv[1:]))


def _mouth(config=None, trace=None):
    mouth = feedback.Feedback(config or _config())
    logged: list[str] = []
    mouth.log = logged.append
    mouth.trace = trace
    return mouth, logged


class StreamTests(unittest.TestCase):
    """Her cloud voice starts before the whole clip has arrived (#135)."""

    def setUp(self):
        elevenlabs._key_for_slot.cache_clear()
        feedback.piper_model.cache_clear()

    def tearDown(self):
        feedback.piper_model.cache_clear()

    def _speak_now(self, body, children=None, trace=None):
        """Say one line through the mouth; report piper, the log, the fakes."""
        mouth, logged = _mouth(trace=trace)
        with mock.patch.object(elevenlabs, "ready", return_value=True), \
                mock.patch.object(feedback.Feedback, "_speak_piper") as piper, \
                _harness(body, children) as (kids, _seen):
            mouth._speak_now("the browser is closed")
        return piper, " ".join(logged), kids

    def test_pw_cat_starts_before_the_body_ends(self):
        body_may_end = threading.Event()
        body = _Body([b"mp3-1"], gate=body_may_end, gate_at=1)
        task = trace_mod.Trace()
        with _harness(body) as (children, _seen):
            speaking = threading.Thread(
                target=elevenlabs.speak, args=("hello", _config()),
                kwargs={"trace": task}, daemon=True)
            speaking.start()
            try:
                self.assertTrue(children.wrote.wait(DEADLINE),
                                "pw-cat was given nothing while the body was open")
                self.assertFalse(body_may_end.is_set())
                self.assertEqual(children.players[0].written, [_pcm(b"mp3-1")])
                # The wait before her first sample is over, so is its span.
                buffering = [s for s in task.spans if s.name == "buffer"]
                self.assertEqual(len(buffering), 1)
                self.assertIsNotNone(buffering[0].ended)
            finally:
                body_may_end.set()
                speaking.join(DEADLINE)
        self.assertFalse(speaking.is_alive())

    def test_mp3_is_asked_for_and_ffmpeg_is_told_not_to_probe(self):
        """Raw PCM at 44.1k is a Pro-tier output format; mp3 works on every
        tier. Probing a pipe holds output until EOF, so it is switched off."""
        with _harness(_Body([b"mp3-1"])) as (children, seen):
            elevenlabs.speak("hello", _config(elevenlabs_master="volume=2.0"))
        self.assertIn("output_format=mp3_44100_128", seen["url"])
        self.assertIn("abc123voice", seen["url"])
        self.assertEqual(seen["body"]["model_id"], "eleven_turbo_v2_5")
        # style is left out entirely: above 0 it makes delivery slow and dull.
        self.assertNotIn("style", seen["body"]["voice_settings"])
        argv = children.ffmpeg.argv
        pairs = _pairs(argv)
        for pair in (("-f", "mp3"), ("-probesize", "32"),
                     ("-analyzeduration", "0"), ("-fflags", "+nobuffer"),
                     ("-af", "volume=2.0")):
            self.assertIn(pair, pairs)
        self.assertLess(argv.index("mp3"), argv.index("-i"))

    def test_a_failure_before_the_first_sample_is_piper_saying_it_all(self):
        piper, log, children = self._speak_now(_Body([b"mp3-1"], fail_at=0))
        piper.assert_called_once_with("the browser is closed")
        self.assertEqual(children.players, [])
        self.assertIn(("-probesize", "32"), _pairs(children.ffmpeg.argv))
        self.assertTrue(children.ffmpeg.killed)
        self.assertIn("elevenlabs failed", log)
        self.assertIn("using piper", log)

    def test_a_failure_after_the_first_sample_is_a_log_line_not_a_repeat(self):
        piper, log, children = self._speak_now(
            _Body([b"mp3-1", b"mp3-2"], fail_at=2))
        piper.assert_not_called()
        player = children.players[0]
        self.assertEqual(player.written, [_pcm(b"mp3-1"), _pcm(b"mp3-2")])
        self.assertTrue(player.waited)
        self.assertIn("cut off mid-sentence", log)
        self.assertNotIn("using piper", log)

    def test_the_mouth_returns_only_when_pw_cat_does(self):
        """#114's mic gate opens when `_speak_now` returns: after the last
        sample, not after the last byte was handed over."""
        released = threading.Event()
        children = _Children(pw_gate=released)
        mouth, _ = _mouth()
        with mock.patch.object(elevenlabs, "ready", return_value=True), \
                mock.patch.object(feedback.Feedback, "_speak_piper") as piper, \
                _harness(_Body([b"mp3-1"]), children):
            speaking = threading.Thread(target=mouth._speak_now,
                                        args=("hello",), daemon=True)
            speaking.start()
            try:
                self.assertTrue(children.wait_entered.wait(DEADLINE),
                                "pw-cat was never waited on")
                self.assertTrue(speaking.is_alive())
            finally:
                released.set()
                speaking.join(2.0)
        self.assertFalse(speaking.is_alive())
        piper.assert_not_called()

    def test_every_child_is_reaped_on_every_path(self):
        paths = {
            "success": (_Body([b"mp3-1"]), _Children(), False),
            "failure before the first sample":
                (_Body([b"mp3-1"], fail_at=0), _Children(), True),
            "failure after it":
                (_Body([b"mp3-1", b"mp3-2"], fail_at=2), _Children(), True),
            "ffmpeg exiting 1":
                (_Body([b"mp3-1"]), _Children(returncode=1), True),
        }
        for path, (body, children, failed) in paths.items():
            with self.subTest(path=path):
                self._speak_now(body, children)
                started = children.ffmpegs + children.players
                self.assertTrue(started)
                for child in started:
                    self.assertTrue(child.waited, f"{child.argv[0]} not reaped")
                if failed:
                    self.assertTrue(children.ffmpeg.killed)

    def test_a_partial_body_with_no_output_yet_is_not_played(self):
        """Nothing heard is decided; a flushed half clip must not follow it."""
        piper, _, children = self._speak_now(
            _Body([b"mp3-1"], fail_at=1), _Children(silent=True))
        events = children.ffmpeg.events
        self.assertIn("kill", events)
        self.assertNotIn("close", events[:events.index("kill")])
        self.assertEqual(children.players, [])
        piper.assert_called_once_with("the browser is closed")

    def test_missing_ffmpeg_or_pw_cat_is_named_before_the_network_is_touched(self):
        for binary in ("ffmpeg", "pw-cat"):
            with self.subTest(binary=binary):
                with _harness(_Body([b"mp3-1"]), missing=(binary,)) as (_, seen):
                    with self.assertRaises(elevenlabs.Unavailable) as caught:
                        elevenlabs.speak("hello", _config())
                self.assertIn(binary, str(caught.exception))
                seen["urlopen"].assert_not_called()

    def test_collect_mode_returns_the_pcm_speak_would_play(self):
        """#137's make-ahead: the same pipeline, collected instead of played."""
        with _harness(_Body([b"mp3-1", b"mp3-2"])) as (played, _):
            elevenlabs.speak("hello", _config())
        with _harness(_Body([b"mp3-1", b"mp3-2"])) as (collected, _):
            pcm, rate = elevenlabs.synth("hello", _config())
        self.assertEqual(pcm, b"".join(played.players[0].written))
        self.assertEqual(rate, 44100)
        self.assertEqual(collected.ffmpeg.argv, played.ffmpeg.argv)
        self.assertEqual(collected.players, [])
        # Nothing has played, so nothing is cut: the caller may say it all.
        with _harness(_Body([b"mp3-1"], fail_at=1)):
            with self.assertRaises(elevenlabs.Unavailable) as caught:
                elevenlabs.synth("hello", _config())
        self.assertNotIsInstance(caught.exception, elevenlabs.Cut)


def _makes_mp3() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    try:
        listed = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                                capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return b"libmp3lame" in listed.stdout


@unittest.skipUnless(_makes_mp3(), "needs ffmpeg with libmp3lame")
class RealFfmpegTests(unittest.TestCase):
    """The real ffmpeg, on a generated sine in a temp dir. No network, no
    audio device: output goes to a pipe."""

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sine.mp3"
            subprocess.run(
                ["ffmpeg", "-loglevel", "quiet", "-f", "lavfi",
                 "-i", "sine=frequency=440:duration=4", "-ac", "1",
                 "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "128k",
                 str(path)], check=True, timeout=60)
            cls.mp3 = path.read_bytes()

    def test_the_default_master_streams(self):
        """PCM while the input is still open: nothing waits for the end."""
        argv = elevenlabs._ffmpeg_argv(Config().elevenlabs_master)
        child = subprocess.Popen(argv, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL)
        try:
            child.stdin.write(self.mp3[:len(self.mp3) // 4])
            child.stdin.flush()
            ready, _, _ = select.select([child.stdout], [], [], 1.0)
            self.assertTrue(ready, "no PCM within 1 s of a quarter of the clip")
            self.assertTrue(os.read(child.stdout.fileno(), 4096))
            child.stdin.close()
            child.stdout.read()
            self.assertEqual(child.wait(30), 0)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()
            child.stdout.close()

    def test_loudnorm_in_master_still_works(self):
        """The rollback is a config line, so it has to keep working."""
        lengths = []
        for master in ("loudnorm=I=-16:TP=-1.5:LRA=11",
                       Config().elevenlabs_master):
            done = subprocess.run(elevenlabs._ffmpeg_argv(master),
                                  input=self.mp3, capture_output=True,
                                  timeout=60)
            self.assertEqual(done.returncode, 0, master)
            self.assertTrue(done.stdout, master)
            lengths.append(len(done.stdout))
        # 0.01 s of 44.1 kHz int16 mono.
        self.assertLessEqual(abs(lengths[0] - lengths[1]), 882)


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

    def _speak(self, speak_error):
        """Say one line with ElevenLabs failing; report what piper was asked."""
        logged: list[str] = []
        mouth = feedback.Feedback(_config())
        mouth.log = logged.append
        with mock.patch.object(elevenlabs, "ready", return_value=True), \
                mock.patch.object(elevenlabs, "speak", side_effect=speak_error), \
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

    def test_a_failed_cloud_voice_leaves_no_open_span(self):
        """A download that dies is still a closed span, and piper speaks (#79)."""
        mouth, _ = _mouth(trace=trace_mod.Trace())
        task = mouth.trace
        with mock.patch.object(elevenlabs, "ready", return_value=True), \
                mock.patch.object(feedback.Feedback, "_speak_piper") as piper, \
                _harness(_Body([b"mp3-1"], fail_at=0)):
            mouth._speak_now("the browser is closed")

        piper.assert_called_once_with("the browser is closed")
        names = [s.name for s in task.spans if s.phase == "synth"]
        self.assertEqual(names, ["request", "buffer"])
        self.assertTrue(all(s.ended is not None for s in task.spans))

    def test_no_span_is_left_open_on_a_cut(self):
        """Part of the sentence played, then the stream broke (#135)."""
        mouth, logged = _mouth(trace=trace_mod.Trace())
        task = mouth.trace
        with mock.patch.object(elevenlabs, "ready", return_value=True), \
                mock.patch.object(feedback.Feedback, "_speak_piper") as piper, \
                _harness(_Body([b"mp3-1", b"mp3-2"], fail_at=2)):
            mouth._speak_now("the browser is closed")

        piper.assert_not_called()
        self.assertIn("cut off mid-sentence", " ".join(logged))
        names = [s.name for s in task.spans if s.phase == "synth"]
        self.assertEqual(names, ["request", "buffer"])
        self.assertTrue(all(s.ended is not None for s in task.spans))

    def test_the_cloud_voice_is_used_when_it_works(self):
        mouth = feedback.Feedback(_config())
        with mock.patch.object(elevenlabs, "ready", return_value=True), \
                mock.patch.object(elevenlabs, "speak") as speak, \
                mock.patch.object(feedback.Feedback, "_speak_piper") as piper:
            mouth._speak_now("hello")
        speak.assert_called_once()
        self.assertEqual(speak.call_args.args[0], "hello")
        piper.assert_not_called()

    def test_piper_is_used_directly_when_the_cloud_is_not_configured(self):
        """The default install has no key and must not pay a round trip."""
        mouth = feedback.Feedback(_config(elevenlabs_enabled=False))
        with mock.patch.object(elevenlabs, "speak") as speak, \
                mock.patch.object(elevenlabs, "api_key", return_value=""), \
                mock.patch.object(feedback.Feedback, "_speak_piper") as piper:
            mouth._speak_now("hello")
        speak.assert_not_called()
        piper.assert_called_once()

    def test_tts_command_still_wins_over_everything(self):
        """The escape hatch is the escape hatch; a cloud voice does not take it."""
        mouth = feedback.Feedback(_config(tts_command="/usr/bin/my-tts"))
        with mock.patch.object(elevenlabs, "ready", return_value=True), \
                mock.patch.object(elevenlabs, "speak") as speak, \
                mock.patch("subprocess.run") as run, \
                mock.patch.object(feedback.Feedback, "_speak_piper") as piper:
            mouth._speak_now("hello")
        speak.assert_not_called()
        piper.assert_not_called()
        self.assertEqual(run.call_args.args[0],
                         ["/usr/bin/my-tts", "--", "hello"])


if __name__ == "__main__":
    unittest.main()
