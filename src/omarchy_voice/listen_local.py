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

import os
import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path

from .config import Config, install_hint

MODEL_ENV = "OMARCHY_VOICE_WHISPER_MODEL"

# whisper.cpp wants 16 kHz mono PCM16. The realtime path runs at 24 kHz, so
# these two never share a recorder -- resampling in Python to save one process
# would cost more code than the process does.
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
    """Loudness of one frame, 0..1. Same shape as the realtime meter.

    Deliberately not imported from realtime: that module opens a websocket at
    import time in no way, but it does pull in the whole engine, and this path
    exists precisely for when that engine cannot run.
    """
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
               threads: int = 4) -> str:
    """PCM16 at SAMPLE_RATE to text, locally. Empty string if nothing usable."""
    if len(pcm) < int(SAMPLE_RATE * 2 * MIN_SPEECH_SECONDS):
        return ""
    model = model_path(config)
    if not model:
        raise Unavailable(f"{MODEL_ENV} is not set and no whisper_model configured")
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
                 "-t", str(threads), "-nt", "-np", "-l", "en"],
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
