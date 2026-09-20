"""#28: wake when the compositor says something changed.

Measured on the live desktop, five window launches: Hyprland emits
`openwindow` at ~75ms and the 150ms poll noticed at ~164ms. The listener is
worth ~91ms there, which is small against a 1.5-7.6s model turn — the case
that justifies it is `wait_for`, which runs up to 25s and burned a core
polling throughout.

Nothing here needs a compositor. The listener is fed from a socketpair, and
the executor's waker is injected, because a unit test that reaches the real
session cannot be made to hold still: wiring this in unconditionally made a
test asserting the 25s cap wait 25 real seconds.
"""
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice import hypr_events
from omarchy_voice.config import Config
from omarchy_voice.tools import Executor


class FakeStream:
    """A Listener reading one end of a socketpair instead of Hyprland."""

    def __init__(self):
        self.ours, self.theirs = socket.socketpair()
        self.listener = hypr_events.Listener()
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def _drain(self):
        try:
            self.listener._drain_socket(self.theirs)
        except OSError:
            pass

    def send(self, data: bytes):
        self.ours.sendall(data)

    def close(self):
        self.listener.stop()
        for sock in (self.ours, self.theirs):
            try:
                sock.close()
            except OSError:
                pass

    def woke(self, timeout=2.0) -> bool:
        got = self.listener.wake.wait(timeout)
        self.listener.wake.clear()
        return got


class ReadingTests(unittest.TestCase):
    def setUp(self):
        self.stream = FakeStream()
        self.addCleanup(self.stream.close)

    def test_only_the_event_name_is_read(self):
        """The payload is a minefield and none of it reaches us.

        That window really was called `ev,probe,with,commas`, captured from the
        live compositor: fields are comma-separated and titles are not escaped,
        so splitting on "," is wrong and cannot be made right. Addresses in
        events also carry no 0x prefix where hyprctl's do, and Hyprland
        truncates event data at 1024 bytes. None of it matters if none of it is
        read -- the woken waiter asks hyprctl, which is authoritative.
        """
        self.stream.send(b"openwindow>>5d811f0beb30,11,foot,ev,probe,with,commas\n")
        self.assertTrue(self.stream.woke())

    def test_a_partial_read_is_buffered(self):
        """recv splits wherever it likes; a name cut across two reads would
        otherwise be lost."""
        self.stream.send(b"openwin")
        self.assertFalse(self.stream.woke(timeout=0.3))
        self.stream.send(b"dow>>a,b\n")
        self.assertTrue(self.stream.woke())

    def test_several_events_in_one_read(self):
        self.stream.send(b"closewindow>>a\nopenwindow>>b,1,foot,x\n")
        self.assertTrue(self.stream.woke())

    def test_an_event_we_do_not_care_about_does_not_wake(self):
        """The wake list is an allowlist, not "anything that arrives"."""
        self.stream.send(b"monitoradded>>DP-9\n")
        self.assertFalse(self.stream.woke(timeout=0.3))

    def test_a_truncated_payload_still_wakes(self):
        """Hyprland cuts event data at 1024 bytes. The name survives, which is
        the only part used."""
        self.stream.send(b"windowtitlev2>>" + b"x" * 1024 + b"\n")
        self.assertTrue(self.stream.woke())


class TickTests(unittest.TestCase):
    def test_the_gap_backs_off(self):
        """Fast while the thing waited for is plausibly imminent, then not.

        A 30ms poll detects a new window at ~94ms against ~164ms, but a wait
        that never succeeds would spend twelve seconds issuing four hundred
        hyprctl queries. After the first second the failure case costs about
        what it cost before.
        """
        slept = []
        with mock.patch("time.sleep", slept.append):
            hypr_events.wait_tick(0.0)
            hypr_events.wait_tick(0.5)
            hypr_events.wait_tick(1.5)
            hypr_events.wait_tick(9.0)
        self.assertEqual(slept, [0.03, 0.03, 0.15, 0.15])

    def test_a_wake_short_circuits_the_gap(self):
        listener = hypr_events.Listener()
        listener.wake.set()
        started = time.monotonic()
        hypr_events.wait_tick(5.0, waker=listener)
        self.assertLess(time.monotonic() - started, 0.1,
                        "should have returned on the event, not the 150ms gap")
        self.assertFalse(listener.wake.is_set(), "cleared for the next tick")

    def test_without_a_waker_it_is_an_ordinary_sleep(self):
        """Which is what makes every wait in the suite patchable."""
        with mock.patch("time.sleep") as sleep:
            hypr_events.wait_tick(0.0, waker=None)
        sleep.assert_called_once_with(0.03)


