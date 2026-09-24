"""The hands: the tool surface the model drives the desktop with.

A small set of *general* tools rather than one tool per desktop action. The
model already knows the desktop's real API (see capabilities.manifest), so a
tool per verb would go stale on every Omarchy release.

Every call passes through `Policy` first. An open microphone is an untrusted
input channel: a misheard sentence should not be able to reformat a disk.
"""

from __future__ import annotations

import json
import re
import os
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote_plus, urlparse

from . import (capabilities, hypr_events, notifications,
               trace as trace_mod, virtual_input)
from . import config as config_mod
from .config import Config, app_dirs, install_hint
from .keys import keys_for_text, normalise_key, normalise_mods

QUERY_KINDS = {
    "clients", "workspaces", "monitors", "activewindow", "activeworkspace",
    "devices", "layers", "binds", "animations", "version",
}

# PPM, not PNG. Every capture here is piped straight into tesseract and then
# discarded, so the compression is work nobody reads: measured on a 2560x1440
# framebuffer, `grim -g ... -` is 0.940s and `grim -t ppm -g ... -` is 0.032s,
# both means of five, both through the pipe. tesseract reads PNM natively.
# The pipe carries about four times the bytes, between two local processes,
# which does not show up against nine tenths of a second of CPU.
CAPTURE_CMD = ["grim", "-t", "ppm", "-g"]

# Every tool that sends input to a window. Membership is a claim about SENDING
# INPUT, not about risk: scroll is here because it turns a wheel over a window,
# even though turning a wheel over a vault is the mildest thing on the list.
#
# The test that enforces this reads each handler's source for input markers and
# fails if one is missing from here, so a tool added later cannot quietly skip
# the guard the way click_text nearly did -- it was protected only because it
# happened to OCR first (#67).
INPUT_TOOLS = frozenset({"type_text", "send_shortcut", "click_text", "scroll"})

# Read-only tools still run under --dry-run so the planner can see the desktop.
READ_ONLY_TOOLS = {"hypr_query", "read_screen", "omarchy_help", "system_query",
                   "read_terminal", "list_terminals", "screenshot", "find_app"}

# MPRIS, through playerctl (#73). One row per player; playerctl leaves a field
# empty when the player does not report it.
MEDIA_FORMAT = "{{playerName}}\t{{status}}\t{{artist}}\t{{title}}"
MEDIA_ACTIONS = ("play", "pause", "play-pause", "next", "previous")
MEDIA_POLL = 0.1
MEDIA_SETTLE = 1.0
# ponytail: tuning knob -- players whose title is a browser tab's title, so it
# is withheld while a private window is open. Add a browser whose MPRIS name
# this does not match.
_BROWSER_PLAYER = re.compile(r"chrom|firefox|brave|vivaldi|msedge|librewolf", re.I)

# Hyprland dispatchers that spawn processes. They bypass allow_shell unless
# we reject them here.
SHELL_DISPATCHERS = {"exec_cmd", "exec_raw", "exec"}

# A few dispatchers take a positional string instead of a table -- `layout`
# ("preselect r") and `workspace.toggle_special` ("scratchpad") both do in
# Omarchy's own bindings. The stub declares every dispatcher as `fun(...)`, so
# it cannot say which, and a hardcoded list of them would be the kind of guess
# this file exists to avoid. `message` is therefore the string form for any
# dispatcher; what stops it being abused is that it is escaped like any other
# value, not which dispatcher it is aimed at.

_DOTTED_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")
_ARG_KEY_RE = re.compile(r"^[A-Za-z_]\w*$")
_DESKTOP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
# What a person calls an app: words, not a command line (#70).
_APP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.+-]*$")

