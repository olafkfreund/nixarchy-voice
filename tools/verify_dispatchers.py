#!/usr/bin/env python3
"""Check that the dispatcher list matches the compositor it validates.

`hypr_dispatch` refuses any dispatcher not in this set, so the set being wrong
is not a cosmetic problem: too small and it refuses things that work, too large
and it passes things that fail at runtime. Before #64 the set came from a stub
pinned by this repo's nixpkgs, which has no relationship to the Hyprland a user
runs -- they agreed by luck, and nothing would have noticed when they stopped.

This asks the running compositor and compares it with whatever stub the machine
has, and prints both so a divergence is a number rather than a suspicion.

    ssh razer 'cd ~/verify60 && python3 tools/verify_dispatchers.py'
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice import capabilities  # noqa: E402


def main() -> int:
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        runtime = Path(f"/run/user/{os.getuid()}/hypr")
        found = sorted(runtime.glob("*"), key=lambda p: p.stat().st_mtime,
                       reverse=True) if runtime.exists() else []
        if not found:
            print("no Hyprland instance found; run this on the desktop machine")
            return 2
        os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = found[0].name

    version = subprocess.run(["hyprctl", "version"], capture_output=True,
                             text=True).stdout.splitlines()
    print(version[0] if version else "unknown Hyprland")

    # An explicit override would defeat the point of this check.
    os.environ.pop("OMARCHY_VOICE_HL_STUB", None)
    capabilities._live_namespaces.cache_clear()

    def flatten(namespaces) -> set[str]:
        return {m if n == "root" else f"{n}.{m}" for n, ms in namespaces for m in ms}

    live = flatten(capabilities._live_namespaces())
    stub_path = capabilities._stub_path()
    stub = flatten(capabilities._stub_namespaces())

    print(f"stub in use: {stub_path}")
    print(f"live: {len(live)}   stub: {len(stub)}")

    checks = [
        ("the compositor answered at all", bool(live)),
        ("a stub was found on this machine", stub_path is not None),
        ("dispatchers() prefers the live set", capabilities.dispatchers() == live),
    ]
    only_live, only_stub = sorted(live - stub), sorted(stub - live)
    checks.append(("live and stub agree", not only_live and not only_stub))

    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if only_live:
        print(f"        on the compositor, missing from the stub: {only_live}")
        print("        (these would be REFUSED though they work)")
    if only_stub:
        print(f"        in the stub, missing from the compositor: {only_stub}")
        print("        (these would pass validation and fail at runtime)")

    passed = all(ok for _, ok in checks)
    print("\n" + ("all checks passed" if passed else "SOME CHECKS FAILED"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
