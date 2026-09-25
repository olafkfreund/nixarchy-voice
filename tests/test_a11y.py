"""#91: app content from the accessibility tree first, OCR as the fallback.

Every tree here is a fake. No test reaches a real bus or walks a real
accessibility tree, except T16, which only imports the typelib, and T5, which
asks a bus address that does not exist.
"""

import itertools
import subprocess
import time
import unittest
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice import trace as trace_mod
from omarchy_voice.config import Config
from omarchy_voice.tools import OCR_LIMIT, Executor, Result, install_hint

GEOM = "1000,50 800x600"
TAIL = "\n… [more text on screen, not read]"
WIN = {"address": "0x1", "class": "org.gnome.Nautilus", "title": "Files", "pid": 4242,
       "at": [1000, 50], "size": [800, 600], "workspace": {"name": "1"},
       "mapped": True, "xwayland": False}


# -- the fake tree ------------------------------------------------------------
class Extents:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.width, self.height = x, y, w, h


class Node:
    """Just enough of Atspi.Accessible. Extents are window-relative whatever
    coordinate type is asked for, which is what a real app answers to WINDOW."""

    def __init__(self, role, name="", box=(0, 0, 0, 0), showing=True,
                 actionable=False, children=(), text=None, pid=0):
        self.role, self.name, self.box = role, name, box
        self.showing, self.actionable, self.text, self.pid = showing, actionable, text, pid
        self.children = list(children)

    def get_name(self): return self.name
    def get_role_name(self): return self.role
    def get_process_id(self): return self.pid
    def get_state_set(self): return self
    def contains(self, _state): return self.showing
    def get_component_iface(self): return self
    def get_extents(self, _coord): return Extents(*self.box)
    def get_text_iface(self): return self if self.text is not None else None
    def get_character_count(self): return len(self.text)
    def get_text(self, start, end): return self.text[start:end]
    def get_action_iface(self): return self if self.actionable else None
    def get_n_actions(self): return 1
    def get_child_count(self): return len(self.children)
    def get_child_at_index(self, i): return self.children[i]


def build(spec):
    """(role, name, (x, y, w, h), showing, actionable, children) -> Node."""
    role, name, box, showing, actionable, children = spec
    return Node(role, name, box, showing, actionable, [build(c) for c in children])


def leaf(role, name, box, showing=True, actionable=False):
    return (role, name, box, showing, actionable, ())


def frame(children, name="Files"):
    return build(("frame", name, (0, 0, 800, 600), True, False, children))


def desktop(*apps):
    """apps: (pid, [frame, ...])"""
    return Node("desktop", children=[Node("application", pid=pid, children=frames)
                                     for pid, frames in apps])


GTK = desktop((4242, [frame([
    leaf("label", "Downloads", (20, 40, 120, 20)),
    leaf("push button", "OK", (10, 20, 80, 30), actionable=True),
])]))


# -- the executor and OCR stubs -------------------------------------------------
def executor(clients=(WIN,)):
    ex = Executor(Config(dry_run=False))
    clients = list(clients)
    ex._query_json = lambda kind: clients
    ex._query_rows = lambda kind: (clients, None)
    ex._visible_workspaces = lambda: {"1"}
    ex._screen_unavailable = lambda: None
    ex._screen_is_recorded = lambda: None
    ex._target_geometry = lambda target="screen": (GEOM, None)
    ex._input_refused = lambda *a, **k: None
    ex.clicked = []
    ex._dispatch = lambda name, args: (ex.clicked.append((args["x"], args["y"])),
                                       Result(True, "ok"))[1]
    ex._press_button = lambda button, double: Result(True, "ok")
    return ex


def no_ocr():
    return mock.patch("omarchy_voice.tools.subprocess.run",
                      side_effect=AssertionError("OCR ran, the tree should have answered"))


class OCR:
    """grim and tesseract, canned. `calls` says whether the fallback ran."""
    TSV = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\t"
           "width\theight\tconf\ttext\n5\t1\t1\t1\t1\t1\t10\t10\t50\t20\t96\tSCANNED\n")

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **_kw):
        self.calls.append(cmd[0])
        if cmd[0] == "grim":
            return subprocess.CompletedProcess(cmd, 0, b"P6\n1 1\n255\n\0\0\0", b"")
        out = self.TSV if "tsv" in cmd else "SCANNED BY OCR"
        return subprocess.CompletedProcess(cmd, 0, out.encode(), b"")

    def __enter__(self):
        self._patches = [mock.patch("omarchy_voice.tools.subprocess.run", self),
                         mock.patch("omarchy_voice.tools.shutil.which",
                                    return_value="/run/current-system/sw/bin/x")]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *_exc):
        for p in self._patches:
            p.stop()
        return False


