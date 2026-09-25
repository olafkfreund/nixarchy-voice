"""The mouth: how the assistant tells you what it heard and did.

Three channels, all optional and all cheap:
  * a notification (Omarchy's shell renders these)
  * a state file, so a bar widget can show a live listening indicator
  * text to speech: ElevenLabs when it is configured, otherwise piper or
    espeak-ng -- and piper again whenever the cloud voice fails, so a bad
    afternoon at an API never costs you the assistant's voice entirely
"""

from __future__ import annotations

import functools
import json
import os
import select
import shlex
import shutil
import struct
import subprocess
import threading
import time
from pathlib import Path

from . import elevenlabs
from .config import Config, LEVEL_FILE, LOG_FILE, STATE_DIR, STATE_FILE, RUNTIME_DIR

# Terminal-only. `omarchy-voice status` prints these; the bar draws its own
# vector mark instead (plugin/olafkfreund.voice-indicator/VoiceMark.qml) because the shared
# glyph space is crowded. A terminal has no such collision problem, so the
# obvious microphone is still the clearest thing to print here.
ICONS = {
    "stopped": "󰍭",
    # Outline, not the filled mic: awake and configured, but not hearing you.
    "idle": "󰍮",
    "listening": "󰍬",
    "thinking": "󱚟",
    "acting": "󱐋",
    "confirm": "󰀦",
    "error": "󰍭",
    "unconfigured": "󰍭",
}


@functools.lru_cache(maxsize=1)
def piper_model() -> Path | None:
    """The Piper voice to speak with, or None if there is not one.

    The package sets OMARCHY_VOICE_PIPER_MODEL. Point it somewhere else to use
    a voice of your own — anything from rhasspy/piper-voices works, as long as
    the .onnx.json sits beside the .onnx.
    """
    raw = os.environ.get("OMARCHY_VOICE_PIPER_MODEL", "")
    if not raw:
        return None
    model = Path(raw)
    return model if model.is_file() else None


@functools.lru_cache(maxsize=4)
def piper_rate(model: Path) -> int:
    """The voice's sample rate, read from its own config.

    Not a constant. The 22050 that used to be hardcoded here is right for the
    medium voices and wrong for others, and a rate that disagrees with the
    audio plays it back at the wrong pitch and speed rather than failing.
    """
    try:
        with open(f"{model}.json") as fh:
            return int(json.load(fh)["audio"]["sample_rate"])
    except (OSError, ValueError, KeyError):
        return 22050


# Per read of the worker's stdout. A healthy worker answers in under 0.2 s; a
# hung one must not hold the mouth, and so the #114 microphone gate, longer.
PIPER_READ_TIMEOUT = 10.0


def piper_python() -> str:
    """The interpreter that can import piper, or "" for no resident worker.

    The package sets OMARCHY_VOICE_PIPER_PYTHON. Unset (the dev shell, a
    non-Nix install, the tests) or empty (the runtime rollback), Piper is
    spoken one process per sentence, as before #136.
    """
    return os.environ.get("OMARCHY_VOICE_PIPER_PYTHON", "")