# Lua's own escapes. Deliberately not json.dumps, which _tool_send_shortcut
# used to reach for: it emits \uXXXX for non-ASCII, where Lua spells that
# \u{XXXX}. This Hyprland's Lua happens to accept both -- measured, with
# `hl.dsp.no_op({ x = "caf\u00e9" })` answering ok while "\q" is refused as an
# invalid escape, so the acceptance is real and not a swallowed error. An
# undocumented overlap between two escape dialects is still not a thing to
# build a security boundary on, so the escaping is written out here.
_LUA_ESCAPES = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _lua_string(value: str) -> str:
    """A Lua double-quoted literal that cannot end anywhere but its own quote."""
    out = ['"']
    for char in value:
        if char in _LUA_ESCAPES:
            out.append(_LUA_ESCAPES[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            # Lua's decimal escape. Three digits so a following digit in the
            # string cannot be read as part of it.
            out.append(f"\\{ord(char):03d}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _lua_value(value) -> tuple[str | None, str | None]:
    # bool before int: isinstance(True, int) is True, and rendering a boolean
    # as 1 would silently change what the dispatcher is told.
    if isinstance(value, bool):
        return ("true" if value else "false"), None
    if isinstance(value, str):
        return _lua_string(value), None
    if isinstance(value, int):
        return str(value), None
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None, f"{value} is not a number Lua can be given"
        return repr(value), None
    return None, (f"values must be text, a number or true/false, not "
                  f"{type(value).__name__}")


def render_dispatch(dispatcher: str, args: dict | None = None,
                    message: str | None = None, *,
                    allow_shell: bool) -> tuple[str | None, str | None]:
    """Build one `hl.dsp.*` call from a validated name and flat arguments.

    The only place Lua is produced. The regex this replaced accepted anything
    between the parentheses of an hl.dsp.* call -- including a function
    literal calling hl.exec_cmd -- so `allow_shell = false` did not hold. A
    regex cannot describe what executable code does; not accepting the code in
    the first place can.

    Returns (lua, error), the convention normalise_key and normalise_mods use.
    """
    known = capabilities.dispatchers()
    if not known:
        return None, ("Hyprland's Lua stub is not installed, so no dispatcher "
                      "can be checked against it. Set OMARCHY_VOICE_HL_STUB to "
                      "hl.meta.lua, or use omarchy_cli instead.")
    name = (dispatcher or "").strip()
    if not _DOTTED_RE.match(name):
        return None, (f"{dispatcher!r} is not a dispatcher name. Give a dotted "
                      'name such as "focus" or "window.close".')
    if name not in known:
        return None, (f"this Hyprland has no dispatcher {name!r}. "
                      "The manifest lists the ones it does have.")
    if name.rsplit(".", 1)[-1] in SHELL_DISPATCHERS and not allow_shell:
        return None, (f"hl.dsp.{name} is process execution; enable allow_shell "
                      "to use it, or launch apps with launch_app / omarchy_cli")

    if message is not None:
        if not isinstance(message, str):
            return None, "message must be text"
        if args:
            return None, "give a dispatcher either args or a message, not both"
        return f"hl.dsp.{name}({_lua_string(message)})", None

    if not args:
        return f"hl.dsp.{name}()", None
    if not isinstance(args, dict):
        return None, "args must be a table of key/value pairs"

    parts = []
    for key in sorted(args):
        if not _ARG_KEY_RE.match(str(key)):
            return None, f"{key!r} is not an argument name"
        rendered, error = _lua_value(args[key])
        if error:
            return None, f"{key}: {error}"
        parts.append(f"{key} = {rendered}")
    return f"hl.dsp.{name}({{ {', '.join(parts)} }})", None


class Denied(Exception):
    """The policy refused an action outright."""


class NeedsConfirmation(Exception):
    """The action is allowed, but only after the user says so out loud."""

    def __init__(self, description: str):
        super().__init__(description)
        self.description = description


@dataclass
class Policy:
    config: Config

    def check(self, description: str) -> None:
        for pattern in self.config.deny_patterns:
            if re.search(pattern, description, re.IGNORECASE):
                raise Denied(f"blocked by deny rule /{pattern}/")
        for pattern in self.config.confirm_patterns:
            if re.search(pattern, description, re.IGNORECASE):
                raise NeedsConfirmation(description)


@dataclass
class Result:
    ok: bool
    output: str
    # Raw PNG, when the tool captured pixels. Only the MCP server looks at it:
    # `as_tool_result` is the text path and stays untouched, so the voice path
    # and every existing caller are unaffected. See _tool_screenshot.
    image: bytes | None = None

    def as_tool_result(self) -> str:
        if self.ok:
            return self.output or "done"
        return f"ERROR: {self.output}"


# --- window composition -----------------------------------------------------
#
# One tool call that builds a whole workspace, because the model is bad at the
# part that is not language. Asked to "open the news", it would launch three
# apps and immediately dispatch move/focus at addresses that did not exist yet:
# a window appears some hundreds of milliseconds *after* the launch command
# returns, so every placement raced the app it was placing. Doing it here means
# one round trip instead of six, and a wait for the window that actually
# arrived rather than a guess at its address.

LAYOUTS = ("columns", "main-and-side", "grid")
PANE_KINDS = ("web", "terminal", "tui", "app")

# How long to wait for one window to map. Chromium web apps are the slow case
# (cold start, profile load); a terminal is up in well under a second.
PANE_TIMEOUT = {"web": 12.0, "app": 10.0, "terminal": 6.0, "tui": 6.0}
# Whole-composition budget. Past this, remaining panes are launched without
# waiting — a slow fourth app must not hold the assistant mute for a minute.
COMPOSE_BUDGET = 32.0
MAX_PANES = 6
# How long to let a launch command run before deciding it is an application
# rather than a hung command. See Executor._shell.
LAUNCH_GRACE = 0.5
# Cap on the text of a command's output handed back to the model. JSON
# queries are read here before anything is cut, so this only ever trims
# prose — see Executor._shell and _query_json.
OUTPUT_LIMIT = 4000
# How much OCR'd text to hand back. The prompt is already ~8k tokens and
# every turn is charged against a per-minute budget, so a screen's worth of
# text has to be bounded.
OCR_LIMIT = 6000
# tesseract page segmentation. 6 means "one uniform block of text", which is
# what omarchy-capture-text uses because there a human has dragged a box around
# a paragraph. A whole screen is not that: it is a bar, a sidebar, tabs and a
# page in columns, and psm 6 read straight past them — it could not see the
# "Files changed" tab on a GitHub pull request at all, while psm 3 (automatic
# segmentation) finds it and reads more of everything else too.
OCR_PAGE_MODE = 3
# The recognition model is nixpkgs' default and this is the one OCR decision
# here that nobody made -- recorded because its absence produced a wrong issue
# (#32 was filed claiming we run tessdata_best; we do not).
#
# `tesseract` brings `tessdata` as a derivation named `all`: 129 languages,
# eng.traineddata at 23,466,654 bytes, which is an exact match for upstream's
# combined tessdata repo. tessdata_best is 15,400,601 and tessdata_fast is
# 4,113,088. The combined set carries a legacy engine beside the LSTM one, and
# `--oem 1` below selects LSTM only -- so roughly 8 MB per language of that
# file is shipped and then ignored at every call.
#
# Measured on one 2560x1440 monitor, same capture, these exact arguments, mean
# of three: ours 2116 ms / 380 words, tessdata_best 2747 ms / 377,
# tessdata_fast 1787 ms / 372. `best` is the SLOWEST of the three, so a smaller
# file is not a slower model here and the intuition runs backwards.
#
# Nothing was changed on that. Unique-token overlap against `fast` was 84% on
# that screen and 67% on a denser one earlier the same day, so the accuracy
# comparison moves with screen content and cannot carry a decision by itself;
# and #23 requires changes be argued against p50/p95 task completion and
# wrong-target rate, not a stopwatch on one image. The seam, if a future
# measurement ever justifies one, is `tesseract.override { tessdata = ...; }`
# in nix/package.nix.
# Words tesseract is less sure of than this are noise, not targets.
MIN_OCR_CONFIDENCE = 45.0
# Substitutions tesseract actually makes, for folding a query onto a misread
# token. NOT a general similarity measure: #48 measured that any cutoff loose
# enough to recover "window" from "wordow" (0.667) also matches "delete"
# against "deleted" (0.923), because the two distributions overlap almost
# entirely -- "close"/"c1ose" and "close"/"clone" both score 0.800. Forgiving
# only these specific confusions separates them: 0 false collisions across the
# 14 confusable UI words in the spec.
#
# `rn`->`m` is the first rule to drop if anything ever does collide: it is the
# only one that can merge two real words ("corner"/"comer").
OCR_CONFUSABLES = str.maketrans({"1": "l", "0": "o", "5": "s", "8": "b",
                                 "q": "g", "|": "l", "!": "l"})


def _ocr_fold(word: str) -> str:
    """A token reduced to what tesseract cannot reliably tell apart."""
    return word.lower().translate(OCR_CONFUSABLES).replace("rn", "m")


def _nearest_word(words: list[dict], query: str) -> dict | None:
    """The OCR token closest to `query`, for a "did you mean" in a refusal.

    Deliberately REPORTED and never acted on. That is what makes a loose
    cutoff safe here: naming "deleted" when the caller asked for "delete" is a
    sentence they read, whereas clicking it is the silent misclick #48 calls
    the worst outcome. The cost of being too loose is a useless sentence; the
    cost of being too tight is the dead end this exists to remove.

    0.6 rather than keys.py:_suggest's 0.72, which was tried first and is too
    tight for this pool: "window"/"wordow" scores 0.667, so the very example
    #48 is about went unnamed. keys.py matches against a small fixed set of
    key names; this matches arbitrary screen text against a misreading of it.
    Measured over the spec's pairs: 0.6 names 11/11 real misreads and suggests
    nothing for 4 unrelated words on screen.
    """
    import difflib

    tokens = {w["text"].lower(): w for w in words if w.get("text")}
    if not tokens:
        return None
    folded = {_ocr_fold(token) for token in tokens}
    wanted = [w for w in re.split(r"\W+", query.lower()) if w]
    for word in wanted:
        # Skip words that DID match. Reporting one of those names a word the
        # caller already got right and says nothing about the one they got
        # wrong: asked for "Files change" against "Files changed", this used
        # to answer "the closest text is 'Files'", which is true and useless.
        if word in tokens or _ocr_fold(word) in folded:
            continue
        close = difflib.get_close_matches(word, list(tokens), n=1, cutoff=0.6)
        if close:
            return tokens[close[0]]
    return None


def _required_hits(word_count: int) -> int:
    """How many of a query's words must be present for a run to count.

    All of them for anything short. A long phrase gets one word of slack,
    because OCR reliably mangles about that many and losing the whole match to
    one misread letter is worse than the occasional near-miss.
    """
    return word_count if word_count <= 3 else word_count - 1
# --- terminals, through tmux ------------------------------------------------
#
# A terminal used to be a picture: grim the window, run tesseract, hope. That
# meant output was garbled, only readable while the window was visible, and
# impossible to see at all with the screen asleep or the session locked. Input
# was worse — wtype into whatever happened to have focus, with no way to tell
# whether it landed.
#
# tmux answers all of it at once, and Omarchy already ships it:
#
#   capture-pane -p        exact scrollback, from a pane on no workspace at all
#   pane_current_command   bash -> sleep -> bash: "is it finished", for free
#   send-keys              input with no focus, no keysym, no window target
#   list-panes -a          structured state across every session
#
# `Work` is the session `omarchy launch terminal tmux` attaches to
# (`tmux attach || tmux new -s Work`), so this shares the terminal the user
# already has rather than hiding one away.
TMUX_SESSION = "Work"
# Window classes that are a terminal. Used to answer "can the user actually see
# this?", because tmux's own `session_attached` cannot: a session is equally
# "attached" whether its client is on the workspace in front of you or on one
# you left an hour ago. Substring-matched, so ghostty's reverse-DNS class and
# wezterm's both land.
TERMINAL_CLASSES = ("foot", "alacritty", "kitty", "ghostty", "wezterm",
                    "term", "console")
# What `pane_current_command` says when nothing is running but the shell. A
# pane sitting at one of these is idle; anything else is a running command.
IDLE_COMMANDS = {"bash", "zsh", "fish", "sh", "dash", "ksh", "nu", "elvish"}
# Scrollback handed back for a read. Generous — this is exact text rather than
# OCR, and the per-minute budget is no longer the binding constraint it was.
TERMINAL_LINES = 200
TERMINAL_OUTPUT_LIMIT = 6000
# How long `run_in_terminal` waits for a quick command before handing back
# "still running" and watching it instead. Long enough for a git status or a
# test run that was going to be fast; short enough not to hold the microphone.
TERMINAL_QUICK_WAIT = 6.0
TERMINAL_POLL = 0.2
# How long to allow for a pane to *start* looking busy after keys are sent.
# `pane_current_command` does not update the instant send-keys returns — the
# shell has not forked yet — so the first poll sees "bash" and, without this, a
# twenty-second command was reported finished in 0.4s with the echoed command
# line handed back as its output. A command that never looks busy inside this
# window really was instant, or was a shell builtin like `cd` that never forks.
#
# Measured rather than guessed: tmux reflected the forked command in 0.024s,
# six times out of six. This is 25x that, and it is the floor on how long an
# instant command appears to take, so it is not worth being generous with —
# 2.5s here meant `echo hello` sat silent for nearly three seconds.
TERMINAL_START_GRACE = 0.6
# How long to give a freshly launched terminal to attach to the session.
TERMINAL_ATTACH_TIMEOUT = 12.0
# A watch that never finishes would sit in the registry forever. Nothing takes
# longer than this that the user would still want announced out of the blue.
WATCH_MAX_SECONDS = 3 * 60 * 60

# --- searching the web ------------------------------------------------------
#
# The query goes in the URL. Nothing is typed.
#
# From a real session: asked to search for something, the assistant pressed
# CTRL+T, typed the query, and pressed Return five different ways before giving
# up — because the window it was driving was opened by `omarchy launch webapp`,
# which is `chrome --app=<url>`. An app window has no tab bar and no address
# bar, so CTRL+T and CTRL+L are no-ops and there was never anywhere for the
# text to go. Twelve tool rounds, no search. `omarchy launch browser <url>`
# does have an omnibox, but it opens a *tab in the window that already exists*,
# so nothing new appears in the window list and the assistant concluded — also
# wrongly — that the launch had failed.
#
# Both problems disappear if the query is part of the URL and the result opens
# as its own window: no typing, and a window that can be waited for, read,
# scrolled and clicked like any other.
SEARCH_SCOPES = {
    "web": "https://www.google.com/search?q={q}",
    "news": "https://www.google.com/search?q={q}&tbm=nws",
    "images": "https://www.google.com/search?q={q}&tbm=isch",
    "videos": "https://www.google.com/search?q={q}&tbm=vid",
    # No consent interstitial and a plainer results page, for when Google's
    # answer panels get in the way of the actual links.
    "duckduckgo": "https://duckduckgo.com/?q={q}",
}
# Scopes whose point is to be looked at. OCR of a wall of thumbnails is noise,
# and the user asked for them because they wanted to see them.
VISUAL_SCOPES = {"images", "videos"}
# A new search replaces the last one's window rather than adding to it, so a
# research session does not end up with nine identical result panes. Only the
# window *this tool* opened is closed — matching on class would also take down
# a duckduckgo pane the user had asked for by name.
# A launched window is mapped well before it has painted anything worth
# reading. Measured: a Google results page needs about two seconds after the
# surface appears, and a slow one is caught by the empty-read retry.
WEB_WINDOW_TIMEOUT = 15.0
WEB_RENDER_SETTLE = 2.0
# The old code slept WEB_RENDER_SETTLE flat before every web read. These turn
# that into a ceiling: read after a short floor, poll while it is still
# changing, stop early once the page has clearly painted.
WEB_PAINT_FLOOR = 0.25
WEB_PAINT_POLL = 0.25
# The length the retry path already treated as "that was not a real read".
WEB_ENOUGH_TEXT = 200
# Chromium's crash-restore bubble covers the top of the first window it opens
# afterwards, which is exactly where search results are. It ate a whole turn.
RESTORE_BUBBLE = "restore pages"

# Chrome's "Profile error occurred" box. Not corruption — the profile's SQLite
# databases check out `integrity: ok`. It is lock contention: `omarchy launch
# webapp` starts a *new* google-chrome process each time, which is meant to
# hand off to the browser already running and exit. When several are launched
# in a row, or one is launched while a heavy page is still loading, the handoff
# loses the race and the new process opens the profile itself. Chrome's own log
# at that moment:
#
#   ERROR ukm_database_backend.cc:142] Failed to open UKM database: database is locked
#   ERROR top_sites_backend.cc:77]     Failed to initialize database.
#
# The dialog has no window class, so _await_new_window already refuses to
# mistake it for a pane, but it still takes focus and covers the screen. It is
# harmless and transient, so it is closed on sight rather than reported.
PROFILE_ERROR_TITLE = "profile error"

# --- reach, patience, and memory -------------------------------------------
#
# A screen shows what fits; an application answers when it is ready; and a
# session ends when listening is toggled off. Each of those is a wall the
# assistant used to stop at, and each has one tool below.

# How far one wheel notch scrolls, in pixels. Measured against a browser on this
# machine: ten notches moved a tracked word from y=547 to y=141, so 40.6 px a
# notch — which is the 40 px Chromium and most GTK apps use. It is the
# application's number, not the compositor's, so a terminal (three lines) or a
# PDF viewer will differ; `amount` is documented as approximate for that reason.
SCROLL_PIXELS_PER_CLICK = 40
# A screen with a couple of lines of overlap, because reading down a page wants
# continuity rather than a clean cut between screenfuls. Clamped so a tiny pane
# still moves and a 4K window does not fire a burst big enough for the
# application's own momentum scrolling to run away with it.
SCROLL_MIN_CLICKS, SCROLL_MAX_CLICKS = 4, 30
SCROLL_PAGE_OVERLAP = 0.85
SCROLL_MAX_PAGES = 10
# REL_WHEEL counts up when the wheel turns away from you, which scrolls the page
# up, so "down" is negative. Measured, not taken from the header: scrolling
# "down" moved tracked words 406 px *up* the screen, twice, on a live browser.
SCROLL_SIGN = {"down": -1, "up": 1, "right": 1, "left": -1}


def _scroll_clicks(height: int, pages: int) -> int:
    """Wheel notches for `pages` screenfuls of a window `height` px tall.

    Fixed at ten notches this used to move 406 px of a 1030 px window — 40% of
    a screen while telling the model it had moved one, which is how you read
    half an article and believe you read all of it.
    """
    per_page = round(height * SCROLL_PAGE_OVERLAP / SCROLL_PIXELS_PER_CLICK)
    per_page = max(SCROLL_MIN_CLICKS, min(per_page, SCROLL_MAX_CLICKS))
    return per_page * pages

# wait_for. The cap is short on purpose: the assistant is mute while it waits,
# and silence is the one thing a voice interface cannot afford much of.
WAIT_DEFAULT = 8.0
WAIT_MAX = 25.0
# A window either exists or it does not, so ask often. OCR costs a screen
# capture and a tesseract run, so ask rarely — the poll gap is on top of that.
WAIT_POLL_WINDOW = 0.35
WAIT_POLL_TEXT = 0.6

# Clipboards hold whole documents. This is handed to the model, so it is
# bounded like OCR output is.
CLIPBOARD_LIMIT = 4000

# The notebook. Small enough that reading it back is never the expensive part
# of a turn.
NOTES_LIMIT = 40
NOTE_LENGTH_LIMIT = 240

def _layout_plan(layout: str, count: int) -> list[tuple[str, int]]:
    """Per pane after the first: (preselect direction, which pane to anchor on).

    Dwindle splits the *focused* window, so a layout is expressed as "stand on
    pane N, then open the next one to the right / below". `hl.dsp.layout` is one
    of the few dispatchers taking a positional string — `hl.dsp.layout("preselect r")`
    — which is why the manifest's "always a table" rule now says "almost always".
    """
    if count < 2:
        return []
    if layout == "main-and-side":
        # Pane 1 keeps the left half; everything else stacks down the right.
        return [("r", 0)] + [("d", i) for i in range(1, count - 1)]
    if layout == "grid":
        # 2x2. Beyond four panes a grid stops being a grid, so keep going right.
        plan = [("r", 0), ("d", 0), ("d", 1)][: max(0, count - 1)]
        return plan + [("r", i) for i in range(3, count - 1)]
    # columns: each new pane opens to the right of the one before it.
    return [("r", i) for i in range(count - 1)]


def _short_class(client: dict) -> str:
    """A window class a person would recognise, for listing in a refusal.

    `chrome-<32 hex>-Default` is an Omarchy web app; what identifies it to a
    human is its title, not its class.
    """
    klass = str(client.get("class") or "?")
    if klass.startswith("chrome-") and klass.count("-") >= 2:
        title = str(client.get("title") or "").split(" - ")[0].strip()
        return title[:24] or klass
    return klass


def _rank_windows(clients: list[dict], name: str) -> list[tuple[float, dict]]:
    """Windows that look like `name`, best first, with their scores.

    Naming a window used to cost two model turns: hypr_query(clients) to get a
    485-token dump of JSON, then the real call with an address read out of it.
    The lookup itself is 13ms. So the point of scoring is not accuracy for its
    own sake -- it is that "read my email window" should be one call.

    The ordering is class before title, because class is what the application
    *is* and title is what it currently shows. A browser and a terminal
    displaying chrome-flags.conf are both "chrome" by substring; only one of
    them is Chrome.

    initialTitle counts as much as title, for the reason the old matcher
    recorded: a page retitles itself the moment it loads, so
    `omarchy launch webapp https://bbc.com` opens a window whose initialTitle
    is "www.bbc.com_/news" and whose title is a headline a second later.

    There is deliberately no bonus for a class that *starts with* the name,
    which an earlier version had. Omarchy web apps take a class like
    `chrome-ejhkdoiecgkmdpomoahkdihbcldkgjci-Default`, so "chrome" prefixed
    five web apps and left the actual `google-chrome` below them. A prefix is
    only more meaningful than a substring when the class is a word rather than
    a generated id, and here it is not.

    Deliberately not fuzzy. Edit distance over window titles would score
    "Gmail" against "Calendar" as different by some amount nobody can reason
    about, and it would need a dependency to do it.
    """
    wanted = (name or "").strip().lower()
    if not wanted:
        return []
    scored = []
    for client in clients:
        klass = str(client.get("class") or "").lower()
        initial = str(client.get("initialClass") or "").lower()
        titles = " ".join(str(client.get(k) or "") for k in
                          ("title", "initialTitle")).lower()
        if wanted in (klass, initial):
            score = 4.0
        elif wanted in klass or wanted in initial:
            score = 2.0
        elif wanted in titles:
            score = 1.0
        else:
            continue
        scored.append((score, client))
    # Highest score first; within a score, the window you were last looking at.
    # Doing the tie-break here rather than in the caller is what lets the
    # caller ask "are the top two equal?" and mean it.
    scored.sort(key=lambda row: (-row[0], row[1].get("focusHistoryID", 999)))
    return scored


def _window_matches(client: dict, hint: str) -> bool:
    """Whether a window looks like the thing `hint` named.

    One definition of "matches", shared with the resolver, so the two cannot
    drift into disagreeing about what a name means.
    """
    return bool(_rank_windows([client], hint))


def _pane_hint(kind: str, target: str, name: str) -> str:
    """What the window this pane opens should look like."""
    target = (target or "").strip()
    if kind == "web":
        host = (urlparse(target).hostname or "").lower()
        # chrome-www.bbc.com__news-Default contains the host either way, but
        # dropping www. also matches apnews.com against a bare host.
        return host[4:] if host.startswith("www.") else host
    if kind == "tui":
        argv = shlex.split(target) if target else []
        return re.sub(r"[^A-Za-z0-9_.-]", "", name or (argv[0] if argv else "")) or ""
    if kind == "app":
        return (target[:-8] if target.endswith(".desktop") else target).partition(":")[0]
    return ""  # a terminal has no distinguishing mark worth guessing at


def _pane_command(kind: str, target: str, name: str) -> list[str] | None:
    """The argv that opens one pane, or None if the kind/target do not fit.

    Every one of these is a *plain* launch, never launch-or-focus: composing a
    workspace means new windows here, not focus stolen away to a copy of the app
    that is already open on another workspace.
    """
    target = (target or "").strip()
    if kind == "web":
        if urlparse(target).scheme.lower() not in ("http", "https"):
            return None
        return ["omarchy", "launch", "webapp", target]
    if kind == "terminal":
        return ["omarchy", "launch", "terminal", *shlex.split(target)] if target \
            else ["omarchy", "launch", "terminal"]
    if kind == "tui":
        if not target:
            return None
        argv = shlex.split(target)
        app_id = re.sub(r"[^A-Za-z0-9_.-]", "", name or argv[0]) or argv[0]
        return ["omarchy", "launch", "tui", f"--app-id={app_id}", *argv]
    if kind == "app":
        app = target[:-8] if target.endswith(".desktop") else target
        if not _DESKTOP_ID_RE.match(app):
            return None
        launcher = shutil.which("uwsm-app") or shutil.which("gtk-launch")
        return [launcher, f"{app}.desktop"] if launcher else None
    return None


# --- tool schemas -----------------------------------------------------------
# Descriptions are written for the model, and carry the failure modes it would
# otherwise have to discover by trial and error.

TOOL_SCHEMAS = [
    {
        "name": "hypr_query",
        "description": (
            "Read desktop state as JSON. Use this before acting whenever the request "
            "refers to something by name ('my browser', 'the terminal on the left') — "
            "it returns window addresses you can target precisely."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": sorted(QUERY_KINDS),
                         "description": "Which hyprctl -j query to run."},
            },
            "required": ["kind"],
            "additionalProperties": False,
        },
    },
    {
        "name": "hypr_dispatch",
        "description": (
            "Run one Hyprland dispatcher. Name it and give its arguments — do not "
            'write Lua. To switch workspace: dispatcher "focus", args '
            '{"workspace": "3"}. To move a window: dispatcher "window.move", args '
            '{"workspace": "2", "window": "address:0x55..."} with an address from '
            "hypr_query. Dispatcher names are the dotted ones in the manifest "
            '("focus", "window.close", "workspace.move"). Argument values are text, '
            "numbers or true/false. exec_cmd and exec_raw are blocked."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dispatcher": {"type": "string",
                               "description": 'Dotted name, e.g. "focus", "window.close".'},
                "args": {"type": "object",
                         "description": "The dispatcher's arguments. Omit if it takes none."},
                "message": {"type": "string",
                            "description": 'For the few dispatchers taking a positional '
                                           'string instead of args: "layout" ("preselect r"), '
                                           '"workspace.toggle_special" ("scratchpad").'},
            },
            "required": ["dispatcher"],
            "additionalProperties": False,
        },
    },
    {
        "name": "send_shortcut",
        "description": (
            "Press a key combination inside a window — the way to drive an application's "
            "own UI (new browser tab, editor save, close a dialog). Prefer this over "
            "typing text for anything that is a command rather than content. Key names "
            "are X keysyms; common spoken names ('enter', 'esc', 'page down') are "
            "translated, and a name that is not a key is refused rather than pressed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mods": {"type": "string", "description": 'e.g. "CTRL", "CTRL SHIFT", or "" for none.'},
                "key": {"type": "string",
                        "description": 'e.g. "T", "Return" (the enter key), "Escape", "Page_Down".'},
                "window": {"type": "string",
                           "description": 'Target: "activewindow", or "address:0x..." / "class:chromium".'},
            },
            "required": ["mods", "key", "window"],
            "additionalProperties": False,
        },
    },
    {
        "name": "omarchy_cli",
        "description": (
            "Run an omarchy command (see the CLI list in the manifest). This is how you "
            "change themes, volume, brightness, screenshots, night light, reminders, "
            "and the rest of the desktop's own features. Give the command without the "
            "leading 'omarchy'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": 'e.g. "theme set catppuccin" or "audio output volume +5".'},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
    {
        "name": "omarchy_help",
        "description": (
            "Find the exact omarchy command for something not in the manifest's common "
            "list — themes, bluetooth, night light, notifications, power profiles, and "
            "the hundred-odd other routes this desktop has. Give a word or two "
            "(\"dark theme\", \"night light\", \"bluetooth\"); you get back real routes "
            "with their arguments. Use it instead of guessing a route, then run what it "
            "gives you with omarchy_cli."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": 'A word or two, e.g. "theme" or "night light".'},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "find_app",
        "description": (
            "What is installed for a name or a purpose — \"zed\", \"password manager\", "
            "\"screen recorder\". Returns desktop ids and their actions. launch_app "
            "already takes a plain name, so call this only to explore or to choose."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": 'A name or a purpose, e.g. "zed" or "email".'},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "launch_app",
        "description": (
            "Start an installed application by the name a person uses (\"zed\", "
            "\"the file manager\") or its desktop id. For apps in the manifest's "
            "list of commands, omarchy_cli with the command shown there is as good. "
            "'terminal' and 'browser' are omarchy routes, not desktop ids, and "
            "fail here. For a SECOND window of an app already open, pass '<desktop- "
            "id>:<action>', e.g. 'google-chrome:new-window'. Not a shell command "
            'line, and for a web page use open_page instead — this hands off to the '
            'browser, which opens an invisible tab.'
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "app": {"type": "string",
                        "description": "An installed app, by the name a person uses or its desktop id."},
                "url": {"type": "string", "description": "Optional http(s) URL to open instead."},
            },
            "required": ["app"],
            "additionalProperties": False,
        },
    },
    {
        "name": "type_text",
        "description": (
            "Type literal text into a window, as if from the keyboard. For content — "
            "a sentence, a search query, a path. Not for key commands; use "
            "send_shortcut for those. Naming a window is a real guarantee, not a "
            "hint: the compositor routes the keys, so they cannot land somewhere "
            "else if a dialog steals focus midway. The default types into whatever "
            "is focused, which is convenient and not guaranteed — name the window "
            "when it matters what receives the text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "window": {
                    "type": "string",
                    "description": (
                        'A name like "chrome", "activewindow" (the default), or an '
                        '"address:0x...". Same values send_shortcut accepts.'
                    ),
                },
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "web_search",
        "description": (
            'Search the web and put the results on screen, in a window on the '
            'workspace the user is already looking at. This is the tool for a '
            'QUESTION — a price, a score, a date, news, who someone is, whether '
            'something is true — and for "show me" (scope images/videos). Do not open '
            "a site's home page and hope; search for the answer. The query goes in "
            'the URL, so there is nothing to type: never CTRL+T or CTRL+L, the web '
            'panes here are app windows with no address bar. Results come back as '
            'text AND stay on screen. Follow up with scroll, click_text or '
            'read_screen on the window it names.'
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for, in plain words."},
                "scope": {
                    "type": "string",
                    "enum": ["web", "news", "images", "videos", "duckduckgo"],
                    "description": (
                        '"web" (default) — Google, whose answer panel often answers the '
                        'question outright. "news" for what happened recently, "images" / '
                        '"videos" when the user wants to SEE something, "duckduckgo" for a '
                        "plain list of links when Google's panels are in the way."
                    ),
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "open_page",
        "description": (
            "Open a URL as its own window and read it. Use this rather than launch_app "
            "with a url: that one hands off to the browser, which opens a tab inside a "
            "window that already exists — nothing new appears, and you cannot tell "
            "whether it worked. This gives you a window with an address you can read, "
            "scroll and click."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "An http(s) URL."},
                "read": {"type": "boolean",
                         "description": "Read the page once it loads. Default true."},
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_terminal",
        "description": (
            "Read a terminal's output as EXACT text, through tmux. Use this instead of "
            "read_screen for anything in a terminal: it is not OCR, it works on panes "
            "that are on another workspace or not on screen at all, and it works with "
            "the display asleep. Empty target picks the pane with something running in "
            "it. Tells you whether the pane is idle or still busy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string",
                           "description": 'A tmux target like "Work:1.1", or empty for the '
                                          "most interesting pane. list_terminals shows them."},
                "lines": {"type": "integer", "description": "Scrollback lines. Default 200."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "list_terminals",
        "description": (
            "What tmux panes exist, what each is running, and whether anyone can see "
            "them. Call it when the user says \"the terminal\" and more than one is open, "
            "or to find something that is still going."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "run_in_terminal",
        "description": (
            "Run a shell command in a terminal the user can see, and read what it "
            "printed. Goes through tmux, so it needs no focus and no keypresses. Only "
            "runs in panes that are on screen — never a hidden one — and opens a "
            "terminal if none is up. A command still going after a few seconds is left "
            "running and watched; you are told, and should say so and move on rather "
            "than waiting."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "One shell command."},
                "target": {"type": "string",
                           "description": "Optional tmux target. Empty picks a visible idle pane."},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
    {
        "name": "watch_terminal",
        "description": (
            "Tell me when the command in a pane finishes. Returns immediately. In the "
            "voice daemon, it watches in the background and interrupts with the "
            "result, even if the user has moved to another workspace. Anywhere else it "
            "says it cannot, and the pane is read later with read_terminal. Use it for "
            "anything long: a build, a test run, a download. Do not poll read_terminal "
            "in a loop."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Empty for the busy pane."},
                "note": {"type": "string",
                         "description": "What it is, in the user's words — \"the test run\"."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "read_notifications",
        "description": (
            'What the desktop has notified about recently — "what was that", '
            '"did anything come in", "what did that say". Newest first. Use this '
            'rather than read_screen for anything that arrived as a notification: '
            'the toast is gone by the time you are asked, and OCR of the screen '
            'cannot recover what is no longer on it. Only notifications seen since '
            'the daemon started are here.'
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "How many to return, newest first. Default 5.",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Optional words to match against the app, summary or body — "
                        "use it when the user names an app or a subject."
                    ),
                },
            },
        },
    },
    {
        "name": "read_screen",
        "description": (
            'OCR the text on screen — CONTENT, where hypr_query gives you window '
            'names. The default reads the whole visible screen, which is what you '
            'want after composing a workspace. Only visible windows can be read; '
            'switch workspace first. OCR is imperfect on small or stylised text, so '
            'quote what you got rather than what you expected. For what is '
            'playing, or to play and pause, use system_query media and '
            'media_control, not the screen.'
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": (
                        'A name like "chrome" or "gmail" for one window — resolved '
                        "here, so you do NOT need hypr_query first. Also "
                        '"screen" for the whole focused monitor (the default), '
                        '"activewindow", or an "address:0x...". Naming one window '
                        "reads less and answers faster than the whole monitor."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Optional words to look for. With it you get the matching lines "
                        "rather than the whole screenful — use it for one fact (a price, "
                        "an error, whether a setting is on), leave it out to summarise."
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "screenshot",
        "description": (
            "Return the screen as an image, for you to look at yourself. Use this "
            "when read_screen's OCR is not enough — small, stylised or laid-out "
            "text, icons, charts, anything where position or appearance matters. "
            "It costs roughly 10x read_screen in tokens (~1800 against ~190 on a "
            "2560x1440 screen), so reach for read_screen first and come here when "
            "it disappoints. The default is the ACTIVE WINDOW, not the whole "
            "monitor, and deliberately unlike read_screen: a full monitor is "
            "downscaled to your client's size cap and 13px text arrives at 8px, "
            "while a window usually fits under the cap and arrives unscaled. Ask "
            'for "screen" only when you need the whole layout.'
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": (
                        'A name like "chrome" or "gmail" for one window, '
                        '"activewindow" / "active" for the focused one (the '
                        'default), "screen" for the whole focused monitor, or an '
                        '"address:0x...". Same values read_screen accepts.'
                    ),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "click_text",
        "description": (
            "Click something on screen by the words you can see on it — a headline, a "
            "button, a link, a menu entry. Say the text, not coordinates: "
            "click_text(text=\"Continue\") or click_text(text=\"US and Iran trade "
            "strikes\", double=True). It reads the screen, finds those words, puts the "
            "pointer on them and clicks. "
            "Only what is visible can be clicked, so switch to the right workspace first. "
            "If you are not sure of the exact wording, call read_screen and use words that "
            "actually came back. Prefer send_shortcut when a keyboard shortcut does the "
            "same job — it is faster and cannot miss."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string",
                         "description": "The visible text to click, e.g. \"Continue\"."},
                "button": {"type": "string", "enum": ["left", "right", "middle"],
                           "description": "Default left."},
                "double": {"type": "boolean",
                           "description": "True to double-click, e.g. to open an item."},
                "target": {"type": "string",
                           "description": 'Where to look: a name like "chrome" or "gmail" (resolved here — you do NOT need hypr_query first), "activewindow", "screen" for the focused monitor, or an "address:0x...". A name reads a smaller region, so it is faster, and it cannot match the same words in another window.'},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "compose_windows",
        "description": (
            'Open several windows and lay them out together, to SET UP a workspace to '
            'work or watch in — "set me up to work on the budget", "I want the game '
            'and the chat". Two to four panes, and it goes to an empty workspace, so '
            'it takes the user away from what they were doing. It is NOT how you '
            'answer a question: for that, and for anything that only needs one '
            'window, use web_search or open_page. It waits for each window to appear '
            'before placing the next, so do NOT follow it with your own move/focus '
            'calls — that races the layout it just built. Takes a few seconds; say '
            'what you are opening first.'
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "panes": {
                    "type": "array",
                    "description": "The windows to open, in order: left to right, then down.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": list(PANE_KINDS),
                                "description": (
                                    "web = a site in its own window (target is an https URL). "
                                    "terminal = a terminal (target is an optional command). "
                                    "tui = a terminal program such as btop or lazygit. "
                                    "app = an installed desktop id from the application list."
                                ),
                            },
                            "target": {
                                "type": "string",
                                "description": 'URL, command, or desktop id — e.g. "https://apnews.com", "btop", "spotify".',
                            },
                            "name": {
                                "type": "string",
                                "description": "Short label for this pane, e.g. \"AP News\". Used in the reply.",
                            },
                        },
                        "required": ["kind", "target"],
                        "additionalProperties": False,
                    },
                },
                "layout": {
                    "type": "string",
                    "enum": list(LAYOUTS),
                    "description": (
                        "columns = equal side by side, best for comparing sources. "
                        "main-and-side = first pane large, the rest stacked beside it, "
                        "best when one thing is the work and the others are reference. "
                        "grid = 2x2, for four peers."
                    ),
                },
                "workspace": {
                    "type": "string",
                    "description": (
                        'Where to build it: "next" for the first empty workspace (the '
                        'default, and usually right — it does not disturb what is open), '
                        '"current", or a number like "4".'
                    ),
                },
            },
            "required": ["panes"],
            "additionalProperties": False,
        },
    },
    {
        "name": "scroll",
        "description": (
            "Scroll a window, to bring what is below the fold into view for read_screen "
            "or click_text. Read the screen again afterwards; the text changed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["down", "up", "left", "right"]},
                "amount": {"type": "integer",
                           "description": ("Roughly how many screenfuls. Default 1, "
                                           "max 10; how far a notch goes is the "
                                           "application's choice, so read to check.")},
                "target": {
                    "type": "string",
                    "description": (
                        'A name like "chrome" (resolved here — no hypr_query needed), '
                        '"activewindow" (default), or an "address:0x...". The pointer '
                        "is moved there first, so with panes side by side this picks "
                        "which one moves."
                    ),
                },
            },
            "required": ["direction"],
            "additionalProperties": False,
        },
    },
    {
        "name": "wait_for",
        "description": (
            "Block until something has happened, then carry on. Use it between doing a "
            "thing and depending on it — a page loading, a window appearing. Returns as "
            "soon as the condition holds; a timeout comes back as a fact to report, not "
            "as an error."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "what": {
                    "type": "string",
                    "enum": ["text", "window", "window_gone"],
                    "description": ('"text": those words on screen. "window"/"window_gone": '
                                    "a window whose class or title contains the value."),
                },
                "value": {"type": "string", "description": "Words, class or title."},
                "timeout": {"type": "number", "description": "Seconds. Default 8, max 25."},
                "target": {"type": "string",
                           "description": 'Where to look: a name like "chrome" or "gmail" (resolved here — you do NOT need hypr_query first), "activewindow", "screen" for the focused monitor, or an "address:0x...". A name reads a smaller region, so it is faster, and it cannot match the same words in another window.'},
            },
            "required": ["what", "value"],
            "additionalProperties": False,
        },
    },
    {
        "name": "clipboard",
        "description": (
            "Read or write the system clipboard. Reading gives exact characters where "
            "read_screen only guesses at pixels: have the application copy something "
            "(CTRL+C, or CTRL+A then CTRL+C for a page) and read it here. Writing leaves "
            "text for the user to paste."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["read", "write"]},
                "text": {"type": "string", "description": "What to copy (write only)."},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "system_query",
        "description": (
            "Ask the machine about itself — disk, memory, battery, network, bluetooth, "
            "audio, uptime, temperature, time, OS version, what is using the CPU, "
            "what is playing. Read-only and always allowed; it needs no shell."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "enum": ["disk", "memory", "battery", "network", "bluetooth",
                             "audio", "uptime", "processes", "temperature", "time", "os",
                             "media"],
                },
            },
            "required": ["topic"],
            "additionalProperties": False,
        },
    },
    {
        "name": "media_control",
        "description": (
            "Play, pause, skip or go back in whatever media player is active: "
            "Spotify, a video in the browser. It needs no window and no screen read. "
            "The answer says what the player reports afterwards, so do not claim it "
            "is playing unless it says Playing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["play", "pause", "play-pause", "next", "previous"]},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "remember",
        "description": (
            "The notebook, and the only memory that outlives a session — when listening "
            "is toggled off the conversation is gone. Note a goal when you take one on "
            "and each step as it lands; list it back when the user picks the thread up "
            "again (\"where were we\")."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["note", "list", "forget"]},
                "text": {
                    "type": "string",
                    "description": ("note: one line that still makes sense tomorrow. "
                                    "forget: words identifying it, or \"all\"."),
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_shell",
        "description": (
            "Run a shell command. Disabled unless the user turned it on in config. "
            "Only reach for it when no other tool can express the request."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
]


# What "ask the machine about itself" is allowed to run. Fixed argv, no shell,
# nothing that writes: this is a reference table, not a command builder, so a
# misheard sentence cannot steer it anywhere. Anything not installed is skipped
# rather than reported as an error — `sensors` and `nmcli` are both optional.
SYSTEM_QUERIES: dict[str, object] = {
    "disk": [("", ["df", "-h", "--output=target,size,used,avail,pcent",
                   "-x", "tmpfs", "-x", "devtmpfs", "-x", "efivarfs"])],
    "memory": [("", ["free", "-h"])],
    "battery": None,  # filled in below; it reads /sys rather than shelling out
    "network": [("connections", ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE",
                                 "connection", "show", "--active"]),
                ("state", ["nmcli", "-t", "-f", "STATE,CONNECTIVITY", "general"])],
    # --timeout is not optional. With no controller present bluetoothctl waits
    # for one forever: on this machine `bluetoothctl show` was still running at
    # five seconds, which on a voice channel is five seconds of silence.
    "bluetooth": None,  # filled in below; it checks for an adapter first
    "audio": [("default sink", ["pactl", "get-default-sink"]),
              ("volume", ["pactl", "get-sink-volume", "@DEFAULT_SINK@"]),
              ("muted", ["pactl", "get-sink-mute", "@DEFAULT_SINK@"])],
    "uptime": [("", ["uptime", "-p"]), ("booted", ["uptime", "-s"])],
    # ps lists every process on the machine. The question is "what is making
    # the fan spin", and the answer is the top of that list, not all of it.
    "processes": [("busiest (%cpu %mem)", ["ps", "-eo", "pcpu,pmem,comm",
                                           "--sort=-pcpu", "--no-headers"], 10)],
    "temperature": [("", ["sensors"])],
    "time": [("", ["date", "+%A %-d %B %Y, %H:%M %Z"]),
             ("timezone", ["timedatectl", "show", "-p", "Timezone", "--value"])],
    "os": [("", ["uname", "-sr"]),
           ("distribution", ["sh", "-c", "true"])],  # replaced below
}


def _os_release() -> str:
    for line in Path("/etc/os-release").read_text().splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.partition("=")[2].strip().strip('"')
    return ""


def _os_report(executor) -> "Result":
    parts = []
    try:
        if pretty := _os_release():
            parts.append(pretty)
    except OSError:
        pass
    for argv in (["uname", "-sr"], ["hyprctl", "version", "-j"]):
        got = executor._shell(argv, timeout=8, limit=800)
        if not got.ok:
            continue
        if argv[0] == "hyprctl":
            try:
                parts.append("Hyprland " + json.loads(got.output).get("tag", "").lstrip("v"))
            except (json.JSONDecodeError, AttributeError):
                pass
        else:
            parts.append(got.output.strip())
    return Result(True, "\n".join(p for p in parts if p) or "could not read the OS version")


def _bluetooth_report(executor) -> "Result":
    if not Path("/sys/class/bluetooth").exists():
        return Result(True, "this machine has no bluetooth adapter")
    parts = []
    for label, argv in (("adapter", ["bluetoothctl", "--timeout", "3", "show"]),
                        ("connected", ["bluetoothctl", "--timeout", "3",
                                       "devices", "Connected"])):
        got = executor._shell(argv, timeout=8, limit=1200)
        if got.ok and got.output.strip():
            parts.append(f"{label}:\n{got.output.strip()}")
    return Result(True, "\n\n".join(parts) or "the bluetooth adapter did not answer")


SYSTEM_QUERIES["battery"] = lambda executor: executor._battery()
SYSTEM_QUERIES["media"] = lambda executor: executor._media_status()
SYSTEM_QUERIES["bluetooth"] = _bluetooth_report
SYSTEM_QUERIES["os"] = _os_report


def attach_waker(executor: "Executor") -> "Executor":
    """Give this executor the compositor's event stream, if there is one.

    Called by the entry points that run against a real session. Kept out of
    Executor.__init__ so that constructing one in a test, or in the nix
    sandbox, never opens a socket.
    """
    listener = hypr_events.listener()
    executor.waker = listener if listener is not None else None
    executor.input_helper = virtual_input.Helper(extent=executor._layout_extent)
    return executor


def tools_for(config: Config) -> list[dict]:
    """The schemas this configuration can actually run.

    `run_shell` is off by default, and a tool that is always going to be
    refused is worse than a tool that is not offered: it costs its schema on
    every turn, and when the model reaches for it — which it does, it is the
    one tool that can express anything — the refusal costs a whole round trip
    before it tries the tool it should have used.
    """
    off = set()
    if not config.allow_shell:
        off.add("run_shell")
    if not config.allow_notifications:
        off.add("read_notifications")
    return [schema for schema in TOOL_SCHEMAS if schema["name"] not in off]


_BARE_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]+$")


def _wait_description(what: str, value: str) -> str:
    return {
        "text": f"{value!r} appeared on screen",
        "window": f"a window matching {value!r} opened",
        "window_gone": f"the window matching {value!r} closed",
    }[what]


def _wait_timeout(what: str, value: str) -> str:
    return {
        "text": f"{value!r} still is not on screen",
        "window": f"no window matching {value!r} has opened",
        "window_gone": f"the window matching {value!r} is still open",
    }[what]


def _matching_lines(text: str, query: str, context: int = 1) -> str:
    """The lines of `text` that answer `query`, with a line either side.

    A screenful of OCR is a couple of thousand tokens charged against a
    per-minute budget. When the question is "what is the price" the answer is
    one line, and sending the other ninety costs the user a turn.
    """
    wanted = [w for w in re.split(r"\W+", query.lower()) if w]
    lines = text.splitlines()
    if not wanted or not lines:
        return ""
    keep: set[int] = set()
    for index, line in enumerate(lines):
        lowered = line.lower()
        hits = sum(1 for w in wanted if w in lowered)
        if hits >= _required_hits(len(wanted)):
            keep.update(range(max(0, index - context),
                              min(len(lines), index + context + 1)))
    if not keep:
        return ""
    out, previous = [], None
    for index in sorted(keep):
        if previous is not None and index > previous + 1:
            out.append("…")
        out.append(lines[index])
        previous = index
    return "\n".join(out).strip()


def _chord(mods: str, key: str) -> str:
    """"CTRL SHIFT" + "Return" -> "CTRL+SHIFT+Return"; a bare key stays bare."""
    parts = [p for p in (mods or "").replace("+", " ").split() if p]
    return "+".join([*parts, key])


def _check_dispatch_args(dispatcher: str, args: dict) -> tuple[dict, str | None]:
    """The checks that used to be run by regex over model-written Lua.

    Same three rules, now applied to the arguments themselves. They survived
    the move because each one exists for a bug that actually happened, not for
    tidiness -- see the notes on each below.
    """
    args = dict(args or {})

    # A bare hex in `window` matches nothing: Hyprland answers "window not
    # found" as a *warning*, which arrives with a zero exit, so a close that
    # did nothing was reported as success. A bare hex can only be an address,
    # so fixing it is unambiguous.
    window = args.get("window")
    if isinstance(window, str) and _BARE_ADDRESS_RE.match(window):
        args["window"] = f"address:{window}"

    # hypr_dispatch is the back door to every dispatcher, send_shortcut
    # included, so the keysym check has to live here too -- otherwise "Enter"
    # is still a silent no-op as long as the model asks for the dispatcher
    # rather than the tool. Hyprland answers ok for a keysym it cannot resolve
    # and presses nothing.
    if dispatcher.rsplit(".", 1)[-1] == "send_shortcut":
        if isinstance(args.get("mods"), str):
            value, problem = normalise_mods(args["mods"])
            if problem:
                return args, problem
            args["mods"] = value
        if isinstance(args.get("key"), str):
            value, problem = normalise_key(args["key"])
            if problem:
                return args, problem
            args["key"] = value

    # The single most repeated mistake in the session log: the model reaches
    # for change_id to navigate, and change_id RENAMES a workspace -- it needs
    # both `workspace` (which one) and `id` (its new number). Given one key it
    # does nothing useful, and Hyprland says so quietly enough that the
    # assistant then told the user there was no workspace 5. Saying it in the
    # manifest did not stop it; refusing the call and naming the right one
    # does, and costs one tool round instead of a workspace switch that
    # silently never happened.
    if dispatcher == "workspace.change_id" and not {"workspace", "id"} <= set(args):
        return args, ('hl.dsp.workspace.change_id renames a workspace and needs both '
                      '`workspace` and `id`. To SWITCH to a workspace, call '
                      'the focus dispatcher with workspace = "N" instead.')
    return args, None


def _desktop_entry_path(app_id: str) -> Path | None:
    for directory in app_dirs():
        candidate = directory / f"{app_id}.desktop"
        if candidate.is_file():
            return candidate
    return None


def _desktop_entry_exists(app_id: str) -> bool:
    """Whether <app_id>.desktop is installed anywhere the launcher will look."""
    return _desktop_entry_path(app_id) is not None


def desktop_actions(app_id: str) -> list[str]:
    """The extra entry points a .desktop declares, e.g. Chrome's new-window.

    This is how a second window gets opened. Plain `launch` on an app that is
    already running focuses what is there, which is right for "open my browser"
    and wrong for "open another one".
    """
    path = _desktop_entry_path(app_id)
    if path is None:
        return []
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("Actions="):
            return [a for a in line.split("=", 1)[1].split(";") if a]
    return []


def _desktop_wm_class(app_id: str) -> str:
    """The window class a .desktop says its app maps with, or "".

    The desktop id alone never matches some windows: Telegram's entry is
    org.telegram.desktop and its window is TelegramDesktop. Once
    _await_new_window stopped taking any new window it found (#75), that
    pane would never have composed without this.
    """
    path = _desktop_entry_path(app_id)
    if path is None:
        return ""
    groups = 0
    for line in path.read_text(errors="replace").splitlines():
        # Only the [Desktop Entry] group. A key under an action group is not
        # the app's class.
        if line.startswith("["):
            groups += 1
            if groups > 1:
                break
        elif line.startswith("StartupWMClass="):
            return line.split("=", 1)[1].strip()
    return ""


# Multi-word omarchy routes the model tends to write with hyphens.
_HYPHENATED_ROUTES = {
    "launch-or-focus": ["launch", "or", "focus"],
    "launch_or_focus": ["launch", "or", "focus"],
    "install-and-launch": ["install", "and", "launch"],
}


def normalise_omarchy(command: str) -> tuple[list[str], str | None]:
    """Split an omarchy command line and repair the two mistakes it arrives with.

    Both are in the session log. `omarchy launch-or-focus webapp ...` writes a
    multi-word route as a hyphenated command name, which is not a route and
    opens nothing. And the manifest lists signatures like
    `omarchy launch or focus webapp <window-pattern> <url>`, whose placeholders
    have been passed through verbatim.

    Shared with `describe`, so the log, the dry run and the confirmation prompt
    all show the command that would actually run.
    """
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return [], f"could not parse command: {exc}"
    while argv and argv[0] == "omarchy":
        argv = argv[1:]
    if not argv:
        return [], "empty command"
    if argv[0] in _HYPHENATED_ROUTES:
        argv = _HYPHENATED_ROUTES[argv[0]] + argv[1:]
    placeholders = [a for a in argv if len(a) > 2 and a.startswith("<") and a.endswith(">")]
    if placeholders:
        return argv, (
            f"{', '.join(placeholders)} is a placeholder from the command signature, "
            "not a value. Replace it with a real one — a window pattern is a short "
            'word matching the window, e.g. "x" for x.com.')
    if error := _misused_launch_browser(argv):
        return argv, error
    return argv, None


def _misused_launch_browser(argv: list[str]) -> str | None:
    """Refuse `omarchy launch browser <url>`, naming the tool that works.

    The manifest lists `omarchy launch browser [url]`, read off the live
    system, so the model reaches for it — and it is the wrong shape for an
    assistant. It hands the URL to the running browser, which opens a TAB in a
    window that already exists: nothing new appears in hyprctl, so there is no
    window to wait for, read, scroll, move or close. In the session log that is
    exactly what happened — "I don't see the Google results; the active window
    is still the OpenAI usage page. The search page didn't appear."

    Persona wording did not beat the manifest here; a refusal that names the
    right call at the moment of the mistake does, which is the same fix already
    used for hl.dsp.workspace.change_id.
    """
    if argv[:2] != ["launch", "browser"]:
        return None
    urls = [a for a in argv[2:] if urlparse(a).scheme.lower() in ("http", "https")]
    if not urls:
        return None  # "open my browser" is a perfectly good request
    url = urls[0]
    query = parse_qs(urlparse(url).query).get("q", [""])[0]
    if query:
        return (f"that is a search URL. Use web_search with query={query!r} — it opens "
                "the results as their own window, which this does not: `omarchy launch "
                "browser <url>` opens a tab inside an existing window, so no new window "
                "appears and you cannot read or verify it.")
    return (f"use open_page with url={url!r} instead. `omarchy launch browser <url>` "
            "opens a tab inside a window that already exists, so nothing new appears in "
            "the window list and you cannot wait for it, read it, or tell if it worked.")


class Executor:
    """Runs tool calls against the real desktop (or narrates them, in dry-run)."""

    def __init__(self, config: Config, on_action: Callable[[str, str], None] | None = None,
                 on_record: Callable[[str], None] | None = None):
        self.config = config
        self.policy = Policy(config)
        # How a held action asks to be released. "Out loud" is true of a voice
        # turn and false of an MCP client, which has no microphone and a user
        # reading text -- and telling a text agent to wait for speech leaves it
        # either stuck or hunting for a way around the gate.
        self.confirm_instruction = (
            "This action needs spoken confirmation. Stop here and ask the user "
            "to confirm out loud; do not try another route around it.")
        # Only a daemon that polls `poll_watches` sets this. A tool must not
        # promise what its process cannot keep: nothing polls in `say` or the
        # MCP server, so "I will say when it finishes" was a lie there (#74).
        self.announces_watches = False
        self.on_action = on_action or (lambda name, desc: None)
        # Set by whoever is running a task -- the bench, or the daemon when
        # trace_timings is on. None means nothing is being measured, which is
        # the normal case and costs one attribute test per call.
        self.trace: trace_mod.Trace | None = None
        # Hyprland's event stream, attached by the entry points that have a
        # session to listen to (see attach_waker). None means "poll", which is
        # what the sandbox and every unit test get.
        self.waker: hypr_events.Listener | None = None
        # The Wayland input helper, attached the same way and for the same
        # reason: constructing an Executor in a test must not spawn a process.
        # Its lifetime is a turn -- see end_turn, which is the release (#30).
        self.input_helper: virtual_input.Helper | None = None

        self.pending: tuple[str, dict] | None = None
        # When the hold was created. The voice session has a better signal than
        # a clock -- it knows the user spoke, and when -- and never reads this.
        # An MCP client has no turns to observe, so its confirmation is dated
        # against this instead. See mcp_server.CONFIRM_DELAY.
        self.pending_since: float | None = None
        self.transcript: list[str] = []
        # Where a call the policy refused or held is logged. The transcript is
        # not the log: nothing reads it. What ran reaches the log through
        # on_action; before this, a denied `rm -rf` and a held power-off left
        # no line in `omarchy-voice log` at all. A no-op by default -- `say`
        # prints its result, and the MCP server's stdout is the protocol.
        self.on_record = on_record or (lambda line: None)
        # The window the last web_search opened, so the next one can replace it.
        self._last_search_window: str | None = None
        # tmux panes being watched for a command to finish, by target.
        self._watches: dict[str, dict] = {}
        self._lock = threading.Lock()

    def record(self, line: str) -> None:
        """A call that did not run: into the transcript and out to the log.

        Only for what the policy decided against. RUN, CONFIRM and CANCEL stay
        plain transcript appends, because on_action and the sessions already
        log them -- sending them here too would write each one twice.
        """
        self.transcript.append(line)
        self.on_record(line)

    # -- dispatch -----------------------------------------------------------
    def call(self, name: str, args: dict) -> Result:
        # The lock is timed separately from the work. A tool that is fast but
        # spent four seconds behind another one is slow to the person waiting,
        # and averaging that into the tool's own time hides which is which.
        waiting = self.trace.mark(trace_mod.LOCK, name) if self.trace else None
        with self._lock:
            if waiting:
                waiting.close()
            span = self.trace.mark(trace_mod.TOOL, name) if self.trace else None
            try:
                return self._call_locked(name, args)
            finally:
                if span:
                    span.close()

    def _call_locked(self, name: str, args: dict) -> Result:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return Result(False, f"unknown tool {name!r}")
        if name == "launch_app" and not args.get("url"):
            # Before describe: the gate must judge `launch dev.zed.Zed`, not
            # `launch zed`, or a rule written against an id misses the name (#70).
            resolved = self._resolve_app(args)
            if isinstance(resolved, Result):
                return resolved
            args = resolved
        description = self.describe(name, args)
        try:
            self.policy.check(description)
        except Denied as exc:
            self.record(f"DENIED  {description} ({exc})")
            return Result(False, f"refused: {exc}. Tell the user you will not do that.")
        except NeedsConfirmation:
            if self.pending:
                held = self.describe(*self.pending)
                self.record(f"HOLD    refused second gate; still holding {held}")
                return Result(False,
                              f"another action is already waiting for confirmation: {held}. "
                              "Confirm or cancel it first; do not try a second gated action.")
            self.pending = (name, args)
            self.pending_since = time.monotonic()
            self.record(f"HOLD    {description}")
            return Result(False, self.confirm_instruction)

        self.transcript.append(f"RUN     {description}")
        self.on_action(name, description)
        if self.config.dry_run and name not in READ_ONLY_TOOLS:
            validator = getattr(self, f"_validate_{name}", None)
            if validator is not None:
                try:
                    error = validator(**args)
                except TypeError as exc:
                    return Result(False, f"bad arguments: {exc}")
                if error:
                    return Result(False, error)
            return Result(True, f"[dry-run] would run: {description}")
        try:
            return handler(**args)
        except TypeError as exc:
            return Result(False, f"bad arguments: {exc}")

    def run_pending(self) -> Result:
        """Execute the action the user just confirmed out loud."""
        with self._lock:
            if not self.pending:
                return Result(False, "nothing was waiting for confirmation")
            name, args = self.pending
            self.pending = None
            self.pending_since = None
            handler = getattr(self, f"_tool_{name}")
            description = self.describe(name, args)
            self.transcript.append(f"CONFIRM {description}")
            self.on_action(name, description)
            if self.config.dry_run and name not in READ_ONLY_TOOLS:
                return Result(True, f"[dry-run] would run: {description}")
            try:
                return handler(**args)
            except TypeError as exc:
                return Result(False, f"bad arguments: {exc}")


    def end_turn(self) -> None:
        """Let go of anything the input helper was holding.

        Called wherever a turn can end, including by an exception. This is the
        whole of #30: closing the pipe destroys the virtual keyboard, and a
        virtual keyboard can only be released by its owner, so an abandoned
        chord is released by the abandonment rather than by anyone remembering
        to release it.
        """
        if self.input_helper is not None:
            self.input_helper.close()

    def drop_pending(self) -> str | None:
        with self._lock:
            if not self.pending:
                return None
            held = self.describe(*self.pending)
            self.pending = None
            self.pending_since = None
            self.transcript.append(f"CANCEL  {held}")
            return held

    @staticmethod
    def describe(name: str, args: dict) -> str:
        if name == "hypr_dispatch":
            # Built from the values that will actually be sent, so the line the
            # policy gate matches cannot disagree with what runs. When this
            # returned the model's raw Lua, a description reading as a
            # workspace switch could carry a shell command inside it.
            if (lua := args.get("lua")) is not None:
                return lua
            dispatcher = args.get("dispatcher", "")
            if (message := args.get("message")) is not None:
                return f'dispatch {dispatcher} {message!r}'
            fields = args.get("args") or {}
            if not isinstance(fields, dict) or not fields:
                return f'dispatch {dispatcher}'.rstrip()
            # Sorted so the transcript of the same action reads the same twice.
            shown = " ".join(f"{k}={fields[k]!r}" for k in sorted(fields))
            return f'dispatch {dispatcher} {shown}'
        if name == "omarchy_cli":
            argv, _ = normalise_omarchy(args.get("command", ""))
            return " ".join(["omarchy", *argv]).rstrip()
        if name == "run_shell":
            return args.get("command", "")
        if name == "launch_app":
            return f'launch {args.get("app", "")}{" " + args["url"] if args.get("url") else ""}'
        if name == "send_shortcut":
            # Report the keysym that will actually be pressed, not the word the
            # model said: the log is the only record of what hit the window.
            mods = normalise_mods(args.get("mods", ""))[0]
            keysym = normalise_key(args.get("key", ""))[0]
            chord = _chord(mods if mods is not None else args.get("mods", ""),
                           keysym or args.get("key", ""))
            return f'press {chord} in {args.get("window", "")}'
        if name == "type_text":
            return f'type {args.get("text", "")!r}'
        if name == "hypr_query":
            return f'query hyprctl {args.get("kind", "")}'
        if name == "omarchy_help":
            return f'look up omarchy command {args.get("query", "")!r}'
        if name == "find_app":
            return f'find apps: {args.get("query", "")!r}'
        if name == "click_text":
            kind = "double-click" if args.get("double") else "click"
            # Name the target: the transcript is the only record of where a
            # click landed, and "click 'Delete'" reads very differently
            # depending on which window it went to.
            where = (args.get("target") or "screen").strip()
            scope = "" if where in ("screen", "", "monitor", "all") else f" in {where}"
            return (f'{kind} {args.get("button", "left")} on '
                    f'{args.get("text", "")!r}{scope}')
        if name == "read_notifications":
            if query := (args.get("query") or "").strip():
                return f'read notifications matching {query!r}'
            return "read recent notifications"
        if name == "read_screen":
            where = args.get("target", "screen")
            if query := (args.get("query") or "").strip():
                return f'read screen ({where}) looking for {query!r}'
            return f'read screen ({where})'
        if name == "screenshot":
            return f'screenshot ({args.get("target", "active")})'
        if name == "scroll":
            return (f'scroll {args.get("target", "activewindow")} '
                    f'{args.get("direction", "")} x{args.get("amount", 1)}')
        if name == "wait_for":
            where = (args.get("target") or "screen").strip()
            scope = ("" if where in ("screen", "", "monitor", "all")
                     or args.get("what") != "text" else f" in {where}")
            return f'wait for {args.get("what", "")} {args.get("value", "")!r}{scope}'
        if name == "clipboard":
            if args.get("action") == "write":
                return f'copy to clipboard: {str(args.get("text", ""))[:60]!r}'
            return "read the clipboard"
        if name == "web_search":
            scope = args.get("scope", "web")
            return f'search the {scope} for {args.get("query", "")!r}'
        if name == "open_page":
            return f'open {args.get("url", "")}'
        if name == "read_terminal":
            return f'read terminal {args.get("target", "") or "(busiest pane)"}'
        if name == "list_terminals":
            return "list the terminal panes"
        if name == "run_in_terminal":
            return f'run in terminal: {args.get("command", "")}'
        if name == "watch_terminal":
            return f'watch terminal {args.get("target", "") or "(busy pane)"}'
        if name == "system_query":
            return f'look up system {args.get("topic", "")}'
        if name == "media_control":
            return f'media {args.get("action", "")}'
        if name == "remember":
            action = args.get("action", "")
            if action == "list":
                return "read the notebook"
            return f'{action} note {str(args.get("text", ""))[:60]!r}'
        if name == "compose_windows":
            panes = args.get("panes") or []
            labels = ", ".join(
                str(p.get("name") or p.get("target", ""))[:32]
                for p in panes if isinstance(p, dict))
            where = args.get("workspace", "next")
            return (f'compose {len(panes)} windows on workspace {where} '
                    f'({args.get("layout", "columns")}): {labels}')
        return f'{name} {args}'

    # -- helpers ------------------------------------------------------------
    def _shell(self, cmd: list[str], timeout: float = 20.0, grace: float | None = None,
               limit: int = OUTPUT_LIMIT) -> Result:
        """Run a command, timed as its own trace phase.

        The span carries `cmd[0]` and nothing else. The arguments are not
        recorded: a program name comes from a fixed set this code chooses, and
        an argument can be anything the user said.

        It closes when this returns, not when the child exits -- `grace` below
        deliberately returns while a launched application keeps running, and
        timing to exit would report a terminal as costing minutes.
        """
        if self.trace is None:
            return self._spawn(cmd, timeout, grace, limit)
        with self.trace.mark(trace_mod.SUBPROCESS, cmd[0]):
            return self._spawn(cmd, timeout, grace, limit)

    def _spawn(self, cmd: list[str], timeout: float = 20.0, grace: float | None = None,
               limit: int = OUTPUT_LIMIT) -> Result:
        """Run a command and read its result.

        `grace` is for commands that start an application. `omarchy launch
        terminal` does not return while the terminal is open, so waiting for it
        blocked the assistant for the full timeout and then reported failure —
        and the model, told the launch had failed, launched again. That is what
        turned one "open a terminal" into terminals appearing every 30 seconds.

        With a grace set, a process still alive after that many seconds is taken
        to be a running application: it is left alone, reaped in the background,
        and reported as started. Callers that need the output (queries) leave
        `grace` unset and wait the full timeout as before.

        LAUNCH_GRACE is measured, not guessed: on this machine every way an
        `omarchy launch` can fail — unknown route, missing argument — returns in
        under 0.35 s. The 1.2 s it used to wait was 0.85 s of silence added to
        every launch, and composition pays that per pane.
        """
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
        except FileNotFoundError:
            return Result(False, f"{cmd[0]} is not installed")
        try:
            stdout, stderr = proc.communicate(timeout=grace if grace is not None else timeout)
        except subprocess.TimeoutExpired:
            if grace is None:
                proc.kill()
                proc.communicate()
                return Result(False, f"{cmd[0]} timed out")
            # Still running: a foreground application, not a hung command.
            # Reap it off-thread so the daemon does not collect zombies.
            threading.Thread(target=proc.communicate, daemon=True).start()
            return Result(True, "started")
        out = (stdout or "").strip()
        err = (stderr or "").strip()
        if proc.returncode != 0:
            return Result(False, err or out or f"exit {proc.returncode}")
        # hyprctl reports dispatcher failures on stdout with a zero exit code.
        if out.startswith("error:"):
            return Result(False, out)
        if len(out) > limit:
            # Say so. This used to cut silently at 4000 characters, which is
            # about five windows' worth of `hyprctl -j clients` — past that the
            # model was handed a JSON document with the end sliced off, and the
            # parse failure looked to it like an empty desktop.
            return Result(True, out[:limit] + f"\n… [truncated at {limit} characters]")
        return Result(True, out)

    # -- tools --------------------------------------------------------------
    def _tool_hypr_query(self, kind: str) -> Result:
        if kind not in QUERY_KINDS:
            return Result(False, f"unknown query {kind!r}")
        # Read the whole document, then slim it. Cutting first was a silent
        # correctness bug: `hyprctl -j clients` runs about 750 characters per
        # window, so from roughly the fifth window on, the model was parsing a
        # JSON array with no closing bracket and concluding nothing was open.
        result = self._shell(["hyprctl", "-j", kind], limit=1 << 22)
        if result.ok and kind == "clients":
            try:
                clients = json.loads(result.output)
            except json.JSONDecodeError:
                return result
            slim = [
                {k: c.get(k) for k in
                 ("address", "class", "title", "pid", "floating", "fullscreen")}
                | {"workspace": c.get("workspace", {}).get("name")}
                for c in clients
            ]
            return Result(True, json.dumps(slim))
        return result

    def _render(self, dispatcher: str, args: dict | None = None,
                message: str | None = None) -> tuple[str | None, str | None]:
        """Check the arguments, then render. Used by the tool and internally."""
        args, error = _check_dispatch_args(dispatcher, args or {})
        if error:
            return None, error
        return render_dispatch(dispatcher, args, message,
                               allow_shell=self.config.allow_shell)

    def _dispatch(self, dispatcher: str, args: dict | None = None,
                  message: str | None = None) -> Result:
        """Render one dispatcher and run it. Every internal call goes here.

        These interpolate addresses and coordinates that came from hyprctl,
        not from the model, so they were never the hole -- but routing them
        through the same renderer is what makes "nothing else builds Lua" a
        property you can check with grep rather than a claim.
        """
        expr, error = self._render(dispatcher, args, message)
        if error:
            return Result(False, error)
        return self._dispatch_lua(expr)

    def _tool_hypr_dispatch(self, dispatcher: str = "", args: dict | None = None,
                            message: str | None = None,
                            lua: str | None = None) -> Result:
        """One dispatcher, named and given arguments -- not written as Lua.

        The `lua` field is the escape hatch for a dispatcher this schema has
        not anticipated, and it is only honoured with allow_shell. Accepting
        Lua source from the model is what made `allow_shell = false` untrue:
        the regex that used to guard this accepted any program as long as it
        sat inside the parentheses of an hl.dsp.* call, and Hyprland evaluates
        that position, with hl.exec_cmd in scope.
        """
        if lua is not None:
            if not self.config.allow_shell:
                return Result(False,
                              "raw Lua needs allow_shell. Name the dispatcher "
                              'instead: dispatcher = "focus", args = { workspace = "3" }.')
            if dispatcher or args or message:
                return Result(False, "give either lua or dispatcher, not both")
            return self._dispatch_lua(lua.strip())
        expr, error = self._render(dispatcher, args, message)
        if error:
            return Result(False, error)
        return self._dispatch_lua(expr)

    def _dispatch_lua(self, lua: str) -> Result:
        """Run one dispatcher, treating "not found" as the failure it is.

        hyprctl reports a missing target as `warning: ... not found` on stdout
        with a zero exit code, so it read as success. The model was told a
        window had been closed when nothing had happened, and moved on to the
        next step of a request whose first step had silently failed.
        """
        result = self._shell(["hyprctl", "dispatch", lua])
        if result.ok and "not found" in result.output.lower():
            first = result.output.splitlines()[0] if result.output else "not found"
            return Result(False, first.strip())
        return result

    def _validate_send_shortcut(self, mods: str, key: str,
                                window: str = "activewindow") -> str | None:
        return normalise_mods(mods)[1] or normalise_key(key)[1]

    def _tool_send_shortcut(self, mods: str, key: str, window: str = "activewindow") -> Result:
        """Press a chord, having first checked the keys exist.

        Hyprland answers `ok` for a keysym it cannot resolve and presses
        nothing, so `key = "Enter"` — the word a person actually says — was a
        silent no-op that the model then reported as done. Both halves are
        resolved here, and an unresolvable one is an error the model can read.
        """
        if refused := self._input_refused(window):
            return Result(False, refused)
        clean_mods, error = normalise_mods(mods)
        if error:
            return Result(False, error)
        keysym, error = normalise_key(key)
        if error:
            return Result(False, error)
        result = self._dispatch("send_shortcut", {
            "mods": clean_mods, "key": keysym, "window": window})
        # Say so when the name was translated, but not for a mere case fold —
        # "read 'T' as t" is noise the model would repeat out loud.
        if result.ok and keysym.lower() != (key or "").strip().lower():
            return Result(True, f"pressed {_chord(clean_mods, keysym)} "
                                f"(read {key!r} as {keysym})")
        return result

    def _validate_omarchy_cli(self, command: str) -> str | None:
        return normalise_omarchy(command)[1]

    def _tool_omarchy_cli(self, command: str) -> Result:
        argv, error = normalise_omarchy(command)
        if error:
            return Result(False, error)
        # Only `omarchy launch ...` starts a foreground application; everything
        # else returns promptly and may have output worth reading, so it keeps
        # the full wait.
        grace = LAUNCH_GRACE if argv[0] == "launch" else None
        return self._shell(["omarchy", *argv], timeout=30, grace=grace)

    def _validate_launch_app(self, app: str, url: str = "") -> str | None:
        if url:
            scheme = urlparse(url).scheme.lower()
            if scheme not in ("http", "https"):
                return "url must be http or https"
            return None
        app = (app or "").strip()
        # "<desktop-id>:<action>" is a valid shape; validate the id half. The
        # action itself is checked against the entry's declared Actions later,
        # where a wrong one can name the alternatives.
        app = app.partition(":")[0].strip()
        if app.endswith(".desktop"):
            app = app[:-8]
        if not _DESKTOP_ID_RE.match(app):
            if not self.config.allow_shell:
                # Say what to do next. A bare refusal made the model retry the
                # same shape, and a failing launch loop is how a single "open a
                # terminal" turned into terminals opening every 30 seconds.
                return ("app must be a desktop id, not a command line. If this is "
                        "an app from the manifest's \"Apps this desktop already "
                        "knows how to open\" list, call omarchy_cli with the exact "
                        "command shown there instead.")
            try:
                argv = shlex.split(app)
            except ValueError as exc:
                return f"could not parse command: {exc}"
            if not argv:
                return "empty app"
        return None

    def _resolve_app(self, args: dict):
        """A name a person uses, turned into the desktop id it means (#70).

        Only name-shaped input: a command line never reaches the matcher. A
        clear match is rewritten; several close ones come back as a choice and
        nothing runs; anything weaker is left for the usual refusal.
        """
        app, colon, action = (args.get("app") or "").strip().partition(":")
        app = app.strip()
        bare = app[:-8] if app.endswith(".desktop") else app
        if not bare or _desktop_entry_exists(bare) or not _APP_NAME_RE.match(app):
            return args
        found = capabilities.find_apps(app)
        match = capabilities.clear_match(found)
        if match:
            self.transcript.append(f"RESOLVE {app!r} → {match['id']}")
            return {**args, "app": match["id"] + colon + action}
        if found and found[0][0] >= 70:
            names = ", ".join(f"{row['name']} ({row['id']})" for _, row in found[:5])
            return Result(False, f"more than one app fits {app!r}: {names}. Ask "
                                 "which, or call launch_app with the id.")
        return args

    def _tool_find_app(self, query: str) -> Result:
        found = capabilities.find_apps(query)
        if not found:
            return Result(True, f"nothing installed matches {query!r}; it is not "
                                "installed under that name.")
        rows = []
        for _, row in found:
            line = f"  {row['name']} ({row['id']})"
            if row["generic"]:
                line += f" — {row['generic']}"
            if row["actions"]:
                line += f" [actions: {', '.join(row['actions'])}]"
            rows.append(line)
        return Result(True, "\n".join(rows) + "\n\nOpen one with launch_app, by name or id.")

    def _tool_launch_app(self, app: str, url: str = "") -> Result:
        error = self._validate_launch_app(app, url)
        if error:
            return Result(False, error)
        if url:
            return self._shell(["xdg-open", url], timeout=10, grace=LAUNCH_GRACE)
        app = (app or "").strip()
        # "google-chrome:new-window" — a desktop entry plus one of the actions
        # it declares. uwsm-app takes this shape directly.
        app, _, action = app.partition(":")
        app = app.strip()
        action = action.strip()
        if app.endswith(".desktop"):
            app = app[:-8]
        if not _DESKTOP_ID_RE.match(app):
            if action:
                return Result(False, f"{app!r} is not a desktop id, so it has no actions")
            return self._shell(shlex.split(app), timeout=10, grace=LAUNCH_GRACE)
        # uwsm-app happily returns success for a .desktop that does not exist,
        # so the model was told "opened" while nothing appeared and then tried
        # again. Check first and hand back the route that does work.
        if not _desktop_entry_exists(app):
            return Result(False,
                          f"no desktop entry named {app!r} on this system. find_app looks "
                          "up what is installed by name or purpose. If this app is in "
                          "the manifest's \"Apps this desktop already knows how to open\" "
                          "list, call omarchy_cli with the exact command shown there.")
        if action:
            available = desktop_actions(app)
            if action not in available:
                return Result(False, f"{app!r} has no action {action!r}"
                                     + (f"; it offers {', '.join(available)}" if available
                                        else " and declares none"))
        launcher = shutil.which("uwsm-app") or shutil.which("gtk-launch")
        if not launcher:
            return Result(False, "no desktop launcher (uwsm-app or gtk-launch)")
        target = f"{app}.desktop:{action}" if action else f"{app}.desktop"
        return self._shell([launcher, target], timeout=10, grace=LAUNCH_GRACE)

    def _tool_omarchy_help(self, query: str) -> Result:
        matches = capabilities.search_commands(query)
        if not matches:
            return Result(False, f"no omarchy command matches {query!r}. Try a single "
                                 "plainer word — \"theme\", \"audio\", \"bluetooth\".")
        return Result(True, "\n".join(matches) +
                      "\n\nRun one of these with omarchy_cli, without the leading 'omarchy'.")

    # -- reading the screen -------------------------------------------------
    class _CannotSee(Exception):
        """The window list could not be read, so what is on screen is unknown."""

    def _sensitive_kind(self, cls: str, title: str) -> str | None:
        """What kind of private thing this window is, or None.

        Returns a CATEGORY and never the text it matched on. The title is the
        sensitive material as much as the thing it identifies -- on this desktop
        the titles carry an inbox count, an email address and what is being
        watched -- so a refusal that quoted it would leak what it refused.

        Class and title are both tested because neither alone is enough. A
        Gmail window here reports class `chrome-<extension id>-Profile_4`,
        which identifies nothing; `pinentry` has a generic title and is
        identified only by class.
        """
        haystack = f"{cls}\n{title}"
        for kind, pattern in config_mod.DEFAULT_SENSITIVE:
            if pattern in self._sensitive_patterns and re.search(pattern, haystack, re.I):
                return kind
        for pattern in self._sensitive_patterns:
            if pattern in config_mod.DEFAULT_SENSITIVE_PATTERNS:
                continue  # already tried above, with its category
            try:
                if re.search(pattern, haystack, re.I):
                    return "something marked private in this configuration"
            except re.error:
                continue  # a bad pattern must not stop a capture being checked
        return None

    @property
    def _sensitive_patterns(self) -> list[str]:
        return list(getattr(self.config, "sensitive_patterns", ()) or ())

    # `comm` is capped at 15 characters by the kernel, so a longer name never
    # matches -- which is why `pgrep -x gpu-screen-recorder` silently finds
    # nothing. The needles are truncated to match, and anything added here that
    # is longer will still work.
    RECORDERS = frozenset(n[:15] for n in (
        "wf-recorder", "gpu-screen-recorder", "obs", "kooha", "wl-screenrec"))

    def _recorded_by_pipewire(self) -> str | None:
        """A screencast through the portal, which is how a browser shares a screen.

        No process name catches that, and it is probably the commonest case.
        A webcam in a call is `Stream/Input/Video` too, so a node has to look
        like a screen rather than merely like video.
        """
        try:
            out = subprocess.run(["pw-dump"], capture_output=True, timeout=4)
            nodes = json.loads(out.stdout or b"[]")
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            return None  # fails open; see _capture_refused
        for node in nodes:
            if not isinstance(node, dict):
                continue
            props = (node.get("info") or {}).get("props") or {}
            if props.get("media.class") != "Stream/Input/Video":
                continue
            looks_like_screen = " ".join(str(props.get(k, "")) for k in
                                         ("application.name", "node.name", "media.role")).lower()
            if "portal" in looks_like_screen or "screen" in looks_like_screen \
                    or "monitor" in looks_like_screen:
                return "the screen is being shared or recorded"
        return None

    def _recorded_by_process(self) -> str | None:
        """A recorder driving wlr-screencopy directly, which PipeWire never sees."""
        try:
            pids = [p for p in os.listdir("/proc") if p.isdigit()]
        except OSError:
            return None  # fails open
        for pid in pids:
            try:
                with open(f"/proc/{pid}/comm") as fh:
                    if fh.read().strip() in self.RECORDERS:
                        return "a screen recorder is running"
            except OSError:
                continue
        return None

    def _screen_is_recorded(self) -> str | None:
        """PipeWire first: cheaper, and it sees what a name list cannot."""
        if not getattr(self.config, "refuse_while_recording", True):
            return None
        return self._recorded_by_pipewire() or self._recorded_by_process()

    def _input_refused(self, target: str, window: dict | None = None) -> str | None:
        """Why input must not be sent to this window, or None.

        The read paths have been guarded since #46 and the write paths never
        were, so `read_screen` refused to look at a password manager while
        `type_text` typed into it and reported success (#67). Reading it
        discloses a secret; typing into it can change a vault, dismiss a
        credential prompt, or put a secret somewhere it was not.

        Refuses rather than asking, because the opt-out already exists:
        `sensitive_patterns` is configurable and `sensitive_patterns_replace`
        drops the defaults entirely. Somebody who wants their agent typing into
        a vault says so there, in one place, rather than in a second setting
        that means the same thing.

        `window` is for callers that have already resolved one -- `click_text`
        and `scroll` both do, for their own geometry -- because the lookup is a
        14 ms hyprctl query and there is no reason to pay it twice.

        Fails closed. If the window cannot be identified, what is about to be
        typed into is unknown, and guessing is the bug (#24). The cost is real:
        input is unavailable, not merely unverified, while hyprctl is
        unhappy. That is the trade #46 already made for reading, and a write is
        the stronger case for it.
        """
        # _query_rows, not _query_json, because the difference between "no
        # windows" and "could not read the windows" is the whole of #24 and it
        # matters here. Nothing open means there is nothing sensitive to
        # protect and the input lands nowhere; an unreadable list means what
        # would receive it is unknown.
        clients, failed = self._query_rows("clients")
        if failed:
            return (f"the window list could not be read ({failed}), so what would "
                    "receive the input is unknown and none was sent. Try again.")
        if not clients:
            return None

        if window is not None:
            candidates = [window]
        elif (target or "").strip() in ("screen", "", "monitor", "all"):
            # click_text and scroll accept the whole monitor. A click there
            # lands on whatever is under the cursor, so unlike a named window
            # -- which is the only thing that can receive the input -- every
            # visible window is a candidate. Same reasoning as _capture_refused.
            geometry, error = self._target_geometry(target)
            if error:
                return (f"{error}, so it is not known what would receive the input "
                        "and none was sent.")
            try:
                candidates = list(self._windows_in(geometry))
            except self._CannotSee as unknown:
                return (f"the window list could not be read ({unknown}), so what would "
                        "receive the input is unknown and none was sent. Try again.")
        else:
            resolved, error = self._resolve_window(target)
            if error or resolved is None:
                return (f"{error or 'that window was not found'}, so it is not known "
                        "what would receive the input and none was sent. Say which "
                        "window you mean.")
            candidates = [resolved]

        kind = next((k for k in (
            self._sensitive_kind(str(c.get("class") or ""), str(c.get("title") or ""))
            for c in candidates) if k), None)
        if not kind:
            return None
        # Names the category and never the title: on this desktop the titles
        # carry inbox counts and email addresses, so a refusal that quoted one
        # would leak what it refused (#46).
        return (f"that window is {kind}, so nothing was sent to it. Ask the user to "
                "type it themselves — that is the one case where a person doing it "
                "is worth more than an agent doing it.")

    def _capture_refused(self, geometry: str) -> str | None:
        """Why this rectangle must not be captured, or None.

        At the capture seam rather than in each tool, so `click_text` and
        `wait_for(text)` inherit it and a capture path added later cannot
        forget it. Refusing `read_screen` alone would leak the same text
        through `click_text`'s word boxes.

        Names the way forward as well as the reason: a monitor-wide refusal
        with no route turns one blocked read into a stuck task, and `target`
        already accepts a window address.
        """
        try:
            covering = self._windows_in(geometry)
        except self._CannotSee as unknown:
            return (f"the window list could not be read ({unknown}), so what is on screen "
                    "is unknown and nothing was read. Try again, or ask the user what is "
                    "in front of them.")
        for client in covering:
            kind = self._sensitive_kind(str(client.get("class") or ""),
                                        str(client.get("title") or ""))
            if kind:
                return (f"{kind} is visible in that area, so it was not read. Read one "
                        "window instead — read_screen with target set to a window "
                        "address from hypr_query(clients) — or ask the user to close "
                        "it. This matches on window class and title, so it misses "
                        "things and it misfires.")
        # After the window check, which costs one hyprctl query and is the more
        # important refusal, so it short-circuits this one.
        #
        # This check fails OPEN, unlike _windows_in three lines above (#51).
        # Deliberate: there, not knowing what is on screen risks the vault.
        # Here, not knowing whether OBS is running risks one frame in a video
        # the user is already choosing to make, and refusing every capture
        # because pw-dump was missing would be the check breaking the feature.
        if recording := self._screen_is_recorded():
            return (f"{recording}, so the screen was not read — the frame would land "
                    "in that recording. Stop it and ask again, or set "
                    "refuse_while_recording to false if you are recording this "
                    "assistant on purpose.")
        return None

    def _windows_in(self, geometry: str) -> list[dict]:
        """Mapped, visible windows whose rectangle meets this capture rectangle.

        The unit is the rectangle, not the focused window: `read_screen`
        defaults to a whole monitor, so a password manager BESIDE the thing
        being read is inside the capture and is exactly the case worth
        catching. Everything needed is already in the `clients` query the
        capture path makes anyway.
        """
        try:
            origin, size = geometry.split(" ", 1)
            gx, gy = (int(v) for v in origin.split(","))
            gw, gh = (int(v) for v in size.split("x"))
        except (ValueError, AttributeError):
            return []
        visible = self._visible_workspaces()
        found = []
        clients, failed = self._query_rows("clients")
        if failed:
            # Fail CLOSED, alone among the capture checks. _screen_unavailable's
            # other tests fail open because the cost of being wrong is small: a
            # confusing read, or fifteen seconds against a dark monitor. Here the
            # cost of being wrong is the vault. An empty client list means
            # "nothing sensitive" and "I cannot see" identically (#24), so this
            # one must not treat silence as safety.
            raise self._CannotSee(failed)
        for client in clients:
            if client.get("hidden") or client.get("mapped") is False:
                continue
            if str((client.get("workspace") or {}).get("name")) not in visible:
                continue
            try:
                wx, wy = client["at"]
                ww, wh = client["size"]
            except (KeyError, TypeError, ValueError):
                continue
            if wx < gx + gw and gx < wx + ww and wy < gy + gh and gy < wy + wh:
                found.append(client)
        return found

    def _visible_workspaces(self) -> set[str]:
        """Workspace names currently being drawn, one per monitor.

        grim captures what the compositor is painting. A window parked on a
        workspace nobody is looking at has no pixels, so OCR of it would come
        back empty and look like a page with no text on it rather than a window
        that is not on screen.
        """
        names = set()
        for monitor in self._query_json("monitors"):
            name = (monitor.get("activeWorkspace") or {}).get("name")
            if name is not None:
                names.add(str(name))
        return names

    def _capture_png(self, geometry: str) -> tuple[bytes | None, str]:
        """grim the region as PNG. Returns (png, "") or (None, reason).

        PNG rather than the PPM `_ocr_region` uses: that one is chosen for
        being fastest into tesseract (#26), and nothing downstream of it ever
        sees the bytes. These bytes go to a model, so they are lossless and in
        a format every client decodes.

        Traced as SUBPROCESS by hand rather than going through `_shell`:
        `_shell` decodes to text, which would mangle a PNG. The phase is what
        #39 wanted from that step, and this gives it without the decode.
        """
        if not shutil.which("grim"):
            return None, install_hint("grim", "grim")
        try:
            span = (self.trace.mark(trace_mod.SUBPROCESS, "grim")
                    if self.trace else None)
            try:
                shot = subprocess.run(["grim", "-t", "png", "-g", geometry, "-"],
                                      capture_output=True, timeout=15)
            finally:
                if span:
                    span.close()
        except (OSError, subprocess.SubprocessError) as exc:
            return None, f"screen capture failed: {exc}"
        if shot.returncode != 0 or not shot.stdout:
            return None, ((shot.stderr or b"").decode(errors="replace").strip()
                          or "screen capture produced nothing")
        return shot.stdout, ""

    def _tool_screenshot(self, target: str = "active") -> Result:
        """Hand the caller the pixels instead of our OCR of them.

        Order matters and is fixed: resolve the geometry, then the privacy
        guard, then capture. A guard that ran after grim would have already
        taken the picture of the password field it then refuses to describe.
        It is the same guard expression `_ocr_region` and `_ocr_words` use --
        a screenshot of a secret discloses strictly more than OCR of it, so
        there is no case for a softer one here.
        """
        geometry, error = self._target_geometry(target)
        if error:
            return Result(False, error)
        if blocked := self._screen_unavailable() or self._capture_refused(geometry):
            return Result(False, blocked)
        png, why = self._capture_png(geometry)
        if png is None:
            return Result(False, why)
        return Result(True, f"screenshot of {target} ({geometry})", image=png)

    def _ocr_region(self, geometry: str) -> Result:
        """grim the region, pipe it through tesseract, hand back the text."""
        for tool, package in (("grim", "grim"), ("tesseract", "tesseract")):
            if not shutil.which(tool):
                return Result(False, install_hint(tool, package))
        if blocked := self._screen_unavailable() or self._capture_refused(geometry):
            return Result(False, blocked)
        try:
            capture = self.trace.mark(trace_mod.CAPTURE) if self.trace else None
            shot = subprocess.run(CAPTURE_CMD + [geometry, "-"],
                                  capture_output=True, timeout=15)
            if capture:
                capture.close()
        except (OSError, subprocess.SubprocessError) as exc:
            return Result(False, f"screen capture failed: {exc}")
        if shot.returncode != 0 or not shot.stdout:
            return Result(False, (shot.stderr or b"").decode(errors="replace").strip()
                          or "screen capture produced nothing")
        try:
            reading = self.trace.mark(trace_mod.OCR) if self.trace else None
            ocr = subprocess.run(
                ["tesseract", "stdin", "stdout", "--oem", "1", "--psm", str(OCR_PAGE_MODE),
                 "-l", os.environ.get("OMARCHY_OCR_LANGS", "eng"), "--dpi", "300",
                 "-c", "preserve_interword_spaces=1"],
                input=shot.stdout, capture_output=True, timeout=45)
        except (OSError, subprocess.SubprocessError) as exc:
            return Result(False, f"OCR failed: {exc}")
        finally:
            if reading:
                reading.close()
        text = (ocr.stdout or b"").decode(errors="replace").strip()
        if not text:
            return Result(False, "no readable text in that region")
        # Long enough for three news panes, short enough not to eat the whole
        # token budget for the turn — the prompt is already ~8k.
        if len(text) > OCR_LIMIT:
            text = text[:OCR_LIMIT] + "\n… [more text on screen, not read]"
        return Result(True, text)

    def _screen_unavailable(self) -> str | None:
        """Why the screen cannot be read or pointed at, or None if it can.

        Two ways to get pixels that are not the desktop:

        * A monitor in DPMS off produces no frames at all. grim does not fail on
          one, it blocks until the timeout — a read at half past midnight hung
          for fifteen seconds and then blamed OCR.
        * A locked session paints the lock screen over everything. That one is
          worse, because the capture succeeds: grim returns the wallpaper and a
          password box, so "read me the news" came back with whatever OCR made
          of a blurred photograph, reported as the news. Nothing in hyprctl
          shows it — an ext-session-lock surface is not a layer, no locker
          process is running under a recognisable name, and logind's LockedHint
          stays "no" — but Omarchy's own shell, which draws the lock, will say.

        `disabled` is deliberately NOT consulted, and this sentence exists so
        nobody adds it back defensively. A headless or virtual output reports
        `disabled: true` while rendering perfectly normally: measured in a
        nixarchy VM, the only monitor read `dpms=True, disabled=True` while
        `grim -t ppm -` returned a valid 1280x800 capture whose OCR matched
        five lines of the windows on it. Refusing that made every virtual and
        nested display unreadable, and told the model to run a dpms dispatch
        that could not help because dpms was already on (#55).

        The cost of being wrong the other way is bounded: grim blocks until its
        timeout rather than failing, which is the fifteen seconds described
        above, so this check is an optimisation over that bound and not the
        only thing standing in front of it.

        Neither check is allowed to be the reason nothing works: if the query
        does not answer, the capture is attempted anyway.
        """
        if self._session_is_locked():
            return ("the session is locked, so the only thing on screen is the "
                    "lock screen. Ask the user to unlock it; do not try to read "
                    "or click through it, and do not report the lock screen as "
                    "the contents of their desktop.")
        monitors = self._query_json("monitors")
        if not monitors:
            return None  # cannot tell; let the capture try
        awake = [m for m in monitors if m.get("dpmsStatus") is not False]
        if awake:
            return None
        return ("every monitor reports itself asleep, so a capture would be of "
                "nothing. If the user says the screen is on, say what you saw "
                "rather than insisting; otherwise hypr_dispatch "
                'hl.dsp.dpms({ state = "on" }) is worth a try.')

    def _session_is_locked(self) -> bool:
        """Whether the lock screen is covering the desktop.

        Only Omarchy's shell knows. It draws the lock itself through
        ext-session-lock, which is not a layer surface, runs under no process
        named for locking, and leaves logind's LockedHint at "no" — so hyprctl,
        ps and loginctl all report an ordinary unlocked desktop while a
        password box is the only thing being painted.

        Not `-q`: that is omarchy-shell's best-effort mode and it suppresses the
        answer along with the errors, which reads as "not locked".
        """
        if not shutil.which("omarchy-shell"):
            return False
        answer = self._shell(["omarchy-shell", "lock", "isLocked"], timeout=4)
        return answer.ok and answer.output.strip().lower() == "true"

    def _ocr_words(self, geometry: str) -> tuple[list[dict], str]:
        """OCR a region into positioned words: [{text, x, y, w, h, conf}, ...].

        tesseract's `tsv` output carries a bounding box per word, which is the
        whole reason clicking by text is possible. Coordinates come back
        relative to the captured image, so the region's own origin is added
        back on to get screen coordinates.
        """
        for tool in ("grim", "tesseract"):
            if not shutil.which(tool):
                return [], f"{tool} is not installed"
        if blocked := self._screen_unavailable() or self._capture_refused(geometry):
            return [], blocked
        try:
            origin_x, origin_y = (int(v) for v in geometry.split()[0].split(","))
        except (ValueError, IndexError):
            return [], "could not read the capture geometry"
        try:
            capture = self.trace.mark(trace_mod.CAPTURE) if self.trace else None
            shot = subprocess.run(CAPTURE_CMD + [geometry, "-"],
                                  capture_output=True, timeout=15)
            if capture:
                capture.close()
            if shot.returncode != 0 or not shot.stdout:
                return [], "screen capture produced nothing"
            reading = self.trace.mark(trace_mod.OCR) if self.trace else None
            ocr = subprocess.run(
                ["tesseract", "stdin", "stdout", "--oem", "1", "--psm", str(OCR_PAGE_MODE),
                 "-l", os.environ.get("OMARCHY_OCR_LANGS", "eng"), "--dpi", "300", "tsv"],
                input=shot.stdout, capture_output=True, timeout=45)
        except (OSError, subprocess.SubprocessError) as exc:
            return [], f"OCR failed: {exc}"
        finally:
            if reading:
                reading.close()

        words = []
        for line in (ocr.stdout or b"").decode(errors="replace").splitlines()[1:]:
            cell = line.split("\t")
            if len(cell) < 12 or not cell[11].strip():
                continue
            try:
                conf = float(cell[10])
                if conf < MIN_OCR_CONFIDENCE:
                    continue
                words.append({"text": cell[11].strip(),
                              "x": origin_x + int(cell[6]), "y": origin_y + int(cell[7]),
                              "w": int(cell[8]), "h": int(cell[9]), "conf": conf})
            except ValueError:
                continue
        return words, ""

    @staticmethod
    def _find_phrase(words: list[dict], query: str) -> tuple[int, int] | None:
        """Centre of the best run of words matching `query`, or None.

        Matched as a sliding window over consecutive words rather than a string
        search, because OCR splits a headline into words and drops the odd one.
        A run scores by how many of the query's words it contains, so
        "US and Iran trade strikes" still finds the headline when tesseract read
        "trade" as "frade".
        """
        wanted = [w for w in re.split(r"\W+", query.lower()) if w]
        if not wanted or not words:
            return None
        best_score, best_span = 0.0, None
        span_len = max(1, len(wanted))
        for start in range(len(words)):
            for length in (span_len, span_len + 1, max(1, span_len - 1)):
                run = words[start:start + length]
                if not run:
                    continue
                text = " ".join(w["text"].lower() for w in run)
                # Exact containment first, so clean OCR behaves exactly as it
                # did. The fold is a fallback for the misread case only: it
                # recovers "settings" from "settinqs" without ever widening to
                # a merely similar word. See OCR_CONFUSABLES (#48).
                # Whole tokens, not substrings. Containment scored
                # click_text("delete") as a hit on the word "deleted" and
                # clicked it, reporting success (#60). The cost is that a
                # prefix no longer matches -- asking for "Setting" will not
                # find a "Settings" button -- which is the trade the spec
                # accepted: a silent wrong click is worse than a refusal that
                # names the near miss.
                tokens = {w["text"].lower() for w in run}
                folded_tokens = {_ocr_fold(t) for t in tokens}
                hits = sum(1 for w in wanted
                           if w in tokens or _ocr_fold(w) in folded_tokens)
                # Every word has to be there, give or take one for a long phrase
                # that OCR has mangled. Accepting half of them meant a two-word
                # target passed on a single word: asked for "Files changed" on a
                # pull request it matched the words "changed files" in the body
                # prose and clicked that, confidently, in the wrong place.
                if hits < _required_hits(len(wanted)):
                    continue
                # Among runs that qualify, prefer the tightest one.
                score = hits / len(wanted) - 0.05 * abs(len(run) - span_len)
                if score > best_score:
                    best_score, best_span = score, run
        if best_span is None:
            return None
        left = min(w["x"] for w in best_span)
        top = min(w["y"] for w in best_span)
        right = max(w["x"] + w["w"] for w in best_span)
        bottom = max(w["y"] + w["h"] for w in best_span)
        return (left + right) // 2, (top + bottom) // 2

    def _layout_extent(self) -> tuple[int, int]:
        """The whole monitor layout, which the helper needs to scale to.

        Absolute pointer coordinates are normalised against this, so it has to
        be the bounding box of every monitor rather than the focused one.
        """
        monitors = self._query_json("monitors")
        if not monitors:
            raise virtual_input.Unavailable(
                "no monitors, so the pointer has no coordinate space")
        left = min(int(m.get("x", 0)) for m in monitors)
        top = min(int(m.get("y", 0)) for m in monitors)
        right = max(int(m.get("x", 0)) + int(m.get("width", 0)) for m in monitors)
        bottom = max(int(m.get("y", 0)) + int(m.get("height", 0)) for m in monitors)
        return right - left, bottom - top

    def _send_input(self, build) -> Result:
        """Send helper lines, or say plainly why nothing was sent.

        Every refusal here is a real one. The old paths reported success after
        pressing Page Down instead of scrolling, and returned a paragraph of
        NixOS advice when a root daemon was missing; a compositor that cannot
        be clicked at should say so once.
        """
        if self.input_helper is None:
            return Result(False, "no input helper on this executor, so nothing "
                                 "can be clicked or scrolled")
        try:
            self.input_helper.send(build())
        except virtual_input.Unavailable as exc:
            return Result(False, str(exc))
        except ValueError as exc:
            return Result(False, str(exc))
        return Result(True, "ok")

    def _press_button(self, button: str, double: bool) -> Result:
        """The actual click. Hyprland has no click dispatcher, so this is the
        Wayland virtual pointer -- no root, no /dev/uinput (#30)."""
        return self._send_input(lambda: virtual_input.click(button, double))

    def _validate_click_text(self, text: str, button: str = "left",
                             double: bool = False) -> str | None:
        if not (text or "").strip():
            return "text is required — say what is on screen that you want clicked"
        if button not in virtual_input.BUTTONS:
            return f"button must be one of {', '.join(virtual_input.BUTTONS)}"
        return None

    # How much around the matched point to re-read before clicking. Wide
    # enough for the phrase plus a little drift, small enough to stay cheap:
    # measured, a 300x60 region costs ~187 ms against ~4050 ms for the full
    # screen, because the cost is the OCR and not the capture.
    VERIFY_REGION = (300, 60)

    def _target_moved(self, text: str, x: int, y: int) -> str | None:
        """Why the thing at (x, y) is no longer `text`, or None to go ahead.

        There is a gap between OCRing the screen and clicking a coordinate
        found in it, and the gap is the OCR -- four seconds during which a
        panel can open or a notification slide in. The coordinate stays valid;
        what sits under it does not (#60).

        This does not make the click atomic and does not pretend to. It
        narrows the window from ~4 s to ~187 ms, and -- the point -- turns an
        undetected wrong click into a refusal the caller can act on (#24).
        """
        width, height = self.VERIFY_REGION
        region = f"{max(0, x - width // 2)},{max(0, y - height // 2)} {width}x{height}"
        words, error = self._ocr_words(region)
        if error:
            # Could not check. Say so rather than clicking on the strength of
            # a read we already know is seconds old.
            return (f"could not re-check what is at {x},{y} before clicking ({error}), "
                    "so nothing was clicked. Read the screen and try again.")
        if self._find_phrase(words, text) is not None:
            return None
        return (f"{text!r} was there when the screen was read but is not there now, "
                f"so nothing was clicked at {x},{y} — something moved or covered it. "
                "Read the screen again to see what is there. (Small or stylised text "
                "can also fail this check even when it has not moved.)")

    def _tool_click_text(self, text: str, button: str = "left",
                         double: bool = False, target: str = "screen") -> Result:
        error = self._validate_click_text(text, button, double)
        if error:
            return Result(False, error)
        geometry, error = self._target_geometry(target)
        if error:
            return Result(False, error)
        # Deliberate, not incidental. This was protected only because the OCR
        # below carries the guard, which is the right outcome by accident --
        # and accidents do not survive somebody changing how this reads (#67).
        if refused := self._input_refused(target):
            return Result(False, refused)

        words, error = self._ocr_words(geometry)
        if error:
            return Result(False, error)
        point = self._find_phrase(words, text)
        if point is None:
            # Name the near miss rather than sending the caller off to
            # read_screen for a word they already guessed right. Reported,
            # never clicked -- see _nearest_word.
            near = _nearest_word(words, text)
            if near is not None:
                where = (near["x"] + near["w"] // 2, near["y"] + near["h"] // 2)
                return Result(False,
                              f"could not find {text!r} on screen. The closest text is "
                              f'{near["text"]!r} at {where[0]},{where[1]} — if that is '
                              "what you meant, call click_text with that wording.")
            return Result(False,
                          f"could not find {text!r} on screen. Read the screen first and "
                          "use wording you can actually see, or scroll it into view.")
        x, y = point
        if stale := self._target_moved(text, x, y):
            return Result(False, stale)
        moved = self._dispatch("cursor.move", {"x": x, "y": y})
        if not moved.ok:
            return Result(False, f"could not move the pointer: {moved.output}")
        pressed = self._press_button(button, double)
        if not pressed.ok:
            return pressed
        return Result(True, f'{"double-" if double else ""}clicked {text!r} at {x},{y}')

    def _tool_read_screen(self, target: str = "screen", query: str = "") -> Result:
        result = self._read_screen_text(target)
        if not result.ok or not (query or "").strip():
            return result
        found = _matching_lines(result.output, query)
        if not found:
            return Result(True, f"nothing on screen matches {query!r}. It may be below "
                                "the fold — scroll and look again — or simply not there.")
        return Result(True, found)

    def _tool_read_notifications(self, limit: int = 5, query: str = "") -> Result:
        try:
            limit = max(1, min(int(limit or 5), 25))
        except (TypeError, ValueError):
            limit = 5
        # Over-read when filtering, or a query for something three notifications
        # back returns nothing because the window was five and four of them were
        # from a chat app.
        entries = notifications.recent(limit * 8 if query else limit)
        words = (query or "").strip().lower()
        if words:
            entries = [e for e in entries if words in
                       f'{e.get("app", "")} {e.get("summary", "")} {e.get("body", "")}'.lower()]
        entries = entries[:limit]
        if not entries:
            if not notifications.HISTORY_FILE.exists():
                return Result(False,
                              "no notifications have been recorded — this needs the "
                              "omarchy-voice daemon running to have seen them arrive")
            return Result(True, f"nothing matching {query!r}" if words
                          else "no notifications recorded yet")
        lines = []
        for entry in entries:
            ago = max(0, int(time.time() - float(entry.get("at") or 0)))
            when = f"{ago}s ago" if ago < 90 else f"{ago // 60}m ago"
            app = entry.get("app") or "unknown"
            body = entry.get("body") or ""
            lines.append(f'{when} — {app}: {entry.get("summary", "")}'
                         + (f" — {body}" if body else ""))
        return Result(True, "\n".join(lines))

    def _wait_tick(self, elapsed: float, slow: float = 0.15) -> None:
        """Sleep until the next check, waking early if the compositor speaks.

        One helper rather than the same five lines in two loops -- writing it
        twice is how the wait loops drifted apart before.

        `self.waker` is attached by whoever built this executor, and is None by
        default. That is deliberate: a unit test must not reach the running
        compositor, and one that does cannot be made to hold still -- a patched
        `time.sleep` does not reach an Event, so a test asserting the 25s cap
        waited 25 real seconds the moment this was wired in unconditionally.
        """
        waker = self.waker
        hypr_events.wait_tick(elapsed, slow=slow,
                              waker=waker if waker and waker.available else None)

    def _resolve_window(self, target: str) -> tuple[dict | None, str | None]:
        """The window `target` names, or (None, why not).

        Accepts what a person would say. Before this, every tool that looks at
        a window took only "activewindow" or a literal address, and the
        refusal told the model to go and call hypr_query(clients) -- so naming
        a window cost a 485-token JSON dump and a second model turn, for a
        lookup that takes 13ms. That advice is gone from the errors below on
        purpose: it is the round trip this exists to remove.

        Does NOT check whether the window is visible. read_screen and scroll
        both need to, and they say different things about it ("nothing to
        read" against "nothing to scroll"); folding the check in here would
        flatten two accurate refusals into one vague one.
        """
        target = (target or "").strip()
        clients = self._query_json("clients")
        if not clients:
            return None, "nothing is open"

        if target in ("", "activewindow", "active", "focused"):
            window = next((c for c in clients if c.get("focusHistoryID") == 0), None)
            return (window, None) if window else (None, "nothing is focused")

        if target.startswith("address:") or _BARE_ADDRESS_RE.match(target):
            address = target[8:] if target.startswith("address:") else target
            window = next((c for c in clients if c.get("address") == address), None)
            return (window, None) if window else (
                None, f"no window at address {address!r}; it has probably closed. "
                      "Name the window instead and it will be found.")

        # Hyprland's own selector syntax, which send_shortcut already takes.
        for prefix, field in (("class:", "class"), ("title:", "title")):
            if target.lower().startswith(prefix):
                wanted = target[len(prefix):].lower()
                hits = [c for c in clients
                        if wanted in str(c.get(field) or "").lower()]
                if not hits:
                    return None, f"no window whose {field} contains {wanted!r}"
                hits.sort(key=lambda c: c.get("focusHistoryID", 999))
                return hits[0], None

        ranked = _rank_windows(clients, target)
        if not ranked:
            # Trimmed, and capped. Web-app classes carry an extension id --
            # chrome-ejhkdoiecgkmdpomoahkdihbcldkgjci-Default -- so the raw
            # list ran to 353 characters of hash on this desktop, charged
            # against the turn budget every time a name missed.
            names = sorted({_short_class(c) for c in clients})
            open_now = ", ".join(names[:8]) + (" …" if len(names) > 8 else "")
            return None, (f"no window matching {target!r}. Open now: {open_now}.")
        # Equal top scores, not merely close ones. Class outranks title, so a
        # browser and a terminal showing chrome-flags.conf is not a tie -- and
        # turning that into a question would put back the turn this removes.
        tied = [c for score, c in ranked if score == ranked[0][0]]
        if len(tied) > 1:
            # A wrong window acted on under a right-looking description is the
            # failure the policy gate cannot catch, because the gate matches
            # the description. So this asks rather than picks.
            shown = "; ".join(
                f'{c.get("class") or "?"} "{str(c.get("title") or "")[:40]}" '
                f'(workspace {(c.get("workspace") or {}).get("name", "?")})'
                for c in tied[:4])
            return None, (f"{target!r} matches {len(tied)} windows: {shown}. "
                          "Say which one, or give its address.")
        return ranked[0][1], None

    def _target_geometry(self, target: str = "screen") -> tuple[str | None, str | None]:
        """Resolve "screen" / "activewindow" / an address to a grim geometry.

        Returns (geometry, error). Shared by read_screen, click_text and the
        text branch of wait_for: each of those used to work out the monitor
        rect for itself, which is how click_text ended up unable to look at a
        single window while read_screen could.
        """
        target = (target or "screen").strip()

        if target in ("screen", "", "monitor", "all"):
            monitors = self._query_json("monitors")
            focused = next((m for m in monitors if m.get("focused")), None) \
                or (monitors[0] if monitors else None)
            if not focused:
                return None, "no monitor to read"
            try:
                return (f'{focused["x"]},{focused["y"]} '
                        f'{focused["width"]}x{focused["height"]}'), None
            except KeyError:
                return None, "could not read the monitor geometry"

        window, error = self._resolve_window(target)
        if error:
            return None, error

        workspace = str((window.get("workspace") or {}).get("name"))
        if workspace not in self._visible_workspaces():
            return None, (f"that window is on workspace {workspace}, which is not on any "
                          "screen right now, so there is nothing to read. Switch to it "
                          'first — hypr_dispatch focus with workspace = "'
                          f'{workspace}" — then read again.')
        try:
            return (f'{window["at"][0]},{window["at"][1]} '
                    f'{window["size"][0]}x{window["size"][1]}'), None
        except (KeyError, IndexError, TypeError):
            return None, "could not read that window's geometry"

    def _read_screen_text(self, target: str = "screen") -> Result:
        geometry, error = self._target_geometry(target)
        if error:
            return Result(False, error)
        return self._ocr_region(geometry)

    # -- composition --------------------------------------------------------
    def _await_new_window(self, before: set[str], timeout: float,
                          hint: str | tuple[str, ...] = "") -> str | None:
        """Block until the window we just launched is mapped, and return it.

        This is the whole reason composition is a tool and not four dispatches:
        `omarchy launch` returns as soon as the process is started, which is
        long before the surface exists. Anything addressed in that gap silently
        misses.

        Taking simply "the newest window" is not enough. A composition ran while
        Chrome happened to raise a "Profile error occurred" dialog, and that
        dialog was claimed as the first pane: every later pane shifted by one and
        the layout came out 1261/621/626 instead of even columns. So a candidate
        has to look like the thing that was asked for — `hint` is the site's host,
        the app id, or the desktop id, matched against the class and the title the
        window was born with. A window with no class at all is never a launched
        application's own window, and is skipped outright.

        `hint` may be several names -- an app's desktop id and the class its
        entry declares -- and any one of them matching is enough. Only a window
        that matched is ever returned (#75). This used to fall back to whatever
        new window had appeared, so a Spotify pane that never mapped adopted
        Discord and moved it onto the composed workspace. A caller that gets
        None says what did appear, through _unmatched_new_windows, and leaves
        it alone.
        """
        # Empty strings are dropped, so ("", "") is "" -- any classed window,
        # the terminal pane's behaviour -- and ("apnews.com", "") never widens
        # to that.
        hints = tuple(h for h in ((hint,) if isinstance(hint, str) else hint) if h)
        started = time.monotonic()
        deadline = started + timeout
        first = True
        while time.monotonic() < deadline:
            # Look before sleeping. The old loop slept 150ms first, so a
            # window already mapped when the call started paid for nothing.
            if not first:
                self._wait_tick(time.monotonic() - started)
            first = False
            rows, failed = self._query_rows("clients")
            if failed:
                # Retry, unlike wait_for above: the window may still be on its
                # way and the deadline already bounds this loop. A failure here
                # costs one iteration, not a wrong answer.
                continue
            fresh = [c for c in rows
                     if c.get("address") not in before and c.get("class")]
            if not fresh:
                continue
            matched = [c for c in fresh
                       if any(_window_matches(c, h) for h in hints)] if hints else fresh
            if matched:
                # The one just mapped is the one with focus; focusHistoryID 0 is
                # the focused window. Ties fall back to whatever came back first.
                matched.sort(key=lambda c: c.get("focusHistoryID", 999))
                return matched[0].get("address")
        return None

    def _unmatched_new_windows(self, before: set[str]) -> str:
        """The new windows that appeared instead of the one asked for, or "".

        Called once _await_new_window has already given up, so the user can be
        told "Discord opened, not Spotify" instead of a bare "did not appear".
        Up to three, most recently focused first. A failed query and an empty
        one both return "", and the callers then say nothing about other
        windows: an unanswered query is not evidence that nothing appeared
        (#24).
        """
        fresh = [c for c in self._query_json("clients")
                 if c.get("address") not in before and c.get("class")]
        fresh.sort(key=lambda c: c.get("focusHistoryID", 999))
        return "; ".join(f"{c['class']} {c.get('title', '')!r} (address:{c.get('address')})"
                         for c in fresh[:3])

    def _target_workspace(self, workspace: str) -> tuple[str | None, str]:
        """Resolve "next" / "current" / "4" to a workspace name, or an error."""
        workspace = (workspace or "next").strip().lower()
        if workspace == "current":
            return None, ""
        if workspace == "next":
            used = {w.get("id") for w in self._query_json("workspaces")
                    if (w.get("windows") or 0) > 0}
            for candidate in range(1, 11):
                if candidate not in used:
                    return str(candidate), ""
            return None, "every workspace from 1 to 10 already has windows on it"
        if not re.fullmatch(r"\d{1,2}", workspace):
            return None, f"{workspace!r} is not a workspace number, \"next\", or \"current\""
        return workspace, ""

    def _query_rows(self, kind: str) -> tuple[list[dict], str | None]:
        """A hyprctl query, and why it could not be answered if it could not.

        The distinction this returns is the whole of #24. A timed-out hyprctl,
        a compositor mid-reload and a desktop with genuinely nothing open all
        produce an empty list, and three callers read emptiness as a positive
        fact -- "the window closed", "nothing was open before". Demonstrated:
        with the baseline query failing, _await_new_window returned a window
        that had been open all along as the one just launched, so
        compose_windows would tile a window the user was working in.

        The write path already learned this. _dispatch_lua exists because
        hyprctl reports a missing target as a warning with a zero exit, and
        "the model was told a window had been closed when nothing had
        happened". That guard never reached the read path.
        """
        result = self._shell(["hyprctl", "-j", kind], timeout=5, limit=1 << 22)
        if not result.ok:
            # The trimmed stderr rather than "it failed": the model can act on
            # "no such instance" and cannot act on a shrug.
            why = (result.output or "").strip().splitlines()
            return [], f"hyprctl {kind} failed: {why[0][:120] if why else 'no output'}"
        try:
            data = json.loads(result.output)
        except json.JSONDecodeError:
            return [], f"hyprctl {kind} returned something that is not JSON"
        if not isinstance(data, list):
            return [], f"hyprctl {kind} returned something that is not a list"
        return data, None

    def _query_json(self, kind: str) -> list[dict]:
        """A hyprctl query, with the reason for a failure discarded.

        Safe only where an empty answer and an unanswerable one lead to the
        same behaviour -- which is true of thirteen of the sixteen callers,
        because they fail closed: "nothing is open", a read that refuses, a
        close that declines. It is NOT true where emptiness is read as a fact.
        Those three use _query_rows: wait_for's window branches, the baselines
        in compose_windows and _search_window, and nothing else should join
        them without checking which kind of caller it is.
        """
        return self._query_rows(kind)[0]

    def _equalize_columns(self, addresses: list[str | None],
                          workspace: str | None) -> None:
        """Even out a columns layout that dwindle halved instead of divided.

        Dwindle splits the *focused* window, so opening three panes to the right
        of each other gives 1/2, 1/4, 1/4 — the third pane is half the width of
        the first, which does not read as columns. Shrinking each pane in turn to
        one nth of the span hands the difference to the subtree holding the rest,
        which then splits it evenly, so the last two come out right on their own:
        on a 2560-wide monitor this takes 1261/621/626 to 845/829/834.

        Only `columns` wants this. `main-and-side` is unequal on purpose, and the
        grid comes out even from the preselects alone.
        """
        def row() -> list[dict]:
            """Every tiled window sharing the column row on the target workspace.

            Not just the panes we placed. A composition landed on an empty
            workspace at the same moment Chrome re-raised a "Profile error"
            dialog onto it; balancing only our own three left them at 420 px
            each beside a 1261 px intruder. Whatever is actually tiled there is
            what has to add up to the width of the screen.
            """
            here = [c for c in self._query_json("clients")
                    if not c.get("floating") and (c.get("fullscreen") or 0) == 0
                    and (workspace is None
                         or str(c.get("workspace", {}).get("name")) == str(workspace))]
            try:
                here.sort(key=lambda c: c["at"][0])
            except (KeyError, IndexError, TypeError):
                return []
            return here

        live = [a for a in addresses if a]
        if len(live) < 2:
            return
        boxes = row()
        if len(boxes) < 3:
            return  # a single split is already even
        for step in range(len(boxes) - 2):
            boxes = row()
            if len(boxes) < 3:
                return  # a window went away mid-layout; leave the rest alone
            try:
                left = boxes[0]["at"][0]
                span = boxes[-1]["at"][0] + boxes[-1]["size"][0] - left
                delta = int(span / len(boxes)) - boxes[step]["size"][0]
            except (KeyError, IndexError, TypeError):
                return
            if abs(delta) < 12:  # already within a gap's width of even
                continue
            # x and y are both required, and `relative` is what makes them a
            # delta rather than an absolute size.
            self._dispatch("window.resize", {
                "x": delta, "y": 0, "relative": True,
                "window": f'address:{boxes[step]["address"]}'})

    def _validate_compose_windows(self, panes: list, layout: str = "columns",
                                  workspace: str = "next") -> str | None:
        if not isinstance(panes, list) or not panes:
            return "panes must be a non-empty list"
        if len(panes) == 1:
            # A layout tool asked to lay out one window is a tell: the request
            # was a question, not a workspace. The model kept reaching here for
            # "who won the race" and "show me pictures of a duck" because this
            # is the habitual route to anything on the web, and persona wording
            # did not move it. Refusing at the point of the mistake does.
            pane = panes[0] if isinstance(panes[0], dict) else {}
            target = str(pane.get("target", ""))
            return ("compose_windows lays several windows out together; for one window "
                    "it is the wrong tool. If this is a question — a price, a result, a "
                    "date, or \"show me\" — call web_search, which puts the answer on the "
                    "workspace the user is already looking at. If you want this exact "
                    f"page, call open_page{f' with url={target!r}' if target else ''}.")
        if len(panes) > MAX_PANES:
            return f"at most {MAX_PANES} panes; more than that is unreadable on one screen"
        if layout not in LAYOUTS:
            return f"layout must be one of {', '.join(LAYOUTS)}"
        for index, pane in enumerate(panes, 1):
            if not isinstance(pane, dict):
                return f"pane {index} is not an object"
            kind = str(pane.get("kind", ""))
            if kind not in PANE_KINDS:
                return f"pane {index}: kind must be one of {', '.join(PANE_KINDS)}"
            if _pane_command(kind, str(pane.get("target", "")),
                             str(pane.get("name", ""))) is None:
                return (f"pane {index}: {str(pane.get('target',''))!r} is not usable as a "
                        f"{kind} target (web needs an http(s) URL, app needs a desktop id)")
        return None

    def _tool_compose_windows(self, panes: list, layout: str = "columns",
                              workspace: str = "next") -> Result:
        error = self._validate_compose_windows(panes, layout, workspace)
        if error:
            return Result(False, error)

        target, error = self._target_workspace(workspace)
        if error:
            return Result(False, error)
        if target is not None:
            move = self._dispatch("focus", {"workspace": str(target)})
            if not move.ok:
                return Result(False, f"could not switch to workspace {target}: {move.output}")

        plan = _layout_plan(layout, len(panes))
        placed: list[str | None] = []
        opened: list[str] = []
        slow: list[str] = []
        unmatched: list[str] = []   # something else appeared instead (#75)
        deadline = time.monotonic() + COMPOSE_BUDGET

        for index, pane in enumerate(panes):
            kind = str(pane.get("kind", ""))
            label = str(pane.get("name", "")).strip() or str(pane.get("target", ""))[:40]
            argv = _pane_command(kind, str(pane.get("target", "")), str(pane.get("name", "")))
            assert argv is not None  # _validate_compose_windows already proved this

            # Defence in depth: a pane is built from a fixed set of shapes, but
            # the deny list is the thing that is allowed to have the last word.
            try:
                self.policy.check(" ".join(argv))
            except (Denied, NeedsConfirmation):
                return Result(False, f"pane {index + 1} ({label}) is not allowed by policy")

            if index > 0 and index - 1 < len(plan):
                direction, anchor = plan[index - 1]
                anchor_address = placed[anchor] if anchor < len(placed) else None
                if anchor_address:
                    self._dispatch("focus", {"window": f"address:{anchor_address}"})
                # Positional string, not a table: `hl.dsp.layout` is the exception
                # to the one-table-argument rule. Best effort — a failed preselect
                # costs a tidy layout, not the window.
                self._dispatch("layout", message=f"preselect {direction}")

            rows, failed = self._query_rows("clients")
            if failed:
                # Before the launch, not after. A baseline that silently came
                # back empty made every window already open look new, and the
                # pane was then built around one the user was working in. The
                # launch is the half that cannot be undone (#24).
                return Result(False, f"{failed}, so a new window could not be "
                                     "told from one already open; nothing was launched")
            before = {c.get("address") for c in rows}
            self.on_action("compose_windows", f"open {label} ({' '.join(argv)})")
            started = self._shell(argv, timeout=30, grace=LAUNCH_GRACE)
            if not started.ok:
                placed.append(None)
                slow.append(f"{label} (failed: {started.output[:60]})")
                continue

            budget = min(PANE_TIMEOUT.get(kind, 8.0), max(1.0, deadline - time.monotonic()))
            hint = _pane_hint(kind, str(pane.get("target", "")), str(pane.get("name", "")))
            # An app matches on its desktop id or on the class its entry
            # declares; neither alone covers every app (#75).
            address = self._await_new_window(
                before, budget, (hint, _desktop_wm_class(hint)) if kind == "app" else hint)
            # Chrome raises its profile-error box when a second browser process
            # races the first for the profile's databases, which is exactly what
            # launching panes back to back does. Clear it between panes so it
            # cannot steal the focus the next preselect depends on.
            if kind == "web":
                self._dismiss_browser_error_dialogs()
            placed.append(address)
            if address is None:
                # Something else may have opened meanwhile. It is named, and
                # left where it is: never moved, focused or used as an anchor.
                if desc := self._unmatched_new_windows(before):
                    unmatched.append(f"{label} (instead: {desc})")
                else:
                    # Still coming, probably. Say so rather than claiming it is up.
                    slow.append(label)
                continue
            opened.append(label)
            if target is not None:
                self._dispatch("window.move", {
                    "workspace": str(target), "window": f"address:{address}"})

        if layout == "columns":
            self._equalize_columns(placed, target)

        first = next((a for a in placed if a), None)
        if first:
            self._dispatch("focus", {"window": f"address:{first}"})

        # A window that is on the target workspace but is not one of ours. On a
        # workspace picked *because* it was empty this is something that turned
        # up mid-build — a Chrome profile dialog, in the case that prompted this
        # — and it is sharing the row, so the panes are narrower than asked for.
        # Reported separately from `slow`: it is not a pane that failed to open.
        others = 0
        if target is not None:
            ours = {a for a in placed if a}
            others = len([c for c in self._query_json("clients")
                          if str(c.get("workspace", {}).get("name")) == str(target)
                          and not c.get("floating")
                          and c.get("address") not in ours])
        where = f"workspace {target}" if target else "this workspace"
        if not opened and not slow and not unmatched:
            return Result(False, "nothing opened")
        summary = f"Composed {where} in a {layout} layout: {', '.join(opened)}." if opened \
            else f"Nothing came up on {where}."
        if slow:
            summary += (f" Still opening or did not appear: {', '.join(slow)} — "
                        "tell the user that, do not claim it is on screen.")
        if unmatched:
            summary += (f" Did not appear as asked: {'; '.join(unmatched)}. Those windows "
                        "were left where they opened; tell the user, and move one with "
                        "window.move only if they say it is the one they wanted.")
        if others:
            summary += (f" {others} other window(s) were already on {where} and are "
                        "sharing the row, so the panes are narrower than planned. "
                        "Mention that only if the user asks why.")
        return Result(True, summary)

    def _kb_layout(self) -> tuple[str, str]:
        """The layout the user is actually typing on, defaulting to us.

        Read per call rather than cached on the executor: a layout switch is a
        thing people do mid-session, and typing the previous layout's
        characters would be exactly the silent wrong action #60 is about.
        keys._keymap does the expensive part and is itself cached by layout.
        """
        def option(name: str) -> str:
            result = self._shell(["hyprctl", "getoption", f"input:{name}", "-j"])
            if not result.ok:
                return ""
            try:
                return str(json.loads(result.output).get("str") or "").strip()
            except (ValueError, AttributeError):
                return ""
        return option("kb_layout").split(",")[0] or "us", option("kb_variant").split(",")[0]

    def _tool_type_text(self, text: str, window: str = "activewindow") -> Result:
        """Type text INTO A NAMED WINDOW, routed by the compositor.

        This used to be three lines around `wtype`, which types into whatever
        holds focus at the moment it runs and exits 0 either way -- so text
        meant for an editor could land in a confirmation dialog that appeared
        half a second earlier, and the tool reported success (#60). A stray
        keystroke into a delete confirmation is the case that prompted this.

        `send_key_state` takes a window the way `send_shortcut` does, so the
        compositor routes it and there is no gap between choosing the target
        and the key arriving. Verified against a real unfocused window: the
        text arrives there and the focused window receives nothing.

        wtype is gone rather than kept as a fallback. Two ways to type, one
        guaranteed and one not, is where the unguaranteed one gets chosen by
        accident.
        """
        if not (text or ""):
            return Result(False, "text is required — say what should be typed")
        if refused := self._input_refused(window):
            return Result(False, refused)
        layout, variant = self._kb_layout()
        events, error = keys_for_text(text, layout, variant)
        if error:
            return Result(False, error)
        # One batch, not one dispatch per key. Measured: 80 key events cost
        # 16.8 ms batched against 1104 ms individually, which is the
        # difference between this being viable and not.
        commands = []
        for keysym, mods in events:
            for state in ("down", "up"):
                expression, failed = self._render("send_key_state", {
                    "key": keysym, "mods": mods, "state": state, "window": window})
                if failed:
                    return Result(False, failed)
                commands.append(f"dispatch {expression}")
        result = self._shell(["hyprctl", "--batch", ";".join(commands)])
        if not result.ok:
            return result
        # hyprctl reports a missing window as a warning with a zero exit code,
        # the same trap _dispatch_lua exists to close.
        if "not found" in result.output.lower():
            return Result(False, f"{window} was not found, so nothing was typed")
        if "error" in result.output.lower():
            return Result(False, f"the compositor refused some keys: {result.output[:200]}")
        where = "the focused window" if window == "activewindow" else window
        return Result(True, f"typed {len(text)} characters into {where}")

    def _tool_run_shell(self, command: str) -> Result:
        if not self.config.allow_shell:
            return Result(False, "shell access is disabled in config (allow_shell = false)")
        return self._shell(["bash", "-lc", command], timeout=30)

    # -- terminals, through tmux --------------------------------------------
    def _tmux(self, *args: str, timeout: float = 8.0) -> Result:
        if not shutil.which("tmux"):
            return Result(False, install_hint("tmux"))
        return self._shell(["tmux", *args], timeout=timeout, limit=1 << 20)

    def _tmux_panes(self) -> list[dict]:
        """Every pane in every session, whether or not anyone is looking at it."""
        fmt = ("#{session_name}\t#{session_attached}\t#{window_index}\t"
               "#{pane_index}\t#{pane_current_command}\t#{pane_title}")
        listed = self._tmux("list-panes", "-a", "-F", fmt)
        if not listed.ok:
            return []
        panes = []
        for line in listed.output.splitlines():
            cell = line.split("\t")
            if len(cell) < 6:
                continue
            panes.append({
                "target": f"{cell[0]}:{cell[2]}.{cell[3]}",
                "session": cell[0],
                "attached": cell[1] not in ("", "0"),
                "command": cell[4],
                "title": cell[5],
                "idle": cell[4] in IDLE_COMMANDS,
            })
        return panes

    def _resolve_pane(self, target: str) -> tuple[dict | None, str]:
        """Which pane `target` means, or (None, why not).

        An empty target is the common case — the user said "the terminal", not
        "Work:1.2". Prefer a pane someone is actually attached to, and prefer a
        busy one, because the pane worth reading is nearly always the one with
        something running in it.
        """
        panes = self._tmux_panes()
        if not panes:
            return None, ("no tmux session is running. Start one with omarchy_cli "
                          '"launch terminal tmux", which opens a terminal attached to '
                          "the Work session; commands run there can be read exactly, "
                          "from any workspace.")
        target = (target or "").strip()
        if target:
            exact = [p for p in panes if p["target"] == target]
            if exact:
                return exact[0], ""
            loose = [p for p in panes
                     if target.lower() in (p["target"] + " " + p["title"]).lower()]
            if len(loose) == 1:
                return loose[0], ""
            if not loose:
                return None, (f"no tmux pane matches {target!r}. Open ones: "
                              + ", ".join(p["target"] for p in panes))
            return None, (f"{target!r} matches several panes: "
                          + ", ".join(p["target"] for p in loose))
        busy = [p for p in panes if p["attached"] and not p["idle"]]
        attached = [p for p in panes if p["attached"]]
        return (busy or attached or panes)[0], ""

    def _capture_pane(self, target: str, lines: int = TERMINAL_LINES) -> Result:
        got = self._tmux("capture-pane", "-p", "-J", "-S", f"-{int(lines)}", "-t", target)
        if not got.ok:
            return got
        # capture-pane pads to the height of the pane; the blank tail is not
        # output, it is empty screen.
        text = "\n".join(got.output.splitlines()).rstrip()
        if not text.strip():
            return Result(True, "(that pane is empty)")
        if len(text) > TERMINAL_OUTPUT_LIMIT:
            text = "… [earlier output not shown]\n" + text[-TERMINAL_OUTPUT_LIMIT:]
        return Result(True, text)

    def _validate_read_terminal(self, target: str = "", lines: int = TERMINAL_LINES) -> str | None:
        try:
            int(lines)
        except (TypeError, ValueError):
            return "lines must be a whole number"
        return None

    def _tool_read_terminal(self, target: str = "", lines: int = TERMINAL_LINES) -> Result:
        pane, why = self._resolve_pane(target)
        if pane is None:
            return Result(False, why)
        captured = self._capture_pane(pane["target"], min(int(lines), 2000))
        if not captured.ok:
            return captured
        running = ("idle at the shell" if pane["idle"]
                   else f"still running {pane['command']!r}")
        return Result(True, f"{pane['target']} ({running}):\n{captured.output}")

    def _tool_list_terminals(self) -> Result:
        panes = self._tmux_panes()
        if not panes:
            return Result(False, "no tmux session is running")
        rows = [f"  {p['target']:<16} {'running ' + p['command'] if not p['idle'] else 'idle':<22}"
                f"{'' if p['attached'] else '(not on screen) '}{p['title'][:40]}"
                for p in panes]
        return Result(True, "tmux panes:\n" + "\n".join(rows))

    def _terminal_on_screen(self) -> bool:
        """Whether a terminal window is being drawn on a workspace in view.

        tmux's `session_attached` is not this. It says a client exists, not that
        anyone can see it — the client may be in a window on a workspace nobody
        has looked at since this morning. Running a command somewhere invisible
        is exactly what this tool must not do, so the compositor gets the last
        word on what "visible" means.
        """
        visible = self._visible_workspaces()
        for client in self._query_json("clients"):
            klass = (client.get("class") or "").lower()
            if not any(name in klass for name in TERMINAL_CLASSES):
                continue
            if str((client.get("workspace") or {}).get("name")) in visible:
                return True
        return False

    def _ensure_visible_session(self) -> tuple[dict | None, str]:
        """A pane the user can watch, opening a terminal if there is not one.

        Two conditions, and both are needed: tmux has a client (so keys sent to
        the pane are being rendered somewhere at all), and a terminal window is
        on a workspace currently being drawn (so that somewhere is in front of
        the user).
        """
        panes = [p for p in self._tmux_panes() if p["attached"]]
        if panes and self._terminal_on_screen():
            idle = [p for p in panes if p["idle"]]
            return (idle or panes)[0], ""
        started = self._shell(["omarchy", "launch", "terminal", "tmux"],
                              timeout=20, grace=LAUNCH_GRACE)
        if not started.ok:
            return None, f"could not open a terminal: {started.output}"
        deadline = time.monotonic() + TERMINAL_ATTACH_TIMEOUT
        while time.monotonic() < deadline:
            time.sleep(0.4)
            fresh = [p for p in self._tmux_panes() if p["attached"]]
            if fresh and self._terminal_on_screen():
                time.sleep(0.5)  # let the shell finish drawing its prompt
                return fresh[0], ""
        return None, "opened a terminal but tmux never attached to it"

    def _validate_run_in_terminal(self, command: str, target: str = "") -> str | None:
        if not (command or "").strip():
            return "command is required"
        if "\n" in command:
            # Naming the way that works matters more than the refusal. Given
            # only "newlines are not sent", the model retried the same heredoc
            # five times and then took thirteen commands and forty-five seconds
            # to write a two-line file.
            return ("newlines are not sent, so heredocs (cat > f <<EOF) cannot work "
                    "here. To write a file, use printf with escapes in ONE line: "
                    "printf '#!/bin/sh\\necho hi\\n' > f  — and note \\n inside single "
                    "quotes is the two characters backslash-n, which printf turns into "
                    "a newline. Append more with >>.")
        return None

    def _tool_run_in_terminal(self, command: str, target: str = "") -> Result:
        if error := self._validate_run_in_terminal(command, target):
            return Result(False, error)
        command = command.strip()
        if target:
            pane, why = self._resolve_pane(target)
            if pane is not None and not (pane["attached"] and self._terminal_on_screen()):
                return Result(False,
                              f"{pane['target']} is not on screen. Commands only run in "
                              "panes the user can see; read that one instead, or leave "
                              "target empty to use a visible terminal.")
        else:
            pane, why = self._ensure_visible_session()
        if pane is None:
            return Result(False, why)
        if not pane["idle"]:
            return Result(False,
                          f"{pane['target']} is busy running {pane['command']!r}; typing "
                          "into it would go to that program. Wait for it with "
                          "watch_terminal, or pick another pane.")

        sent = self._tmux("send-keys", "-t", pane["target"], "--", command, "Enter")
        if not sent.ok:
            return sent
        started_at = time.monotonic()
        deadline = started_at + TERMINAL_QUICK_WAIT
        seen_busy = False
        while time.monotonic() < deadline:
            time.sleep(TERMINAL_POLL)
            current = next((p for p in self._tmux_panes()
                            if p["target"] == pane["target"]), None)
            if current is None:
                return Result(False, "that pane went away while the command was running")
            if not current["idle"]:
                seen_busy = True
                continue
            # Idle. Either it finished, or it has not started yet — and telling
            # those apart is the whole reason for the grace period.
            if seen_busy or time.monotonic() - started_at > TERMINAL_START_GRACE:
                out = self._capture_pane(pane["target"])
                return Result(True, f"ran {command!r} in {pane['target']}:\n{out.output}")

        if not self.announces_watches:
            return Result(True,
                          f"{command!r} is still running in {pane['target']} after "
                          f"{TERMINAL_QUICK_WAIT:.0f}s. Nothing here will say when it "
                          "finishes; tell the user, and read it later with read_terminal.")
        self.watch(pane["target"], command, seen_busy=seen_busy)
        return Result(True,
                      f"{command!r} is still running in {pane['target']} after "
                      f"{TERMINAL_QUICK_WAIT:.0f}s, so I am watching it and will say when "
                      "it finishes. Tell the user that, and carry on with something else "
                      "rather than waiting.")

    # -- watching a pane ----------------------------------------------------
    def watch(self, target: str, label: str = "", seen_busy: bool = False) -> None:
        self._watches[target] = {"label": label or "the command",
                                 "started": time.monotonic(),
                                 "seen_busy": seen_busy}

    def _validate_watch_terminal(self, target: str = "", note: str = "") -> str | None:
        return None

    def _tool_watch_terminal(self, target: str = "", note: str = "") -> Result:
        pane, why = self._resolve_pane(target)
        if pane is None:
            return Result(False, why)
        if pane["idle"]:
            out = self._capture_pane(pane["target"], 40)
            return Result(False,
                          f"{pane['target']} is already idle — nothing is running there to "
                          f"wait for. What it last showed:\n{out.output}")
        if not self.announces_watches:
            return Result(False,
                          f"{pane['target']} is running {pane['command']}. Nothing in this "
                          "process will say when it finishes, so do not promise that. "
                          "Read it later with read_terminal.")
        # Busy was just confirmed above, so idle from here means finished —
        # this watch does not need the start-up grace.
        self.watch(pane["target"], note or pane["command"], seen_busy=True)
        return Result(True,
                      f"watching {pane['target']} ({pane['command']}). I will say when it "
                      "finishes, even if the user has moved to another workspace. Do not "
                      "wait here — say that it is being watched and carry on.")

    def poll_watches(self) -> list[dict]:
        """Watches that are over, and why. Called from the daemon's background loop.

        Three ways to be over, and the middle one is the reason this is not a
        one-liner. A pane that is idle has either finished or *not started yet*:
        `pane_current_command` still says "bash" for a moment after the keys are
        sent, because the shell has not forked. Reporting that as finished
        handed back a twenty-second command as done in under half a second.
        So a watch has to see the pane busy before idle means anything — unless
        the grace period passes without it ever looking busy, which is what an
        instant command or a shell builtin like `cd` looks like.

        Returns and forgets, so a finished job is announced exactly once.
        """
        if not self._watches:
            return []
        panes = {p["target"]: p for p in self._tmux_panes()}
        finished, now = [], time.monotonic()
        for target, watch in list(self._watches.items()):
            pane = panes.get(target)
            age = now - watch["started"]
            if pane is None:
                reason = "vanished"
            elif not pane["idle"]:
                watch["seen_busy"] = True
                if age <= WATCH_MAX_SECONDS:
                    continue
                reason = "timed_out"
            elif not watch["seen_busy"] and age <= TERMINAL_START_GRACE:
                continue  # keys are sent but the shell has not forked yet
            else:
                reason = "finished"
            del self._watches[target]
            finished.append({
                "target": target,
                "label": watch["label"],
                "seconds": age,
                "vanished": reason == "vanished",
                "timed_out": reason == "timed_out",
                "tail": "" if reason == "vanished" else self._capture_pane(target, 30).output,
            })
        return finished

    # -- the web ------------------------------------------------------------
    def _open_web_window(self, url: str, hint: str,
                         timeout: float = WEB_WINDOW_TIMEOUT) -> tuple[dict | None, str]:
        """Open `url` as its own window and hand back the client, or say why not.

        `omarchy launch webapp` is deliberate: it is `chrome --app=<url>`, which
        makes a real window rather than a tab in one that already exists. A tab
        is invisible to hyprctl, so there is no way to wait for it, read it,
        move it or close it — the assistant that opened one was left guessing
        whether anything had happened, and guessed wrong.
        """
        rows, failed = self._query_rows("clients")
        if failed:
            return None, (f"{failed}, so a new window could not be told from one "
                          "already open; nothing was launched")
        before = {c.get("address") for c in rows}
        launched = self._shell(["omarchy", "launch", "webapp", url],
                               timeout=20, grace=LAUNCH_GRACE)
        if not launched.ok:
            return None, f"could not open the browser: {launched.output}"
        address = self._await_new_window(before, timeout, hint)
        if address is None:
            # Not read and not returned: a caller that got it would OCR it as
            # the page, and web_search would close it on the next search (#75).
            if desc := self._unmatched_new_windows(before):
                return None, (f"the browser did not open a window within {timeout:.0f}s. "
                              f"A different window did appear ({desc}); it is not the "
                              "page, so it was not read. Say so rather than assuming "
                              "it worked.")
            return None, ("the browser did not open a window within "
                          f"{timeout:.0f}s. Say so rather than assuming it worked.")
        self._dismiss_browser_error_dialogs()
        window = next((c for c in self._query_json("clients")
                       if c.get("address") == address), None)
        if window is None:
            return None, "the window opened and then went away again"
        return window, ""

    def _await_paint(self, window: dict, ceiling: float) -> Result:
        """Read the window as soon as it has stopped changing, or give up.

        Takes the window rather than a rect, and re-resolves the rect on every
        attempt. #33: the rect used to be resolved once by the caller and
        handed to both this call and the retry, so the retry read wherever the
        window had been up to two seconds and a full OCR earlier. That is the
        worst case to get wrong -- the retry exists *because* the first read
        came back short, which is exactly when the page is still settling and
        the window most likely to have been moved, resized or re-tiled.

        Returns the last read either way: "it never settled" and "it settled on
        very little" are the same thing to the caller, which retries on length.
        """
        geometry, error = self._web_geometry(window)
        if error:
            return Result(False, error)
        deadline = time.monotonic() + ceiling
        # Bounded by attempts as well as by the clock. A read is not cheap --
        # OCR of a browser window measures 1-4s on this machine -- so in
        # practice the first read alone spends the budget and the deadline is
        # what stops us. The attempt cap is what stops a spin when a read
        # returns instantly, which is every test that stubs the OCR out.
        attempts = max(2, int(ceiling / WEB_PAINT_POLL))
        time.sleep(min(WEB_PAINT_FLOOR, ceiling))
        result = self._ocr_region(geometry)
        previous = result.output if result.ok else ""
        for _ in range(attempts - 1):
            if result.ok and len(result.output) >= WEB_ENOUGH_TEXT:
                return result
            if time.monotonic() >= deadline:
                return result
            time.sleep(min(WEB_PAINT_POLL, max(0.0, deadline - time.monotonic())))
            geometry = self._web_geometry(window)[0] or geometry
            result = self._ocr_region(geometry)
            # Two reads the same means it has stopped painting. On a page with
            # almost nothing on it that is also true, and correct: there is
            # nothing more coming.
            if result.ok and result.output == previous and previous:
                return result
            previous = result.output if result.ok else previous
        return result

    def _web_geometry(self, window: dict) -> tuple[str | None, str | None]:
        """This window's rect, re-read from hyprctl if it is still there.

        The rect the caller is holding was taken when the window first mapped,
        which for a page that was still being placed is not where it ended up.
        A stale rect OCRs the desktop beside the window. Falls back to what the
        caller had if the window has gone from the client list, because a
        slightly wrong read beats no read at all.
        """
        address = window.get("address")
        if address:
            for client in self._query_json("clients"):
                if client.get("address") == address:
                    window = client
                    break
        try:
            return (f'{window["at"][0]},{window["at"][1]} '
                    f'{window["size"][0]}x{window["size"][1]}'), None
        except (KeyError, IndexError, TypeError):
            return None, "could not read that window's geometry"

    def _read_web_window(self, window: dict, settle: float = WEB_RENDER_SETTLE) -> Result:
        """OCR a freshly opened page, as soon as it has actually painted.

        A window is mapped well before it has drawn anything. Reading straight
        away returns a blank page, which is indistinguishable from a page with
        nothing on it — so an empty or very short read is retried once.

        This used to `time.sleep(settle)` unconditionally, settle being two
        seconds, on every read and again on the retry. Two seconds is what a
        slow page needs; a page that painted in 300ms paid it anyway. So the
        wait is now a poll against the thing actually being waited for.

        The predicate has to be about pixels. The window existing does not mean
        it has drawn, which is the whole reason the old code slept instead of
        watching the client list — and it is why this cannot be an `openwindow`
        event. There is a floor before the first read because a page that has
        painted one header in 50ms would otherwise be taken as finished, and
        the ceiling is the old `settle`, so the worst case is what happened
        before.
        """
        result = self._await_paint(window, settle)
        if not result.ok or len(result.output) < WEB_ENOUGH_TEXT:
            retry = self._await_paint(window, settle)
            if retry.ok and len(retry.output) > len(result.output if result.ok else ""):
                result = retry
        if result.ok and RESTORE_BUBBLE in result.output.lower():
            return Result(True, result.output + (
                "\n\n[Chromium is showing its \"Restore pages\" crash prompt over this "
                "page. Dismiss it with click_text on \"Restore\" or \"No thanks\", or "
                "send_shortcut Escape, then read again — what is above is partly that "
                "prompt, not the page.]"))
        return result

    def _dismiss_browser_error_dialogs(self) -> int:
        """Close Chrome's "Profile error occurred" boxes. See PROFILE_ERROR_TITLE.

        Matched on an empty class *and* the title, so this can only ever take
        down an unclassed dialog — never a real window, whatever it is called.
        """
        closed = 0
        for client in self._query_json("clients"):
            if client.get("class"):
                continue
            if PROFILE_ERROR_TITLE in (client.get("title") or "").lower():
                self._dispatch("window.close", {
                    "window": f'address:{client["address"]}'})
                closed += 1
        return closed

    def _close_last_search(self) -> bool:
        """Take down the window the previous search opened, if it is still up.

        Tracked by address rather than matched by class: the class of a Google
        results pane is indistinguishable from one the user asked for by name,
        and closing a window somebody wanted is a worse failure than leaving a
        stale one behind.
        """
        address = self._last_search_window
        self._last_search_window = None
        if not address:
            return False
        if not any(c.get("address") == address for c in self._query_json("clients")):
            return False
        self._dispatch("window.close", {"window": f"address:{address}"})
        time.sleep(0.4)
        return True

    def _validate_web_search(self, query: str, scope: str = "web") -> str | None:
        if not (query or "").strip():
            return "query is required — say what to search for"
        if scope not in SEARCH_SCOPES:
            return f"scope must be one of {', '.join(SEARCH_SCOPES)}"
        return None

    def _tool_web_search(self, query: str, scope: str = "web") -> Result:
        if error := self._validate_web_search(query, scope):
            return Result(False, error)
        query = " ".join(query.split())
        url = SEARCH_SCOPES[scope].format(q=quote_plus(query))
        self._close_last_search()
        window, why = self._open_web_window(url, urlparse(url).hostname or "")
        if window is None:
            return Result(False, why)
        address = window["address"]
        self._last_search_window = address

        if scope in VISUAL_SCOPES:
            return Result(True, f"{scope} for {query!r} are on screen now "
                                f"(window address:{address}). They are pictures, so tell "
                                "the user to look rather than describing them from OCR.")
        read = self._read_web_window(window)
        if not read.ok:
            return Result(True, f"the results for {query!r} are on screen "
                                f"(window address:{address}) but could not be read: "
                                f"{read.output}")
        return Result(True,
                      f"results for {query!r} (window address:{address}, and on screen "
                      f"for the user to see):\n\n{read.output}\n\n"
                      "This is OCR of a results page, so quote it rather than embroidering "
                      "it, and scroll or click_text on the window to go further.")

    def _validate_open_page(self, url: str, read: bool = True) -> str | None:
        if urlparse(url or "").scheme.lower() not in ("http", "https"):
            return "url must be an http or https address"
        return None

    def _tool_open_page(self, url: str, read: bool = True) -> Result:
        if error := self._validate_open_page(url, read):
            return Result(False, error)
        host = urlparse(url).hostname or ""
        window, why = self._open_web_window(url, host[4:] if host.startswith("www.") else host)
        if window is None:
            return Result(False, why)
        address = window["address"]
        if not read:
            return Result(True, f"opened {url} (window address:{address})")
        got = self._read_web_window(window)
        if not got.ok:
            return Result(True, f"opened {url} (window address:{address}) but could not "
                                f"read it: {got.output}")
        return Result(True, f"opened {url} (window address:{address}):\n\n{got.output}")

    # -- reach: scrolling ---------------------------------------------------
    def _window_geometry(self, target: str) -> tuple[dict | None, str]:
        """The client `target` names, or (None, why not)."""
        window, error = self._resolve_window(target)
        return (window, "") if window else (None, error or "nothing is open")

    def _validate_scroll(self, direction: str, amount: int = 1,
                         target: str = "activewindow") -> str | None:
        if direction not in SCROLL_SIGN:
            return f"direction must be one of {', '.join(SCROLL_SIGN)}"
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            return "amount must be a whole number of screens"
        if amount < 1:
            return "amount must be at least 1"
        return None

    def _tool_scroll(self, direction: str, amount: int = 1,
                     target: str = "activewindow") -> Result:
        """Turn the wheel over a window.

        Pointing at the window first is not decoration: a wheel event goes to
        whatever is under the cursor, so without the move, "scroll the article"
        scrolled whichever pane the mouse happened to be resting on — and in a
        composed workspace that is usually the wrong one.
        """
        if error := self._validate_scroll(direction, amount, target):
            return Result(False, error)
        amount = min(int(amount), SCROLL_MAX_PAGES)
        window, why = self._window_geometry(target)
        if window is None:
            return Result(False, why)
        # Passes the resolved window: _window_geometry already paid the 14 ms
        # hyprctl query, and there is no reason to pay it twice.
        if refused := self._input_refused(target, window):
            return Result(False, refused)

        if blocked := self._screen_unavailable():
            return Result(False, blocked)
        workspace = str((window.get("workspace") or {}).get("name"))
        if workspace not in self._visible_workspaces():
            return Result(False, f"that window is on workspace {workspace}, which is not "
                                 "on screen, so there is nothing to scroll. Switch to it "
                                 'first — hypr_dispatch focus with workspace = '
                                 f'"{workspace}".')
        try:
            x = window["at"][0] + window["size"][0] // 2
            y = window["at"][1] + window["size"][1] // 2
        except (KeyError, IndexError, TypeError):
            return Result(False, "could not read that window's geometry")

        title = (window.get("title") or window.get("class") or "the window")[:40]
        # No Page_Down fallback any more. It existed because ydotool was
        # frequently absent, and the reason for its absence was the root
        # daemon this no longer needs. Pressing a key and calling it a scroll
        # worked only where the page itself had focus, and reported success
        # either way (#30).
        moved = self._dispatch("cursor.move", {"x": x, "y": y})
        if not moved.ok:
            return Result(False, f"could not point at the window: {moved.output}")
        span = window["size"][0] if direction in ("left", "right") else window["size"][1]
        clicks = _scroll_clicks(span, amount) * SCROLL_SIGN[direction]
        # `S <dx> <dy>`: horizontal travels in dx, vertical in dy. ydotool
        # needed both axes named on every call; the helper does not.
        horizontal = direction in ("left", "right")
        dx, dy = (clicks, 0) if horizontal else (0, clicks)
        turned = self._send_input(lambda: virtual_input.scroll(dx, dy))
        if not turned.ok:
            return turned
        screens = "a screen" if amount == 1 else f"{amount} screens"
        return Result(True, f"scrolled {title} {direction} about {screens}. "
                            "Read it again to see what is there now.")

    # -- patience: waiting for something to happen --------------------------
    def _validate_wait_for(self, what: str, value: str, timeout: float = WAIT_DEFAULT) -> str | None:
        if what not in ("text", "window", "window_gone"):
            return 'what must be "text", "window" or "window_gone"'
        if not (value or "").strip():
            return "value is required — say what you are waiting for"
        try:
            float(timeout)
        except (TypeError, ValueError):
            return "timeout must be a number of seconds"
        return None

    def _tool_wait_for(self, what: str, value: str,
                       timeout: float = WAIT_DEFAULT,
                       target: str = "screen") -> Result:
        """Poll until the condition holds, and say how long it took.

        A timeout here is a finding, not a failure: "the page did not load in
        ten seconds" is something the user wants said out loud, and it is much
        better than reading a stale screen and reporting its contents as new.
        """
        if error := self._validate_wait_for(what, value, timeout):
            return Result(False, error)
        limit = max(0.5, min(float(timeout), WAIT_MAX))
        started = time.monotonic()
        gap = WAIT_POLL_TEXT if what == "text" else WAIT_POLL_WINDOW
        last_error = ""

        while True:
            if what == "text":
                # Resolved every iteration, not hoisted: a window being waited
                # on can be moved or resized while the wait is running, and
                # OCRing its old rect is how a wait succeeds on the wrong
                # pixels.
                geometry, last_error = self._target_geometry(target)
                if last_error:
                    return Result(False, last_error)
                words, last_error = self._ocr_words(geometry)
                if last_error:
                    return Result(False, last_error)
                found = self._find_phrase(words, value) is not None
            else:
                clients, failed = self._query_rows("clients")
                if failed:
                    # Stop rather than poll on. If hyprctl is not answering,
                    # the next fifteen attempts will not either, and burning
                    # the budget in silence is worse than saying so. Note what
                    # this deliberately does NOT say: "the window is still
                    # open" would be the same guess as "it closed", in the
                    # other direction, and guessing is the bug (#24).
                    return Result(False, f"{failed}, so whether "
                                         f"{_wait_description(what, value)} is unknown")
                exists = any(_window_matches(c, value) for c in clients)
                found = exists if what == "window" else not exists

            waited = time.monotonic() - started
            if found:
                return Result(True, f"{_wait_description(what, value)} after "
                                    f"{waited:.1f}s. Carry on.")
            if waited + gap >= limit:
                return Result(True, f"waited {limit:.0f}s and {_wait_timeout(what, value)}. "
                                    "That is what happened — say so, or look with "
                                    "read_screen before deciding what to do next.")
            if what == "text":
                # Deliberately not the compositor's event stream. No event
                # fires when words appear on a page, so the Event would never
                # be set and this would run at the backoff interval anyway --
                # the look of an improvement with none of it. This gap is set
                # by what an OCR read costs, not by compositor latency.
                time.sleep(gap)
            else:
                self._wait_tick(waited, slow=gap)

    # -- exact text: the clipboard ------------------------------------------
    def _validate_clipboard(self, action: str, text: str = "") -> str | None:
        if action not in ("read", "write"):
            return 'action must be "read" or "write"'
        if action == "write" and not (text or "").strip():
            return "text is required to write to the clipboard"
        return None

    def _tool_clipboard(self, action: str, text: str = "") -> Result:
        if error := self._validate_clipboard(action, text):
            return Result(False, error)
        if action == "write":
            if not shutil.which("wl-copy"):
                return Result(False, install_hint("wl-copy", "wl-clipboard"))
            # Not one pipe between here and wl-copy. It forks a process that
            # holds the selection until something else takes it, and that child
            # inherits our file descriptors: with stderr on a pipe, reading to
            # EOF meant waiting for a process designed to outlive us, so a copy
            # that had already worked was reported as a ten-second timeout.
            # A file has no such problem — the parent exits, we read it after.
            with tempfile.TemporaryFile() as errors:
                try:
                    done = subprocess.run(["wl-copy", "--", text],
                                          stdin=subprocess.DEVNULL,
                                          stdout=subprocess.DEVNULL, stderr=errors,
                                          timeout=10)
                except (OSError, subprocess.SubprocessError) as exc:
                    return Result(False, f"could not write the clipboard: {exc}")
                if done.returncode != 0:
                    errors.seek(0)
                    detail = errors.read().decode(errors="replace").strip()
                    return Result(False, detail or "wl-copy failed")
            return Result(True, f"copied {len(text)} characters to the clipboard")

        if not shutil.which("wl-paste"):
            return Result(False, install_hint("wl-paste", "wl-clipboard"))
        got = self._shell(["wl-paste", "--no-newline", "--type", "text/plain"],
                          timeout=10, limit=CLIPBOARD_LIMIT)
        if not got.ok:
            # wl-paste exits non-zero on an empty selection and on one holding
            # only an image, and the two read very differently to a user. Its
            # own words for empty are "Nothing is copied".
            lowered = got.output.lower()
            if "empty" in lowered or "nothing is copied" in lowered:
                return Result(True, "the clipboard is empty")
            return Result(True, "the clipboard does not hold any text "
                                f"({got.output.strip() or 'no text/plain offer'})")
        if not got.output.strip():
            return Result(True, "the clipboard is empty")
        return got

    # -- the machine about itself -------------------------------------------
    def _tool_system_query(self, topic: str) -> Result:
        """Read-only facts, from a fixed list of commands.

        Deliberately not run_shell. Every one of these is a question people ask
        out loud — "how much space is left", "am I still on wifi" — and none of
        them is worth making someone open the shell tool for, which would hand
        an open microphone the whole command line at the same time.
        """
        recipe = SYSTEM_QUERIES.get(topic)
        if recipe is None:
            return Result(False, f"unknown topic {topic!r}. Choose one of: "
                                 + ", ".join(sorted(SYSTEM_QUERIES)))
        if callable(recipe):
            return recipe(self)
        parts = []
        for label, argv, *cap in recipe:
            if not shutil.which(argv[0]):
                continue
            got = self._shell(argv, timeout=10, limit=1200)
            if not got.ok or not got.output.strip():
                continue
            body = got.output.strip()
            if cap:
                body = "\n".join(body.splitlines()[:cap[0]])
            parts.append(f"{label}:\n{body}" if label else body)
        if not parts:
            return Result(False, f"nothing on this machine could answer {topic!r}")
        return Result(True, "\n\n".join(parts))

    def _battery(self) -> Result:
        """/sys rather than upower, because the answer is often "there isn't one"."""
        root = Path("/sys/class/power_supply")
        try:
            supplies = sorted(root.iterdir())
        except OSError:
            supplies = []
        rows = []
        for entry in supplies:
            def read(name: str) -> str:
                try:
                    return (entry / name).read_text().strip()
                except OSError:
                    return ""
            if read("type").lower() == "battery":
                percent, status = read("capacity"), read("status")
                rows.append(f"{entry.name}: {percent or '?'}% ({status or 'unknown'})")
            elif read("type").lower() == "mains" and read("online") == "1":
                rows.append(f"{entry.name}: on mains power")
        if not rows:
            return Result(True, "this machine has no battery — it is a desktop, "
                                "always on mains power")
        return Result(True, "\n".join(rows))

    # -- media, over MPRIS --------------------------------------------------
    def _media_status(self) -> Result:
        """What each MPRIS player reports, asked rather than photographed (#73).

        No recording check: nothing is captured. The lock and sensitive-window
        checks still apply, because a title is as private as the window it
        came from -- a browser player's title is the tab's, so it is withheld
        while a private window is open on ANY workspace, or when that is unknown.
        """
        if self._session_is_locked():
            return Result(False, "the session is locked, so what is playing was not "
                                 "read. Ask the user to unlock first.")
        if not shutil.which("playerctl"):
            return Result(False, install_hint("playerctl", "playerctl"))
        got = self._shell(["playerctl", "-a", "--format", MEDIA_FORMAT, "status"],
                          timeout=4)
        if "No players found" in (got.output or ""):
            return Result(True, "no media player is running, so nothing is playing")
        if not got.ok:
            why = (got.output or "").strip().splitlines()
            return Result(False, f"playerctl failed: {why[0] if why else 'no output'}")
        browser: list[str] = []  # why browser titles are withheld; read once, lazily

        def browser_withheld() -> str:
            if not browser:
                rows, err = self._query_rows("clients")
                if err:
                    browser.append("the window list could not be read")
                else:
                    kinds = (self._sensitive_kind(str(r.get("class", "")),
                                                  str(r.get("title", "")))
                             for r in rows)
                    kind = next((k for k in kinds if k), None)
                    browser.append(f"{kind} is open" if kind else "")
            return browser[0]

        lines = []
        for line in got.output.strip().splitlines():
            player, status, artist, title = (line.split("\t") + ["", "", ""])[:4]
            row = f"{player}: {status}"
            if title:
                kind = self._sensitive_kind(player, f"{artist}\n{title}")
                why = f"it looks like {kind}" if kind else ""
                if not why and _BROWSER_PLAYER.search(player):
                    why = browser_withheld()
                if why:
                    row += f" — title withheld ({why})"
                else:
                    row += f" — {artist} – {title}" if artist else f" — {title}"
            lines.append(row)
        return Result(True, "\n".join(lines))

    def _validate_media_control(self, action: str) -> str | None:
        if action not in MEDIA_ACTIONS:
            return (f"unknown media action {action!r}. Choose one of: "
                    + ", ".join(MEDIA_ACTIONS))
        return None

    def _tool_media_control(self, action: str) -> Result:
        """Send one MPRIS command, then report what the player says, not what was hoped.

        No lock check, like launch_app, and the reply carries a status word,
        never a title.
        """
        error = self._validate_media_control(action)
        if error:
            return Result(False, error)
        if not shutil.which("playerctl"):
            return Result(False, install_hint("playerctl", "playerctl"))
        nothing = Result(False, f"no media player is running, so there is nothing to "
                                f"{action}. Open the music in its app first.")

        def status() -> Result:
            return self._shell(["playerctl", "status"], timeout=4)

        before = ""
        if action == "play-pause":
            pre = status()
            if "No players found" in (pre.output or ""):
                return nothing
            before = pre.output.strip() if pre.ok else ""
        sent = self._shell(["playerctl", action], timeout=4)
        if "No players found" in (sent.output or ""):
            return nothing
        if not sent.ok:
            return Result(False, f"playerctl {action} failed: {sent.output.strip()[:120]}")
        # None: next/previous, and a play-pause whose pre-read failed -- one read,
        # reported without claiming a change.
        expected = {"play": lambda s: s == "Playing",
                    "pause": lambda s: s == "Paused",
                    "play-pause": (lambda s: s != before) if before else None,
                    }.get(action)
        deadline = time.monotonic() + MEDIA_SETTLE
        while True:
            now = status()
            state = now.output.strip() if now.ok else "unknown"
            if (expected is None or expected(state)
                    or time.monotonic() + MEDIA_POLL > deadline + 1e-6):
                break
            time.sleep(MEDIA_POLL)
        if expected is None or expected(state):
            return Result(True, f"sent {action}; the player now reports {state}")
        return Result(True, f"sent {action}, but the player still reports {state}, "
                            f"so it may not have taken it")

    # -- the notebook -------------------------------------------------------
    def _notes_path(self) -> Path:
        from .config import STATE_DIR
        return STATE_DIR / "notes.json"

    def _read_notes(self) -> list[dict]:
        try:
            data = json.loads(self._notes_path().read_text())
        except (OSError, json.JSONDecodeError):
            return []
        return [n for n in data if isinstance(n, dict) and n.get("text")] \
            if isinstance(data, list) else []

    def _write_notes(self, notes: list[dict]) -> str | None:
        path = self._notes_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(notes[-NOTES_LIMIT:], indent=1))
            path.chmod(0o600)
        except OSError as exc:
            return f"could not write the notes: {exc}"
        return None

    def _validate_remember(self, action: str, text: str = "") -> str | None:
        if action not in ("note", "list", "forget"):
            return 'action must be "note", "list" or "forget"'
        if action == "note" and not (text or "").strip():
            return "text is required — say what to write down"
        if action == "forget" and not (text or "").strip():
            return 'text is required — words identifying the note, or "all"'
        return None

    def _tool_remember(self, action: str, text: str = "") -> Result:
        if error := self._validate_remember(action, text):
            return Result(False, error)
        notes = self._read_notes()

        if action == "list":
            if not notes:
                return Result(True, "the notebook is empty")
            return Result(True, "\n".join(
                f'{n.get("when", "?")}  {n["text"]}' for n in notes))

        if action == "forget":
            if text.strip().lower() == "all":
                kept, dropped = [], len(notes)
            else:
                wanted = [w for w in re.split(r"\W+", text.lower()) if w]
                kept = [n for n in notes
                        if not all(w in n["text"].lower() for w in wanted)]
                dropped = len(notes) - len(kept)
            if not dropped:
                return Result(False, f"no note matches {text!r}; nothing was forgotten")
            if error := self._write_notes(kept):
                return Result(False, error)
            return Result(True, f"forgot {dropped} note(s)")

        line = " ".join(text.split())[:NOTE_LENGTH_LIMIT]
        notes.append({"when": time.strftime("%Y-%m-%d %H:%M"), "text": line})
        if error := self._write_notes(notes):
            return Result(False, error)
        return Result(True, f"noted. {len(notes[-NOTES_LIMIT:])} note(s) in the notebook")
