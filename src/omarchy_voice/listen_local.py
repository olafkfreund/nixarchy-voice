"""Speech in, without the API: record from the mic, transcribe with whisper.cpp.

The typed path already runs against anything OpenAI-compatible -- Ollama, LM
Studio, vLLM -- so `omarchy-voice say "close the browser"` works with the API
down or the account empty. The half that was missing was getting the sentence
in: `say` needed it typed, which is a strange thing to need from a voice
assistant, and the offline story stopped at "and then type it".

This is the other half. Nothing here touches the network. Both callers share
it: `omarchy-voice ask` dictates one instruction, and the wake word listens for
one word, and they differ only in how long they listen and what they do with
the text.
"""

from __future__ import annotations

import io
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
import wave
from pathlib import Path

from .config import Config, install_hint

MODEL_ENV = "OMARCHY_VOICE_WHISPER_MODEL"

# whisper.cpp wants 16 kHz mono PCM16.
SAMPLE_RATE = 16000
FRAME_BYTES = 1600  # 50 ms

# Nothing said in this long and we were never going to hear anything.
DEFAULT_MAX_SECONDS = 15.0
# Speech has stopped for this long: the sentence is over.
DEFAULT_HANG_SECONDS = 1.2
# Refuse to transcribe less than this. A door closing is not an instruction,
# and whisper hallucinates confidently on a fragment -- "Thank you." is the
# classic, and it would be passed to the planner as though someone said it.
MIN_SPEECH_SECONDS = 0.35