class ExecutorWiringTests(unittest.TestCase):
    def test_an_executor_has_no_waker_by_default(self):
        """A unit test, or the nix sandbox, must never open the socket."""
        self.assertIsNone(Executor(Config()).waker)

    def test_a_window_already_there_is_not_slept_on(self):
        """The old loop slept 150ms before its first look, so a window that
        was already mapped paid for nothing."""
        window = {"address": "0xa", "class": "foot", "title": "shell",
                  "focusHistoryID": 0}
        ex = Executor(Config())
        ex._query_json = lambda kind: [window]
        ex._query_rows = lambda kind: (ex._query_json(kind), None)
        with mock.patch("time.sleep") as sleep:
            address = ex._await_new_window(set(), timeout=5.0, hint="foot")
        self.assertEqual(address, "0xa")
        sleep.assert_not_called()

    def test_a_late_window_is_still_waited_for(self):
        """The pair to the above: not sleeping first must not mean not
        waiting at all."""
        seen = {"n": 0}
        window = {"address": "0xa", "class": "foot", "title": "shell",
                  "focusHistoryID": 0}

        def clients(kind):
            seen["n"] += 1
            return [window] if seen["n"] >= 3 else []

        ex = Executor(Config())
        ex._query_json = clients
        ex._query_rows = lambda kind: (ex._query_json(kind), None)
        with mock.patch("time.sleep"):
            address = ex._await_new_window(set(), timeout=5.0, hint="foot")
        self.assertEqual(address, "0xa")
        self.assertGreaterEqual(seen["n"], 3)

    def test_waiting_for_text_does_not_use_the_event_stream(self):
        """No compositor event fires when words appear on a page.

        Wiring the text branch to the listener would look like an improvement
        and be none: the Event would never be set, so it would run at the
        backoff interval regardless. Asserted rather than left to be noticed.
        """
        ex = Executor(Config())
        ex.waker = mock.Mock(available=True, wake=mock.Mock())
        ex._ocr_words = lambda geometry: ([], "")
        ex._target_geometry = lambda target="screen": ("0,0 10x10", None)
        with mock.patch("time.sleep") as sleep:
            ex.call("wait_for", {"what": "text", "value": "nope", "timeout": 1})
        self.assertTrue(sleep.called, "the text branch sleeps, it does not wait on events")
        ex.waker.wake.wait.assert_not_called()

    def test_the_listener_never_needs_the_executor_lock(self):
        """The deadlock this design could have had: a tool holding the lock
        must be able to wait, and the reader must not want it."""
        ex = Executor(Config())
        stream = FakeStream()
        self.addCleanup(stream.close)
        ex.waker = stream.listener
        stream.listener._connected = True

        done = threading.Event()

        def wait_inside_the_lock():
            with ex._lock:
                ex._wait_tick(0.0)
            done.set()

        thread = threading.Thread(target=wait_inside_the_lock, daemon=True)
        thread.start()
        stream.send(b"openwindow>>a,1,foot,x\n")
        self.assertTrue(done.wait(2.0), "waiter never returned")


class AvailabilityTests(unittest.TestCase):
    def test_no_runtime_dir_is_not_an_error(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(hypr_events.socket_path())

    def test_a_missing_instance_directory_is_not_an_error(self, ):
        with mock.patch.dict("os.environ",
                             {"XDG_RUNTIME_DIR": "/nonexistent-runtime"},
                             clear=True):
            self.assertIsNone(hypr_events.socket_path())

    def test_an_unconnected_listener_reports_unavailable(self):
        self.assertFalse(hypr_events.Listener().available)


if __name__ == "__main__":
    unittest.main()
