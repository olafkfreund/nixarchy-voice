"""Clicks and the wheel, through Wayland rather than a root daemon.

`ydotool` wrote to `/dev/uinput`, which needs a daemon running as root. Without
it `scroll` pressed Page Up/Down and `click_text` returned a paragraph of NixOS
advice. Both were handling a problem that need not exist: the compositor
already speaks `zwp_virtual_keyboard_v1` and `zwlr_virtual_pointer_v1`, which
need no privilege at all.

The reason to take ai-mirror's helper rather than write one is not the 478
lines of C. It is this, from ai-mirror's own notes:

    the registered MCP server, which closes its helper so the helper releases
    all held keys and buttons (a virtual keyboard can only be released by its
    owner)

**Releasing is a property of the pipe's lifetime, not a call anyone has to
remember.** That is what `ydotool` could not offer at any price: a turn
abandoned between a press and its release left the key down, and nothing in
this program could let go. Here the helper's life is the turn's life, so an
abandoned chord is released by the abandonment itself.

Which makes the lifetime the design, and everything else plumbing:

  opened   lazily, on the first click or scroll in a turn
  closed   when the turn ends -- normally, cancelled, or by an exception --
           and on listening stopping, daemon shutdown, or a revoke

## The panic key

ai-mirror revokes agent control with SUPER + SHIFT + ESCAPE, which writes
`owner: off` and bumps `generation` in its state file. This watches that
number and closes the helper when it moves, so one keypress stops every
synthetic input on the machine rather than one program's.

What it deliberately does **not** do is require a grant. `owner` is `off` most
of the time -- ai-mirror is switched on for a task and off after -- so
demanding `owner == "agent"` would mean voice could not click until a different
program's agent mode was on. That would quietly reverse #19, which made voice's
ai-mirror integration opt-in and off by default. Voice takes no grant; the key
merely stops it.

And no ai-mirror at all is not a revoke. A missing state file means there is
nothing to honour, not that input is refused.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
from pathlib import Path

# evdev button codes, the same ones ai-mirror's input.py maps. Not ydotool's
# 0xC0/0xC1/0xC2, which encoded press-and-release in one value.
BUTTONS = {"left": 272, "right": 273, "middle": 274}

HELPER = "ai-mirror-input"

# How long to wait for the helper's readiness line before calling it broken.
# It either opens the two Wayland globals immediately or it cannot.
READY_TIMEOUT = 5.0


def _state_path() -> Path | None:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    return Path(runtime) / "ai-mirror" / "state.json" if runtime else None


def generation() -> int | None:
    """ai-mirror's control generation, or None when there is no ai-mirror.

    Read rather than watched. The file is written by atomic replace, which
    swaps the inode out from under an inotify watch -- the trap
    plugin/voice.orb/VoiceOrb.qml already records for the level file, and the
    reason it polls too.
    """
    path = _state_path()
    if path is None:
        return None
    try:
        return int(json.loads(path.read_text()).get("generation", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        # No ai-mirror, or a half-written file. Either way there is no revoke
        # to honour, and refusing input would make an absent program a gate.
        return None


class Unavailable(RuntimeError):
    """The helper could not be started, and the caller should say why."""


class Helper:
    """One helper process, and the pipe to it."""

    def __init__(self, binary: str = HELPER, extent=None):
        self.binary = binary
        # (width, height) of the whole monitor layout, or a callable returning
        # it. Injected so a test never asks a compositor.
        self._extent_source = extent
        self._proc: subprocess.Popen | None = None
        self._generation: int | None = None

    def _extent(self) -> tuple[int, int]:
        source = self._extent_source
        if callable(source):
            return source()
        if source:
            return source
        raise Unavailable(
            "the pointer's coordinate space is unknown, so no input was sent")

    @property
    def open(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        if self.open:
            return
        # The helper refuses to start without these and exits 1: it needs the
        # layout size to turn absolute coordinates into the pointer's
        # normalised range. Checked in ai_mirror_input.c's main(), not
        # documented anywhere else.
        env = dict(os.environ)
        width, height = self._extent()
        env["AI_MIRROR_EXTENT_W"] = str(width)
        env["AI_MIRROR_EXTENT_H"] = str(height)
        try:
            proc = subprocess.Popen(
                [self.binary], env=env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1, close_fds=True)
        except OSError as exc:
            raise Unavailable(
                f"{self.binary} could not start ({exc}). Clicking needs a "
                "compositor with zwlr_virtual_pointer_v1.") from None
        # It prints a line when both Wayland globals are bound. Waiting for it
        # is the difference between "the helper is running" and "the helper can
        # actually move the pointer" -- a compositor missing the protocol exits
        # here rather than silently swallowing every click.
        ready, _, _ = select.select([proc.stdout], [], [], READY_TIMEOUT)
        line = proc.stdout.readline().strip() if ready and proc.stdout else ""
        if line != "READY":
            proc.kill()
            raise Unavailable(
                f"{self.binary} started but reported {line or 'nothing'!r}. "
                "This compositor may not implement zwlr_virtual_pointer_v1.")
        self._proc = proc
        # Recorded at open, compared at every send. A revoke between the two is
        # exactly what the key is for.
        self._generation = generation()

    def revoked(self) -> bool:
        """Whether control was withdrawn since this helper opened."""
        if self._generation is None:
            return False          # no ai-mirror when we opened; nothing to revoke
        now = generation()
        return now is not None and now != self._generation

    def send(self, lines: list[str]) -> None:
        """Write commands, after checking the helper is still allowed to.

        The check is here rather than on a timer because the only input worth
        stopping is the input about to happen. Missing a revoke leaves voice
        holding a channel the user believes they closed, and that is the one
        direction of this error that matters.
        """
        if self.revoked():
            self.close()
            raise Unavailable(
                "desktop control was withdrawn (SUPER + SHIFT + ESCAPE), so "
                "that input was not sent")
        if not self.open:
            self.start()
        assert self._proc is not None and self._proc.stdin is not None
        try:
            self._proc.stdin.write("".join(f"{line}\n" for line in lines))
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self.close()
            raise Unavailable(f"the input helper went away ({exc})") from None

    def close(self) -> None:
        """Let go of everything, by ceasing to exist.

        No release command is sent. The helper has one, and using it would put
        this back on remembering to call something -- which is the property
        that failed with ydotool. Closing the pipe destroys the virtual
        keyboard, and a virtual keyboard can only be released by its owner.
        """
        proc, self._proc, self._generation = self._proc, None, None
        if proc is None or proc.poll() is not None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=2)
        except (OSError, subprocess.SubprocessError):
            proc.kill()


def click(button: str, double: bool = False) -> list[str]:
    """The lines for a click. `B <code> 1` then `B <code> 0`, twice if double."""
    code = BUTTONS.get(button)
    if code is None:
        raise ValueError(f"button must be one of {', '.join(BUTTONS)}")
    return [f"B {code} 1", f"B {code} 0"] * (2 if double else 1)


def scroll(dx: int, dy: int) -> list[str]:
    return [f"S {int(dx)} {int(dy)}"]
