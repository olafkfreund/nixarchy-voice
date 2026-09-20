"""#27: naming a window should find it, in one call.

Before this, `read_screen`, `click_text`, `scroll` and `wait_for` took only
"screen", "activewindow" or a literal address, and the refusal told the model
to "call hypr_query(clients) for current addresses". Measured: that query
returns 1942 characters — about 485 tokens — so naming a window cost a model
turn to ask, and a second to act, at 1.5–7.6s each. The lookup itself is 13ms.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice.config import Config
from omarchy_voice.tools import Executor, Result, _rank_windows

CHROME_ISSUES = {"address": "0xa", "class": "google-chrome",
                 "title": "Issues · olafkfreund/Factory - Google Chrome",
                 "initialTitle": "github.com", "workspace": {"name": "4"},
                 "at": [0, 0], "size": [800, 600], "focusHistoryID": 2}
CHROME_GMAIL = {"address": "0xb", "class": "google-chrome",
                "title": "Gmail", "initialTitle": "mail.google.com",
                "workspace": {"name": "2"},
                "at": [100, 100], "size": [900, 700], "focusHistoryID": 1}
TERMINAL = {"address": "0xc", "class": "foot", "title": "chrome-flags.conf",
            "initialTitle": "shell", "workspace": {"name": "1"},
            "at": [0, 0], "size": [600, 400], "focusHistoryID": 0}
CLIENTS = [CHROME_ISSUES, CHROME_GMAIL, TERMINAL]


def executor(clients=None, monitors=None):
    ex = Executor(Config(dry_run=False))
    rows = CLIENTS if clients is None else clients
    mons = monitors or [{"x": 0, "y": 0, "width": 2560, "height": 1440,
                         "focused": True, "activeWorkspace": {"name": "1"}}]
    ex._query_json = lambda kind: {"clients": rows, "monitors": mons,
                                   "workspaces": []}[kind]
    ex._visible_workspaces = lambda: {"1", "2", "4"}
    ex._session_is_locked = lambda: False
    return ex


class RankingTests(unittest.TestCase):
    def test_an_exact_class_outranks_everything(self):
        ranked = _rank_windows(CLIENTS, "google-chrome")
        self.assertEqual([c["address"] for _, c in ranked[:2]], ["0xb", "0xa"])

    def test_class_beats_title(self):
        """A terminal showing chrome-flags.conf is not Chrome.

        Both match "chrome" by substring. Class is what the application *is*;
        title is what it happens to be showing.
        """
        ranked = _rank_windows(CLIENTS, "chrome")
        self.assertEqual(ranked[0][1]["class"], "google-chrome")
        self.assertGreater(ranked[0][0], ranked[-1][0])
        self.assertEqual(ranked[-1][1]["class"], "foot")

    def test_initial_title_counts_as_much_as_title(self):
        """A page retitles itself the moment it loads, so the title a window
        was born with is often the only one that names the site."""
        ranked = _rank_windows(CLIENTS, "mail.google.com")
        self.assertEqual(ranked[0][1]["address"], "0xb")

    def test_a_web_app_does_not_outrank_the_browser(self):
        """Found on the live desktop, not in a fixture.

        Omarchy web apps take a class like
        `chrome-ejhkdoiecgkmdpomoahkdihbcldkgjci-Default`. An earlier version
        scored "class starts with the name" above "class contains it", so
        "chrome" surfaced five web apps and buried the actual google-chrome
        below them. A prefix only means more than a substring when the class
        is a word rather than a generated id.
        """
        webapp = {"address": "0xd",
                  "class": "chrome-ejhkdoiecgkmdpomoahkdihbcldkgjci-Default",
                  "title": "Element | agents", "workspace": {"name": "3"},
                  "focusHistoryID": 5}
        ranked = _rank_windows([webapp, CHROME_GMAIL], "chrome")
        self.assertEqual({score for score, _ in ranked}, {2.0},
                         "neither outranks the other on class alone")

    def test_a_missing_name_lists_what_is_open_without_the_hashes(self):
        ex = executor(clients=[{"address": "0xd", "focusHistoryID": 0,
                                "class": "chrome-ejhkdoiecgkmdpomoahkdihbcldkgjci-Default",
                                "title": "Element | agents - Element",
                                "workspace": {"name": "3"}}])
        _, error = ex._resolve_window("inkscape")
        self.assertIn("Element", error)
        self.assertNotIn("ejhkdoiecgkmdpomoah", error)

    def test_a_name_that_matches_nothing_ranks_nothing(self):
        self.assertEqual(_rank_windows(CLIENTS, "inkscape"), [])

    def test_an_empty_name_is_not_a_wildcard(self):
        self.assertEqual(_rank_windows(CLIENTS, ""), [])
        self.assertEqual(_rank_windows(CLIENTS, "   "), [])


class ResolveTests(unittest.TestCase):
    def test_a_name_resolves_in_one_call(self):
        """The whole point: one query, no round trip through the model."""
        ex = executor()
        queries = []
        ex._query_json = lambda kind: (queries.append(kind),
                                       {"clients": CLIENTS}[kind])[1]
        window, error = ex._resolve_window("gmail")
        self.assertIsNone(error)
        self.assertEqual(window["address"], "0xb")
        self.assertEqual(queries, ["clients"])

    def test_a_real_tie_is_refused_and_names_the_candidates(self):
        ex = executor()
        window, error = ex._resolve_window("google-chrome")
        self.assertIsNone(window)
        self.assertIn("Gmail", error)
        self.assertIn("Issues", error)
        self.assertIn("workspace 4", error)
        self.assertIn("workspace 2", error)

    def test_an_ambiguous_name_captures_nothing(self):
        """Refusing after taking a screenshot has still acted on the wrong
        window. The gate matches a one-line description, so a silently-picked
        wrong target produces a correct-looking description of the wrong
        action."""
        ex = executor()
        with mock.patch.object(Executor, "_shell") as shell:
            result = ex.call("read_screen", {"target": "google-chrome"})
        self.assertFalse(result.ok)
        shell.assert_not_called()

    def test_a_clear_best_match_resolves_without_asking(self):
        """The pair to the test above: without this, refusing everything
        would pass it.

        Scores differ here — "gmail" is in one window's title and nowhere
        else — so there is nothing to ask about.
        """
        ex = executor()
        window, error = ex._resolve_window("gmail")
        self.assertIsNone(error)
        self.assertEqual(window["address"], "0xb")

    def test_focus_orders_the_candidates_but_does_not_choose(self):
        """Being focused is not a tie-break.

        The spec asked for both "ties break toward the focused window" and
        "equal scores are a question", which collide exactly here. The
        question wins: picking the focused window *because* it is focused is
        the silent choice the whole ambiguity rule exists to prevent, and
        "activewindow" already means "the one I am looking at" for anyone who
        wants that. Focus only decides which candidate is named first.
        """
        focused = {**CHROME_GMAIL, "focusHistoryID": 0}
        ex = executor(clients=[CHROME_ISSUES, focused])
        window, error = ex._resolve_window("google-chrome")
        self.assertIsNone(window, "equal scores stay a question")
        self.assertLess(error.index("Gmail"), error.index("Issues"),
                        "the focused one is named first")

    def test_explicit_selectors_are_exact(self):
        ex = executor()
        window, error = ex._resolve_window("title:chrome")
        self.assertIsNone(error)
        self.assertEqual(window["class"], "foot", "title: must not match class")
        window, error = ex._resolve_window("class:foot")
        self.assertIsNone(error)
        self.assertEqual(window["address"], "0xc")
        _, error = ex._resolve_window("class:gmail")
        self.assertIsNotNone(error, "class: must not match a title")

    def test_the_old_forms_are_untouched(self):
        ex = executor()
        for target, address in (("activewindow", "0xc"), ("active", "0xc"),
                                ("address:0xa", "0xa"), ("0xa", "0xa")):
            with self.subTest(target=target):
                window, error = ex._resolve_window(target)
                self.assertIsNone(error)
                self.assertEqual(window["address"], address)

    def test_no_refusal_sends_the_model_back_to_hypr_query(self):
        """The round trip is what this change removes. Leaving it in the
        errors as advice would keep the model paying for it."""
        ex = executor()
        errors = [ex._resolve_window(t)[1] for t in
                  ("address:0xgone", "inkscape", "google-chrome",
                   "class:nothing", "title:nothing")]
        ex_empty = executor(clients=[])
        errors.append(ex_empty._resolve_window("anything")[1])
        for error in errors:
            self.assertIsNotNone(error)
            self.assertNotIn("hypr_query", error)

    def test_a_dead_end_still_says_what_is_open(self):
        ex = executor()
        _, error = ex._resolve_window("inkscape")
        self.assertIn("foot", error)
        self.assertIn("google-chrome", error)


class ReachesEveryToolTests(unittest.TestCase):
    """#26 routed read_screen, click_text and wait_for through one resolver,
    so by-name targeting arrives at all four from a single change."""

    def test_read_screen_by_name(self):
        ex = executor()
        seen = []
        ex._ocr_region = lambda geometry: (seen.append(geometry),
                                           Result(True, "text"))[1]
        result = ex.call("read_screen", {"target": "gmail"})
        self.assertTrue(result.ok)
        self.assertEqual(seen, ["100,100 900x700"])

    def test_click_text_by_name(self):
        ex = executor()
        seen = []
        ex._ocr_words = lambda geometry: (seen.append(geometry), ([], ""))[1]
        ex.call("click_text", {"text": "Send", "target": "gmail"})
        self.assertEqual(seen, ["100,100 900x700"])

    def test_scroll_by_name(self):
        ex = executor()
        with mock.patch.object(Executor, "_shell",
                               staticmethod(lambda *a, **k: Result(True, "ok"))), \
             mock.patch("shutil.which", return_value="/usr/bin/ydotool"):
            result = ex.call("scroll", {"direction": "down", "target": "gmail"})
        self.assertTrue(result.ok, result.output)

    def test_wait_for_text_by_name(self):
        ex = executor()
        seen = []
        ex._ocr_words = lambda geometry: (seen.append(geometry), ([], ""))[1]
        ex.call("wait_for", {"what": "text", "value": "Sent",
                             "timeout": 0.5, "target": "gmail"})
        self.assertEqual(set(seen), {"100,100 900x700"})


class WidenedMatcherTests(unittest.TestCase):
    """_window_matches now shares the scorer, so what a name matches changed.

    Named in the spec as a behaviour change rather than left to be found:
    wait_for(window_gone) may now report "still open" where it used to say
    "gone". That is the safer direction, and it is asserted deliberately.
    """

    def test_a_name_that_only_matches_a_title_still_counts(self):
        ex = executor()
        result = ex.call("wait_for", {"what": "window_gone",
                                      "value": "chrome-flags", "timeout": 0.5})
        self.assertTrue(result.ok)
        self.assertIn("still open", result.output)

    def test_window_gone_is_true_when_nothing_matches(self):
        ex = executor()
        result = ex.call("wait_for", {"what": "window_gone",
                                      "value": "inkscape", "timeout": 0.5})
        self.assertTrue(result.ok)
        self.assertIn("closed", result.output)


if __name__ == "__main__":
    unittest.main()