def tree(fake):
    """Patch the desktop the reader asks for. A Mock, so calls are counted."""
    return mock.patch("omarchy_voice.a11y.desktop", mock.Mock(return_value=fake))


class TreeAnswers(unittest.TestCase):
    def test_a_gtk_tree_answers_read_screen_without_ocr(self):  # T1
        ex = executor()
        with tree(GTK), no_ocr():
            result = ex._tool_read_screen()
        self.assertTrue(result.ok, result.output)
        self.assertIn("Downloads", result.output)
        self.assertIn("OK", result.output)

    def test_window_relative_extents_are_shifted_by_the_window_origin(self):  # T7
        ex = executor()
        with tree(GTK), no_ocr():
            result = ex._tool_click_text("OK")
        self.assertTrue(result.ok, result.output)
        self.assertEqual(ex.clicked, [(1050, 85)])

    def test_button_and_push_button_are_both_clickable(self):  # T8
        for role in ("button", "push button"):
            with self.subTest(role=role):
                ex = executor()
                fake = desktop((4242, [frame([
                    leaf(role, "Continue", (100, 100, 100, 40), actionable=True)])]))
                with tree(fake), no_ocr():
                    result = ex._tool_click_text("Continue")
                self.assertTrue(result.ok, result.output)
                self.assertEqual(ex.clicked, [(1150, 170)])

    def test_password_fields_are_never_read_or_matched(self):  # T9
        for role in ("password text", "Password_Text", "passwordtext"):
            with self.subTest(role=role):
                secret = Node(role, "", (100, 200, 200, 30), text="hunter2")
                fake = desktop((4242, [Node("frame", "Files", (0, 0, 800, 600), children=[
                    Node("label", "Username", (100, 160, 200, 20)), secret])]))
                ex = executor()
                with tree(fake), no_ocr():
                    read = ex._tool_read_screen()
                    click = ex._tool_click_text("hunter2")
                self.assertTrue(read.ok, read.output)
                self.assertIn("Username", read.output)
                self.assertNotIn("hunter2", read.output)
                self.assertFalse(click.ok)
                self.assertEqual(ex.clicked, [])

    def test_only_showing_nodes_inside_the_rect_are_read(self):  # T10
        fake = desktop((4242, [frame([
            leaf("label", "Visible", (20, 20, 100, 20)),
            leaf("label", "Hidden", (20, 60, 100, 20), showing=False),
            ("panel", "", (0, 100, 400, 200), False, False, [
                leaf("label", "Buried", (20, 120, 100, 20))]),
            # Inside 1000..1800 as a raw number; outside once the origin is added.
            leaf("label", "Offscreen", (1200, 100, 80, 20)),
        ])]))
        ex = executor()
        with tree(fake), no_ocr():
            read = ex._tool_read_screen()
            for word in ("Hidden", "Buried", "Offscreen"):
                with self.subTest(word=word):
                    self.assertFalse(ex._tool_click_text(word).ok)
        self.assertIn("Visible", read.output)
        for word in ("Hidden", "Buried", "Offscreen"):
            self.assertNotIn(word, read.output)
        self.assertEqual(ex.clicked, [])

    def test_a_button_beats_body_text(self):  # T18
        fake = desktop((4242, [frame([
            leaf("paragraph", "Sign in", (20, 20, 60, 20)),
            leaf("button", "Sign in", (300, 400, 100, 40), actionable=True),
        ])]))
        ex = executor()
        with tree(fake), no_ocr():
            result = ex._tool_click_text("Sign in")
        self.assertTrue(result.ok, result.output)
        self.assertEqual(ex.clicked, [(1350, 470)])

    def test_wait_for_and_target_moved_read_the_tree(self):  # T20
        ex = executor()
        with tree(GTK), no_ocr():
            waited = ex._tool_wait_for("text", "Downloads", timeout=0.5)
            moved = ex._target_moved("OK", 1050, 85)
        self.assertTrue(waited.ok, waited.output)
        self.assertIn("after", waited.output)
        self.assertIsNone(moved)

    def test_the_tree_text_is_capped_like_ocr(self):  # T21
        texts = [chr(ord("a") + i) * 1750 for i in range(4)]
        fake = desktop((4242, [Node("frame", "Files", (0, 0, 800, 600), children=[
            Node("paragraph", "", (0, 10 * i, 800, 10), text=t) for i, t in enumerate(texts)])]))
        ex = executor()
        with tree(fake), no_ocr():
            result = ex._ocr_region(GEOM)
        self.assertTrue(result.ok)
        self.assertTrue(result.output.endswith(TAIL))
        self.assertEqual(len(result.output), OCR_LIMIT + len(TAIL))

    def test_the_trace_names_a11y_and_holds_no_text(self):  # T19
        ex = executor()
        ex.trace = task = trace_mod.Trace()
        with tree(GTK), no_ocr():
            self.assertTrue(ex._ocr_region(GEOM).ok)
        task.finish()
        self.assertIn("a11y", [s.phase for s in task.spans])
        for text in ("Downloads", "OK", "Files"):
            self.assertNotIn(text, task.line())
            for span in task.spans:
                self.assertNotIn(text, span.name)


