#!/usr/bin/env python3
"""Prove, against a real compositor, that typed text lands where addressed.

Every claim #60 rests on is about a live Hyprland, and none of it can be
checked by a unit test: that `send_key_state` routes to an UNFOCUSED window,
that a bare keysym ignores case, that the shift pairing is layout-dependent,
and that an off-layout character fails rather than typing something else.

Run it against an idle machine -- it spawns terminals and types into them:

    ssh razer 'nix develop /path/to/repo -c python3 tools/verify_input.py'
    nix develop -c python3 tools/verify_input.py          # the local session

It records the compositor version, because the answers are version-dependent
and a future divergence should be visible rather than mysterious. Nothing is
left behind; the probe windows are killed on the way out.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice.keys import keys_for_text  # noqa: E402

PROBE = "VERIFY_INPUT_PROBE"
OTHER = "VERIFY_INPUT_OTHER"


def sh(*args: str, **kw) -> str:
    return subprocess.run(args, capture_output=True, text=True, **kw).stdout.strip()


def hypr(*args: str) -> str:
    return sh("hyprctl", *args)


def clients() -> list[dict]:
    try:
        return json.loads(hypr("clients", "-j"))
    except ValueError:
        return []


def address(title: str) -> str | None:
    return next((c["address"] for c in clients() if c.get("title") == title), None)


def spawn(title: str, sink: Path) -> None:
    sink.unlink(missing_ok=True)
    subprocess.Popen(
        ["foot", f"--title={title}", "--", "sh", "-c",
         f'read x; printf "%s" "$x" > {sink}'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def type_into(window: str, text: str, layout: str) -> str | None:
    """Send `text` to `window` the way tools.py does. Returns an error or None."""
    events, error = keys_for_text(text, layout)
    if error:
        return error
    parts = []
    for keysym, mods in [*events, ("Return", "")]:
        for state in ("down", "up"):
            parts.append(
                f'dispatch hl.dsp.send_key_state({{ key = "{keysym}", '
                f'mods = "{mods}", state = "{state}", window = "{window}" }})')
    out = sh("hyprctl", "--batch", ";".join(parts))
    bad = [ln for ln in out.splitlines() if ln.strip() and ln.strip() != "ok"]
    return "; ".join(bad) if bad else None


def check(name: str, got, want) -> bool:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        want {want!r}\n        got  {got!r}")
    return ok


def main() -> int:
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        runtime = Path(f"/run/user/{os.getuid()}/hypr")
        instances = sorted(runtime.glob("*"), key=lambda p: p.stat().st_mtime,
                           reverse=True) if runtime.exists() else []
        if not instances:
            print("no Hyprland instance found; run this on the desktop machine")
            return 2
        os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = instances[0].name

    print(hypr("version").splitlines()[0])
    try:
        layout = (json.loads(hypr("getoption", "input:kb_layout", "-j"))
                  .get("str") or "us").split(",")[0]
    except ValueError:
        layout = "us"
    print(f"layout: {layout}\n")

    sink, decoy = Path("/tmp/verify_input.txt"), Path("/tmp/verify_other.txt")
    spawn(PROBE, sink)
    spawn(OTHER, decoy)
    time.sleep(3)
    target, distraction = address(PROBE), address(OTHER)
    if not target or not distraction:
        print("could not spawn probe windows")
        return 2

    passed = True
    try:
        # The whole claim: the DECOY holds focus, the probe does not.
        hypr("dispatch", f'hl.dsp.focus({{ window = "address:{distraction}" }})')
        time.sleep(1)
        focused = json.loads(hypr("activewindow", "-j")).get("title")
        passed &= check("the decoy window holds focus", focused, OTHER)

        # Characters a US table would get wrong on a gb layout, and vice versa.
        sample = 'Hi! a@b "q" x-y/z'
        error = type_into(f"address:{target}", sample, layout)
        passed &= check("no compositor errors", error, None)
        time.sleep(2)
        passed &= check("text reached the UNFOCUSED window",
                        sink.read_text() if sink.exists() else None, sample)
        passed &= check("the focused window received nothing",
                        decoy.read_text() if decoy.exists() else "", "")

        # Fails closed rather than typing something else.
        # AltGr. The keymap puts these at level 2 and this code calls that
        # MOD5; if that name is wrong, asking for "o-slash" types "o" instead
        # -- silently, which is this issue's whole failure mode. So it is
        # typed for real rather than asserted about.
        altgr = next((c for c in "\u00f8\u0142\u00e6" if keys_for_text(c, layout)[1] is None), None)
        if altgr:
            sink.unlink(missing_ok=True)
            spawn(PROBE, sink)
            time.sleep(3)
            again = address(PROBE)
            hypr("dispatch", f'hl.dsp.focus({{ window = "address:{distraction}" }})')
            time.sleep(1)
            type_into(f"address:{again}", altgr, layout)
            time.sleep(2)
            passed &= check("an AltGr character types as itself, not its base key",
                            sink.read_text() if sink.exists() else None, altgr)
        else:
            print("  SKIP  no AltGr character on this layout")

        # Fails closed rather than typing something else. A CJK character is on
        # no Latin layout. "o-slash" is NOT a valid probe on gb, which has it at
        # AltGr -- that is how this check was wrong the first time it was run.
        passed &= check("an off-layout character refuses",
                        keys_for_text("\u4e2d", layout)[1] is not None, True)
    finally:
        for title in (PROBE, OTHER):
            subprocess.run(["pkill", "-f", title], capture_output=True)

    print("\n" + ("all checks passed" if passed else "SOME CHECKS FAILED"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
