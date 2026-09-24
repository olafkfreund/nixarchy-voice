"""A cloud voice, with the local one still wired underneath.

Piper is intelligible and free and never stops working. It also sounds like
1998. This speaks the same status lines through ElevenLabs when it is
configured, and falls back to Piper on any failure at all -- no key, no
network, quota gone, ffmpeg missing. Degrade, never mute: the assistant going
silent because a cloud API had a bad afternoon is a worse bug than a plain
voice.

The approach here -- fetch mp3 and decode locally, turbo model, master the
result before playing it -- is what jaredrhod/backtalk (AGPL-3.0, Jared
Rhodenizer) worked out the expensive way in its `mouth.py`. The reasoning is
restated in the comments below; none of the code is theirs.

The clip is streamed (#135): the mp3 goes into ffmpeg as it arrives and the
PCM into pw-cat as it comes out, so she starts speaking before the download
ends. The mastering is a fixed gain and a limiter for the same reason:
loudnorm holds its output for a 3 s look-ahead, which on a one-sentence clip
is all of it.
"""

from __future__ import annotations

import contextlib
import functools
import json
import os
import shutil
import subprocess
import threading
import urllib.error
import urllib.request

from . import trace as trace_mod
from .config import Config, install_hint

API = "https://api.elevenlabs.io/v1"

# What we ask the API for, and therefore what comes out of ffmpeg. Raw PCM at
# this rate is a Pro-tier output format; mp3 works on every tier, and the
# decode costs milliseconds against a network round trip measured in hundreds.
FORMAT = "mp3_44100_128"
RATE = 44100
# pw-cat, told what the PCM is: handed a bare pipe it asks libsndfile to
# identify the format and gets "Format not recognised".
PLAYER = ["pw-cat", "--playback", "--raw", "--format", "s16",
          "--rate", str(RATE), "--channels", "1", "-"]
CHUNK = 4096


class Unavailable(RuntimeError):
    """ElevenLabs cannot speak this line; the message says why."""


class Cut(Unavailable):
    """Part of the sentence played; the rest is lost, and the caller must not
    repeat it."""


@functools.lru_cache(maxsize=4)
def _key_for_slot(slot: str) -> str:
    """The key behind one keyring slot name. Cached: this runs per sentence.

    Keyed by slot rather than cached in a bare global so a test -- or a config
    reload that changes the slot -- gets the right answer instead of the first
    one ever asked for.
    """
    if slot and shutil.which("secret-tool"):
        try:
            done = subprocess.run(["secret-tool", "lookup", "service", slot],
                                  capture_output=True, text=True, timeout=5)
            if done.returncode == 0 and done.stdout.strip():
                return done.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    # Last resort, and a real downgrade: an export line in a shell profile is
    # a plaintext key sitting on disk, readable by anything running as you and
    # liable to end up in a dotfiles repo. That is exactly what the keyring
    # avoids, which is why it is tried first.
    return os.environ.get("ELEVENLABS_API_KEY", "")


def api_key(config: Config) -> str:
    """The API key, from the keyring first and the environment second.

    Never from config.toml and never from any file in this repo. A key in the
    config is a key in whatever backup or git remote the config reaches.
    """
    return _key_for_slot(config.elevenlabs_key_slot)


def ready(config: Config) -> bool:
    """Whether to even try. All three: switched on, a voice, and a key."""
    return bool(config.elevenlabs_enabled
                and config.elevenlabs_voice_id
                and api_key(config))