class FallsBackToOcr(unittest.TestCase):
    def assertOcr(self, ex, ocr):
        result = ex._ocr_region(GEOM)
        self.assertEqual(result.output, "SCANNED BY OCR")
        self.assertIn("tesseract", ocr.calls)

    def test_frames_only_falls_back_to_ocr(self):  # T2
        fake = desktop((4242, [frame([])]))
        with tree(fake), OCR() as ocr:
            self.assertOcr(executor(), ocr)

    def test_no_accessibility_falls_back_to_ocr(self):  # T3
        with tree(None), OCR() as ocr:
            self.assertOcr(executor(), ocr)

    def test_tree_off_frames_only_and_role_mismatch_are_told_apart(self):  # T11
        from omarchy_voice import a11y
        chromium = desktop((4242, [frame([
            leaf("button", "Reload", (10, 10, 40, 40), actionable=True),
            leaf("link", "Home", (60, 10, 40, 20), actionable=True),
            leaf("paragraph", "Some body text", (10, 60, 400, 20)),
        ])]))
        cases = [("tree off", None, False, True),
                 ("frames only", desktop((4242, [frame([])])), True, True),
                 ("chromium roles", chromium, True, False)]
        for label, fake, walked, ocr_ran in cases:
            with self.subTest(case=label):
                with tree(fake), OCR() as ocr, \
                        mock.patch.object(a11y, "collect", wraps=a11y.collect) as walk:
                    result = executor()._ocr_region(GEOM)
                self.assertTrue(result.ok)
                self.assertEqual(walk.called, walked)
                self.assertEqual("tesseract" in ocr.calls, ocr_ran)
                if not ocr_ran:
                    self.assertIn("Reload", result.output)

    def test_xwayland_overlap_or_ambiguous_frames_fall_back(self):  # T12
        other = dict(WIN, address="0x2", pid=5151, title="Other", at=[1200, 100],
                     size=[400, 300])
        cases = {
            "xwayland": ([dict(WIN, xwayland=True)], GTK),
            "overlap": ([WIN, other], desktop(
                (4242, [frame([leaf("label", "Downloads", (20, 40, 120, 20))])]),
                (5151, [frame([leaf("label", "Else", (20, 40, 120, 20))], name="Other")]))),
            "ambiguous": ([WIN], desktop((4242, [
                frame([leaf("label", "One", (20, 40, 120, 20))], name="Alpha"),
                frame([leaf("label", "Two", (20, 40, 120, 20))], name="Beta")]))),
        }
        for label, (clients, fake) in cases.items():
            with self.subTest(case=label), tree(fake), OCR() as ocr:
                self.assertOcr(executor(clients), ocr)

    def test_second_window_list_failure_falls_back_to_ocr(self):  # T13
        ex = executor()
        count = itertools.count()
        ex._query_rows = lambda kind: ([WIN], None) if next(count) == 0 \
            else ([], "hyprctl timed out")
        with tree(GTK), OCR() as ocr:
            self.assertOcr(ex, ocr)

    def test_a_partial_walk_falls_back(self):  # T14
        with self.subTest(case="cap"):
            many = Node("frame", "Files", (0, 0, 800, 600), children=[
                Node("label", f"n{i}", (10, 10, 50, 20)) for i in range(5001)])
            with tree(desktop((4242, [many]))), OCR() as ocr:
                self.assertOcr(executor(), ocr)
        with self.subTest(case="deadline"):
            clock = itertools.count(0, 0.6)
            with tree(GTK), OCR() as ocr, \
                    mock.patch.object(time, "monotonic", lambda: next(clock)):
                self.assertOcr(executor(), ocr)

    def test_one_treeless_window_sends_the_region_to_ocr(self):  # T15
        term = dict(WIN, address="0x3", pid=999, title="shell",
                    **{"class": "foot"},
                    at=[0, 50], size=[900, 600])
        ex = executor([WIN, term])
        ex._target_geometry = lambda target="screen": ("0,0 2000x700", None)
        with tree(GTK), OCR() as ocr:
            self.assertEqual(ex._ocr_region("0,0 2000x700").output, "SCANNED BY OCR")
        self.assertIn("tesseract", ocr.calls)

    def test_a_tree_answer_needs_no_tesseract(self):  # T17
        with mock.patch("omarchy_voice.tools.shutil.which", return_value=None):
            with tree(GTK), no_ocr():
                self.assertIn("Downloads", executor()._tool_read_screen().output)
            with tree(None), no_ocr():
                self.assertEqual(executor()._tool_read_screen().output,
                                 install_hint("grim", "grim"))