class PiperWorker:
    """piper_worker.py, held open so the voice is loaded once, not per sentence.

    Modelled on listen_local.Server. Pipes only: no socket, no port, no token
    (#72). The worker exits on EOF on its stdin, and the kernel closes that
    pipe however the daemon dies, so it cannot outlive us.
    """

    def __init__(self, proc):
        self.proc = proc
        self.failed = False
        self.played = False  # whether the last speak got any audio to pw-cat

    @classmethod
    def start(cls, model: Path | None, timeout: float = 10.0):
        """A loaded worker, or None -- the caller then speaks per sentence."""
        python = piper_python()
        if not python or not model or not shutil.which("pw-cat"):
            return None
        try:
            # -P: without it the script's directory, ours, comes first on
            # sys.path and our trace.py, config.py, ... shadow piper's imports.
            proc = subprocess.Popen(
                [python, "-P", str(Path(__file__).with_name("piper_worker.py")),
                 str(model)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0)
        except OSError:
            return None
        worker = cls(proc)
        try:
            if worker._length(timeout) == 0:
                return worker
        except (OSError, EOFError, struct.error):
            pass
        worker.stop()
        return None

    @property
    def alive(self) -> bool:
        return not self.failed and self.proc.poll() is None

    def _read(self, size: int, timeout: float | None = None) -> bytes:
        """Exactly `size` bytes, with a deadline on every read.

        Unbuffered on purpose: select cannot see bytes a BufferedReader holds.
        """
        fd = self.proc.stdout.fileno()
        data = b""
        while len(data) < size:
            wait = PIPER_READ_TIMEOUT if timeout is None else timeout
            if not select.select([fd], [], [], wait)[0]:
                raise TimeoutError
            got = os.read(fd, size - len(data))
            if not got:
                raise EOFError
            data += got
        return data

    def _length(self, timeout: float | None = None) -> int:
        return struct.unpack(">I", self._read(4, timeout))[0]

    def speak(self, text: str, rate: int) -> None:
        """One sentence out of the speakers, chunk by chunk as piper makes it.

        Raises on any failure; `played` then says whether any of it was heard.
        """
        self.played = False
        line = text.replace("\r", " ").replace("\n", " ").encode() + b"\n"
        play = subprocess.Popen(
            # The same pw-cat as the per-sentence path below: its end is how
            # the mouth knows the sentence is over.
            ["pw-cat", "--playback", "--raw", "--format", "s16",
             "--rate", str(rate), "--channels", "1", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        try:
            self.proc.stdin.write(line)
            while size := self._length():
                play.stdin.write(self._read(size))
                self.played = True
        finally:
            try:
                play.stdin.close()
            except BrokenPipeError:
                pass
            play.wait()

    def stop(self) -> None:
        self.failed = True
        try:
            self.proc.stdin.close()  # a healthy worker exits on EOF
        except OSError:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


class Feedback:
    def __init__(self, config: Config):
        self.config = config
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._level_at = 0.0
        # Set by the voice daemon around the recorder's lifetime. Nothing
        # else has a microphone -- the CLI, the planner, a test -- so False is
        # the honest default and speech is never held back from something that
        # could not feed back anyway.
        self.mic_open = False
        # The task being timed, set by the engine around a turn (#79). The
        # cloud voice appends SYNTH spans from the speaking thread: safe, as
        # `list.append` is atomic and the line is only read once `_say` has
        # waited for the mouth to return.
        self.trace = None
        # Piper's resident worker (#136): started where Piper speaks, never
        # here, and never again once it has failed.
        self.piper = None
        self._piper_failed = False
        # ponytail: one global lock; the pipe carries one sentence at a time,
        # and two sentences never overlap on the speakers anyway.
        self._piper_lock = threading.Lock()

    # -- bar state ----------------------------------------------------------
    def state(self, status: str, text: str = "") -> None:
        """Write the current status where a bar widget can poll it."""
        payload = {
            "status": status,
            "icon": ICONS.get(status, ICONS["idle"]),
            "text": text,
            "class": status,
            "updated": time.time(),
        }
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(STATE_FILE)

    def level(self, value: float) -> None:
        """Publish a 0..1 microphone level for the orb overlay.

        Capped at 20 Hz. Frames arrive at 10 Hz today, but the cap means a
        smaller frame size later cannot turn this into a write storm.
        """
        now = time.monotonic()
        if now - self._level_at < 0.05:
            return
        self._level_at = now
        try:
            tmp = LEVEL_FILE.with_suffix(".tmp")
            tmp.write_text(f"{max(0.0, min(1.0, value)):.3f}")
            tmp.replace(LEVEL_FILE)
        except OSError:
            pass

    # -- user-visible -------------------------------------------------------
    def notify(self, title: str, body: str = "", urgency: str = "low") -> None:
        if not self.config.notify or not shutil.which("notify-send"):
            return
        subprocess.run(
            ["notify-send", "-a", "OMA", "-u", urgency, "--", title, body],
            capture_output=True,
        )

    def speak(self, text: str) -> None:
        if not self.config.speak or not text:
            return
        if self.mic_open:
            # The local voice comes out of the speakers, and the microphone is
            # in the same room, so without the check this voice can talk into
            # an open mic and be heard as the user.
            self.log(f"held    not spoken aloud while listening: {text[:60]}")
            return
        threading.Thread(target=self._speak_now, args=(text,), daemon=True).start()

    # -- made ahead (#137) ----------------------------------------------------
    def can_make_ahead(self) -> bool:
        """Only the cloud voice is made ahead: Piper and the rest speak as today."""
        return not self.config.tts_command and elevenlabs.ready(self.config)

    def make_ahead(self, text: str) -> tuple[bytes, int]:
        """The whole clip for a line that has not played yet.

        Untimed: its spans would land inside the line playing now (#79).
        """
        return elevenlabs.synth(text, self.config, trace=None)

    def _speak_now(self, text: str, made=None) -> None:
        """Say one line and return when it has played (#114).

        `made` is what `make_ahead` gave for it: a clip to play, or the
        exception it raised, for Piper to say the line whole instead.
        """
        if made is not None:
            if not isinstance(made, BaseException):
                try:
                    # #135's pw-cat, told what the PCM is.
                    player = subprocess.Popen(
                        elevenlabs.PLAYER, stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except OSError as exc:
                    made = exc  # nothing played: Piper says it all
                else:
                    try:
                        player.stdin.write(made[0])
                    except BrokenPipeError:
                        pass
                    finally:
                        try:
                            player.stdin.close()
                        except BrokenPipeError:
                            pass
                        player.wait()  # the mouth returns when pw-cat does
                    return
            self.log(f"tts     elevenlabs failed ({made}) — using piper")
            self._speak_piper(text)
            return
        if self.config.tts_command:
            cmd = shlex.split(self.config.tts_command)
            subprocess.run([*cmd, "--", text], capture_output=True)
            return
        if elevenlabs.ready(self.config):
            try:
                elevenlabs.speak(text, self.config, trace=self.trace)
                return
            except elevenlabs.Cut as exc:
                # Part of the sentence was heard. Saying it all again in
                # Piper would be a repeat, not a fallback; the next sentence
                # starts over from the top.
                self.log(f"tts     elevenlabs cut off mid-sentence ({exc})")
                return
            except Exception as exc:
                # Every way the cloud can fail lands here -- no network, quota
                # gone, key revoked, ffmpeg missing -- and every one of them
                # falls through to Piper below. Silence would be the worse
                # bug, so the only thing this costs is a line in the log
                # saying which voice you are hearing and why.
                self.log(f"tts     elevenlabs failed ({exc}) — using piper")
        self._speak_piper(text)

    # -- Piper, resident -----------------------------------------------------
    def start_piper(self) -> bool:
        """Start the worker now, at daemon start. A failure is not retried."""
        with self._piper_lock:
            if self.piper is None and not self._piper_failed:
                self.piper = PiperWorker.start(piper_model())
                self._piper_failed = self.piper is None
            return self.piper is not None

    def stop_piper(self) -> None:
        worker, self.piper = self.piper, None
        if worker is not None:
            worker.stop()

    def _piper_gave_up(self, reason: str) -> None:
        # The reason is a class name or "timeout", never the sentence (#79).
        self.stop_piper()
        self._piper_failed = True
        self.log(f"warn    tts: piper resident failed ({reason}) — per sentence")

    def _speak_resident(self, text: str, model: Path) -> bool:
        """True if the sentence was spoken, or heard in part, by the worker."""
        with self._piper_lock:
            if self.piper is None and not self._piper_failed:
                self.piper = PiperWorker.start(model)  # the lazy start
                if self.piper is None:
                    self._piper_gave_up("did not start")
            if self.piper is None:
                return False
            try:
                self.piper.speak(text, piper_rate(model))
                return True
            except Exception as exc:
                heard = self.piper.played
                self._piper_gave_up(
                    "timeout" if isinstance(exc, TimeoutError) else type(exc).__name__)
                # Piper gives one chunk per sentence, so any audio at all
                # means it was heard: saying it twice is worse than once.
                return heard

    def _speak_piper(self, text: str) -> None:
        model = piper_model()
        if model and piper_python() and self._speak_resident(text, model):
            return
        if model and shutil.which("piper") and shutil.which("pw-cat"):
            piper = subprocess.Popen(
                # -m is not optional: piper refuses to start without a voice,
                # and finds the matching .onnx.json beside it on its own.
                ["piper", "--model", str(model), "--output-raw"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            assert piper.stdin is not None
            play = subprocess.Popen(
                # pw-cat rather than aplay: PipeWire is already a dependency
                # for the microphone, so this adds nothing. It needs --raw
                # and the rate spelled out — handed a bare stream it asks
                # libsndfile to identify the format and gets "Format not
                # recognised", because a pipe is not seekable.
                ["pw-cat", "--playback", "--raw", "--format", "s16",
                 "--rate", str(piper_rate(model)), "--channels", "1", "-"],
                stdin=piper.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Ours to close, or pw-cat waits on a writer that is still us.
            if piper.stdout is not None:
                piper.stdout.close()
            try:
                piper.stdin.write(text.encode())
                piper.stdin.close()
            except BrokenPipeError:
                pass
            play.wait()
            piper.wait()
            return
        if shutil.which("espeak-ng"):
            subprocess.run(["espeak-ng", "-s", "165", "--", text], capture_output=True)

    # -- log ----------------------------------------------------------------
    def log(self, line: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a") as fh:
            fh.write(f"{stamp}  {line}\n")
        if self.config.verbose:
            print(f"  {line}", flush=True)
