#!/usr/bin/env python3
"""Replay every `heard` line of session.log through the router (#71).

Prints `ROUTE` or `model` per line, then a total. This is how the router is
judged: the measured fixed commands must be ROUTE and nothing else may be, and
a new route earns its row by showing up here first.

It reads ~/.local/state/omarchy-voice/session.log and nothing else. It never
calls the Executor, so it cannot act: a ROUTE line is a description of what
would be dispatched, not a dispatch.

    python3 tools/eval_router.py          # against FIXTURE_CLIENTS below
    python3 tools/eval_router.py --live   # against `hyprctl -j clients`, read only
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice import router  # noqa: E402
from omarchy_voice.config import LOG_FILE, Config  # noqa: E402
from omarchy_voice.tools import Executor  # noqa: E402

# The windows the spec measured against. The unit tests import this list, so
# the 11-routed bar has one fixture. No title contains "terminal" or "weather"
# except Weather's own.
FIXTURE_CLIENTS = [
    {"class": "discord", "title": "Discord", "address": "0x1"},
    {"class": "chrome-cifhbcnohmdccbgoicgdjpfamggdegmo-Default",
     "title": "Microsoft Teams (PWA) - Chat", "address": "0x2"},
    {"class": "org.gnome.Weather", "title": "Weather", "address": "0x3"},
    {"class": "Alacritty", "title": "~/src", "address": "0x4"},
    {"class": "Alacritty", "title": "htop", "address": "0x5"},
    {"class": "google-chrome", "title": "GitHub - Google Chrome", "address": "0x6"},
]

_HEARD = re.compile(r"\sheard\s+((['\"]).*\2)\s*$")


def main() -> int:
    live = "--live" in sys.argv[1:]
    if live:
        executor = Executor(Config())
        clients = lambda: executor._query_rows("clients")  # noqa: E731
    else:
        clients = lambda: (FIXTURE_CLIENTS, None)  # noqa: E731
    total = routed = 0
    for line in LOG_FILE.read_text(errors="replace").splitlines():
        if not (m := _HEARD.search(line)):
            continue
        try:
            text = ast.literal_eval(m[1])
        except (ValueError, SyntaxError):
            continue
        total += 1
        hit = router.route(text, clients)
        if hit is None:
            print(f"model  {text!r}")
            continue
        routed += 1
        what = "answer" if hit.tool is None else Executor.describe(hit.tool, hit.args)
        print(f"ROUTE  {text!r} -> {what}")
    print(f"{total} lines, {routed} routed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
