"""The shared speaker floor for the two voice processes."""

import asyncio
import ctypes
import fcntl
import os
import threading
import time


class Floor:
    def __init__(self, path=None, clock=time.monotonic):
        self.path = path or os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}",
                                         "nixarchy-voice-floor.lock")
        self.clock = clock
        self.lock = threading.Lock()
        self.keys = set()
        self.fd = None
        self.timer = None

    def _write(self, line):
        os.ftruncate(self.fd, 0)
        os.pwrite(self.fd, (line + "\n").encode(), 0)

    def _try_hold(self, key, fd):
        with self.lock:
            if key in self.keys:
                return True, False
            adopted = False
            if self.fd is None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return False, False
                self.fd = fd
                adopted = True
                self._write(f"{os.getpid()} {self.clock()}")
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None
            self.keys.add(key)
            return True, adopted

    def hold(self, key, cancelled=None):
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            while True:
                if cancelled is not None and cancelled():
                    return False
                done, adopted = self._try_hold(key, fd)
                if done:
                    if adopted:
                        fd = None
                    return True
                time.sleep(0.05)
        finally:
            if fd is not None:
                os.close(fd)

    async def ahold(self, key):
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            while True:
                done, adopted = self._try_hold(key, fd)
                if done:
                    if adopted:
                        fd = None
                    return True
                await asyncio.sleep(0.05)
        finally:
            if fd is not None:
                os.close(fd)

    def _unlock(self):
        if self.fd is not None:
            line = os.pread(self.fd, 256, 0).decode().strip()
            self._write(f"{line} {self.clock()}")
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None

    def release(self, key, tail=0):
        with self.lock:
            if key not in self.keys:
                return
            self.keys.remove(key)
            if self.keys:
                return
            if tail <= 0:
                self._unlock()
                return
            timer = threading.Timer(tail, lambda: self._timed_unlock(timer))
            timer.daemon = True
            self.timer = timer
            timer.start()

    def _timed_unlock(self, timer):
        with self.lock:
            if self.timer is timer:
                self.timer = None
                self._unlock()

    def drop(self):
        with self.lock:
            self.keys.clear()
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None
            self._unlock()


FLOOR = Floor()


def floor_overlaps(capture_start):
    with FLOOR.lock:
        # Held by us, a pending release's tail included: our own turn never counts.
        if FLOOR.fd is not None:
            return False
    fd = os.open(FLOOR.path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        line = os.pread(fd, 256, 0).decode()
        if not line.endswith("\n"):
            return False
        fields = line.split()
        if len(fields) not in (2, 3) or fields[0] == str(os.getpid()):
            return False
        try:
            released = float(fields[2]) if len(fields) == 3 else None
        except ValueError:
            return False
        if released is not None:
            return released >= capture_start
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        if os.pread(fd, 256, 0).decode() == line:
            os.ftruncate(fd, 0)
            os.pwrite(fd, f"{line.strip()} {FLOOR.clock()}\n".encode(), 0)
        return True
    finally:
        os.close(fd)


def pdeathsig_preexec():
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, 9) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_PDEATHSIG)")
