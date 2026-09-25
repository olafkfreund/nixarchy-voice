"""Build the capability manifest handed to the model.

The point of this module: never hardcode desktop API syntax. Hyprland's Lua
dispatcher API changed shape in 0.56 (``hyprctl dispatch workspace 1`` is now
``hl.dsp.focus({ workspace = "1" })``), and Omarchy's CLI grows every release.
So the manifest is read off the running system:

  * ``/usr/share/hypr/stubs/hl.meta.lua``      -> the dispatcher tree
  * ``/usr/share/omarchy/default/hypr/bindings/`` -> real, version-correct call syntax
  * ``omarchy commands --json``                -> the CLI surface
  * ``hyprctl`` + desktop entries              -> what exists on *this* machine

It is cached, keyed on the versions of the things it was built from, so a
system update rebuilds it and nothing else has to change.
"""

from __future__ import annotations

import contextlib
import difflib
import functools
import gzip
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from .config import CACHE_DIR, app_dirs

# Omarchy itself exports OMARCHY_PATH into the session, pointing at the store
# tree it was built from.
OMARCHY_PATH = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy"))

# There is deliberately no HL_STUB constant. It used to be one, resolved at
# import from a single environment variable with a /usr/share default that
# does not exist on NixOS -- so it answered "which stub" once, early, and
# wrongly. `_stub_path()` answers it on demand and in order of authority, and
# `_namespaces()` prefers the running compositor over any file (#64).

# Omarchy command groups a voice assistant actually reaches for.
#
# An allow list, not a skip list. The skip list this replaces named seven groups
# and let the other fifty through, which is how the CLI section grew to 15 KB —
# more than half the manifest — on installer plumbing, hardware probes and
# plugin management, none of which anyone says out loud. Omarchy adds groups
# every release and they are far more often plumbing than speech, so the default
# for an unrecognised group should be "leave it out".
#
# Deliberately absent: `install`, `update`, `pkg`, `refresh`, `restart`,
# `migrate`, `drive`. Those are all held by the confirmation gate anyway, and
# listing them invites the model to reach for them.
#
# Also deliberately absent: `voice`. Those routes are this program, and they
# exist for the person at the terminal. Handing the model `omarchy voice stop`
# gives it a way to hang up on the conversation it is having.
VOICE_GROUPS = {
    "audio", "bar", "bluetooth", "brightness", "capture", "display", "file",
    "font", "games", "launch", "menu", "monitor", "network", "notification",
    "osd", "power", "powerprofiles", "reminder", "screensaver", "share", "show",
    "system", "theme", "toggle", "tui", "voxtype", "weather", "webapp",
}

# Long enough to disambiguate two similar routes, short enough that 128 of them
# do not cost more than everything else in the manifest put together.
SUMMARY_CHARS = 44

# Only short routes get a summary. Omarchy's routes are English, and a long one
# has already said what it does: `omarchy audio output volume <raise|lower|...>`
# gains nothing from "Adjust the output volume" after it. A two-word route like
# `omarchy capture qr` has not, so it keeps one.
#
# This is not cosmetic. Every token here is spent again on every single turn —
# cached tokens still count against the API's tokens-per-minute limit — so the
# manifest's size is directly how many things the user can say in a minute.
SUMMARY_MAX_SEGMENTS = 2


def _run(cmd: list[str], timeout: float = 10.0) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def system_versions() -> dict[str, str]:
    return {
        "omarchy": _run(["omarchy", "version"]) or "unknown",
        "hyprland": _hyprland_version(),
    }


_VERSIONS: dict[str, str] | None = None


def _versions() -> dict[str, str]:
    """The versions, read once per process for the cache key (#111).

    A reading with an "unknown" in it is not kept, so it is read again."""
    global _VERSIONS
    if _VERSIONS is not None:
        return _VERSIONS
    versions = system_versions()
    if "unknown" not in versions.values():
        _VERSIONS = versions
    return versions


def _hyprland_version() -> str:
    """Just "Hyprland 0.56.0".

    `hyprctl version` leads with "built from branch unknown at commit <sha>
    dirty (unknown)" — every package built outside a git checkout says that,
    which on NixOS is all of them. It costs ~25 tokens of the manifest on every
    single turn, tells the model the compositor is "dirty" when it is not, and
    changes the cache key on rebuilds of the very same version.
    """
    first = (_run(["hyprctl", "version"]).splitlines() or ["unknown"])[0]
    return first.split(" built from")[0].strip() or "unknown"


@functools.lru_cache(maxsize=4)
def _parse_stub(path: str, mtime_ns: int) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """The hl.dsp namespace, as ((namespace, members), ...), sorted.

    Keyed on the stub's path and mtime rather than on `_cache_key()`, which
    shells out to `hyprctl version` and `omarchy version`: `dispatchers()` is
    consulted on every dispatch, and two subprocesses per window move is the
    kind of thing this file exists to avoid. The stub is the only input, so its
    mtime is the whole of the key.

    Returns tuples rather than a dict so an lru_cache hit cannot hand a caller
    something it can mutate under the next one.
    """
    namespaces: dict[str, list[str]] = {}
    current: str | None = None
    for line in Path(path).read_text(errors="replace").splitlines():
        cls = re.match(r"---@class HL\.Dsp(\w*)Namespace", line)
        if cls:
            current = cls.group(1).lower() or "root"
            namespaces.setdefault(current, [])
            continue
        if current is None:
            continue
        field = re.match(r"---@field (\w+) fun\(", line)
        if field:
            namespaces[current].append(field.group(1))
        elif not line.startswith("---@field"):
            current = None
    return tuple((name, tuple(sorted(namespaces[name]))) for name in sorted(namespaces))


def _stub_path() -> Path | None:
    """The first stub that exists, in order of how much it is to be trusted.

    An explicit OMARCHY_VOICE_HL_STUB means somebody CHOSE this file, so it
    beats everything including the live compositor: pointing it at a file is
    what you do to debug, and silently ignoring that wastes exactly the time
    the variable exists to save.

    OMARCHY_VOICE_HL_STUB_FALLBACK is a different claim -- "this is what the
    packaging shipped" -- and loses to the running system. They used to share
    one name, set with --set-default, which is why an explicit-first rule could
    never reach the system's own stub: the wrapper had always already set it.
    """
    candidates = [
        os.environ.get("OMARCHY_VOICE_HL_STUB"),
        "/run/current-system/sw/share/hypr/stubs/hl.meta.lua",
        f"/etc/profiles/per-user/{os.environ.get('USER', '')}/share/hypr/stubs/hl.meta.lua",
        str(Path.home() / ".nix-profile/share/hypr/stubs/hl.meta.lua"),
        os.environ.get("OMARCHY_VOICE_HL_STUB_FALLBACK"),
        "/usr/share/hypr/stubs/hl.meta.lua",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate)
    return None


