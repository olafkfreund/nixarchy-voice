"""Configuration loading.

Config lives at ~/.config/omarchy-voice/config.toml. Every key has a working
default, so the file is optional.
"""

from __future__ import annotations

import os
import stat
import sys
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
CACHE_HOME = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))


def install_hint(tool: str, package: str = "") -> str:
    """"<tool> is not installed", with advice that works on this machine.

    This text is handed to the model, which repeats it to the user as an
    instruction — so it has to be an instruction that actually runs here.
    Every tool named this way is wrapped onto PATH by the omarchy-voice
    package, so reaching this message at all means something bypassed the
    wrapper.
    """
    return (f"{tool} is not installed. The omarchy-voice package wraps it onto "
            f"PATH, so this means the wrapper was bypassed — run the "
            f"`omarchy-voice` binary rather than the module directly, or add "
            f"pkgs.{package or tool} to your configuration and rebuild")


def app_dirs() -> list[Path]:
    """Every directory holding .desktop entries, in XDG precedence order.

    Reads XDG_DATA_DIRS rather than assuming /usr/share. On a distribution that
    installs into the store (NixOS) nothing is under /usr at all, and hardcoding
    it costs the model the entire list of apps it is allowed to launch — with no
    error, just a manifest that quietly says the machine has no software on it.
    """
    raw = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    roots = [DATA_HOME, *(Path(d) for d in raw.split(":") if d)]
    seen: dict[Path, None] = {}
    for root in roots:
        seen.setdefault(root / "applications", None)
    return list(seen)
ENV_FILE = CONFIG_HOME / "omarchy-voice" / "env"
SAFETY_ID_FILE = CONFIG_HOME / "omarchy-voice" / "safety-id"


def _runtime_dir() -> Path:
    # Prefer the systemd runtime dir (mode 700). Never fall back to a world-
    # writable /tmp — the control socket would be an unauthenticated desktop
    # remote for every local user.
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg) / "omarchy-voice"
    return STATE_HOME / "omarchy-voice" / "run"


RUNTIME_DIR = _runtime_dir()


def load_env_file(path: Path = ENV_FILE) -> list[str]:
    """Merge ~/.config/omarchy-voice/env into os.environ.

    The systemd unit reads this file through EnvironmentFile, so the daemon has
    the keys either way. Without this, the *CLI* does not. A real environment
    variable always wins. Returns warnings (world-readable file, etc.).
    """
    warnings: list[str] = []
    try:
        st = path.stat()
        text = path.read_text()
    except OSError:
        return warnings
    if st.st_mode & 0o077:
        warnings.append(f"{path} is readable by group/other — chmod 600")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value
    return warnings


CONFIG_DIR = CONFIG_HOME / "omarchy-voice"
CONFIG_FILE = CONFIG_DIR / "config.toml"
CACHE_DIR = CACHE_HOME / "omarchy-voice"
STATE_DIR = STATE_HOME / "omarchy-voice"
SOCKET_PATH = RUNTIME_DIR / "control.sock"
STATE_FILE = RUNTIME_DIR / "state.json"
# Its own file, deliberately. This changes ten times a second while listening;
# status changes a few times a minute. A watcher on the status file should not
# have to wake for every audio frame.
LEVEL_FILE = RUNTIME_DIR / "level"
LOG_FILE = STATE_DIR / "session.log"

# Actions matched by these patterns always ask before running. They are the
# things you cannot undo by saying "no, the other one".
DEFAULT_CONFIRM = [
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bpoweroff\b",
    r"\bsuspend\b",
    r"\bhibernat",
    r"\bomarchy\s+update\b",
    r"\bomarchy\s+drive\b",
    r"\bomarchy\s+pkg\b",
    r"\bomarchy\s+install\b",
    r"\bomarchy\s+refresh\b",
    r"\bomarchy\s+reinstall\b",
    r"\bhl\.dsp\.exit\b",
    r"\bclose[-_ ]?all\b",
    # Recoverable — the previous generation is still in the boot menu — but it
    # swaps the running system out from under whoever is talking.
    r"\bnixos-rebuild\b",
    r"\bhome-manager\s+switch\b",
    r"\bnixarchy-apply\b",
    r"\bnix\s+flake\s+update\b",
]

# Never run, whatever the model decides. A voice channel is an open microphone;
# anything on this list is not worth the tail risk of a misheard sentence.
DEFAULT_DENY = [
    r"\brm\s+-[a-zA-Z]*[rf]",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\b(shred|wipefs)\b",
    r">\s*/dev/[sn][dv]",
    r"\bpasswd\b",
    r"\bsudo\b",
    r"\bpkexec\b",
    r"\bcryptsetup\b",
    r"\bcurl\b.*\|\s*(ba)?sh",
    r"\bgit\s+push\b",
    r"\bssh\b",
    # Garbage collection is the `rm -rf` of a NixOS machine: it removes the old
    # generations, which are the only way back from a bad rebuild. It needs no
    # sudo for the user profile and contains none of the words above, so
    # without these it walked straight through the gate.
    r"\bnix-collect-garbage\b",
    r"\bnix\s+store\s+(delete|gc)\b",
    r"\bnix-store\s+--delete\b",
    r"\bnix\s+profile\s+wipe-history\b",
    r"\bnix-env\s+--delete-generations\b",
]


