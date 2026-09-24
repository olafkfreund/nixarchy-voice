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
#
# Each rule has a name, which is what `confirm_patterns_remove` takes (#109).
# Names are an interface: once shipped a name never changes, though the
# pattern under it may be rewritten.
DEFAULT_CONFIRM_RULES = {
    "shutdown": r"\bshutdown\b",
    "reboot": r"\breboot\b",
    "poweroff": r"\bpoweroff\b",
    "suspend": r"\bsuspend\b",
    "hibernate": r"\bhibernat",
    "omarchy-update": r"\bomarchy\s+update\b",
    "omarchy-drive": r"\bomarchy\s+drive\b",
    "omarchy-pkg": r"\bomarchy\s+pkg\b",
    "omarchy-install": r"\bomarchy\s+install\b",
    "omarchy-refresh": r"\bomarchy\s+refresh\b",
    "omarchy-reinstall": r"\bomarchy\s+reinstall\b",
    "hyprland-exit": r"\bhl\.dsp\.exit\b",
    "close-all": r"\bclose[-_ ]?all\b",
    # Recoverable — the previous generation is still in the boot menu — but it
    # swaps the running system out from under whoever is talking.
    "nixos-rebuild": r"\bnixos-rebuild\b",
    "home-manager-switch": r"\bhome-manager\s+switch\b",
    "nixarchy-apply": r"\bnixarchy-apply\b",
    "nix-flake-update": r"\bnix\s+flake\s+update\b",
}
DEFAULT_CONFIRM = list(DEFAULT_CONFIRM_RULES.values())

# Never run, whatever the model decides. A voice channel is an open microphone;
# anything on this list is not worth the tail risk of a misheard sentence.
# Named like the confirm rules; `deny_patterns_remove` takes the names.
DEFAULT_DENY_RULES = {
    "rm-rf": r"\brm\s+-[a-zA-Z]*[rf]",
    "mkfs": r"\bmkfs\b",
    "dd": r"\bdd\s+if=",
    "shred-wipefs": r"\b(shred|wipefs)\b",
    "write-block-device": r">\s*/dev/[sn][dv]",
    "passwd": r"\bpasswd\b",
    "sudo": r"\bsudo\b",
    "pkexec": r"\bpkexec\b",
    "cryptsetup": r"\bcryptsetup\b",
    "curl-pipe-shell": r"\bcurl\b.*\|\s*(ba)?sh",
    "git-push": r"\bgit\s+push\b",
    "ssh": r"\bssh\b",
    # Garbage collection is the `rm -rf` of a NixOS machine: it removes the old
    # generations, which are the only way back from a bad rebuild. It needs no
    # sudo for the user profile and contains none of the words above, so
    # without these it walked straight through the gate.
    "nix-collect-garbage": r"\bnix-collect-garbage\b",
    "nix-store-gc": r"\bnix\s+store\s+(delete|gc)\b",
    "nix-store-delete": r"\bnix-store\s+--delete\b",
    "nix-profile-wipe-history": r"\bnix\s+profile\s+wipe-history\b",
    "nix-env-delete-generations": r"\bnix-env\s+--delete-generations\b",
    # Secret paths (#100). A heuristic, like DEFAULT_SENSITIVE: a symlink or an
    # unlisted name gets past it. Paths, not words, so a lookup for "shadow"
    # or "secret" still runs.
    "secret-shadow": r"/etc/g?shadow\b",                  # password hashes, shadow- backup
    "secret-ssh-dir": r"/\.ssh(/|\b)",                    # keys, authorized_keys, known_hosts
    "secret-gnupg": r"/\.gnupg(/|\b)",                    # private-keys-v1.d, trustdb
    "secret-agenix-sops": r"/run/(agenix|secrets)(\.d)?(/|\b)",  # agenix / sops-nix; not /run/user
    "secret-dotenv":
        r"""(^|[\s/"'=])[\w-]*\.env(\.local|\.production|\.development)?(?=$|[\s"';|&)])""",
                                                          # .env, secrets.env, .env.local;
                                                          # not .env.example, .envrc,
                                                          # environment.py, process.env.X
    "secret-ssh-key": r"\bid_(rsa|ecdsa|ed25519|dsa)\b(?!\.pub)",  # SSH private keys outside ~/.ssh
    "secret-login-stores": r"/\.(netrc|git-credentials|pgpass)\b",  # plaintext login stores
    "secret-aws": r"/\.aws/credentials\b",                # cloud keys
    "secret-gh-token": r"/\.config/gh/hosts\.yml\b",      # GitHub CLI token
    "secret-claude-login": r"/\.claude/\.credentials\.json\b",  # Claude Code's own login
    "secret-pass-store": r"/\.password-store(/|\b)",      # pass store (names are the inventory)
    "secret-keyrings": r"/\.local/share/keyrings(/|\b)",  # GNOME keyring files
    # Agent credential and MCP config files (#144). Login tokens, and MCP server
    # tables whose env / headers can hold tokens. Paths, not words.
    "secret-claude-config": r"""(^|[\s/"'=])\.claude\.json\b""",  # ~/.claude.json and every backup
    "secret-codex": r"/\.codex/(auth\.json|config\.toml)\b",      # not AGENTS.md, skills/
    "secret-gemini": r"/\.gemini/(oauth_creds|gemini-credentials|settings)\.json\b",  # not GEMINI.md
    "secret-copilot": r"/\.config/github-copilot(/|\b)",          # apps.json token, auth.db
    "secret-claude-desktop": r"/\.config/claude(/|\b)",           # Electron profile: cookies, tokens
    "secret-opencode": r"/(\.local/share/opencode/(mcp-)?auth|\.config/opencode/opencode)\.jsonc?\b",
}
DEFAULT_DENY = list(DEFAULT_DENY_RULES.values())