def _stub_namespaces() -> tuple[tuple[str, tuple[str, ...]], ...]:
    path = _stub_path()
    if path is None:
        return ()
    try:
        return _parse_stub(str(path), path.stat().st_mtime_ns)
    except OSError:
        return ()


# Walks hl.dsp and prints the dotted name of every function under it. A plain
# constant, never built from anything: #22 is about Lua not being assembled
# from input, and the way to keep that checkable is for there to be exactly one
# literal to look at.
_LIVE_DISPATCHER_LUA = """
local out = {}
local function walk(t, prefix)
  for k, v in pairs(t) do
    if type(v) == "function" then out[#out+1] = prefix .. k
    elseif type(v) == "table" then walk(v, prefix .. k .. ".") end
  end
end
walk(hl.dsp, "")
table.sort(out)
return table.concat(out, ",")
"""

# Below this, assume the walk was truncated rather than believe it. Measured:
# the bare top level of hl.dsp is 20 entries and the full walk is 51, so 20 is
# the largest value a metatable hiding the sub-tables could not reach. If
# hl.dsp ever gains an __index, pairs() returns a fraction of the set and every
# missing dispatcher starts being refused -- silently, which is the one failure
# mode this file exists to prevent.
_MIN_PLAUSIBLE_DISPATCHERS = 20


@functools.lru_cache(maxsize=1)
def _live_namespaces() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """The dispatcher set as the RUNNING compositor reports it, or ().

    Authoritative in a way no file can be: it is not a description of the
    compositor, it is the compositor. The stub this used to read is pinned by
    this repo's nixpkgs and has no relationship to the Hyprland a user runs;
    the two agreeing was luck (#64).

    Cached for the process lifetime. The set changes only when Hyprland
    restarts, and that takes the session with it.
    """
    out = _run(["hyprctl", "repl", _LIVE_DISPATCHER_LUA], timeout=5.0)
    if not out or out.startswith("error:") or "(" in out.splitlines()[0]:
        return ()
    names = [n.strip() for n in out.strip().split(",") if n.strip()]
    if len(names) < _MIN_PLAUSIBLE_DISPATCHERS:
        return ()
    grouped: dict[str, list[str]] = {}
    for name in names:
        namespace, _, member = name.rpartition(".")
        grouped.setdefault(namespace or "root", []).append(member)
    return tuple((k, tuple(sorted(v))) for k, v in sorted(grouped.items()))


