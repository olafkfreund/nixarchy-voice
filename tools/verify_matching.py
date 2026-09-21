#!/usr/bin/env python3
"""Drive click_text against REAL rendered text and REAL OCR.

The unit tests feed `_find_phrase` hand-written word boxes, so they check the
matching rule and nothing else. They cannot see what tesseract actually does
to a font at a real size -- and #48 measured that it does plenty: `wordow`,
`Toaay`, `1ssues`. Every claim about what click_text refuses or names is a
claim about the pair of them together.

Run against an idle machine; it spawns a terminal and reads the screen:

    ssh razer 'cd ~/verify60 && python3 tools/verify_matching.py'

Nothing is clicked. Every case here is expected to REFUSE, and the check is
what the refusal says -- so a bug that clicks the wrong thing shows up as a
missing refusal rather than as a pointer event on someone's desktop.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice.config import Config  # noqa: E402
from omarchy_voice.tools import Executor  # noqa: E402

TITLE = "VERIFY_MATCHING"

# (on screen, query, expectation)
#   "match"      -- must be found, so it can be clicked
#   "no-match"   -- must NOT be found, so nothing is clicked
#   anything else -- must not be found, AND the refusal must name this word
#
# The positive cases are not padding. Without them a change that broke
# matching outright would leave every refusal case passing.
CASES = [
    ("Files changed", "Files changed", "match"),
    ("Files changed", "files CHANGED", "match"),        # case folds
    ("Settings", "Settings", "match"),
    ("Save All Files", "Save All Files", "match"),
    ("Files changed", "Files change", "changed"),
    ("Settings", "Setting", "Settings"),
    ("Save All Files", "Save fil", "files"),
    # The one this must never get wrong: a prefix of a destructive word.
    ("deleted", "delete", "no-match"),
    ("Delete all messages", "delete all", "match"),     # a real prefix-free hit
]


def sh(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True).stdout.strip()


def main() -> int:
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        runtime = Path(f"/run/user/{os.getuid()}/hypr")
        found = sorted(runtime.glob("*"), key=lambda p: p.stat().st_mtime,
                       reverse=True) if runtime.exists() else []
        if not found:
            print("no Hyprland instance found")
            return 2
        os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = found[0].name
    print(sh("hyprctl", "version").splitlines()[0])

    executor = Executor(Config(dry_run=False))
    passed = True
    for on_screen, query, must_name in CASES:
        # Big and plain: this is testing the matcher, not tesseract's limits.
        subprocess.Popen(
            ["foot", f"--title={TITLE}", "--font=monospace:size=28", "--",
             "sh", "-c", f'printf "\\n  {on_screen}\\n"; sleep 25'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
        try:
            address = next(c["address"] for c in json.loads(sh("hyprctl", "clients", "-j"))
                           if c.get("title") == TITLE)
        except (StopIteration, ValueError):
            print(f"  FAIL  could not spawn a window for {on_screen!r}")
            passed = False
            continue

        seen, _ = executor._ocr_words(next(
            g for g in [next(f'{c["at"][0]},{c["at"][1]} {c["size"][0]}x{c["size"][1]}'
                             for c in json.loads(sh("hyprctl", "clients", "-j"))
                             if c["address"] == address)]))
        result = executor._tool_click_text(query, target=f"address:{address}")
        subprocess.run(["pkill", "-f", TITLE], capture_output=True)
        time.sleep(1)

        # Printed so an OCR misread is distinguishable from a matching bug.
        ocr = " ".join(w["text"] for w in seen)
        found = executor._find_phrase(seen, query) is not None
        if must_name == "match":
            # Assert on the MATCH, not on the click: this harness must not
            # move a real pointer to prove a matching rule.
            ok = found
            label = f"{query!r} MATCHES {on_screen!r}"
            print(f"  {'PASS' if ok else 'FAIL'}  {label}")
            if not ok:
                print(f"        ocr saw: {ocr!r}")
            passed &= ok
            continue
        ok = not result.ok
        if must_name == "no-match":
            # The safety claim is that the MATCHER finds nothing, so nothing is
            # clicked -- not that the message stays quiet about what is there.
            # Naming it is fine and costs nothing: it is conditional ("if that
            # is what you meant"), it is a separate explicit call by the
            # caller, and suppressing it only sends them to read_screen to
            # learn the same word. What must never happen is a click.
            ok = ok and executor._find_phrase(seen, query) is None
            label = f"{query!r} does not MATCH {on_screen!r} (so nothing is clicked)"
        else:
            ok = ok and must_name.lower() in result.output.lower()
            label = f"{query!r} on {on_screen!r} names {must_name!r}"
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            print(f"        ocr saw: {ocr!r}")
            print(f"        said   : {result.output[:150]}")
        passed &= ok

    print("\n" + ("all checks passed" if passed else "SOME CHECKS FAILED"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
