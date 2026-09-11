"""The mouth: how the assistant tells you what it heard and did.

Three channels, all optional and all cheap:
  * a notification (Omarchy's shell renders these)
  * a state file, so a bar widget can show a live listening indicator
  * text to speech, if piper or espeak-ng is around
"""

from __future__ import annotations

import functools
import json
import os
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .config import Config, LEVEL_FILE, LOG_FILE, STATE_DIR, STATE_FILE, RUNTIME_DIR

ICONS = {
    "idle": "󰍬",
    "listening": "󰍬",
    "thinking": "󱚟",
    "acting": "󱐋",
    "confirm": "󰀦",
    "error": "󰍭",
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


class Feedback:
    def __init__(self, config: Config):
        self.config = config
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._level_at = 0.0
        # Set by the realtime session around the recorder's lifetime. Nothing
        # else has a microphone -- the CLI, the planner, a test -- so False is
        # the honest default and speech is never held back from something that
        # could not feed back anyway.
        self.mic_open = False

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
            # in the same room. The realtime audio is held while she is
            # replying; this is not, so without the check it is the one voice
            # that can talk into an open mic -- and the server hears it as the
            # user and cancels whatever she was saying.
            self.log(f"held    not spoken aloud while listening: {text[:60]}")
            return
        threading.Thread(target=self._speak_now, args=(text,), daemon=True).start()

    def _speak_now(self, text: str) -> None:
        if self.config.tts_command:
            cmd = shlex.split(self.config.tts_command)
            subprocess.run([*cmd, "--", text], capture_output=True)
            return
        model = piper_model()
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
