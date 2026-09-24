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

import difflib
import functools
import hashlib
import json
import os
import re
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
    if cached.exists() and not refresh:
        rows = []
        for line in cached.read_text().splitlines():
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
    for stale in CACHE_DIR.glob(f"*-{COMMAND_INDEX}"):
        stale.unlink(missing_ok=True)
    cached.write_text("\n".join(f"{sig}\t{summ}" for sig, summ in rows))
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
            command = fields.get("Exec", "").split()
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


def find_apps(query: str, limit: int = 8) -> list[tuple[int, dict]]:
    """Installed apps for the name a person uses, best first, with a score.

    100 the whole name, id or command; 95 initials; 90 every word in the name;
    70 in GenericName or Keywords; 40 in Comment; up to 60 for a close spelling,
    which is what whisper hands over ("zedd"). Lexical on purpose: measured at
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
        ident = {row["id"].lower(), row["id"].lower().rsplit(".", 1)[-1],
                 row["command"].lower()} - {""}
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


def live_state() -> str:
    """A snapshot of the desktop right now — refreshed on every request."""
    def query(what: str):
        raw = _run(["hyprctl", "-j", what])
        try:
            return json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return None

    parts = []
    monitors = query("monitors") or []
    parts.append("Monitors: " + ", ".join(
        f'{m["name"]} {m["width"]}x{m["height"]} (workspace {m.get("activeWorkspace", {}).get("name")})'
        for m in monitors
    ))
    workspaces = query("workspaces") or []
    parts.append("Workspaces in use: " + ", ".join(
        f'{w["name"]} ({w.get("windows", 0)} windows)' for w in sorted(
            workspaces, key=lambda w: w.get("id", 0)) if w.get("id", 0) > 0
    ))
    active = query("activewindow") or {}
    if active.get("class"):
        parts.append(f'Focused window: {active.get("class")} — "{active.get("title")}"')
    clients = query("clients") or []
    if clients:
        rows = [
            f'    {c.get("class","?")} — "{(c.get("title") or "")[:70]}" '
            f'[workspace {c.get("workspace", {}).get("name")}, address {c.get("address")}]'
            for c in clients if not c.get("hidden")
        ]
        parts.append("Open windows:\n" + "\n".join(rows[:25]))
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
purpose ("password manager", "screen recorder"), call find_app.

{agents}"""


def _cache_key() -> str:
    """What the cached manifest is keyed on.

    The system inputs, and *this file*. Without the last one, editing the
    template or the essentials table changed nothing: the daemon went on
    serving a manifest built before the change, from a cache whose key only
    moved when Omarchy or Hyprland did. That cost an hour of wondering why a
    corrected instruction was not reaching the model.
    """
    versions = system_versions()
    stamp = json.dumps(versions, sort_keys=True)
    stub = _stub_path()
    for path in ([stub] if stub else []) + [OMARCHY_PATH / "default/hypr/bindings",
                                            Path(__file__)]:
        try:
            stamp += str(path.stat().st_mtime_ns)
        except OSError:
            pass
    return hashlib.sha256(stamp.encode()).hexdigest()[:16]


def manifest(refresh: bool = False) -> str:
    """The stable half of the system prompt. Cached, and cache-friendly:
    identical bytes across requests so the API prefix cache can hold it."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / f"manifest-{_cache_key()}.md"
    if cached.exists() and not refresh:
        return cached.read_text()

    versions = system_versions()
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
    for stale in CACHE_DIR.glob("manifest-*.md"):
        stale.unlink(missing_ok=True)
    cached.write_text(text)
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


def missing_tools() -> list[str]:
    return [t for t in ("hyprctl", "omarchy", "wtype", "pw-record") if not shutil.which(t)]


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