# Keys that used to mean something. Kept out of `unknown_keys` so an existing
# config does not get reported as full of typos, and named in `doctor` so the
# user learns why the setting stopped having an effect rather than wondering.
REALTIME_REMOVED = "the OpenAI realtime engine was removed in 2.0.0 (#121)"
RETIRED_KEYS = {
    "mode": "listening is toggle-only now; there is no always-on mode",
    "realtime_model": REALTIME_REMOVED,
    "realtime_voice": REALTIME_REMOVED,
    "realtime_turn_detection": REALTIME_REMOVED,
    "realtime_sample_rate": REALTIME_REMOVED,
    "realtime_transcribe_model": REALTIME_REMOVED,
    "history_items": REALTIME_REMOVED,
    "silence_gate": REALTIME_REMOVED,
    "silence_hold_seconds": REALTIME_REMOVED,
}


# Windows whose contents must never reach a capture. Matched against the
# window class and the title, because neither alone is enough: a Gmail window
# on this desktop reports class `chrome-<extension id>-Profile_4`, which
# identifies nothing, while `pinentry` and `gcr-prompter` have generic titles
# and are identified only by class.
#
# Seeded from omarchy-hermes-companion (MIT, Philippe Sthely),
# daemon/perception.py, which solves the same problem for an always-on
# screen-watcher. A blocklist is a heuristic: it misses things and it misfires.
DEFAULT_SENSITIVE = [
    ("a password manager",
     r"1password|bitwarden|keepass|keepassxc|proton.?pass|gnome-keyring|seahorse"),
    ("a credential prompt",
     r"polkit|pinentry|gcr-prompter|kwalletd|hyprlock|omarchy-lock|swaylock"),
    ("a private browsing window",
     r"private browsing|incognito|inprivate|private window|navigation priv"),
    ("a credential or one-time code",
     r"password|passcode|2fa|one-time|\botp\b"),
    ("a banking or payment page",
     r"\bbank\b|banque|revolut|paypal|stripe dashboard|credit card|carte bancaire"),
]

