"""App content from the accessibility tree, so OCR is the fallback (#91).

A tree gives the text an app is showing exactly, with a rectangle per node,
in milliseconds; OCR guesses it from pixels in seconds. Everything below is a
pure function over any Atspi-like object, so tests pass in fakes.

This is the only file under src/ that imports `gi`, and it does so lazily in
`desktop()`: importing this module never touches a bus.

Nothing here writes. Accessibility is on when the user turned it on, and
`IsEnabled` is read, never set. The reader never sets a D-Bus property, and
never relaunches an app to get a tree out of it.
"""

from __future__ import annotations

import re
import threading
import time

# libatspi is not thread-safe, and tools run through asyncio.to_thread
# (mcp_server.py). Every walk holds this.
LOCK = threading.Lock()

# Atspi.CoordType.WINDOW and Atspi.StateType.SHOWING, as numbers so a walk
# over a fake needs no typelib. T16 checks them against the real one.
WINDOW = 1
SHOWING = 25

TEXT_CAP = 2000


def desktop():
    """The Atspi desktop, or None when the tree cannot or must not be read.

    None when gi or the Atspi typelib is missing, when
    org.a11y.Status.IsEnabled is not true, or when reading it fails in any
    way. IsEnabled is one Properties.Get with a 1 s timeout, before anything
    touches Atspi. NO_AUTO_START: asking must not start the a11y bus.
    """
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            "org.a11y.Bus", "/org/a11y/bus", "org.freedesktop.DBus.Properties", "Get",
            GLib.Variant("(ss)", ("org.a11y.Status", "IsEnabled")),
            GLib.VariantType("(v)"), Gio.DBusCallFlags.NO_AUTO_START, 1000, None)
        if reply.unpack()[0] is not True:
            return None
        from gi.repository import Atspi
        Atspi.set_timeout(500, 2000)
        return Atspi.get_desktop(0)
    except Exception:  # noqa: BLE001 -- any failure means "no tree", so OCR
        return None


def _children(node):
    return [c for c in (node.get_child_at_index(i) for i in range(node.get_child_count()))
            if c is not None]


def window_nodes(desktop, client):
    """The frame for this Hyprland client, or None when it is not certain.

    Matched by pid. One frame for the pid is that frame. Several frames means
    the one whose name is the client's title or starts with it; none or more
    than one such is unknown, so OCR.
    """
    frames = [frame for app in _children(desktop)
              if app.get_process_id() == client.get("pid")
              for frame in _children(app)]
    if len(frames) == 1:
        return frames[0]
    title = str(client.get("title") or "")
    named = [f for f in frames if title and (f.get_name() or "").startswith(title)]
    return named[0] if len(named) == 1 else None


def _text(node) -> str:
    iface = node.get_text_iface()
    if iface is not None:
        text = iface.get_text(0, min(iface.get_character_count(), TEXT_CAP))
    else:
        text = node.get_name()
    return (text or "").strip()[:TEXT_CAP]


def collect(frame, origin, rect, deadline, cap=5000):
    """[(text, x, y, w, h, actionable)] on screen inside `rect`, or None.

    Depth first, descending only into SHOWING children. Extents are
    window-relative and `origin` (the client's `at`) is added. A node is kept
    if its screen rectangle meets `rect`. A password field is never read; that
    is the only role test, so `button` and `push button` read the same.
    Passing `cap` nodes or `deadline` returns None: a partial tree is unknown.
    """
    ox, oy = origin
    gx, gy, gw, gh = rect
    found, visited, stack = [], 0, [frame]
    while stack:
        node = stack.pop()
        visited += 1
        if visited > cap or time.monotonic() > deadline:
            return None
        if "password" in re.sub(r"[\s_-]", "", (node.get_role_name() or "").lower()):
            continue
        component = node.get_component_iface()
        box = component.get_extents(WINDOW) if component is not None else None
        x, y, w, h = (box.x + ox, box.y + oy, box.width, box.height) if box else (0, 0, 0, 0)
        if box and x < gx + gw and gx < x + w and y < gy + gh and gy < y + h:
            if text := _text(node):
                action = node.get_action_iface()
                kept = (text, x, y, w, h, action is not None and action.get_n_actions() > 0)
                if not found or found[-1][0] != text:
                    found.append(kept)
                elif kept[5] and not found[-1][5]:
                    # A duplicate is dropped, but a button beats the words before it.
                    found[-1] = kept
        stack.extend(reversed([c for c in _children(node)
                               if c.get_state_set().contains(SHOWING)]))
    return found
