"""What the desktop popped up, kept where a tool can read it back.

Oma could see every window and OCR the screen, and still could not answer
"what was that notification" -- the toast is gone by the time anyone asks, and
it was never a window in the first place. The only route was `read_screen`,
which sends a picture of the entire desktop to OpenAI to recover four words
that had been on it for five seconds. That is expensive, imprecise, and a much
larger privacy step than the question deserves.

nixarchy's notification daemon is its own quickshell instance, and its IPC
offers `dismiss`, `clear` and do-not-disturb but nothing that reads history
back. So this watches the bus instead: every notification on a desktop is a
`Notify` method call on org.freedesktop.Notifications, and `busctl --user
monitor --json=short` reports them as JSON, one per line, with no dependency
beyond systemd.

Recorded to a file rather than kept in memory, because the reader is not always
the writer: `omarchy-voice say "what did that say"` is a separate process from
the daemon that saw the notification.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

from .config import STATE_DIR

# Ring the file rather than letting it grow: this is a "what just happened"
# buffer, not an archive, and nobody asks about the notification from Tuesday.
MAX_RECORDS = 200
# Rewrites are O(file), so do not do one on every notification.
TRIM_AT = 400

HISTORY_FILE = STATE_DIR / "notifications.jsonl"

# Positions in the Notify signature `susssasa{sv}i`, which is stable: it is
# the freedesktop spec, not this daemon's choice.
_APP, _SUMMARY, _BODY = 0, 3, 4


def parse(line: str) -> dict | None:
    """One busctl JSON line into a record, or None if it is not a notification.

    Deliberately total: the monitor emits every message on the bus and this is
    called on all of them, so anything unexpected is "not a notification"
    rather than an exception that would take the watcher down.
    """
    try:
        event = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None
    # Valid JSON is not necessarily an object: `null` and `[]` parse fine and
    # then have no .get, and this runs on every message the bus carries -- an
    # exception here would end the watcher thread with nothing to say why.
    if not isinstance(event, dict):
        return None
    if event.get("member") != "Notify" or event.get("type") != "method_call":
        return None
    data = ((event.get("payload") or {}).get("data")) or []
    if len(data) <= _BODY:
        return None
    summary = str(data[_SUMMARY] or "").strip()
    body = str(data[_BODY] or "").strip()
    if not summary and not body:
        return None
    stamp = event.get("timestamp-realtime")
    return {
        "at": (stamp / 1_000_000) if isinstance(stamp, (int, float)) else time.time(),
        "app": str(data[_APP] or "").strip(),
        "summary": summary,
        "body": body,
    }


def record(entry: dict, path: Path | None = None) -> None:
    # Resolved at call time, not bound as a default: a default argument freezes
    # HISTORY_FILE at import, so a test (or anything else) that redirects the
    # module attribute would still write to the real one.
    path = path or HISTORY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")
    try:
        if sum(1 for _ in path.open()) > TRIM_AT:
            kept = path.read_text().splitlines()[-MAX_RECORDS:]
            path.write_text("\n".join(kept) + "\n")
    except OSError:
        pass


def recent(limit: int = 10, path: Path | None = None) -> list[dict]:
    """Newest first, because "what was that" means the last one."""
    path = path or HISTORY_FILE
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    # Sorted rather than just reversed. In practice the file is already in
    # arrival order, but the file is the only thing ordering these and a record
    # carries the time it happened -- trusting write order would make "the last
    # notification" wrong for the one case that matters, which is two arriving
    # at once. The file is capped, so this sorts a few hundred lines at most.
    out.sort(key=lambda e: e.get("at") or 0, reverse=True)
    return out[:limit]


class Watcher:
    """Tails the session bus in a thread for as long as the daemon runs."""

    def __init__(self, path: Path | None = None):
        self.path = path or HISTORY_FILE
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> str | None:
        """Begin watching. Returns a reason if it could not, else None."""
        try:
            self._proc = subprocess.Popen(
                ["busctl", "--user", "monitor", "--json=short"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except (FileNotFoundError, OSError) as exc:
            return f"could not watch notifications: {exc}"
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()
        return None

    def _read(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            if self._stop.is_set():
                return
            entry = parse(line)
            if entry:
                try:
                    record(entry, self.path)
                except OSError:
                    # A full disk must not take the voice session down over a
                    # feature nobody has asked for yet this session.
                    continue

    def stop(self) -> None:
        self._stop.set()
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
