"""Wake when Hyprland says something changed. Nothing more than that.

`_await_new_window` slept 150ms between `hyprctl -j clients` queries. Measured
on this machine, launching a real window five times: the compositor emits
`openwindow` at ~75ms and the poll noticed at ~164ms. So the poll ran ~91ms
behind, every time, very consistently.

That is a small prize against a model turn of 1.5-7.6s, and a 30ms poll
recovers most of it for one changed constant. The reason this exists anyway is
the other end: `wait_for` runs up to 25 seconds, and polling that at any useful
granularity burns a core for the duration, while a blocked socket read costs
nothing at all.

**This never parses an event payload.** Only the name before `>>` is read.
That is not an optimisation, it is the whole safety argument, because the
payload is a minefield and none of it can reach us:

  openwindow>>5d811f0beb30,11,foot,ev,probe,with,commas

- Fields are comma-separated and titles are *not* escaped. That window really
  was called `ev,probe,with,commas`. Splitting on "," is wrong and cannot be
  made right.
- Event addresses carry no `0x` prefix; `hyprctl -j clients` addresses do. Key
  anything on one and it silently never matches the other.
- Hyprland truncates event *data* at 1024 bytes
  (`EventManager.cpp:128`, `data.substr(0, 1024)`).

None of that matters if none of it is read. A woken waiter asks `hyprctl`,
which is authoritative, exactly as the poll it replaces did. This listener
replaces the *sleep*, not the query. It also means a `hyprctl reload` needs no
handling: there is no cached state to invalidate.

The failure that does survive is not about parsing:

  EventManager.cpp:169   const size_t MAX_QUEUED_EVENTS = 64;
  EventManager.cpp:175   "Socket2 fd {} overflowed event queue, removing"

A client that reads too slowly is disconnected and not told. There are no
sequence numbers in the stream, so a gap is undetectable from inside: silence
is not evidence that nothing happened. Hence the reader thread does nothing but
read -- no matching, no `hyprctl`, no callbacks. Sixty-four queued events is
generous for a thread that only drains, and nothing for one that stops to do
work per event during a workspace switch. The work happens on the waiting
thread, after it wakes.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

# Anything that might change what a waiter is waiting for. Over-waking costs
# one `hyprctl` query and is harmless -- the waiter re-checks and sleeps again
# -- so this errs wide. Under-waking is a missed wake-up, and the caller's own
# poll interval covers that.
WAKE_EVENTS = frozenset({
    "openwindow", "closewindow", "movewindowv2", "windowtitle", "windowtitlev2",
    "activewindow", "activewindowv2", "workspace", "workspacev2", "focusedmon",
    "fullscreen", "changefloatingmode", "openlayer", "closelayer",
})

# Long enough not to spin against a compositor that is gone, short enough that
# a Hyprland restart is picked up within a turn or two.
RECONNECT_DELAY = 2.0


def socket_path() -> Path | None:
    """The running compositor's event socket, or None.

    HYPRLAND_INSTANCE_SIGNATURE is set in a session, but `omarchy-voice mcp`
    can be started from a context that has no Wayland environment -- an ssh
    login, a coding agent's own process -- so fall back to the newest instance
    directory rather than giving up.
    """
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        return None
    base = Path(runtime) / "hypr"
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if signature:
        candidate = base / signature / ".socket2.sock"
        return candidate if candidate.exists() else None
    try:
        instances = sorted(base.glob("*/.socket2.sock"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    return instances[0] if instances else None


class Listener:
    """A socket, a thread that only reads it, and an Event."""

    def __init__(self) -> None:
        self.wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._connected = False

    @property
    def available(self) -> bool:
        """Whether events are currently arriving. Waiters poll regardless."""
        return self._connected

    def start(self) -> Listener:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="hypr-events")
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self.wake.set()

    # -- the reading thread, which does nothing else ------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            path = socket_path()
            if path is None:
                self._connected = False
                if self._stop.wait(RECONNECT_DELAY):
                    return
                continue
            try:
                self._drain(path)
            except OSError:
                pass
            # EOF or error: the compositor went away, or it dropped us for
            # overflowing its queue. Either way what we last saw may be stale,
            # and the waiter's own hyprctl query is what makes that safe.
            self._connected = False
            self.wake.set()
            if self._stop.wait(RECONNECT_DELAY):
                return

    def _drain(self, path: Path) -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5.0)
            sock.connect(str(path))
            self._connected = True
            self._drain_socket(sock)

    def _drain_socket(self, sock: socket.socket) -> None:
        """Read lines and set the Event. Split out so a test can hand this a
        socketpair -- the suite must never reach the running compositor."""
        sock.settimeout(5.0)
        buffer = b""
        while not self._stop.is_set():
            try:
                chunk = sock.recv(8192)
            except socket.timeout:
                continue
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            # recv splits wherever it likes. Keep the trailing partial line;
            # an event name cut across two reads would otherwise be lost.
            *lines, buffer = buffer.split(b"\n")
            for line in lines:
                name = line.split(b">>", 1)[0].decode(errors="replace")
                if name in WAKE_EVENTS:
                    self.wake.set()


_listener: Listener | None = None
_lock = threading.Lock()


def listener() -> Listener:
    """The one listener for this process.

    Shared, because nine `omarchy-voice mcp` servers were running on this host
    at once while this was written -- one socket and one thread per wait would
    be nine of each against a compositor that disconnects clients it considers
    slow.
    """
    global _listener
    with _lock:
        if _listener is None:
            _listener = Listener().start()
        return _listener


def wait_tick(elapsed: float, fast: float = 0.03, slow: float = 0.15,
              fast_for: float = 1.0, waker: Listener | None = None) -> None:
    """Sleep until the next check is worth making.

    Fast while the thing waited for is plausibly imminent, then backing off:
    a 30ms poll detects a new window at ~94ms against ~164ms, but a wait that
    never succeeds would otherwise spend twelve seconds issuing four hundred
    `hyprctl` queries. After the first second the failure case costs about
    what it cost before.

    With a listener attached the Event usually fires first and neither number
    matters; without one, the fast start is what keeps this close to the
    listener's own latency rather than degrading to nothing.
    """
    gap = fast if elapsed < fast_for else slow
    if waker is None:
        time.sleep(gap)
        return
    waker.wake.wait(gap)
    # Cleared here rather than by the setter, so a wake that arrives while the
    # waiter is busy is not lost -- it is still set when the waiter comes back.
    waker.wake.clear()