class GuardsFirst(unittest.TestCase):
    def test_the_guards_refuse_before_the_tree(self):  # T6
        vault = dict(WIN, address="0x9", pid=77, **{"class": "1Password"},
                     title="Personal Vault")

        def sensitive(ex):
            ex._query_rows = lambda kind: ([WIN, vault], None)

        def unreadable(ex):
            ex._query_rows = lambda kind: ([], "hyprctl timed out")

        def recording(ex):
            ex._screen_is_recorded = lambda: "OBS is recording the screen"

        def asleep(ex):
            ex._screen_unavailable = lambda: "the session is locked"

        for label, arrange in [("sensitive #46", sensitive), ("window list #51", unreadable),
                               ("recording #52", recording), ("dpms or lock #67", asleep)]:
            with self.subTest(case=label):
                ex = executor()
                arrange(ex)
                expected = ex._screen_unavailable() or ex._capture_refused(GEOM)
                self.assertTrue(expected)
                with tree(GTK) as desk, no_ocr(), \
                        mock.patch("omarchy_voice.tools.shutil.which", return_value="/x"):
                    region = ex._ocr_region(GEOM)
                    words, error = ex._ocr_words(GEOM)
                self.assertEqual((region.ok, region.output), (False, expected))
                self.assertEqual((words, error), ([], expected))
                desk.assert_not_called()


class TheBus(unittest.TestCase):
    def test_is_enabled_false_is_none_and_only_get_is_called(self):  # T4
        from gi.repository import Atspi, Gio

        from omarchy_voice import a11y

        class Reply:
            def unpack(self):
                return (False,)

        class Connection:
            def __init__(self):
                self.methods, self.args = [], []

            def call_sync(self, name, path, iface, method, params, *rest):
                self.methods.append(method)
                self.args.append((name, path, iface, params.unpack()))
                return Reply()

        conn = Connection()
        with mock.patch.object(Gio, "bus_get_sync", return_value=conn), \
                mock.patch.object(Atspi, "get_desktop") as get_desktop, \
                mock.patch.object(Atspi, "set_timeout") as set_timeout:
            self.assertIsNone(a11y.desktop())
        self.assertEqual(conn.methods, ["Get"])
        self.assertEqual(conn.args, [("org.a11y.Bus", "/org/a11y/bus",
                                      "org.freedesktop.DBus.Properties",
                                      ("org.a11y.Status", "IsEnabled"))])
        get_desktop.assert_not_called()
        set_timeout.assert_not_called()

    def test_an_unreachable_bus_is_none(self):  # T5
        from omarchy_voice import a11y
        started = time.monotonic()
        self.assertIsNone(a11y.desktop())
        self.assertLess(time.monotonic() - started, 1.5)

    def test_the_atspi_typelib_imports(self):  # T16
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi

        from omarchy_voice import a11y
        self.assertTrue(callable(Atspi.set_timeout))
        self.assertEqual(int(Atspi.CoordType.WINDOW), a11y.WINDOW)
        self.assertEqual(int(Atspi.StateType.SHOWING), a11y.SHOWING)


if __name__ == "__main__":
    unittest.main()
