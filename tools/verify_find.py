#!/usr/bin/env python3
"""Run the #70 requests against THIS machine's installed apps.

The unit tests use desktop entries they wrote themselves, so they check the
matching rule and nothing else. Whether "the file manager" finds anything on a
real desktop depends on what that desktop's entries actually declare -- which
is the question this answers. It is also the yardstick for whether matching
ever needs more than words: every request that misses here is a case for it.

Nothing is launched. It prints, per request, the top candidates, whether
launch_app would open one on its own (a clear match), and how long it took:

    python3 tools/verify_find.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice import capabilities  # noqa: E402

# The 24 requests from spec/2026-09-24-70-find-what-is-installed.md.
REQUESTS = [
    "zed", "the file manager", "my password manager", "obsidian", "code",
    "visual studio code", "vs code", "firefox", "browser", "discord", "spotify",
    "screen recorder", "obs", "calculator", "terminal", "disk usage", "slack",
    "image viewer", "zedd", "obsidien", "music", "email", "notes", "settings",
]


def main() -> int:
    started = time.perf_counter()
    count = len(capabilities.app_index())
    print(f"{count} apps indexed in {(time.perf_counter() - started) * 1e3:.0f} ms\n")
    for said in REQUESTS:
        started = time.perf_counter()
        found = capabilities.find_apps(said, limit=3)
        took = (time.perf_counter() - started) * 1e3
        match = capabilities.clear_match(found)
        verdict = f"LAUNCH {match['id']}" if match else ("choice" if found else "nothing")
        top = ", ".join(f"{row['name']} ({row['id']}) {score}" for score, row in found)
        print(f"{said:20} {took:5.1f} ms  {verdict:28} {top}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
