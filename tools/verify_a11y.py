#!/usr/bin/env python3
"""Check the #91 accessibility-tree reader against THIS desktop, read-only.

The unit tests walk fake trees. Whether a real window answers from its tree,
and whether the rectangles it reports are where the text actually is, is the
question this answers:

    python3 tools/verify_a11y.py

It reads org.a11y.Status IsEnabled and ScreenReaderEnabled and stops with one
line if accessibility is off. For each visible window it prints the class,
pid, xwayland, whether --force-renderer-accessibility is on the process's
command line, the node count, whether the tree answers or it falls back to
OCR and why, and how long the walk took. For up to five named, actionable
nodes per answering window it prints the screen rectangle and whether OCR of
exactly that rectangle contains the name -- only where the capture guard
passes, and a window the guard refuses is not walked at all.

Nothing is written, pressed, typed or launched: no property is changed, no
input is sent, no app is started or restarted. Titles are never printed.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice import a11y  # noqa: E402
from omarchy_voice.config import Config  # noqa: E402
from omarchy_voice.tools import Executor  # noqa: E402

FLAG = b"--force-renderer-accessibility"


def status(name: str):
    """One org.a11y.Status property, read with Properties.Get."""
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    reply = bus.call_sync(
        "org.a11y.Bus", "/org/a11y/bus", "org.freedesktop.DBus.Properties", "Get",
        GLib.Variant("(ss)", ("org.a11y.Status", name)), GLib.VariantType("(v)"),
        Gio.DBusCallFlags.NO_AUTO_START, 1000, None)
    return reply.unpack()[0]


def flag_on(pid) -> str:
    try:
        return "yes" if FLAG in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") else "no"
    except (OSError, TypeError):
        return "?"


def ocr(geometry: str) -> str:
    """grim + tesseract of exactly this rectangle, bypassing the tree."""
    shot = subprocess.run(["grim", "-t", "ppm", "-g", geometry, "-"],
                          capture_output=True, timeout=15)
    read = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "6"],
                          input=shot.stdout, capture_output=True, timeout=45)
    return " ".join(read.stdout.decode(errors="replace").split()).lower()


def main() -> int:
    try:
        enabled, reader = status("IsEnabled"), status("ScreenReaderEnabled")
    except Exception as exc:  # noqa: BLE001
        print(f"the accessibility bus did not answer ({exc}); every window reads by OCR")
        return 0
    print(f"IsEnabled={enabled} ScreenReaderEnabled={reader}")
    if enabled is not True:
        print("accessibility is off, so every window reads by OCR. Not turned on here.")
        return 0
    desktop = a11y.desktop()
    ex = Executor(Config(dry_run=True))
    visible = ex._visible_workspaces()
    clients, failed = ex._query_rows("clients")
    if failed:
        print(f"window list: {failed}")
        return 1
    unconfirmed = 0
    for client in clients:
        if client.get("hidden") or client.get("mapped") is False:
            continue
        if str((client.get("workspace") or {}).get("name")) not in visible:
            continue
        (x, y), (w, h) = client["at"], client["size"]
        geometry = f"{x},{y} {w}x{h}"
        head = (f"{client.get('class') or '?':40.40} pid={client.get('pid')} "
                f"xwayland={bool(client.get('xwayland'))} flag={flag_on(client.get('pid'))}")
        if refused := ex._capture_refused(geometry):
            print(f"{head}  not walked: the capture guard refuses ({refused.split(',')[0]})")
            continue
        started = time.monotonic()
        frame = nodes = None
        if desktop is not None:
            with a11y.LOCK:
                frame = a11y.window_nodes(desktop, client)
                if frame is not None:
                    nodes = a11y.collect(frame, (x, y), (x, y, w, h), time.monotonic() + 1.0)
        took = (time.monotonic() - started) * 1000
        chrome = {frame.get_name() if frame is not None else None, client.get("title")}
        if desktop is None:
            verdict = "OCR: accessibility off or unreadable"
        elif client.get("xwayland"):
            verdict = "OCR: xwayland"
        elif frame is None:
            verdict = "OCR: no frame for this pid"
        elif nodes is None:
            verdict = "OCR: partial walk (cap or deadline)"
        elif all(n[0] in chrome for n in nodes):
            verdict = "OCR: frames only" + (", flag absent" if flag_on(client.get("pid")) == "no"
                                            else "")
        else:
            verdict = "TREE answers"
        print(f"{head}  nodes={len(nodes or [])} {verdict}  walk={took:.0f}ms")
        if not verdict.startswith("TREE"):
            continue
        for text, nx, ny, nw, nh, actionable in [n for n in nodes if n[5]][:5]:
            rect = f"{nx},{ny} {max(nw, 1)}x{max(nh, 1)}"
            if ex._capture_refused(rect):
                print(f"    {text[:40]!r:44} {rect:24} not OCR-checked: guard refuses")
                continue
            seen = " ".join(text.split()).lower() in ocr(rect)
            unconfirmed += not seen
            print(f"    {text[:40]!r:44} {rect:24} OCR {'confirms' if seen else 'does NOT confirm'}")
    if unconfirmed:
        print(f"{unconfirmed} rectangle(s) not confirmed by OCR: a finding against decision 11")
    return 0


if __name__ == "__main__":
    sys.exit(main())
