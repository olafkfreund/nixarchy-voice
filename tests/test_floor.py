import asyncio
import fcntl
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import _isolated  # noqa: F401 -- before any omarchy_voice import

from omarchy_voice import floor


CHILD = """
import sys
from omarchy_voice.floor import Floor
f = Floor()
f.hold('child')
print('ready', flush=True)
sys.stdin.read(1)
f.release('child', 0)
"""


class FloorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.path = Path(tmp.name) / "nixarchy-voice-floor.lock"
        self.floor = floor.Floor()
        old = floor.FLOOR
        floor.FLOOR = self.floor
        self.addCleanup(setattr, floor, "FLOOR", old)
        self.addCleanup(self.floor.drop)

    def child(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        proc = subprocess.Popen([sys.executable, "-u", "-c", CHILD],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                text=True, env=env)
        self.addCleanup(self.stop_child, proc)
        self.assertEqual(proc.stdout.readline().strip(), "ready")
        return proc

    @staticmethod
    def stop_child(proc):
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=2)

    def child_can_lock(self):
        code = """import fcntl, os
fd = os.open(os.environ['XDG_RUNTIME_DIR'] + '/nixarchy-voice-floor.lock', os.O_RDWR)
try:
 fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
 print('free')
except BlockingIOError:
 print('held')
"""
        return subprocess.check_output([sys.executable, "-c", code], text=True).strip()

    def test_hold_waits_for_child(self):
        proc = self.child()
        result = []
        import threading
        worker = threading.Thread(target=lambda: result.append(self.floor.hold("a")))
        worker.start()
        self.addCleanup(worker.join, 2)
        time.sleep(0.12)
        self.assertEqual(result, [])
        proc.stdin.write("x")
        proc.stdin.flush()
        proc.wait(timeout=2)
        worker.join(timeout=2)
        self.assertEqual(result, [True])

    def test_spanning_child_hold_is_rejected(self):
        start = time.monotonic()
        proc = self.child()
        self.assertTrue(floor.floor_overlaps(start))
        proc.stdin.write("x")
        proc.stdin.flush()
        proc.wait(timeout=2)
        self.assertTrue(floor.floor_overlaps(start))

    def test_capture_after_release_is_accepted(self):
        proc = self.child()
        proc.stdin.write("x")
        proc.stdin.flush()
        proc.wait(timeout=2)
        self.assertFalse(floor.floor_overlaps(time.monotonic()))

    def test_overlap_survives_us_taking_the_floor_afterwards(self):
        # A capture open across the other's turn, then our own reply (#1568, review).
        start = time.monotonic()
        proc = self.child()
        proc.stdin.write("x")
        proc.stdin.flush()
        proc.wait(timeout=2)
        self.assertTrue(self.floor.hold("a"))
        self.assertTrue(floor.floor_overlaps(start))
        self.floor.release("a", 0)
        self.assertTrue(floor.floor_overlaps(start))
        self.assertFalse(floor.floor_overlaps(time.monotonic()))

    def test_crash_record_is_completed_and_later_capture_accepted(self):
        start = time.monotonic()
        proc = self.child()
        proc.kill()
        proc.wait(timeout=2)
        self.assertEqual(self.child_can_lock(), "free")
        self.assertTrue(floor.floor_overlaps(start))
        self.assertFalse(floor.floor_overlaps(time.monotonic()))
        fields = self.path.read_text().split()
        self.assertEqual(len(fields), 3)
        self.assertGreaterEqual(float(fields[2]), start)

    def test_tail_is_cancelled_without_dropping_lock(self):
        proc = self.child()
        proc.stdin.write("x")
        proc.stdin.flush()
        proc.wait(timeout=2)
        self.assertTrue(self.floor.hold("a"))
        self.floor.release("a", 0.2)
        self.assertEqual(self.child_can_lock(), "held")
        # Our own tail is still our turn: we must not reject what we hear.
        self.assertFalse(floor.floor_overlaps(time.monotonic()))
        self.assertTrue(self.floor.hold("a"))
        time.sleep(0.25)
        self.assertEqual(self.child_can_lock(), "held")
        self.floor.release("a", 0)
        self.assertEqual(self.child_can_lock(), "free")

    def test_repeated_key_is_one_hold(self):
        for _ in range(3):
            self.assertTrue(self.floor.hold("a"))
        self.floor.release("a", 0)
        self.assertEqual(self.child_can_lock(), "free")

    def test_wait_can_be_cancelled(self):
        self.child()
        polls = 0

        def cancelled():
            nonlocal polls
            polls += 1
            return polls == 3

        self.assertFalse(self.floor.hold("a", cancelled=cancelled))
        self.assertEqual(polls, 3)

    def test_async_wait_can_be_cancelled(self):
        self.child()

        async def run():
            task = asyncio.create_task(self.floor.ahold("a"))
            await asyncio.sleep(0.1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(run())
        self.assertEqual(self.floor.keys, set())

    def test_new_short_line_truncates_old_line(self):
        self.assertTrue(self.floor.hold("a"))
        self.floor.release("a", 0)
        self.assertEqual(len(self.path.read_text().split()), 3)
        self.assertTrue(self.floor.hold("a"))
        self.assertEqual(len(self.path.read_text().split()), 2)

    def test_descriptor_is_not_inherited(self):
        self.assertTrue(self.floor.hold("a"))
        self.assertFalse(os.get_inheritable(self.floor.fd))
        code = "import os; print([os.readlink('/proc/self/fd/' + n) for n in os.listdir('/proc/self/fd') if os.path.exists('/proc/self/fd/' + n)])"
        links = subprocess.check_output([sys.executable, "-c", code],
                                        text=True, close_fds=False)
        self.assertNotIn(str(self.path), links)


if __name__ == "__main__":
    unittest.main()
