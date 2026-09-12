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
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request

from .config import Config, install_hint

API = "https://api.elevenlabs.io/v1"

# What we ask the API for, and therefore what comes out of ffmpeg. Raw PCM at
# this rate is a Pro-tier output format; mp3 is not, and the decode costs
# milliseconds against a network round trip measured in hundreds.
FORMAT = "mp3_44100_128"
RATE = 44100


class Unavailable(RuntimeError):
    """ElevenLabs cannot speak this line; the message says why."""


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


def synth(text: str, config: Config, timeout: float = 30.0) -> tuple[bytes, int]:
    """One line of speech as int16 mono PCM, plus its sample rate.

    Raises Unavailable on anything going wrong -- the caller falls back to the
    local voice, so failing loudly here is cheap and failing quietly is not.

    ponytail: the whole clip is fetched before ffmpeg sees it, so speech starts
    one round trip late. Status lines are a sentence. Stream both ends (a
    thread feeding ffmpeg's stdin) if this ever has to read a paragraph.
    """
    key = api_key(config)
    if not key or not config.elevenlabs_voice_id:
        raise Unavailable("ElevenLabs is not configured")
    if not shutil.which("ffmpeg"):
        raise Unavailable(install_hint("ffmpeg"))

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
        with urllib.request.urlopen(request, timeout=timeout) as response:
            mp3 = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:200]
        raise Unavailable(f"ElevenLabs HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise Unavailable(f"could not reach ElevenLabs: {exc}") from exc
    if not mp3:
        raise Unavailable("ElevenLabs returned no audio")

    # Mastering is not a flourish. The voices you audition on their website are
    # mastered demo clips; raw API output is quieter and flatter and does not
    # match what you picked, so the loudnorm chain is what makes the voice the
    # one you chose.
    try:
        done = subprocess.run(
            ["ffmpeg", "-loglevel", "quiet", "-i", "pipe:0",
             "-af", config.elevenlabs_master,
             "-f", "s16le", "-ar", str(RATE), "-ac", "1", "pipe:1"],
            input=mp3, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unavailable(f"ffmpeg failed: {exc}") from exc
    if done.returncode != 0 or not done.stdout:
        raise Unavailable("ffmpeg decoded no audio from the ElevenLabs mp3")
    return done.stdout, RATE


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