def _namespaces() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Where the dispatcher set comes from, in order of authority.

    An explicitly chosen stub, then the running compositor, then whatever stub
    can be found. Empty only when nothing answers, which callers must treat as
    "cannot validate" and refuse (#22).
    """
    if os.environ.get("OMARCHY_VOICE_HL_STUB"):
        chosen = _stub_namespaces()
        if chosen:
            return chosen
    return _live_namespaces() or _stub_namespaces()


def dispatcher_tree() -> str:
    """Parse the hl.dsp namespace out of Hyprland's own LuaLS stub."""
    lines = []
    for name, members in _namespaces():
        prefix = "hl.dsp." if name == "root" else f"hl.dsp.{name}."
        if members:
            lines.append(f"  {prefix}{{{', '.join(members)}}}")
    return "\n".join(lines)


def dispatchers() -> frozenset[str]:
    """Every dispatcher this Hyprland has, as dotted names: "focus",
    "window.close", "workspace.change_id".

    The allowlist `render_dispatch` validates against. Read off the installed
    compositor, never hardcoded -- 0.56 replaced the string dispatchers with
    this Lua API, and a baked-in list is exactly what that release would have
    stranded.

    Empty when the stub is missing, which callers must treat as "cannot
    validate" and refuse. Falling open here would hand back the hole this
    function exists to close.
    """
    return frozenset(
        member if name == "root" else f"{name}.{member}"
        for name, members in _namespaces()
        for member in members
    )


def dispatch_examples(limit: int = 16) -> str:
    """Harvest real dispatcher calls from Omarchy's own keybindings.

    These are guaranteed-correct for the installed Hyprland: they are what the
    running desktop binds to keys right now.
    """
    bindings = OMARCHY_PATH / "default/hypr/bindings"
    if not bindings.is_dir():
        return ""
    seen: dict[str, str] = {}
    for path in sorted(bindings.glob("*.lua")):
        for line in path.read_text(errors="replace").splitlines():
            match = re.search(r'o\.bind\([^,]+,\s*"([^"]+)"\s*,\s*(hl\.dsp\.[^\n]+?)\)\s*$', line)
            if match:
                desc, call = match.group(1), match.group(2).rstrip(")") + ")"
                seen.setdefault(desc, call)
            else:
                shell = re.search(r'o\.bind\([^,]+,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)', line)
                if shell:
                    seen.setdefault(shell.group(1), f'shell: {shell.group(2)}')
    rows = [f"  {desc}  →  {_as_call(call)}" for desc, call in list(seen.items())[:limit]]
    return "\n".join(rows)


_SCRAPED_RE = re.compile(r'^hl\.dsp\.([\w.]+)\((.*)\)$', re.DOTALL)
_PAIR_RE = re.compile(r'(\w+)\s*=\s*("(?:[^"\\]|\\.)*"|true|false|-?\d+(?:\.\d+)?)')


def _as_call(call: str) -> str:
    """A scraped `hl.dsp.*` call, rewritten as the hypr_dispatch arguments.

    Omarchy's bindings are where version-correct argument *shapes* come from,
    so they stay the source -- but showing them as Lua taught the model to
    write Lua, which hypr_dispatch no longer takes. Anything this cannot parse
    degrades to the bare dispatcher name rather than being dropped: knowing the
    dispatcher exists is worth more than nothing, and knowing a wrong argument
    shape is worth less.
    """
    if call.startswith("shell: "):
        return call
    match = _SCRAPED_RE.match(call.strip())
    if not match:
        return call
    name, inside = match.group(1), match.group(2).strip()
    if not inside:
        return f'dispatcher "{name}"'
    if inside.startswith('"') and inside.endswith('"') and '=' not in inside:
        return f'dispatcher "{name}", message {inside}'
    if not (inside.startswith("{") and inside.endswith("}")):
        return f'dispatcher "{name}"'
    pairs = _PAIR_RE.findall(inside)
    if not pairs:
        return f'dispatcher "{name}"'
    shown = ", ".join(f'"{key}": {value}' for key, value in pairs)
    return f'dispatcher "{name}", args {{{shown}}}' 


def omarchy_commands(limit: int = 120) -> str:
    """The Omarchy CLI surface, straight from `omarchy commands --json`.

    Filtered to what a voice assistant should reach for: no dev/hardware
    plumbing, no hidden commands, and nothing needing a sudo password (a
    background daemon has no terminal to type one into).
    """
    raw = _run(["omarchy", "commands", "--json"], timeout=20)
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    entries = data if isinstance(data, list) else data.get("commands", [])
    rows = []
    for entry in entries:
        route = entry.get("route", "")
        if not route or entry.get("hidden") or entry.get("requires_sudo"):
            continue
        if entry.get("group") not in VOICE_GROUPS:
            continue
        signature = f'{route} {entry.get("args", "")}'.strip()
        # No column padding. Aligning summaries at column 62 spent roughly a
        # fifth of this section on spaces, and the model does not read columns.
        summary = ""
        if len(route.split()) - 1 <= SUMMARY_MAX_SEGMENTS:
            summary = (entry.get("summary") or "")[:SUMMARY_CHARS].strip()
        rows.append(f'  {signature}' + (f'  — {summary}' if summary else ""))
        if len(rows) >= limit:
            break
    return "\n".join(rows)


COMMAND_INDEX = "command-index.tsv"
# ponytail: count bound, LRU via utime on hit; >8 keys written between two
# daemon turns still evicts the daemon.
CACHE_KEEP = 8


def _load(cached: Path) -> str | None:
    """A cache entry's text, or None. A hit is marked recently used."""
    try:
        text = cached.read_text()
    except OSError:  # missing, or pruned by another process since
        return None
    with contextlib.suppress(OSError):
        os.utime(cached)
    return text


def _store(cached: Path, text: str, pattern: str) -> None:
    """Write atomically, then keep only the CACHE_KEEP newest entries matching
    `pattern`. The dot-prefixed temp name matches neither kind's glob, so a
    concurrent pruner never touches a file still being written."""
    tmp = cached.with_name(f".{cached.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, cached)
    entries = []
    for path in CACHE_DIR.glob(pattern):
        try:
            entries.append((path.stat().st_mtime_ns, path))
        except OSError:
            pass
    entries.sort(key=lambda entry: entry[0], reverse=True)
    for _, path in entries[CACHE_KEEP:]:
        if path != cached:
            path.unlink(missing_ok=True)


def command_index(refresh: bool = False) -> list[tuple[str, str]]:
    """Every voice-relevant omarchy route, as (signature, summary).

    This used to be pasted into the manifest — 128 routes, ~2,270 tokens, resent
    on every single turn against a per-minute budget, so that the assistant could
    reach for `omarchy notification dismiss` about once a week. It is now looked
    up on demand by the omarchy_help tool instead. The fifteen things anyone
    actually says out loud stay inline in ESSENTIALS.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / f"{_cache_key()}-{COMMAND_INDEX}"
    text = None if refresh else _load(cached)
    if text is not None:
        rows = []
        for line in text.splitlines():
            signature, _, summary = line.partition("\t")
            if signature:
                rows.append((signature, summary))
        return rows

    raw = _run(["omarchy", "commands", "--json"], timeout=20)
    rows = []
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    for entry in (data if isinstance(data, list) else data.get("commands", [])):
        route = entry.get("route", "")
        if not route or entry.get("hidden") or entry.get("requires_sudo"):
            continue
        if entry.get("group") not in VOICE_GROUPS:
            continue
        rows.append((f'{route} {entry.get("args", "")}'.strip(),
                     (entry.get("summary") or "").strip()))
    _store(cached, "\n".join(f"{sig}\t{summ}" for sig, summ in rows), f"*-{COMMAND_INDEX}")
    return rows


def search_commands(query: str, limit: int = 12) -> list[str]:
    """Routes matching `query`, best first. Every word has to appear somewhere."""
    words = [w for w in re.split(r"\W+", query.lower()) if w]
    if not words:
        return []
    scored = []
    for signature, summary in command_index():
        haystack = f"{signature} {summary}".lower()
        # Any word, not every word. The model asks the way a person would —
        # "dark theme", "turn off the night light" — and requiring all of them
        # returned nothing for "dark theme" because no route says "dark".
        # Matching any, then ranking by how many hit, finds `theme set` first.
        hits = [w for w in words if w in haystack]
        if not hits:
            continue
        # A hit in the route itself beats a hit in the prose describing it.
        score = sum(2 if w in signature.lower() else 1 for w in hits)
        scored.append((-score, len(signature), signature, summary))
    scored.sort()
    return [f"  {sig}" + (f"  — {summ}" if summ else "")
            for _, _, sig, summ in scored[:limit]]


# Words a person wraps around an app's name that are not part of it.
STOP_WORDS = {"open", "launch", "start", "run", "the", "my", "a", "an", "app",
              "please", "up", "me", "for", "program", "application"}
_ENTRY_KEYS = ("Name", "GenericName", "Keywords", "Comment", "Exec", "NoDisplay",
               "Hidden", "OnlyShowIn", "NotShowIn", "Actions")


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def app_index() -> list[dict]:
    """Every app the launcher would show, read off this machine now (#70).

    Scanned on every call rather than cached: 36 ms for 303 entries here, and
    an index that lags an install is worse than a slow one.

    ponytail: a full scan per lookup. If a trace ever shows it, cache on the
    application directories' mtimes.
    """
    desktops = set(os.environ.get("XDG_CURRENT_DESKTOP", "Hyprland").split(":"))
    rows, seen = [], set()
    for root in app_dirs():
        if not root.is_dir():
            continue
        for entry in sorted(root.glob("*.desktop")):
            if entry.stem in seen:
                continue
            # A dangling symlink is normal here: on a store-based distribution
            # every entry is a link, and a collected generation leaves the link
            # behind. One dead link must not take the whole index with it.
            try:
                text = entry.read_text(errors="replace")
            except OSError:
                continue
            seen.add(entry.stem)
            # Only the [Desktop Entry] group: an action group's Name= is
            # "New Window", not the app.
            fields: dict[str, str] = {}
            for line in text.split("\n[", 1)[0].splitlines():
                key, _, value = line.partition("=")
                if key in _ENTRY_KEYS and key not in fields:
                    fields[key] = value.strip()
            if (not fields.get("Name") or fields.get("NoDisplay") == "true"
                    or fields.get("Hidden") == "true"):
                continue
            only = {d for d in fields.get("OnlyShowIn", "").split(";") if d}
            never = {d for d in fields.get("NotShowIn", "").split(";") if d}
            if (only and not only & desktops) or never & desktops:
                continue
            # The program the entry runs, which the gate checks as `launch
            # <program>` (#129): unquoted, and past `env NAME=value ...`.
            try:
                command = shlex.split(fields.get("Exec", ""))
            except ValueError:
                command = fields.get("Exec", "").split()
            if command[:1] == ["env"]:
                command = command[1:]
            while command and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", command[0]):
                command = command[1:]
            rows.append({
                "id": entry.stem, "name": fields["Name"],
                "generic": fields.get("GenericName", ""),
                "keywords": fields.get("Keywords", ""),
                "comment": fields.get("Comment", ""),
                "command": os.path.basename(command[0]) if command else "",
                "actions": [a for a in fields.get("Actions", "").split(";") if a],
            })
    return rows


def _initials(query: list[str], name: list[str]) -> bool:
    """ "vs code" names Visual Studio Code: one word spells the others' initials."""
    for i, word in enumerate(query):
        if len(word) < 2:
            continue
        for start in range(len(name) - len(word) + 1):
            if "".join(w[0] for w in name[start:start + len(word)]) == word:
                rest = query[:i] + query[i + 1:]
                if all(w in name for w in rest):
                    return True
    return False


# A last id part that names a package, toolkit, platform or role, not an app (#96).
GENERIC_ID_PARTS = frozenset({"desktop", "app", "application", "client", "gtk",
                              "qt", "android", "debug", "setup"})


def _id_name(app_id: str) -> str:
    """The last part of a reverse-DNS id when it can be the app's name, else "".

    `dev.zed.Zed` → "zed". Not `org.telegram.desktop` (a generic word), not
    `ubuntu-24.04` (a version), and not `waydroid.<android package>`, whose
    tail is a developer's identifier ("android" is Instagram).
    """
    lowered = app_id.lower()
    tail = lowered.rsplit(".", 1)[-1]
    if (lowered.startswith("waydroid.") or tail in GENERIC_ID_PARTS
            or not any(ch.isalpha() for ch in tail)):
        return ""
    return tail


def find_apps(query: str, limit: int = 8) -> list[tuple[int, dict]]:
    """Installed apps for the name a person uses, best first, with a score.

    100 the whole name, id, command, or the id's last part when it names the
    app (#96); 95 initials; 90 every word in the name; 70 in GenericName or
    Keywords; 40 in Comment; up to 60 for a close spelling, which is what
    whisper hands over ("zedd"). Lexical on purpose: measured at
    20 of 24 real requests, and the one semantic miss ("notes" for Obsidian)
    the model covers once it can look an app up by name (#70).
    """
    said = _words(query)
    wanted = [w for w in said if w not in STOP_WORDS] or said
    if not wanted:
        return []
    phrase = " ".join(wanted)
    scored = []
    for row in app_index():
        name = _words(row["name"])
        ident = {row["id"].lower(), _id_name(row["id"]), row["command"].lower()} - {""}
        described = set(_words(row["generic"])) | set(_words(row["keywords"]))
        if phrase == " ".join(name) or phrase in ident:
            score = 100
        elif _initials(wanted, name):
            score = 95
        elif all(w in name for w in wanted):
            score = max(71, 90 - (len(name) - len(wanted)))
        elif all(w in set(name) | described for w in wanted):
            score = 70
        elif all(w in set(name) | described | set(_words(row["comment"])) for w in wanted):
            score = 40
        else:
            ratio = max(difflib.SequenceMatcher(None, phrase, target).ratio()
                        for target in [" ".join(name), *ident])
            score = int(60 * ratio) if ratio >= 0.8 else 0
        if score:
            scored.append((score, row))
    # On Omarchy "browser" means the configured default, which is preferred-*.
    scored.sort(key=lambda s: (-s[0], not s[1]["id"].startswith("preferred-"),
                               s[1]["name"].lower()))
    return scored[:limit]


def clear_match(results: list[tuple[int, dict]]) -> dict | None:
    """The one app meant, or None when it is a choice ("code", "discord").

    95 and up is the whole name, id or command, or its initials ("vs code").
    A close spelling never launches unasked: "zedd" is a lookup, not a launch.
    """
    if not results or results[0][0] < 95:
        return None
    if len(results) > 1 and results[1][0] >= results[0][0] - 15:
        return None
    return results[0][1]


# Command-line tools on PATH, described from files only (#82). Nothing here runs
# a binary or opens a socket: `--help` would be running whatever is on PATH.
# ponytail: one known tldr cache path (the Python client's); add tealdeer's or
# tlrc's when a machine has one.
TLDR_PAGES = Path.home() / ".cache/tldr/pages"
_PATH_COMMANDS: tuple | None = None   # (stamp, index), rebuilt when the stamp moves
MAN_HEAD = 8192
COMMAND_STOP = STOP_WORDS | {"to", "of", "in", "on", "and", "or", "with", "by", "from",
                             "into", "is", "it", "this", "that", "i", "how", "do",
                             "can", "what", "which", "tool", "command", "some"}
_ALIAS = re.compile(r"alias of `([^`]+)`")
_ROFF = re.compile(r"\\f(\[[^]]*\]|\(..|.)|\\[,/&|^ ]|\\\(..")


def _path_dirs() -> list[str]:
    return [d for d in os.environ.get("PATH", "").split(os.pathsep)
            if d and not d.startswith("/nix/store/")]


def _man_dirs() -> list[str]:
    roots = [d for d in os.environ.get("MANPATH", "").split(os.pathsep) if d]
    # man-db's own rule when MANPATH is unset: <bin>/../share/man.
    roots = roots or [os.path.join(d, "..", "share", "man") for d in _path_dirs()]
    return [os.path.join(r, s) for r in roots for s in ("man1", "man8")]


def _tldr_dirs() -> list[Path]:
    return [TLDR_PAGES / "linux", TLDR_PAGES / "common"]


def _stamp() -> tuple:
    """Where every directory the index reads points now, and when it last changed."""
    stamp = []
    for d in [*_path_dirs(), *_man_dirs(), *map(str, _tldr_dirs())]:
        try:
            stamp.append((os.path.realpath(d), os.stat(d).st_mtime_ns))
        except OSError:
            continue
    return tuple(stamp)


def _roff(text: str) -> str:
    return " ".join(_ROFF.sub("", text.replace("\\-", "-")).split())


def _man_head(path: str) -> list[str]:
    opener = gzip.open if path.endswith(".gz") else open
    try:
        with opener(path, "rt", errors="replace") as f:
            return f.read(MAN_HEAD).splitlines()
    except (OSError, EOFError, gzip.BadGzipFile):
        return []


def _man_section(lines: list[str], name: str) -> list[str]:
    body, inside = [], False
    for line in lines:
        if line.startswith((".SH", ".Sh")):
            if inside:
                break
            inside = line[3:].strip().strip('"').upper() == name
        elif inside:
            body.append(line)
    return body


def _man_desc(lines: list[str]) -> str:
    for line in lines:
        if line.startswith(".Nd "):                     # mdoc
            return _roff(line[4:])
    text = " ".join(l for l in _man_section(lines, "NAME") if not l.startswith("."))
    _, dash, desc = text.partition("\\-")
    return _roff(desc if dash else text.partition(" - ")[2])


def _man_synopsis(lines: list[str]) -> list[str]:
    rows: list[str] = []
    for line in _man_section(lines, "SYNOPSIS"):
        macro, _, rest = line.partition(" ")
        if line.startswith(".") and macro not in (".B", ".I", ".BR", ".BI", ".IR", ".RB"):
            if rows and rows[-1]:
                rows.append("")
            continue
        text = _roff(rest if line.startswith(".") else line)
        if text:
            rows[-1:] = [f"{rows[-1]} {text}".strip()] if rows else [text]
    return [r for r in rows if r][:4]


def _tldr_file(name: str) -> str:
    for folder in _tldr_dirs():
        try:
            return (folder / f"{name}.md").read_text(errors="replace")
        except OSError:
            continue
    return ""


def _tldr_page(name: str, hop: bool = True) -> tuple[str, list[tuple[str, str]]]:
    """A tldr page's description and its (what, command) examples."""
    text = _tldr_file(name)
    desc = [l[2:].strip() for l in text.splitlines() if l.startswith("> ")
            and not l[2:].startswith(("More information", "See also"))]
    alias = _ALIAS.search(" ".join(desc))
    if alias and hop:
        target = _tldr_page(alias.group(1).replace(" ", "-"), hop=False)
        if target[0]:
            return target
    examples, what = [], ""
    for line in text.splitlines():
        if line.startswith("- "):
            what = line[2:].strip().rstrip(":")
        elif line.startswith("`") and what:
            examples.append((what, line.strip().strip("`")))
            what = ""
    return " ".join(desc), examples


def _build_path_commands() -> dict[str, dict]:
    pages: dict[str, str] = {}
    for folder in _man_dirs():
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for file in names:
            stem = file.removesuffix(".gz").rsplit(".", 1)[0]
            pages.setdefault(stem, os.path.join(folder, file))
    index: dict[str, dict] = {}
    for folder in _path_dirs():
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if name in index or name.startswith(("omarchy-", "nixarchy-", ".")):
                continue
            try:
                if not entry.is_file() or not os.access(entry.path, os.X_OK):
                    continue
            except OSError:
                continue
            tldr, examples = _tldr_page(name)
            lines = _man_head(pages[name]) if name in pages else []
            man = _man_desc(lines)
            index[name] = {
                "desc": "; ".join(d for d in (tldr, man) if d),
                "src": "+".join(s for s, d in (("tldr", tldr), ("man", man)) if d),
                "examples": examples, "synopsis": _man_synopsis(lines),
                "path": entry.path,
            }
    return index


def path_commands() -> dict[str, dict]:
    """Every command on PATH, with what the files say it does (#82).

    Built on first use and held in process; rebuilt when any PATH, man or tldr
    directory moves or changes (about 1 ms to check, 0.3 s to rebuild).
    """
    global _PATH_COMMANDS
    stamp = _stamp()
    if _PATH_COMMANDS is None or _PATH_COMMANDS[0] != stamp:
        _PATH_COMMANDS = (stamp, _build_path_commands())
    return _PATH_COMMANDS[1]


def _stems(text: str) -> set[str]:
    # A 5-letter prefix, so "convert" meets "conversion". "colour" still misses "color".
    return {w[:5] for w in _words(text) if w not in COMMAND_STOP}


def find_commands(query: str, limit: int = 8) -> list[tuple[float, str, dict]]:
    """Commands for a name or a purpose, best first, with a score.

    An exact name comes first, described or not. The rest are scored by
    IDF-weighted word overlap with the name's parts and the description, never
    the examples; a match on every word counts 1.5 times. Only described
    commands can be found by purpose.
    """
    index = path_commands()
    exact = query.strip()
    said = _stems(query)
    found: list[tuple[float, str, dict]] = []
    if exact in index:
        found.append((float("inf"), exact, index[exact]))
    if said:
        docs = {name: _stems(name) | _stems(row["desc"])
                for name, row in index.items() if row["desc"] and name != exact}
        idf = {w: math.log(len(index) / (1 + sum(w in d for d in docs.values())))
               for w in said}
        scored = []
        for name, words in docs.items():
            hits = said & words
            if not hits:
                continue
            score = sum(idf[w] for w in hits) * (1.5 if hits == said else 1)
            scored.append((score, name, index[name]))
        scored.sort(key=lambda s: (-s[0], "tldr" not in s[2]["src"], len(s[1]), s[1]))
        found += scored
    return found[:limit]


def close_commands(query: str) -> list[str]:
    """Up to three installed names spelled like `query` ("yt-dl" → yt-dlp)."""
    return difflib.get_close_matches(query.strip(), list(path_commands()), n=3)


# The only two property lists `systemctl show` is ever asked for (#83). Without
# -p it prints Environment= and ExecStart=, which can hold secrets.
_SHOW_EXISTS = "LoadState,ActiveState,UnitFileState,Description"
_SHOW_DETAIL = "LoadState,ActiveState,SubState,UnitFileState,Result,ActiveEnterTimestamp"
_UNIT_NAME_RE = re.compile(r"[A-Za-z0-9@._:-]{1,128}")


def _own_units() -> dict[str, str]:
    """The user's own unit files, name → Description=. No other line is read."""
    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"
    own = {}
    for path in sorted(root.glob("*.service")):
        if path.name.endswith("@.service"):
            continue
        desc = ""
        try:
            for line in path.read_text(errors="replace").splitlines():
                if line.startswith("Description="):
                    desc = line.partition("=")[2].strip()
                    break
        except OSError:
            continue
        own[path.name] = desc
    return own


def user_services() -> tuple[list[dict], bool]:
    """Every user service systemd has loaded, plus the user's own unit files.

    Read on every call, never cached: "is it running" has to be true now.
    The bool is whether systemd answered.
    """
    raw = _run(["systemctl", "--user", "list-units", "--type=service", "--all",
                "-o", "json", "--no-pager"])
    try:
        listed = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        listed = None
    ok = isinstance(listed, list)
    own = _own_units()
    rows: dict[str, dict] = {}
    for unit in listed if ok else []:
        name = str(unit.get("unit", "")) if isinstance(unit, dict) else ""
        if not name.endswith(".service") or name.endswith("@.service"):
            continue
        rows[name] = {k: str(unit.get(k, "")) for k in ("unit", "load", "active", "sub",
                                                         "description")}
        rows[name]["own"] = name in own
    for name, desc in own.items():
        rows.setdefault(name, {"unit": name, "load": "not loaded", "active": "", "sub": "",
                               "description": desc, "own": True})
    return list(rows.values()), ok


def find_services(query: str, limit: int = 8) -> tuple[list[dict], bool]:
    """User services for a name or a purpose, best first, and whether systemd answered.

    An exact name comes first; the rest by how many of the query's words the
    name or description holds; ties to the user's own, then the shorter name.
    """
    rows, ok = user_services()
    q = query.strip()
    exact = q if q.endswith(".service") else q + ".service"
    said = _stems(query)
    scored = []
    for row in rows:
        if row["unit"] == exact:
            score = math.inf
        else:
            score = len(said & (_stems(row["unit"].removesuffix(".service"))
                                | _stems(row["description"])))
            if not score:
                continue
        scored.append((-score, not row["own"], len(row["unit"]), row["unit"], row))
    scored.sort(key=lambda s: s[:4])
    return [s[4] for s in scored[:limit]], ok


def _show(unit: str, props: str) -> dict[str, str] | None:
    raw = _run(["systemctl", "--user", "show", "-p", props, "--", unit])
    if not raw:
        return None
    wanted = props.split(",")
    pairs = (line.partition("=")[::2] for line in raw.splitlines())
    return {k: v for k, v in pairs if k in wanted}


def service_detail(unit: str) -> dict[str, str] | None:
    """The fixed detail properties of a unit taken from the index."""
    return _show(unit, _SHOW_DETAIL)


def service_exists(name: str) -> dict[str, str] | None:
    """Ask systemd about a unit by exact name; None if the name is not a unit name."""
    if not _UNIT_NAME_RE.fullmatch(name) or name.startswith("-"):
        return None
    return _show(name if name.endswith(".service") else name + ".service", _SHOW_EXISTS)


_MCP_NAME_RE = re.compile(r"[\w.@:-]{1,64}")
_MCP_TRANSPORTS = ("stdio", "http", "sse", "ws")


def mcp_servers() -> list[tuple[str, str, str]] | None:
    """Claude Code's configured MCP servers as (scope, name, transport) (#83).

    User scope and local scope from ~/.claude.json (or $CLAUDE_CONFIG_DIR's),
    read on every call. No env, header, url, command or args value is ever
    read into a variable. [] if there is no file, None if it cannot be read.
    """
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    path = Path(base) / ".claude.json" if base else Path.home() / ".claude.json"
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    rows = []

    def add(scope, servers):
        if not isinstance(servers, dict):
            return
        for key, server in servers.items():
            server = server if isinstance(server, dict) else {}
            kind = server.get("type")
            transport = (kind if kind in _MCP_TRANSPORTS
                         else "stdio" if "command" in server else "?")
            name = key if _MCP_NAME_RE.fullmatch(key) else "(unnamed)"
            rows.append((scope, name, transport))

    add("user", data.get("mcpServers"))
    projects = data.get("projects")
    for project, settings in (projects.items() if isinstance(projects, dict) else ()):
        if isinstance(settings, dict):
            add(f"local: {Path(project).name}", settings.get("mcpServers"))
    return rows


def live_state() -> str:
    """A snapshot of the desktop right now — refreshed on every request."""
    def query(what: str):
        raw = _run(["hyprctl", "-j", what])
        try:
            return json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return None

    # A failed query is not an empty desktop (#69, #75). This goes to the model
    # every turn, and "no windows" read as a fact sends it acting on nothing.
    unknown = "unknown (hyprctl did not answer)"
    parts = []
    monitors = query("monitors")
    parts.append("Monitors: " + (unknown if monitors is None else ", ".join(
        f'{m["name"]} {m["width"]}x{m["height"]} (workspace {m.get("activeWorkspace", {}).get("name")})'
        for m in monitors
    ) or "none"))
    workspaces = query("workspaces")
    parts.append("Workspaces in use: " + (unknown if workspaces is None else ", ".join(
        f'{w["name"]} ({w.get("windows", 0)} windows)' for w in sorted(
            workspaces, key=lambda w: w.get("id", 0)) if w.get("id", 0) > 0
    ) or "none"))
    active = query("activewindow")
    if active is None:
        parts.append(f"Focused window: {unknown}")
    elif active.get("class"):
        parts.append(f'Focused window: {active.get("class")} — "{active.get("title")}"')
    clients = query("clients")
    rows = [
        f'    {c.get("class","?")} — "{(c.get("title") or "")[:70]}" '
        f'[workspace {c.get("workspace", {}).get("name")}, address {c.get("address")}]'
        for c in clients or [] if not c.get("hidden")
    ]
    if clients is None:
        parts.append(f"Open windows: {unknown}")
    elif rows:
        parts.append("Open windows:\n" + "\n".join(rows[:25]))
    else:
        parts.append("Open windows: none")
    if any(unknown in part for part in parts):
        parts.append("Part of this snapshot is unknown; call hypr_query before "
                     "acting on what is missing.")
    return "\n".join(parts)


# The fifteen actions a voice assistant reaches for most, and the exact command
# for each. This exists because the two generated sections below both miss them:
# `dispatch_examples` scrapes only `hl.dsp.*` and bare-string bindings, so every
# app binding in applications.lua is invisible to it (they pass a *table*, e.g.
# `o.bind("SUPER + SHIFT + RETURN", "Browser", { omarchy = "browser" })`), and
# `omarchy_commands` truncates long before reaching most of these.
#
# Every route here was checked against `omarchy commands --json`; `verify_essentials`
# re-checks them, and `doctor` reports any that a system update has broken.
ESSENTIALS = [
    ("Open a terminal",            "omarchy launch terminal"),
    # Deliberately without the [url] the CLI accepts. Given the URL form, the
    # model reached for it every time — and it hands the address to the running
    # browser, which opens a TAB in a window that already exists. Nothing new
    # appears in hyprctl, so the assistant cannot wait for it, read it, or tell
    # whether it worked, and in the session log it concluded (wrongly) that the
    # launch had failed. Advertising the route was teaching the mistake.
    ("Open the browser (no URL — see below)", "omarchy launch browser"),
    ("Open the editor",            "omarchy launch editor"),
    ("Open the file manager",      "omarchy launch nautilus"),
    ("Open a web app, or focus it if already open",
                                   "omarchy launch or focus webapp <window-pattern> <url>"),
    ("Open any app, or focus it if already open",
                                   "omarchy launch or focus <window-pattern> <launch-command>"),
    ("Open a terminal app (TUI)",  "omarchy launch or focus tui <command> [args...]"),
    ("Take a screenshot",          "omarchy capture screenshot [smart|region|windows|fullscreen]"),
    ("Read text off the screen (OCR)", "omarchy capture text"),
    ("Open a menu",                "omarchy menu [keybindings|clipboard|emoji|file|images|input]"),
    ("Lock the screen",            "omarchy system lock"),
    ("Change the volume",          "omarchy audio output volume <raise|lower|mute-toggle|+N|-N>"),
    ("Mute the microphone",        "omarchy audio input mute"),
    ("Change the theme",           "omarchy theme set <theme-name>   (omarchy theme list)"),
    ("Dismiss a notification",     "omarchy notification dismiss <summary>"),
]

# Said right after the table, where the browser row is still in view.
WEB_NOTE = (
    "To put a web page on screen use the TOOLS, not the CLI: web_search(query) "
    "for anything you need to look up, open_page(url) for one specific address. "
    "Both open a window you can then read, scroll and click. `omarchy launch "
    "browser <url>` only opens a tab inside an existing window, which never "
    "appears in the window list, and the web panes here are Chromium app "
    "windows with no address bar to type into."
)

# A second window of an already-running app. `omarchy launch ...` and a plain
# launch_app both focus what is already open, which is right for "open my
# browser" and wrong for "open another one".
SECOND_WINDOW = (
    "For ANOTHER window of an app that is already open, use the launch_app tool "
    "with '<desktop-id>:<action>', e.g. launch_app(app=\"google-chrome:new-window\") "
    "or 'google-chrome:new-private-window' for incognito. Launching normally "
    "focuses the existing window instead of opening a second one."
)


def app_bindings() -> str:
    """The apps and web apps this desktop binds to keys, with how to open each.

    applications.lua passes a *table* as the third argument to o.bind — e.g.
    `{ omarchy = "browser" }`, `{ webapp = "https://chatgpt.com" }` — which the
    `hl.dsp.*`/bare-string scraper in dispatch_examples cannot see. Without this
    the model knows these apps exist but not how to open them, and invents URLs:
    asked for ChatGPT it guessed the long-dead chat.openai.com.
    """
    path = OMARCHY_PATH / "default/hypr/bindings/applications.lua"
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return ""
    rows: dict[str, str] = {}
    for line in text.splitlines():
        match = re.search(r'o\.bind\(\s*"[^"]+"\s*,\s*"([^"]+)"\s*,\s*\{([^}]*)\}', line)
        if not match:
            continue
        label, table = match.group(1), match.group(2)
        if (app := re.search(r'omarchy\s*=\s*"([^"]+)"', table)):
            # nautilus-cwd -> "nautilus cwd", but leave "browser --private"
            # alone: only a hyphen *between* word characters is a route separator.
            route = re.sub(r"(?<=\w)-(?=\w)", " ", app.group(1))
            rows.setdefault(label, f"launch {route}")
        elif (url := re.search(r'webapp\s*=\s*"([^"]+)"', table)):
            pattern = label.lower().replace(" ", "")
            rows.setdefault(label, f"launch or focus webapp {pattern} {url.group(1)}")
        elif (tui := re.search(r'tui\s*=\s*"([^"]+)"', table)):
            rows.setdefault(label, f"launch or focus tui {tui.group(1)}")
        elif (cmd := re.search(r'launch\s*=\s*"([^"]+)"', table)):
            rows.setdefault(label, f"launch or focus {cmd.group(1)} {cmd.group(1)}")
    return "\n".join(f"  {label:<22} omarchy {how}" for label, how in rows.items())


# The window and workspace calls a voice assistant needs constantly.
#
# These are NOT in the scraped examples below, and that gap is the reason they
# are written out here: Omarchy generates its "Switch to workspace 1..10"
# bindings in a Lua loop, and dispatch_examples only reads literal o.bind(...)
# lines. So the single most common spoken command — "go to workspace four" —
# had no worked example anywhere in the manifest, and the model guessed. It
# reached for hl.dsp.workspace.change_id, which is a *rename* and needs both
# `workspace` and `id`, so workspace navigation silently did nothing.
# Each row is (what, dispatcher, args) — the arguments hypr_dispatch takes,
# not Lua source. The manifest renders them, so what the model is shown here
# and what the tool accepts cannot drift apart.
HYPR_ESSENTIALS = [
    ("Switch to workspace N",            "focus", {"workspace": "4"}),
    ("Next / previous workspace",        "focus", {"workspace": "e+1"}),
    ("Back to the previous workspace",   "focus", {"workspace": "previous"}),
    ("Move this window to workspace N",  "window.move", {"workspace": "4", "follow": True}),
    ("Focus a specific window",          "focus", {"window": "address:0x55..."}),
    ("Focus left/right/up/down",         "focus", {"direction": "l"}),
    ("Close the focused window",         "window.close", {}),
    ("Fullscreen the focused window",    "window.fullscreen", {"mode": "fullscreen"}),
    ("Float / unfloat it",               "window.float", {"action": "toggle"}),
]

HYPR_WARNING = (
    'Switching workspaces is the focus dispatcher with workspace = "N", never '
    "workspace.change_id — change_id RENAMES a workspace and requires both "
    "`workspace` and `id`. If a dispatch returns an error, read it and fix the "
    "call; do not repeat it."
)


def _call_form(dispatcher: str, args: dict) -> str:
    """How a hypr_dispatch call is written, for the manifest."""
    if not args:
        return f'dispatcher "{dispatcher}"'
    shown = ", ".join(f'"{k}": {json.dumps(v)}' for k, v in args.items())
    return f'dispatcher "{dispatcher}", args {{{shown}}}'


def hypr_essentials() -> str:
    return "\n".join(f"  {what:<34} {_call_form(dispatcher, args)}"
                      for what, dispatcher, args in HYPR_ESSENTIALS)


def essentials() -> str:
    rows = "\n".join(f"  {what:<44} {how}" for what, how in ESSENTIALS)
    return f"{rows}\n\n  {WEB_NOTE}"


def verify_hypr_essentials() -> list[str]:
    """Which HYPR_ESSENTIALS name a dispatcher this Hyprland does not have.

    Parse-only — running them would move the user's windows. It catches the
    failure that mattered here: a call written against an API that has since
    changed, which shows up as silence rather than an error the user can see.
    """
    known = dispatchers()
    if not known:
        return []
    return [f"{what} -> {dispatcher}"
            for what, dispatcher, _ in HYPR_ESSENTIALS if dispatcher not in known]


def _omarchy_routes() -> set[str]:
    """Every `omarchy` route this machine actually has."""
    try:
        data = json.loads(_run(["omarchy", "commands", "--json"], timeout=15) or "{}")
    except json.JSONDecodeError:
        return set()
    return {c["route"] for c in data.get("commands", []) if c.get("route")}


def verify_essentials() -> list[str]:
    """Which ESSENTIALS no longer resolve to a real route. Empty is good.

    Prefix-matched: the table carries argument placeholders, and a route is
    stored without them.
    """
    routes = _omarchy_routes()
    if not routes:
        return []
    broken = []
    for what, how in ESSENTIALS:
        words = how.split()
        if not any(" ".join(words[:n]) in routes for n in range(len(words), 1, -1)):
            broken.append(f"{what} -> {how}")
    return broken


TEMPLATE = """\
# The machine you are operating

Nixarchy: Omarchy {omarchy} on NixOS + Hyprland ({hyprland}), Wayland.

Software is declared, not installed: there is no package manager to run, /usr
does not exist, and everything lives read-only in /nix/store. If something is
missing, say so and stop — never offer a command that installs it.

## Start here — the common actions

Run these with the omarchy_cli tool. Square brackets are optional, angle
brackets are yours to fill in.

{essentials}

## Apps this desktop already knows how to open

Use these exact commands rather than guessing a URL or a binary name.

{app_bindings}

{second_window}

## Hyprland dispatchers (read from this machine's Lua API stub)

Call these with hypr_dispatch. Name the dispatcher and give its arguments —
do NOT write Lua; a Lua expression is refused. The name is the dotted one from
the list below with the `hl.dsp.` dropped: `hl.dsp.window.close` is
`dispatcher "window.close"`. Arguments are text, numbers or true/false, so
`hl.dsp.cursor.move({{ x = 400, y = 300 }})` is written
`dispatcher "cursor.move", args {{"x": 400, "y": 300}}`. The exception is
`layout`, which takes a layout message instead of arguments:
`dispatcher "layout", message "preselect r"`. Available:

{dispatchers}

### The calls you will need most

{hypr_essentials}

{hypr_warning}

## Dispatcher calls this desktop actually binds to keys

Copy these shapes. They are correct for the installed Hyprland version.

{examples}

## The rest of the Omarchy CLI

Not listed here. There are over a hundred more routes — themes, audio, network,
notifications, screenshots, toggles. Call `omarchy_help` with a word or two to
find the exact one ("dark theme", "bluetooth", "night light"), then run what it
gives you with omarchy_cli. Do not guess a route you have not seen.

## Applications installed here

Every installed application can be opened by the name a person uses: pass it
to launch_app ("zed", "the file manager"). To see what is installed for a
purpose ("password manager", "screen recorder"), call find_app. For
command-line tools, call find_command before saying whether something is
installed or how to use it.

For the user's background services, call find_service before saying whether
one exists or is running. For configured MCP servers, system_query mcp.

{agents}"""


def _cache_key() -> str:
    """What the cached manifest is keyed on.

    The system inputs, and *this file*. Without the last one, editing the
    template or the essentials table changed nothing: the daemon went on
    serving a manifest built before the change, from a cache whose key only
    moved when Omarchy or Hyprland did. That cost an hour of wondering why a
    corrected instruction was not reaching the model.

    Mtimes alone are not enough: every file in the Nix store has mtime 1, so a
    rebuild that changed the template kept the key and served the old manifest
    (#103). So this file is keyed on its content -- identical checkouts share a
    key, a changed template moves it -- and the stub and the Omarchy bindings,
    both symlinks into the store, on their resolved paths as well as mtimes.

    The versions are read once per process (#111): an Omarchy or Hyprland
    upgrade takes effect at the next daemon restart, and an "unknown" reading
    is read again on the next call. The coding agents installed right now are
    part of the key too, so installing or removing one rebuilds the manifest.
    """
    versions = _versions()
    stamp = json.dumps(versions, sort_keys=True)
    stamp += ",".join(b for b, _, _ in CODING_AGENTS if shutil.which(b))
    stub = _stub_path()
    for path in ([stub] if stub else []) + [OMARCHY_PATH / "default/hypr/bindings"]:
        try:
            stamp += str(path.resolve()) + str(path.stat().st_mtime_ns)
        except OSError:
            pass
    try:
        stamp += hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        pass
    return hashlib.sha256(stamp.encode()).hexdigest()[:16]


def manifest(refresh: bool = False) -> str:
    """The stable half of the system prompt. Cached, and cache-friendly:
    identical bytes across requests so the API prefix cache can hold it."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / f"manifest-{_cache_key()}.md"
    text = None if refresh else _load(cached)
    if text is not None:
        return text

    versions = _versions()
    text = TEMPLATE.format(
        omarchy=versions["omarchy"],
        hyprland=versions["hyprland"],
        dispatchers=dispatcher_tree() or "  (Lua stub not found — use hyprctl syntax with care)",
        essentials=essentials(),
        app_bindings=app_bindings() or "  (none found)",
        second_window=SECOND_WINDOW,
        hypr_essentials=hypr_essentials(),
        hypr_warning=HYPR_WARNING,
        examples=dispatch_examples() or "  (none found)",
        agents=coding_agents(),
    )
    _store(cached, text, "manifest-*.md")
    return text


# Coding agents, in the order they are offered to the model. One line each:
# this is spent on every turn, and a paragraph per agent would cost more than
# the whole Omarchy CLI section.
CODING_AGENTS = [
    ("claude", "claude -p '<prompt>'",
     "Claude Code. Reads and edits files, runs commands, answers about a repo."),
    ("codex", "codex exec --skip-git-repo-check '<prompt>'",
     "OpenAI Codex. Same shape; the flag is needed outside a git repo."),
    ("gh", "gh <command>",
     "GitHub: pull requests, issues, runs. `gh pr list`, `gh run watch`."),
    ("ollama", "ollama run <model> '<prompt>'",
     "A local model, offline and free. `ollama list` shows which."),
]


def coding_agents() -> str:
    """The agents on this machine, or nothing at all if there are none.

    An empty section beats a section listing things that are not installed:
    she reaches for what the manifest names, and naming an absent binary buys
    a failed command and a confused turn.
    """
    rows = [f"  {call}\n      {why}"
            for binary, call, why in CODING_AGENTS if shutil.which(binary)]
    if not rows:
        return ""
    return ("""
## Coding agents on this machine

These are command-line programs, not desktop apps -- do not try to launch them
from the application list. Start one with run_in_terminal, and if it will take
more than a few seconds say so and call watch_terminal rather than waiting: it
returns at once and interrupts you with the result, even from another
workspace.

Quote the prompt in single quotes, and keep it to one line -- run_in_terminal
sends no newlines.

"""
            + "\n".join(rows) + "\n")


def unreadable_sources() -> list[str]:
    """The manifest inputs this machine could not be read from. Empty is good.

    Every one of these degrades silently by design — a missing source drops a
    section and the manifest is still built, because a thinner manifest beats
    no assistant at all. That is the right runtime behaviour and the wrong
    thing to stay quiet about: the symptom is Oma not knowing how to do
    something, three steps removed from the cause.

    OMARCHY_PATH is the one that actually bites. Omarchy exports it into the
    session, but a systemd user service only has it if the session imported it
    into the user manager first, and without it the dispatcher examples — the
    only version-correct call syntax in the whole manifest — go to zero.
    """
    problems = []
    if not OMARCHY_PATH.is_dir():
        problems.append(
            f"OMARCHY_PATH is {OMARCHY_PATH}, which is not a directory — the "
            "dispatcher examples and the Omarchy version come from there. If "
            "the daemon is a user service, check `systemctl --user "
            "show-environment | grep OMARCHY_PATH`")
    elif not (OMARCHY_PATH / "default/hypr/bindings").is_dir():
        problems.append(
            f"{OMARCHY_PATH}/default/hypr/bindings is missing — no real "
            "dispatcher call syntax to show the model")
    if not dispatchers():
        problems.append(
            "no Hyprland dispatcher tree — the running compositor could not be "
            "asked and no stub was found. Start Hyprland, or set "
            "OMARCHY_VOICE_HL_STUB to an hl.meta.lua")
    if not _omarchy_routes():
        problems.append(
            "`omarchy commands --json` returned nothing — the CLI surface is "
            "missing from the manifest, and the checks below cannot run")
    return problems