# Keys that used to mean something. Kept out of `unknown_keys` so an existing
# config does not get reported as full of typos, and named in `doctor` so the
# user learns why the setting stopped having an effect rather than wondering.
RETIRED_KEYS = {
    "mode": "listening is toggle-only now; there is no always-on mode",
}


# Sections whose keys are namespaced rather than flattened, because the plain
# names are already taken by another section.
PREFIXED_SECTIONS = {"realtime"}

# List-valued policy keys union with the built-in lists unless the matching
# `*_replace` flag is set. Unknown keys are kept so doctor can report typos.
LIST_UNION_KEYS = {
    "confirm_patterns": DEFAULT_CONFIRM,
    "deny_patterns": DEFAULT_DENY,
}


@dataclass
class Config:
    # --- openai ------------------------------------------------------------
    planner_model: str = "gpt-4.1"
    api_key_env: str = "OPENAI_API_KEY"
    # Where `omarchy-voice say` sends its chat completions. Anything speaking
    # OpenAI's /v1/chat/completions works -- Ollama, LM Studio, vLLM,
    # OpenRouter -- because the planner only needs tool calls back in that
    # shape, not OpenAI specifically.
    #
    # The realtime session is not affected and cannot be: it speaks OpenAI's
    # websocket protocol, which nothing else implements. This is the typed
    # path, `say` and `--dry-run`, which is also the one you want working when
    # the API is down or the account is out of credit.
    base_url: str = "https://api.openai.com/v1"
    # Tool rounds allowed on one spoken instruction before the assistant has to
    # be asked again. A goal worked properly is a loop — act, wait, look, act —
    # so 8 ran out halfway through anything with three steps in it and the user
    # had to say "carry on". Each round costs a turn against the per-minute
    # token budget, which is why this is 12 and not 30.
    max_turns: int = 12

    # --- ears --------------------------------------------------------------
    # There is no mode. Listening is off when the daemon starts and only the
    # toggle turns it on — see RETIRED_KEYS. An always-on microphone is not a
    # setting worth having on a machine that streams room audio to an API.
    device: str = ""  # PipeWire target; empty means the default source
    # Whether the microphone stays live while she is speaking.
    #
    # Off by default, and the default matters: with speakers, her voice leaves
    # the room and comes back into an open mic. The server's turn detection
    # hears it, cancels the reply mid-word, and transcribes it as the user —
    # a session log has her saying "OH-mah, OH-mah, OH-mah", hearing it back as
    # "어마", and answering herself. Worse, a stray fragment that transcribes as
    # an instruction gets *run*: one arrived as "Бела." and pressed CTRL+R.
    #
    # Turn it on if you wear headphones, or if you have set up PipeWire's
    # echo-cancel module — then you get to interrupt her mid-sentence, which is
    # the thing this costs.
    barge_in: bool = False

    # Whether sustained silence is withheld from the API instead of uploaded.
    #
    # Listening streams the room continuously, and the room is mostly quiet:
    # every 100 ms frame of nobody-talking was billed at the same audio rate as
    # speech. The gate holds frames below `silence_level` once nothing has been
    # said for `silence_hold_seconds`, and flushes a short pre-roll when speech
    # resumes so the first syllable is not clipped.
    #
    # The hold is not optional padding. Server-side turn detection decides a
    # turn ended by hearing the pause after it, so cutting the audio the instant
    # someone stops talking means the turn never ends and the reply never comes.
    # The gate only starts once that pause has already been sent.
    silence_gate: bool = True
    # Loudness below which a frame counts as room tone, on the same 0..1 curve
    # the orb uses. `frame_level` already returns exactly 0.0 for silence and
    # room tone, so this is margin above that, not the floor itself.
    silence_level: float = 0.02
    # How long to keep streaming after the last speech-level frame. Comfortably
    # longer than the pause semantic_vad needs to call a turn finished.
    silence_hold_seconds: float = 1.5
    # Stop capturing after this long with nothing said, as if the toggle had
    # been pressed. Listening is a mode you enter and forget: without this,
    # walking away from an open microphone streams the room until you come back.
    # The websocket stays up, so resuming is immediate. 0 disables it.
    idle_stop_seconds: int = 600
    # How many conversation items to keep before the oldest turns are deleted.
    # Everything still in the conversation is re-sent as input on every turn, so
    # this is the ceiling on what a long session costs per turn: without it, an
    # hour-old session pays for the whole hour on every sentence. 40 is roughly
    # a dozen turns of speech and tool calls -- far more context than a desktop
    # instruction needs, and far less than unbounded. 0 disables it.
    history_items: int = 40

    # --- realtime ----------------------------------------------------------
    # These live under [realtime] in the config file; the loader prefixes that
    # section's keys, because `model` already means the planner model.
    realtime_model: str = "gpt-realtime-2.1"
    realtime_voice: str = "marin"
    realtime_turn_detection: str = "semantic_vad"
    realtime_sample_rate: int = 24000
    # Transcribes YOUR audio back to us. Off by default upstream, so without it
    # the log records what the assistant said and nothing about what was asked —
    # which makes a misheard command impossible to tell from a bad decision.
    # Empty string disables it. Shape verified against the live API:
    # session.audio.input.transcription = {"model": ...}
    #
    # Off by default because it is not free: it runs a second model over every
    # second of input audio, in addition to the realtime model that is already
    # listening to it, and the only thing that consumes the result is two lines
    # in the session log. That is a debugging aid with a bill attached, so it is
    # opt-in -- turn it on when you need to tell a misheard command from a bad
    # decision, which is exactly when it earns the money.
    realtime_transcribe_model: str = ""

    # The word that starts a session hands-free. Empty means off, and off is the
    # default: this keeps a microphone open locally whenever listening is *not*
    # on, which is a thing to choose rather than inherit.
    #
    # Nothing leaves the machine until the word is heard. The audio goes to
    # whisper.cpp on this CPU, and only once someone actually speaks -- silence
    # never reaches the transcriber, let alone the API. That is the point: it
    # makes an always-available assistant cost nothing while nobody is talking,
    # which is the opposite of leaving listening switched on.
    #
    # Pick something not said in normal conversation. Short names are misheard:
    # check `omarchy-voice log` for what whisper actually returns and add the
    # spelling it keeps giving you as a second word ("oma ohma") rather than
    # arguing with the transcriber.
    wake_word: str = ""
    # Longest single snippet the wake listener will consider. Kept short: this
    # is one word, not a sentence, and every second here is a second of CPU
    # spent transcribing someone's unrelated conversation.
    wake_max_seconds: float = 4.0

    # Path to a whisper.cpp ggml model for the local listeners -- `ask` and the
    # wake word. Empty means use OMARCHY_VOICE_WHISPER_MODEL, which the package
    # wrapper sets; running the module directly sets neither, which is why
    # `doctor` names it rather than failing at the microphone.
    whisper_model: str = ""

    # --- hands -------------------------------------------------------------
    allow_shell: bool = False
    # Whether the desktop's notifications are recorded and readable.
    #
    # On, because the alternative is worse for exactly the privacy this costs:
    # without it "what was that notification" is answered by `read_screen`,
    # which sends a picture of the whole desktop -- every other window included
    # -- to OpenAI to recover four words. This sends the four words.
    #
    # It does mean notification bodies are written to
    # ~/.local/state/omarchy-voice/notifications.jsonl, message previews and
    # all, and that whatever the model is asked about goes to the API. Off
    # records nothing and does not offer the tool.
    allow_notifications: bool = True
    confirm_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_CONFIRM))
    deny_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_DENY))
    confirm_patterns_replace: bool = False
    deny_patterns_replace: bool = False
    confirm_words: list[str] = field(default_factory=lambda: ["confirm", "yes do it", "go ahead"])
    cancel_words: list[str] = field(default_factory=lambda: ["cancel", "never mind", "nevermind"])

    # --- mouth -------------------------------------------------------------
    notify: bool = True
    speak: bool = False  # TTS replies, needs piper or espeak-ng
    tts_command: str = ""

    # --- misc --------------------------------------------------------------
    dry_run: bool = False
    verbose: bool = False
    unknown_keys: list[str] = field(default_factory=list)
    retired_keys: list[str] = field(default_factory=list)