# Just the patterns, for the union machinery below. A user's additions are
# matched the same way but reported without a category, because we do not know
# what they added.
DEFAULT_SENSITIVE_PATTERNS = [pattern for _kind, pattern in DEFAULT_SENSITIVE]
# Names for `sensitive_patterns_remove` (#109). strict: a sixth category
# without a name fails at import rather than going unremovable.
DEFAULT_SENSITIVE_RULES = dict(zip(
    ("password-manager", "credential-prompt", "private-browsing", "credential-text",
     "banking"),
    DEFAULT_SENSITIVE_PATTERNS, strict=True))

# Lines of a tmux pane that look like a secret, withheld before the model sees
# the pane (#101). A heuristic, like DEFAULT_SENSITIVE: it misses a plain
# password on a line by itself and it may one day misfire. Deliberately not a
# Config field. If a key is ever added, dropping a default that misfires takes
# #109's `*_remove` shape (drop one named default, keep the rest), never a
# `*_replace`. Case-sensitive; checked in this order, first match wins.
TERMINAL_SECRETS = [
    ("a token",
     r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_\w{22,}|sk-ant-[\w-]{20,}"
     r"|sk-(?:proj-)?[\w-]{20,}|xox[abposr]-[A-Za-z0-9-]{10,}|(?:AKIA|ASIA)[A-Z0-9]{16}"
     r"|AIza[\w-]{35}|glpat-[\w-]{20,})"),
    ("a private key", r"AGE-SECRET-KEY-1[0-9A-Z]{58}"),
    ("a password in a URL", r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"),
    ("a secret setting",
     r"^\s*(?:export\s+)?[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|PRIVATE_?KEY"
     r"|ACCESS_?KEY)[A-Z0-9_]*=['\"]?[A-Za-z0-9+/_.~-]{8,}['\"]?\s*$"),
]
# A PEM private key spans lines. Age *armor* is ciphertext and does not match.
TERMINAL_PEM_BEGIN = r"^\s*-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----\s*$"
TERMINAL_PEM_END = r"^\s*-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----\s*$"
TERMINAL_PEM_BODY = r"^\s*(?:[A-Za-z0-9+/=]{16,}|[A-Za-z-]+: .*|)\s*$"


# Sections whose keys are namespaced rather than flattened, because the plain
# names are already taken by another section.
PREFIXED_SECTIONS = {"realtime", "elevenlabs"}

# List-valued policy keys union with the built-in rules unless the matching
# `*_replace` flag is set. `*_remove` drops built-in rules by name first (#109).
# Unknown keys are kept so doctor can report typos.
LIST_UNION_KEYS = {
    "confirm_patterns": DEFAULT_CONFIRM_RULES,
    "deny_patterns": DEFAULT_DENY_RULES,
    "sensitive_patterns": DEFAULT_SENSITIVE_RULES,
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
    # This is the typed path only, `say` and `--dry-run`, which is also the one
    # you want working when the API is down or the account is out of credit.
    # The daemon thinks with Claude whatever this is set to.
    base_url: str = "https://api.openai.com/v1"
    # Tool rounds allowed on one spoken instruction before the assistant has to
    # be asked again. A goal worked properly is a loop — act, wait, look, act —
    # so 8 ran out halfway through anything with three steps in it and the user
    # had to say "carry on". Each round costs a turn against the per-minute
    # token budget, which is why this is 12 and not 30.
    max_turns: int = 12

    # Which brain answers `say`/`ask`: "auto" | "claude-code" | "chat".
    #
    # "chat" is Planner, above — OpenAI Chat Completions, billed per token.
    # "claude-code" hands the same instruction to Claude Code itself over the
    # Claude Agent SDK, which gives the model our tools plus Claude Code's
    # Read and ToolSearch, gated by our policy through a PreToolUse hook, and
    # bills against the Claude subscription instead of API credit. "auto" (the
    # default) uses claude-code when it is ready to run and falls back to
    # chat otherwise, so a broken or unconfigured CLI never makes `say` fail.
    claude_backend: str = "auto"
    # MUST be a full model id (e.g. "claude-sonnet-5"), never a bare alias —
    # an alias can silently resolve to an older model through the CLI, which
    # is a hard-to-notice regression for a voice assistant.
    claude_model: str = "claude-sonnet-5"
    # Working directory handed to Claude Code, and so what its Read tool
    # sees. Empty means $HOME.
    claude_cwd: str = ""
    # Whether to make the Claude Code subprocess use your claude.ai login
    # rather than an API key, by blanking ANTHROPIC_API_KEY for it alone.
    #
    # On by default because it is the reason this backend exists. The CLI
    # prefers an API key over the subscription whenever one is set, so on a
    # machine that exports ANTHROPIC_API_KEY for anything else, every spoken
    # turn quietly went to metered billing instead of the plan.
    #
    # Turn it off if you have an API key and no subscription -- then the key is
    # the only thing that can authenticate and blanking it breaks the backend.
    claude_use_subscription: bool = True
    # How long to wait when testing whether a backend's endpoint is reachable,
    # before deciding it is not. Every turn pays this once when a backend is
    # unreachable, so it is short: the fallback answering slowly is better than
    # the preferred backend answering never.
    reachability_timeout: float = 1.5
    # Explicit override for the `claude` binary. Search order is the
    # OMARCHY_VOICE_CLAUDE_CLI env var, then this, then `shutil.which("claude")`
    # — set this only when the CLI is not on PATH and an env var is
    # inconvenient (e.g. it was installed somewhere PATH does not reach).
    claude_cli: str = ""

    # --- ears --------------------------------------------------------------
    # There is no mode. Listening is off when the daemon starts and only the
    # toggle turns it on — see RETIRED_KEYS. An always-on microphone is not a
    # setting worth having (the wake word below is the opt-in exception).
    device: str = ""  # PipeWire target; empty means the default source
    # Whether the microphone stays live while she is speaking.
    #
    # Off by default, and the default matters: with speakers, her voice leaves
    # the room and comes back into an open mic, and is transcribed as the
    # user — a session log has her saying "OH-mah, OH-mah, OH-mah", hearing it back as
    # "어마", and answering herself. Worse, a stray fragment that transcribes as
    # an instruction gets *run*: one arrived as "Бела." and pressed CTRL+R.
    #
    # Turn it on if you wear headphones, or if you have set up PipeWire's
    # echo-cancel module — then you get to interrupt her mid-sentence, which is
    # the thing this costs.
    barge_in: bool = False
    # How long after she stops a spoken "confirm" or "cancel" is taken as the
    # user's on the local engine (#86). Measured from the return of the last
    # pw-cat, which already includes the 0.35 s echo tail. A room calibration
    # knob: raise it if the log shows `consent  refused` lines that were
    # echoes. A tts_command that backgrounds its own playback returns early,
    # so this starts too soon; the check against what she said still holds.
    spoken_confirm_guard_seconds: float = 1.0

    # Loudness below which a frame counts as room tone, on the same 0..1 curve
    # the orb uses. `frame_level` already returns exactly 0.0 for silence and
    # room tone, so this is margin above that, not the floor itself.
    silence_level: float = 0.02
    # Local engine: this much quiet ends a sentence, and then it is transcribed.
    # Every turn starts with it, so it is dead air by construction. Raise it if
    # you are cut off mid-sentence; the log's `heard` lines show where.
    # 0.6 s since #138; 0.8 s before.
    end_of_speech_seconds: float = 0.6
    # Stop capturing after this long with nothing said, as if the toggle had
    # been pressed. Listening is a mode you enter and forget: without this,
    # walking away from an open microphone streams the room until you come back.
    # The daemon stays up, so resuming is immediate. 0 disables it.
    idle_stop_seconds: int = 600

    # --- engine (the [realtime] section, #121) -----------------------------
    # Lives under [realtime] in the config file; the loader prefixes that
    # section's keys. Which engine `omarchy-voice run` starts: "local" is the
    # only one -- whisper.cpp on this CPU, Claude Code on your subscription,
    # and ElevenLabs (or piper) for the voice. "openai" is refused: the OpenAI
    # realtime engine was removed in 2.0.0 (#121). Anything else runs local,
    # and doctor flags it.
    realtime_engine: str = "local"

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
    # Longest single snippet the wake listener will consider. It counts from
    # the moment the recorder opens, not from the first word, and "Oma, turn it
    # down" in one breath has to fit (#72). A recording that hits this cap is
    # never acted on, only wakes listening -- its end may be missing.
    wake_max_seconds: float = 8.0
    # Extra words whisper should expect, comma-separated: names it keeps
    # mishearing. The wake word, Claude, Hyprland, Omarchy and the coding
    # agents are always included.
    whisper_vocabulary: str = ""

    # Path to a whisper.cpp ggml model for the local listeners -- `ask` and the
    # wake word. Empty means use OMARCHY_VOICE_WHISPER_MODEL, which the package
    # wrapper sets; running the module directly sets neither, which is why
    # `doctor` names it rather than failing at the microphone.
    whisper_model: str = ""

    # --- hands -------------------------------------------------------------
    allow_shell: bool = False
    # Whether each finished task's timings go to session.log, so a turn that
    # felt slow in real use can be explained afterwards rather than guessed at.
    # Off, because a bench measures only the tasks somebody scripted and this
    # is the honest alternative -- but it is still a thing recording what you
    # were doing, and that is opt-in here (see allow_notifications below).
    #
    # It records phase names and durations. Not what was on screen, not the
    # window, not the tool's arguments: the emitter has no parameter those
    # could arrive through. See trace.py.
    trace_timings: bool = False
    # Whether the measured fixed commands are run before the model is asked
    # (#71): workspace N, open a terminal, what windows are open, and
    # focus/move/close one named window. Off turns every sentence back to the
    # model, with no rebuild.
    router: bool = True
    # Whether the brain may drive the desktop through ai-mirror: the real
    # mouse, the real keyboard, the real screen. Off, because ai-mirror being
    # installed is not a decision to hand any of that over -- and when it is
    # on, ai-mirror still asks a human before control (ai-mirror#10).
    desktop_control: bool = False
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
    #
    # Off by default all the same: the argument above is a good reason to turn
    # it on, not a reason to have chosen it for someone. Writing every message
    # preview that crosses the desktop to disk is a decision its owner makes.
    allow_notifications: bool = False
    confirm_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_CONFIRM))
    deny_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_DENY))
    confirm_patterns_replace: bool = False
    deny_patterns_replace: bool = False
    sensitive_patterns: list[str] = field(
        default_factory=lambda: list(DEFAULT_SENSITIVE_PATTERNS))
    sensitive_patterns_replace: bool = False
    # Built-in rules to drop, by name (#109). Names, never patterns: a name
    # stays put when upstream rewrites the pattern under it.
    confirm_patterns_remove: list[str] = field(default_factory=list)
    deny_patterns_remove: list[str] = field(default_factory=list)
    sensitive_patterns_remove: list[str] = field(default_factory=list)
    # Refuse a capture while the screen is being recorded or shared: a read
    # during a screencast lands in a video somebody else will watch. Off for
    # the one person most likely to meet it -- somebody recording a
    # demonstration of this assistant, who wants exactly those captures.
    refuse_while_recording: bool = True
    confirm_words: list[str] = field(default_factory=lambda: ["confirm", "yes do it", "go ahead"])
    cancel_words: list[str] = field(default_factory=lambda: ["cancel", "never mind", "nevermind"])

    # --- mouth -------------------------------------------------------------
    notify: bool = True
    speak: bool = False  # TTS replies, needs piper or espeak-ng
    tts_command: str = ""

    # --- elevenlabs ----------------------------------------------------------
    # A cloud voice, and therefore the one thing here that can fail. Piper stays
    # wired underneath: any failure -- no key, no network, quota gone -- falls
    # back to the local voice and logs why. Degrade, never mute.
    elevenlabs_enabled: bool = False
    # The voice to speak in. An opaque id from YOUR account, not a name:
    # `omarchy-voice voices` lists them. There is no sensible default, and a
    # guessed one would be somebody else's voice or nobody's.
    elevenlabs_voice_id: str = ""
    # Where the API key lives. Never a file in this repo and never the config:
    #   secret-tool store --label omarchy-voice service omarchy-voice-elevenlabs
    # ELEVENLABS_API_KEY works as a last resort, but an export in a shell
    # profile is a plaintext key on disk, which is what the keyring avoids.
    elevenlabs_key_slot: str = "omarchy-voice-elevenlabs"
    # Turbo for English. Not the multilingual model and not style > 0: both
    # make delivery slower and duller, which is the wrong trade for a desktop
    # assistant answering in one sentence.
    elevenlabs_model: str = "eleven_turbo_v2_5"
    elevenlabs_stability: float = 0.5
    elevenlabs_similarity: float = 0.75
    # Mastering applied locally with ffmpeg. Their site previews are mastered
    # demo clips and raw API output never matches them, so the voice you
    # audition on the website is not the voice you get without this.
    # A fixed gain, then a limiter at -1.5 dBFS (0.84), so the clip can be
    # played as it arrives (#135). The gain is the mean that brings the
    # owner's five calibration clips to -16 LUFS integrated, rounded to
    # 0.5 dB. `master = "loudnorm=I=-16:TP=-1.5:LRA=11"` under [elevenlabs]
    # restores the old sound, and the old wait: loudnorm holds its output
    # for a 3 s look-ahead, which on one sentence is the whole clip.
    # G pending: owner calibration on #135
    elevenlabs_master: str = "volume=0dB,alimiter=limit=0.84:level=0"

    # --- misc --------------------------------------------------------------
    dry_run: bool = False
    verbose: bool = False
    unknown_keys: list[str] = field(default_factory=list)
    retired_keys: list[str] = field(default_factory=list)
    # Set by load(): (ok, text) about removed or replaced built-in rules, for
    # doctor and the start-up log.
    policy_notes: list[tuple[bool, str]] = field(default_factory=list)
    # Set by load(): whether allow_notifications came from the file at all. Not
    # a key anyone writes -- it is how the one-time notice knows to stay quiet
    # for someone who already chose.
    allow_notifications_explicit: bool = False


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
    cfg.allow_notifications_explicit = "allow_notifications" in data
    cfg.unknown_keys = unknown
    cfg.retired_keys = sorted(set(data) & set(RETIRED_KEYS))

    notes: list[tuple[bool, str]] = []
    for key, rules in LIST_UNION_KEYS.items():
        if not (key in data or f"{key}_remove" in data):
            continue
        label = key.removesuffix("_patterns")
        remove = data.get(f"{key}_remove", [])
        if not (isinstance(remove, list) and all(isinstance(n, str) for n in remove)):
            notes.append((False, f"{key}_remove must be a list of rule names; ignored"))
            remove = []
        remove = list(dict.fromkeys(remove))
        if data.get(f"{key}_replace"):
            merged = getattr(cfg, key)
            if remove:
                notes.append((False, f"{key}_remove is ignored because {key}_replace = true"))
            missing = [n for n, p in rules.items() if p not in merged]
            if missing:
                notes.append((False, f"{key}_replace: built-in rules not in your list: "
                                     f"{', '.join(missing)}"))
        else:
            unknown = [n for n in remove if n not in rules]
            if unknown:
                notes.append((False, f'{key}_remove: no built-in rule named '
                                     f'{", ".join(unknown)} (names: README "Rule names")'))
            known = set(remove) & rules.keys()
            if known == rules.keys():
                # An empty policy by subtraction is more likely a mistake than a
                # wish; `*_replace` is the way to say it on purpose.
                notes.append((False, f"{key}_remove names every built-in {label} rule; "
                                     f"not applied"))
                known = set()
            merged = list(dict.fromkeys(
                [*(p for n, p in rules.items() if n not in known), *data.get(key, [])]))
            if known:
                notes.append((True, f"{label} rules removed: "
                                    f"{', '.join(n for n in rules if n in known)}"))
        cfg = replace(cfg, **{key: merged, f"{key}_remove": remove})
    cfg.policy_notes = notes

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
