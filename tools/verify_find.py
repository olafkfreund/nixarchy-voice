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

The id-tail lists come from #96: none of GENERIC_TAILS should show LAUNCH.

Then the #83 service requests: the top 5 user services for each, with their
state, read from `systemctl --user list-units` (nothing is started), and how
many MCP servers Claude Code has configured, per scope kind -- counts only, no
name. It exits 1 if "notarealunit" matches anything or a warm service query
takes 50 ms or more.
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

# The last part of a desktop id, said alone (#96). A generic one is not a name.
GENERIC_TAILS = ["android", "setup", "debug", "04", "vending", "desktop", "app", "gtk"]
# The 23 that name their app on p620. katana and livewallpaper are accepted losses.
NAME_TAILS = [
    "gemini4352", "katana", "livewallpaper", "lmstudio", "lanmouse", "turtle",
    "boxbuddyrs", "gdmsettings", "diskutility", "fileroller", "soundrecorder",
    "systemmonitor", "texteditor", "tubeconverter", "xournalpp", "waydroidhelper",
    "colorprofileviewer", "simplescan", "demo4", "printeditor4", "shaper",
    "widgetfactory4", "nodeeditor",
]

# The #83 requests (plan decision 16).
SERVICE_REQUESTS = ["voxtype", "is voxtype running", "stream deck", "lan mouse",
                    "pipewire", "messages", "mail watch", "notarealunit"]


def services() -> int:
    print("\n-- user services (#83)")
    capabilities.find_services("warm up")
    bad = 0
    for said in SERVICE_REQUESTS:
        started = time.perf_counter()
        found, ok = capabilities.find_services(said, limit=5)
        took = (time.perf_counter() - started) * 1e3
        top = ", ".join(f"{r['unit']} [{r['load'] if r['load'] == 'not loaded' else r['active']}]"
                        for r in found)
        print(f"{said:20} {took:5.1f} ms  {'' if ok else 'SYSTEMD DID NOT ANSWER  '}{top}")
        bad += took >= 50 or (said == "notarealunit" and bool(found))
    servers = capabilities.mcp_servers()
    if servers is None:
        print("MCP: could not read Claude Code's configuration")
    else:
        local = sum(scope.startswith("local") for scope, _, _ in servers)
        print(f"MCP: user: {len(servers) - local}, local: {local}")
    return 1 if bad else 0


def main() -> int:
    started = time.perf_counter()
    count = len(capabilities.app_index())
    print(f"{count} apps indexed in {(time.perf_counter() - started) * 1e3:.0f} ms\n")
    for heading, requests in (("#70 requests", REQUESTS),
                              ("generic id tails (#96)", GENERIC_TAILS),
                              ("name-shaped id tails (#96)", NAME_TAILS)):
        print(f"-- {heading}")
        for said in requests:
            started = time.perf_counter()
            found = capabilities.find_apps(said, limit=3)
            took = (time.perf_counter() - started) * 1e3
            match = capabilities.clear_match(found)
            verdict = f"LAUNCH {match['id']}" if match else ("choice" if found else "nothing")
            top = ", ".join(f"{row['name']} ({row['id']}) {score}" for score, row in found)
            print(f"{said:20} {took:5.1f} ms  {verdict:28} {top}")
    return services()


if __name__ == "__main__":
    sys.exit(main())