def default_source() -> str:
    """The current default PipeWire source name, or '' if there is none."""
    import subprocess
    try:
        out = subprocess.run(["pactl", "get-default-source"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    return "" if out in ("", "@DEFAULT_SOURCE@") else out


def _pactl(*args: str) -> str:
    import subprocess
    try:
        return subprocess.run(["pactl", *args], capture_output=True,
                              text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def default_sink() -> str:
    out = _pactl("get-default-sink")
    return "" if out in ("", "@DEFAULT_SINK@") else out


def echo_risk(config: Config) -> str:
    """Why her own voice might come back in as yours, or '' if it will not.

    The failure this detects is not theoretical. On a machine whose microphone
    and line out were the same audio interface, with both at 100% and no echo
    cancellation, a session log has her saying "OH-mah, OH-mah, OH-mah",
    hearing it back as "어마", and replying "Yes, I'm here." Later her own
    sentence returned as two user turns. A fragment that transcribed as an
    instruction — "Бела." — pressed CTRL+R.
    """
    if not config.barge_in:
        return ""  # the microphone is already held shut while she speaks
    source = (config.device or default_source()).lower()
    sink = default_sink().lower()
    if not source or not sink:
        return ""
    if "echo-cancel" in source or "echo_cancel" in source:
        return ""
    if any(word in source for word in ("headset", "headphone")):
        return ""

    def device_of(name: str) -> str:
        # alsa_output.usb-Focusrite_Scarlett_Solo_...-00.HiFi__Line__sink
        # alsa_input .usb-Focusrite_Scarlett_Solo_...-00.HiFi__Mic1__source
        body = name.split(".", 1)[-1]
        return body.split(".hifi")[0].split("__")[0]

    if device_of(source) == device_of(sink):
        return ("barge_in is on and your microphone and speakers are the same device "
                f"({device_of(sink)}). Her voice will come back in as yours and be "
                "transcribed as a command. Set barge_in = false, wear headphones, or load "
                "PipeWire's echo-cancel module.")
    return ("barge_in is on with speakers rather than headphones. If she starts "
            "answering herself, set barge_in = false.")


def heard_wake_word(text: str, wake: str) -> bool:
    """Whether a locally-transcribed snippet contains the wake word.

    Matched loosely on purpose. whisper punctuates and capitalises what it
    hears, so "Oma," "Oma." and "OMA!" are all the same word being said, and a
    wake word that needed an exact match would fail on the punctuation the
    transcriber added rather than on anything the user did. Names are also
    heard imperfectly -- configure any spelling that keeps coming back in the
    log as a second word rather than fighting the model about it.
    """
    if not wake:
        return False
    cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in text.lower())
    words = set(cleaned.split())
    return any(part in words for part in wake.lower().split() if part)


def after_wake_word(text: str, wake: str) -> str:
    """What was said after the wake word, if it opened the sentence (#72).

    "Oma, close it" and "Hey Oma, close it" are an instruction; "I told Oma to
    close it" is someone talking about her, and only wakes listening. So the
    wake word must be one of the first two words, and what follows still has to
    survive `clean` -- "Oma?" leaves nothing.
    """
    parts = {p for p in wake.lower().split() if p}
    if not parts:
        return ""
    words = text.split()
    for i, word in enumerate(words[:2]):
        bare = "".join(c for c in word.lower() if c.isalnum())
        if bare in parts:
            return clean(" ".join(words[i + 1:]).lstrip(" ,.!?"))
    return ""


def vocabulary(config: Config | None = None) -> str:
    """The words whisper should expect on this desktop, for its prompt.

    Without it "Claude" comes back "clone" or "cloud" (#72). Short on purpose:
    whisper's prompt window is small and a long one biases decoding.
    """
    from .capabilities import CODING_AGENTS  # not at import: see _level
    words = []
    if config is not None:
        words += getattr(config, "wake_word", "").split()
    words += ["Claude", "Claude Code", "Hyprland", "Omarchy", "workspace"]
    words += [name for name, *_ in CODING_AGENTS]
    if config is not None:
        words += [w.strip() for w in getattr(config, "whisper_vocabulary", "").split(",")]
    seen, out = set(), []
    for word in words:
        if word and word.lower() not in seen:
            seen.add(word.lower())
            out.append(word)
    line = ", ".join(out)
    return line if len(line) <= 200 else line[:line.rfind(", ", 0, 200)]


class Server:
    """whisper-server, held open so the model is loaded once, not per sentence.

    Measured: whisper-cli is 0.32 s an utterance with the model in page cache and
    1.37 s without; this is 0.05 s. Loopback only, and every path sits behind a
    random prefix: a web page can POST to a local port without reading the
    answer, and /load would otherwise take a model path from anyone (#72).

    ponytail: the prefix is on the command line, so another local user can read
    it from /proc. A web page cannot, which is the threat this covers. A Unix
    socket closes the rest, if whisper-server ever takes one.
    """

    def __init__(self, proc, port: int, token: str):
        self.proc, self.port, self.token = proc, port, token
        self.failed = False

    @classmethod
    def start(cls, config: Config | None = None, timeout: float = 10.0):
        """A running server, or None -- the caller then uses whisper-cli."""
        model = model_path(config)
        binary = shutil.which("whisper-server")
        if not model or not binary:
            return None
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        token = secrets.token_hex(16)
        try:
            proc = subprocess.Popen(
                [binary, "-m", model, "--host", "127.0.0.1", "--port", str(port),
                 "--request-path", f"/{token}", "-l", "en", "-nt"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return None
        server = cls(proc, port, token)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and proc.poll() is None:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                return server
            except OSError:
                time.sleep(0.1)
        server.stop()
        return None

    @property
    def alive(self) -> bool:
        return not self.failed and self.proc.poll() is None

    def transcribe(self, pcm: bytes, prompt: str = "", timeout: float = 10.0) -> str:
        audio = io.BytesIO()
        with wave.open(audio, "wb") as fh:
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(SAMPLE_RATE)
            fh.writeframes(pcm)
        boundary = secrets.token_hex(16)
        body = b""
        for name, value in (("response_format", b"text"), ("prompt", prompt.encode())):
            body += (f'--{boundary}\r\nContent-Disposition: form-data; '
                     f'name="{name}"\r\n\r\n').encode() + value + b"\r\n"
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                 f'filename="utterance.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode()
        body += audio.getvalue() + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/{self.token}/inference", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return reply.read().decode(errors="replace")

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()


class Unavailable(RuntimeError):
    """Local listening cannot run; the message says what is missing."""


def model_path(config: Config | None = None) -> str:
    """Where the ggml model is, preferring an explicit setting."""
    explicit = getattr(config, "whisper_model", "") if config else ""
    return explicit or os.environ.get(MODEL_ENV, "")


def check_ready(config: Config | None = None) -> list[str]:
    problems = []
    if not shutil.which("whisper-cli"):
        problems.append(install_hint("whisper-cli", "whisper-cpp"))
    if not shutil.which("pw-record"):
        problems.append(install_hint("pw-record", "pipewire"))
    path = model_path(config)
    if not path:
        problems.append(
            f"no whisper model: {MODEL_ENV} is unset and whisper_model is empty. "
            "The omarchy-voice package sets it; running the module directly does not.")
    elif not Path(path).exists():
        problems.append(f"whisper model {path} does not exist")
    return problems


def _level(chunk: bytes) -> float:
    """Loudness of one frame, 0..1."""
    import array
    import math
    usable = len(chunk) // 2 * 2
    if usable < 2:
        return 0.0
    samples = array.array("h")
    samples.frombytes(chunk[:usable])
    window = samples[::4] or samples
    rms = math.sqrt(sum(s * s for s in window) / len(window)) / 32768.0
    value = (rms ** 0.5) * 1.28
    return 0.0 if value < 0.10 else min(1.0, (value - 0.10) / 0.90)


def record_utterance(
    device: str = "",
    level: float = 0.02,
    hang: float = DEFAULT_HANG_SECONDS,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    on_level=None,
) -> bytes:
    """Capture one spoken sentence: wait for speech, stop when it ends.

    Returns raw PCM16. Empty if nothing was ever said, which the caller must
    treat as "say again" rather than as an empty instruction.
    """
    cmd = ["pw-record", "--rate", str(SAMPLE_RATE), "--channels", "1",
           "--format", "s16", "--latency", "20ms"]
    if device:
        cmd += ["--target", device]
    cmd.append("-")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        raise Unavailable(install_hint("pw-record", "pipewire")) from exc

    started = time.monotonic()
    heard_at = 0.0
    chunks: list[bytes] = []
    try:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(FRAME_BYTES)
            if not chunk:
                break
            now = time.monotonic()
            loud = _level(chunk)
            if on_level:
                on_level(loud)
            if loud > level:
                heard_at = now
                chunks.append(chunk)
            elif heard_at:
                # Keep the trailing quiet: whisper reads a hard cut at the end
                # of a word as a different word.
                chunks.append(chunk)
                if now - heard_at > hang:
                    break
            if now - started > max_seconds:
                break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    return b"".join(chunks) if heard_at else b""


def transcribe(pcm: bytes, config: Config | None = None,
               threads: int = 4, server: Server | None = None) -> str:
    """PCM16 at SAMPLE_RATE to text, locally. Empty string if nothing usable."""
    if len(pcm) < int(SAMPLE_RATE * 2 * MIN_SPEECH_SECONDS):
        return ""
    model = model_path(config)
    if not model:
        raise Unavailable(f"{MODEL_ENV} is not set and no whisper_model configured")
    prompt = vocabulary(config)
    if server is not None and server.alive:
        try:
            return clean(server.transcribe(pcm, prompt))
        except Exception:
            # Once, for the rest of the session: the caller logs it, and
            # whisper-cli below answers this utterance and every later one.
            server.failed = True
    if not shutil.which("whisper-cli"):
        raise Unavailable(install_hint("whisper-cli", "whisper-cpp"))

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "utterance.wav"
        with wave.open(str(wav), "wb") as fh:
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(SAMPLE_RATE)
            fh.writeframes(pcm)
        try:
            done = subprocess.run(
                ["whisper-cli", "-m", model, "-f", str(wav),
                 "-t", str(threads), "-nt", "-np", "-l", "en", "--prompt", prompt],
                capture_output=True, text=True, timeout=120)
        except FileNotFoundError as exc:
            raise Unavailable(install_hint("whisper-cli", "whisper-cpp")) from exc
        except subprocess.TimeoutExpired:
            return ""
    if done.returncode != 0:
        raise Unavailable(
            f"whisper-cli failed: {(done.stderr or '').strip()[:200] or 'no output'}")
    return clean(done.stdout)


# Things whisper emits for silence, music, or a door closing. It does not
# return an empty string for an empty clip -- it returns its best guess at
# what a human would have said, confidently, and the planner would act on it.
_NOISE = {
    "", "you", "thank you", "thanks for watching", "thank you.",
    "thanks for watching!", "bye", "bye.", ".", "...", "[blank_audio]",
    "(silence)", "so", "uh", "um",
}


def clean(raw: str) -> str:
    """whisper-cli output to one instruction, or empty if it heard nothing real."""
    text = " ".join(raw.split()).strip()
    # Bracketed annotations are whisper describing the audio, not transcribing
    # it: [BLANK_AUDIO], (upbeat music), [Music].
    while text.startswith(("[", "(")):
        close = text.find("]" if text[0] == "[" else ")")
        if close == -1:
            return ""
        text = text[close + 1:].strip()
    if text.lower().strip(" .!?") in {n.strip(" .!?") for n in _NOISE}:
        return ""
    return text