def _get(url: str, key: str, timeout: float = 15.0) -> dict:
    request = urllib.request.Request(url, headers={"xi-api-key": key})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def voices(config: Config) -> list[dict]:
    """The voices on this account, for `voices` to print.

    Ids are opaque per-account strings, so this is the only way to find one:
    there is nothing to guess and a guessed id is somebody else's voice.
    """
    key = api_key(config)
    if not key:
        raise Unavailable("no ElevenLabs API key")
    try:
        data = _get(f"{API}/voices", key)
    except urllib.error.HTTPError as exc:
        raise Unavailable(f"ElevenLabs HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise Unavailable(f"could not reach ElevenLabs: {exc}") from exc
    listed = []
    for voice in data.get("voices") or []:
        labels = voice.get("labels") or {}
        listed.append({
            "name": voice.get("name", ""),
            "voice_id": voice.get("voice_id", ""),
            "accent": labels.get("accent", ""),
            "gender": labels.get("gender", ""),
            "description": labels.get("description", ""),
        })
    return listed


def _ffmpeg_argv(master: str) -> list[str]:
    """mp3 on stdin to mastered int16 mono PCM on stdout, as it arrives.

    `-f mp3` because FORMAT asks the API for mp3: told the format, ffmpeg
    does not probe for it. Probing, like loudnorm, holds the output until the
    input ends, which on a short pipe is the whole clip.
    """
    return ["ffmpeg", "-loglevel", "quiet", "-f", "mp3",
            "-probesize", "32", "-analyzeduration", "0", "-fflags", "+nobuffer",
            "-i", "pipe:0", "-af", master,
            "-f", "s16le", "-ar", str(RATE), "-ac", "1",
            "-flush_packets", "1", "pipe:1"]


def _feed(response, ffmpeg, failures: list[str]) -> None:
    """The download, into ffmpeg as it arrives. Runs on its own thread.

    On a failure ffmpeg is killed, not closed: closing would flush a partial
    clip into the speakers after the caller has decided nothing was heard.
    """
    try:
        got = False
        while chunk := response.read(CHUNK):
            got = True
            ffmpeg.stdin.write(chunk)
            ffmpeg.stdin.flush()
        if not got:
            failures.append("ElevenLabs returned no audio")
        ffmpeg.stdin.close()
    except Exception as exc:
        failures.append(f"the ElevenLabs stream broke: {exc}")
        ffmpeg.kill()


def _run(text: str, config: Config, timeout: float, trace,
         play: bool) -> bytes:
    """Fetch, decode and master one line; play it, or collect it.

    Returns the PCM when collecting, b"" when playing. Raises Unavailable if
    nothing was played, and Cut if some was.
    """
    def span(name: str):
        return (trace.mark(trace_mod.SYNTH, name) if trace
                else contextlib.nullcontext())

    key = api_key(config)
    if not key or not config.elevenlabs_voice_id:
        raise Unavailable("ElevenLabs is not configured")
    if not shutil.which("ffmpeg"):
        raise Unavailable(install_hint("ffmpeg"))
    if play and not shutil.which("pw-cat"):
        raise Unavailable("pw-cat is not installed; nothing to play the "
                          "audio with")

    body = json.dumps({
        "text": text,
        "model_id": config.elevenlabs_model,
        # style is deliberately absent, not zero-by-accident: anything above 0
        # makes delivery slower and duller, which is the wrong trade for an
        # assistant answering in one sentence.
        "voice_settings": {
            "stability": config.elevenlabs_stability,
            "similarity_boost": config.elevenlabs_similarity,
        },
    }).encode()
    url = (f"{API}/text-to-speech/{config.elevenlabs_voice_id}"
           f"/stream?output_format={FORMAT}")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"xi-api-key": key, "Content-Type": "application/json"})
    try:
        with span("request"):
            response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:200]
        raise Unavailable(f"ElevenLabs HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise Unavailable(f"could not reach ElevenLabs: {exc}") from exc

    # From the headers to her first sample: the rest of the wait (#79).
    buffered = (trace.mark(trace_mod.SYNTH, "buffer").close if trace
                else lambda: None)
    failures: list[str] = []
    collected: list[bytes] = []
    ffmpeg = player = feeder = None
    played = 0
    try:
        # Mastering is not a flourish. The voices you audition on their
        # website are mastered demo clips; raw API output is quieter and
        # flatter and does not match what you picked.
        ffmpeg = subprocess.Popen(
            _ffmpeg_argv(config.elevenlabs_master), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        feeder = threading.Thread(target=_feed, args=(response, ffmpeg, failures),
                                  daemon=True)
        feeder.start()
        while chunk := ffmpeg.stdout.read1(CHUNK):
            if not played:
                buffered()
                if play:
                    player = subprocess.Popen(
                        PLAYER, stdin=subprocess.PIPE,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if play:
                player.stdin.write(chunk)
                player.stdin.flush()
            else:
                collected.append(chunk)
            played += len(chunk)
        if player is not None:
            player.stdin.close()
            player.wait()
        ffmpeg.wait(timeout)
        feeder.join(timeout)
    except Exception as exc:
        failures.append(str(exc) or type(exc).__name__)
    except BaseException:
        if player is not None:
            player.kill()
        raise
    finally:
        buffered()
        if ffmpeg is not None:
            # Popen.kill() does nothing to a child already reaped, so this
            # only ever stops one still running.
            ffmpeg.kill()
            ffmpeg.wait()
        if player is not None:
            # The mouth returns when pw-cat has played it all (#114).
            with contextlib.suppress(OSError):
                player.stdin.close()
            player.wait()
        response.close()
        if feeder is not None:
            # ponytail: a feeder blocked in read() on a stalled socket can
            # outlive this call by up to `timeout`; it is a daemon thread and
            # holds no process.
            feeder.join(1.0)

    if not failures and ffmpeg.returncode:
        failures.append(f"ffmpeg exited {ffmpeg.returncode}")
    if not failures and not played:
        failures.append("ffmpeg decoded no audio from the ElevenLabs mp3")
    if failures:
        if play and played:
            raise Cut(failures[0])
        raise Unavailable(failures[0])
    return b"".join(collected)


def speak(text: str, config: Config, timeout: float = 30.0,
          trace=None) -> None:
    """Say one line in the cloud voice, streamed; return when pw-cat exits.

    Returning only once pw-cat has exited is #114's contract: the caller opens
    the microphone when this returns, and her voice must not be in it.
    Raises Unavailable if the line failed before a sample played (the caller
    says it all with Piper), and Cut if it failed after (the caller logs it
    and does not repeat it). With a `trace`, the SYNTH spans are `request`
    and `buffer`: only the wait before her first sample, never the text.
    """
    _run(text, config, timeout, trace, play=True)


def synth(text: str, config: Config, timeout: float = 30.0,
          trace=None) -> tuple[bytes, int]:
    """One whole line of speech as int16 mono PCM, plus its sample rate.

    The same pipeline as `speak`, collected instead of played, for a caller
    that makes a line ahead of saying it (#137). Nothing has played, so every
    failure is Unavailable and any partial PCM is dropped.

    ponytail: collecting waits for the whole clip, which is what the caller
    asked for; `speak` is the one that starts early.
    """
    return _run(text, config, timeout, trace, play=False), RATE


def check_ready(config: Config) -> list[str]:
    """Everything standing between this config and a cloud voice, in English."""
    problems = []
    if not config.elevenlabs_enabled:
        problems.append("elevenlabs_enabled is false: set enabled = true "
                        "under [elevenlabs] in config.toml")
    if not config.elevenlabs_voice_id:
        problems.append("no elevenlabs_voice_id: run `omarchy-voice voices` "
                        "and copy an id from your own account")
    if not shutil.which("ffmpeg"):
        problems.append(install_hint("ffmpeg"))
    if not api_key(config):
        problems.append(
            "no ElevenLabs API key: store one with `secret-tool store --label "
            f"omarchy-voice service {config.elevenlabs_key_slot}`, or export "
            "ELEVENLABS_API_KEY")
        return problems
    try:
        voices(config)
    except Unavailable as exc:
        problems.append(str(exc))
    return problems
