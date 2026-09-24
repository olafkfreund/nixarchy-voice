#!/usr/bin/env python3
"""Run the #82 requests against THIS machine's installed command-line tools.

The unit tests use a PATH, man pages and tldr pages they wrote themselves, so
they check the rule and nothing else. Whether "resize an image" finds mogrify
here depends on what this machine's man and tldr files say -- which is the
question this answers.

Read-only: it lists directories and reads files. Nothing found is run.

    python3 tools/verify_commands.py

Exits 1 if top-8 finds fewer than 10 of the 12 scored requests, an installed
exact name is not first, `notarealtool` matches, or a warm query takes 50 ms
or more.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice import capabilities  # noqa: E402

# The 12 scored requests from spec/2026-09-24-82-installed-commands.md.
SCORED = [
    ("convert a video", {"ffmpeg"}), ("json query", {"jq"}),
    ("resize an image", {"mogrify", "magick"}), ("check disk usage", {"duf"}),
    ("download a youtube video", {"yt-dlp"}), ("ssh to a host", {"ssh"}),
    ("take a screenshot", {"grim"}), ("copy to clipboard", {"wl-copy"}),
    ("pick a color", {"hyprpicker"}), ("find files by name", {"fd"}),
    ("search text in files", {"rg"}), ("system monitor", {"btop"}),
]
UNSCORED = ["pdf to text", "pick a colour"]     # not installed; the stem miss
EXACT = ["ffmpeg", "mogrify", "nix-locate"]
WARM_LIMIT_MS = 50


def timed(query: str) -> tuple[list[str], float]:
    started = time.perf_counter()
    found = [name for _, name, _ in capabilities.find_commands(query)]
    return found, (time.perf_counter() - started) * 1e3


def main() -> int:
    failures = []
    started = time.perf_counter()
    index = capabilities.path_commands()
    cold = (time.perf_counter() - started) * 1e3
    started = time.perf_counter()
    capabilities._stamp()
    stamp = (time.perf_counter() - started) * 1e3
    described = sum(1 for row in index.values() if row["desc"])
    print(f"{len(index)} commands, {described} described; "
          f"cold build {cold:.0f} ms, stamp {stamp:.1f} ms\n")

    top1 = top8 = 0
    slowest = 0.0
    print("-- scored requests")
    for said, expected in SCORED:
        found, took = timed(said)
        slowest = max(slowest, took)
        hit = next((i for i, n in enumerate(found) if n in expected), None)
        top1 += hit == 0
        top8 += hit is not None
        verdict = f"HIT #{hit + 1}" if hit is not None else "MISS"
        print(f"  {said!r:28} {verdict:7} {took:5.1f} ms  {', '.join(found)}")
    print("-- not scored")
    for said in UNSCORED:
        found, took = timed(said)
        print(f"  {said!r:28} {'':7} {took:5.1f} ms  {', '.join(found)}")
    print("-- exact names")
    for name in EXACT:
        found, took = timed(name)
        slowest = max(slowest, took)
        first = found[0] if found else "-"
        if name in index and first != name:
            failures.append(f"{name} is installed but not first")
        print(f"  {name!r:28} {'FIRST' if first == name else first:7} {took:5.1f} ms")
    close = capabilities.close_commands("yt-dl")
    print(f"  'yt-dl' close spellings: {', '.join(close) or '-'}")
    fake, _ = timed("notarealtool")
    print(f"  'notarealtool' matches: {', '.join(fake) or 'nothing'}")
    if fake:
        failures.append("notarealtool matched")

    print(f"\ntop-1 {top1}/12 (not gated), top-8 {top8}/12, slowest warm {slowest:.1f} ms")
    if top8 < 10:
        failures.append(f"top-8 {top8}/12 is below 10")
    if slowest >= WARM_LIMIT_MS:
        failures.append(f"a warm query took {slowest:.1f} ms")
    for failure in failures:
        print(f"FAIL: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