def load(path: Path | None = None, **overrides) -> Config:
    """Load config.toml, then apply keyword overrides (CLI flags win)."""
    path = path or CONFIG_FILE
    data: dict = {}
    if path.exists():
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
        for key, value in raw.items():
            if isinstance(value, dict):
                prefix = f"{key}_" if key in PREFIXED_SECTIONS else ""
                data.update({f"{prefix}{k}": v for k, v in value.items()})
            else:
                data[key] = value

    known = {f.name for f in Config.__dataclass_fields__.values()}
    unknown = sorted(set(data) - known - set(RETIRED_KEYS))
    cfg = Config(**{k: v for k, v in data.items() if k in known})
    cfg.unknown_keys = unknown
    cfg.retired_keys = sorted(set(data) & set(RETIRED_KEYS))

    for key, builtin in LIST_UNION_KEYS.items():
        if key in data and not data.get(f"{key}_replace", False):
            merged = list(dict.fromkeys([*builtin, *data[key]]))
            cfg = replace(cfg, **{key: merged})

    return replace(cfg, **{k: v for k, v in overrides.items() if v is not None})


def _secure_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def ensure_dirs() -> None:
    for directory in (CONFIG_DIR, CACHE_DIR, STATE_DIR, RUNTIME_DIR):
        _secure_mkdir(directory)


def dir_is_private(path: Path) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(st.st_mode)
        and (st.st_mode & 0o077) == 0
        and st.st_uid == os.getuid()
    )


def warn_env_permissions(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
