"""The measured fixed commands, answered before the model is asked (#71).

Every command used to cost at least two Claude turns, 9-14 s, even "switch to
workspace one". The session log was replayed to see which commands a person
actually says, and four fixed shapes covered every command in it: workspace N,
open a terminal, what windows are open, and focus/move/close one named open
window. Only those are routed. Volume, brightness, media, lock, screenshots
and themes were never said, so they are not here. "Open <app>" is not routed
either: "today's weather" clear-matches GNOME Weather.

The rule is exact hits only. The whole sentence must be one of the shapes
(`re.fullmatch`, never a search), and anything else -- a second sentence, a
negation, a compound, a pronoun, a name that fits two windows -- goes to the
model exactly as before. A named window must be the only window that matches
at any score, which is stricter than `_resolve_window`'s unique top score: a
guess is the model's job, not this module's. A window to close must also match
by class, never by title alone: a web page writes its window's title.

Adding a route: a toggle says "toggled", never "on" or "off", because the
router cannot see the state it flipped. A direction word is part of the
pattern, never a wildcard.

ponytail: a phrase table and six regexes. That is the ceiling on purpose. A new
route comes from `tools/eval_router.py` showing it in the log: one row and one
test each.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .tools import _rank_windows, _short_class


@dataclass(frozen=True)
class Route:
    """One call to run, and the line to speak if it works.

    `answer` is set only for the window list, which runs nothing.
    """

    tool: str | None
    args: dict = field(default_factory=dict)
    said: str = ""
    answer: str | None = None


NUMBERS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
           "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
WORDS = {v: k for k, v in NUMBERS.items()}

# Any of these and the sentence is not a plain command: a negation, a
# correction, or two things at once. Checked before any pattern.
REFUSE = {"don't", "dont", "not", "never", "no", "stop", "cancel", "undo",
          "without", "except", "and", "then", "but"}

# A target that is the last turn or several windows, not one named window.
PRONOUNS = {"it", "this", "that", "them", "this one", "that one", "everything",
            "all", "window", "windows"}

_POLITE = re.compile(r"\b(?:(?:can|could|would|will) you|please|for me|just)\b")
_LEADING = ("okay", "ok", "so", "right", "now")

_N = r"(10|[1-9]|one|two|three|four|five|six|seven|eight|nine|ten)"
_NAME = r"(?:the |my )?(.+?)(?: window| app)?"

_WORKSPACE = re.compile(rf"(?:(?:switch|go|change) (?:back )?to )?workspace {_N}")
_TERMINAL = re.compile(r"open (?:up )?(?:a )?(?:new )?terminal")
_LIST = re.compile(r"what windows (?:do i have|are|have i got) open(?:ed)?"
                   r"(?: on (?:this|my|the) desktop)?")
_MOVE = re.compile(rf"move {_NAME} (?:back )?to workspace {_N}")
_CLOSE = re.compile(rf"close {_NAME}")
_FOCUS = re.compile(rf"(?:bring up|focus|switch to|go to) {_NAME}")


def _normalise(text: str, wake: str = "") -> str | None:
    """One sentence, lowercase, without the politeness; None for two sentences."""
    text = text.lower().strip().strip("\"'“”‘’").strip()
    text = text.replace("’", "'")
    pieces = [p for p in re.split(r"[.?!]", text) if p.strip()]
    if len(pieces) != 1:
        return None
    text = _POLITE.sub(" ", pieces[0].replace(",", " "))
    words = text.split()
    wakes = {w for w in wake.lower().split() if w}
    while words:
        if words[0] in _LEADING or words[0] in wakes:
            words = words[1:]
        elif words[0] == "hey" and len(words) > 1 and words[1] in wakes:
            words = words[2:]
        else:
            break
    return " ".join(words)


def _spoken(client: dict) -> str:
    """A window's name as a person says it: `org.gnome.Weather` is "Weather".

    A PWA is already its title. A raw class loses its reverse-DNS prefix and
    gains a capital, because `discord` is the class and "Discord" is the name.
    """
    name = _short_class(client)
    if name == str(client.get("class") or "?"):
        name = name.rsplit(".", 1)[-1]
        name = name[:1].upper() + name[1:]
    return name


def _one_window(clients, name: str, *, by_class: bool = False) -> dict | None:
    """The only open window `name` fits, or None -- never the best of several.

    `by_class` refuses a window that fits only by its title (score 1.0). A web
    page sets its window's title, so "close notes" would close the browser
    whose tab says "Release notes". Close asks for it; focus and move are
    undone by the next command, and a Teams web app is only its title.
    """
    if name in PRONOUNS or any(w in PRONOUNS for w in name.split()):
        return None
    rows, error = clients()
    if error:
        return None
    ranked = _rank_windows(rows, name)
    if len(ranked) != 1 or (by_class and ranked[0][0] < 2.0):
        return None
    return ranked[0][1]


def _window_list(rows: list[dict]) -> str:
    if not rows:
        return "Nothing is open."
    counts = Counter(_spoken(c) for c in rows)
    names = [n if k == 1 else f"{WORDS.get(str(k), k)} {n}" for n, k in counts.items()]
    if len(names) == 1:
        return f"Open: {names[0]}."
    return f"Open: {', '.join(names[:-1])}, and {names[-1]}."


def route(text: str, clients, wake: str = "") -> Route | None:
    """The call for `text` if it is exactly one fixed command, else None.

    `clients` is a zero-argument callable returning `(rows, error)`, as
    `Executor._query_rows("clients")` does. It is called only once a pattern
    that needs windows has matched, so a miss costs no `hyprctl`.
    """
    sentence = _normalise(text, wake)
    if not sentence:
        return None
    if REFUSE & set(sentence.split()):  # "do not" is refused by "not"
        return None

    if m := _WORKSPACE.fullmatch(sentence):
        n = NUMBERS.get(m[1], m[1])
        return Route("hypr_dispatch", {"dispatcher": "focus", "args": {"workspace": n}},
                     f"Workspace {WORDS[n]}.")
    if _TERMINAL.fullmatch(sentence):
        return Route("omarchy_cli", {"command": "launch terminal"}, "Opening a terminal.")
    if _LIST.fullmatch(sentence):
        rows, error = clients()
        if error:
            return None  # "nothing is open" would be a guess
        return Route(None, {}, "", answer=_window_list(rows))
    if m := _MOVE.fullmatch(sentence):
        if not (window := _one_window(clients, m[1])):
            return None
        n = NUMBERS.get(m[2], m[2])
        return Route("hypr_dispatch",
                     {"dispatcher": "window.move",
                      "args": {"workspace": n, "follow": False,
                               "window": f"address:{window['address']}"}},
                     f"Moved {_spoken(window)} to workspace {WORDS[n]}.")
    if m := _CLOSE.fullmatch(sentence):
        if not (window := _one_window(clients, m[1], by_class=True)):
            return None
        return Route("hypr_dispatch",
                     {"dispatcher": "window.close",
                      "args": {"window": f"address:{window['address']}"}},
                     f"Closed {_spoken(window)}.")
    if m := _FOCUS.fullmatch(sentence):
        if not (window := _one_window(clients, m[1])):
            return None
        return Route("hypr_dispatch",
                     {"dispatcher": "focus",
                      "args": {"window": f"address:{window['address']}"}},
                     f"{_spoken(window)}.")
    return None
