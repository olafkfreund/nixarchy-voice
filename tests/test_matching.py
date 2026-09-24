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
    ex._query_rows = lambda kind: (ex._query_json(kind), None)
    ex._visible_workspaces = lambda: {"1", "2", "4"}
    ex._session_is_locked = lambda: False
    return ex


# #67 guards every input tool on what the target window IS, so these tests now
# need a desktop to exist. An ordinary terminal: nothing the sensitive-window
# patterns match, which is what they always implicitly assumed.
ORDINARY_WINDOW = {"address": "0xabc", "class": "foot", "title": "shell",
                   "at": [0, 0], "size": [800, 600], "workspace": {"name": "1"},
                   "mapped": True, "focusHistoryID": 0}


def with_desktop(executor):
    """Give an executor one ordinary window, so the #67 input guard can see."""
    executor._query_rows = lambda kind: ([ORDINARY_WINDOW], None)
    executor._query_json = lambda kind: [ORDINARY_WINDOW]
    return executor


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
        ex._query_rows = lambda kind: (ex._query_json(kind), None)
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
        # An executor has no input helper unless an entry point attaches one
        # (#30), which is what stops a unit test spawning a process.
        ex.input_helper = type("Fake", (), {"send": lambda *a: None,
                                            "close": lambda *a: None})()
        with mock.patch.object(Executor, "_shell",
                               staticmethod(lambda *a, **k: Result(True, "ok"))), \
             mock.patch("shutil.which", return_value="/usr/bin/x"):
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


# --- #48: a misread word should not dead-end the caller ---------------------

# The two sets the spec measured. Every pair in MISREADS is a real tesseract
# error; every pair in DISTINCT is a pair of genuinely different UI words that
# a similarity cutoff would have confused. The point of the fold is that it
# separates them, which no ratio threshold did: "close"/"c1ose" and
# "close"/"clone" both score 0.800.
MISREADS = [("window", "wordow"), ("today", "toaay"), ("issues", "1ssues"),
            ("trade", "frade"), ("changed", "chanqed"), ("settings", "settinqs"),
            ("files", "fi1es"), ("packaging", "packaqing"), ("close", "c1ose"),
            ("search", "searcn"), ("account", "accaunt")]

DISTINCT = [("delete", "deleted"), ("save", "have"), ("close", "clone"),
            ("open", "oper"), ("files", "tiles"), ("cancel", "cancer"),
            ("send", "sent"), ("copy", "copay"), ("reply", "apply"),
            ("discard", "discord"), ("merge", "merged"), ("delete", "select"),
            ("close", "closed"), ("edit", "exit")]


def word(text, x=100, y=200, w=60, h=14, conf=90):
    return {"text": text, "x": x, "y": y, "w": w, "h": h, "conf": conf}


class OcrFoldTests(unittest.TestCase):
    def test_no_distinct_word_pair_ever_folds_together(self):
        """The property the whole design rests on.

        If one of these ever folds equal, click_text can be talked into
        clicking "deleted" when the caller said "delete" -- silently, and
        reporting success. This test failing is not a test to update.
        """
        from omarchy_voice.tools import _ocr_fold

        for a, b in DISTINCT:
            with self.subTest(pair=(a, b)):
                self.assertNotEqual(_ocr_fold(a), _ocr_fold(b))

    def test_the_substitutions_tesseract_makes_are_forgiven(self):
        from omarchy_voice.tools import _ocr_fold

        recovered = [a for a, b in MISREADS if _ocr_fold(a) == _ocr_fold(b)]
        # Not all of them -- the rest are left to the near-miss message rather
        # than guessed at. Pinned so a shrinking map is noticed.
        self.assertIn("close", recovered)      # c1ose, the digit-for-letter case
        self.assertIn("settings", recovered)   # settinqs, the q-for-g case
        self.assertGreaterEqual(len(recovered), 5)


class FoldedMatchTests(unittest.TestCase):
    def setUp(self):
        self.executor = Executor(Config())

    def test_a_digit_for_letter_misread_is_found(self):
        words = [word("c1ose", x=400, y=300)]
        point = self.executor._find_phrase(words, "close")
        self.assertIsNotNone(point)

    def test_clean_ocr_is_unaffected(self):
        words = [word("Continue", x=400, y=300)]
        self.assertIsNotNone(self.executor._find_phrase(words, "Continue"))

    def test_the_fold_adds_no_collision_of_its_own(self):
        """Every DISTINCT pair that plain containment already rejects must
        still be rejected after folding. This is the fold's whole safety
        claim, and it is what a similarity cutoff could not deliver."""
        for want, on_screen in DISTINCT:
            if want in on_screen:
                continue  # containment matched these before the fold existed
            with self.subTest(want=want, on_screen=on_screen):
                words = [word(on_screen, x=400, y=300)]
                self.assertIsNone(self.executor._find_phrase(words, want))

    def test_a_longer_word_is_no_longer_matched(self):
        """Documented as a defect by #48, fixed by #60.

        Scoring by substring containment made click_text("delete") a hit on
        the word "deleted", and it clicked it and reported success. Matching
        whole tokens closes it.
        """
        words = [word("deleted", x=400, y=300)]
        self.assertIsNone(self.executor._find_phrase(words, "delete"))

    def test_the_cost_of_that_fix_is_prefix_matching(self):
        """Named rather than discovered later. Asking for "Setting" no longer
        finds a "Settings" button; #48's near-miss message names it instead."""
        words = [word("Settings", x=400, y=300)]
        self.assertIsNone(self.executor._find_phrase(words, "Setting"))


class NearMissTests(unittest.TestCase):
    """The half the fold cannot recover is named, never clicked."""

    def setUp(self):
        self.executor = with_desktop(Executor(Config(dry_run=False)))
        self.executor._target_geometry = lambda target="screen": ("0,0 800x600", None)
        self.dispatched = []
        self.executor._dispatch = lambda *a, **k: self.dispatched.append(a) or Result(True, "ok")

    def words(self, tokens):
        self.executor._ocr_words = lambda geometry: (tokens, "")

    def test_the_closest_text_is_named_with_its_position(self):
        self.words([word("wordow", x=1200, y=870, w=80, h=20)])
        result = self.executor._tool_click_text("window")
        self.assertFalse(result.ok)
        self.assertIn("wordow", result.output)
        self.assertIn("1240,880", result.output)

    def test_naming_the_near_miss_does_not_click_it(self):
        """A message-level assertion alone would pass on an implementation
        that clicked first and described afterwards."""
        self.words([word("wordow", x=1200, y=870, w=80, h=20)])
        self.executor._tool_click_text("window")
        self.assertEqual(self.dispatched, [])

    def test_nothing_close_keeps_the_old_advice(self):
        self.words([word("Continue", x=400, y=300)])
        result = self.executor._tool_click_text("Aardvark")
        self.assertFalse(result.ok)
        self.assertIn("Read the screen first", result.output)
        self.assertEqual(self.dispatched, [])


class WaitForSharesTheFoldTests(unittest.TestCase):
    """_find_phrase has two callers, so the fold reaches wait_for too.

    Decided in the plan rather than left to be discovered: wait_for(text) polls
    until timeout today when tesseract misreads the word it is waiting for --
    the identical root cause as click_text's dead end. Fixing the shared
    function is what fixes both.
    """

    def test_a_wait_succeeds_on_a_folded_match(self):
        ex = Executor(Config(dry_run=False))
        ex._target_geometry = lambda target="screen": ("0,0 800x600", None)
        ex._ocr_words = lambda geometry: ([word("Settinqs", x=10, y=10)], "")
        result = ex.call("wait_for", {"what": "text", "value": "Settings",
                                      "timeout": 0.5})
        self.assertTrue(result.ok, result.output)

    def test_a_wait_still_times_out_on_text_that_is_not_there(self):
        ex = Executor(Config(dry_run=False))
        ex._target_geometry = lambda target="screen": ("0,0 800x600", None)
        ex._ocr_words = lambda geometry: ([word("Continue", x=10, y=10)], "")
        result = ex.call("wait_for", {"what": "text", "value": "Aardvark",
                                      "timeout": 0.5})
        # A timeout is reported as a fact, not a failure (#24), so the check
        # is what it says rather than result.ok.
        self.assertIn("still is not on screen", result.output)


# --- #60: input that can say where it landed --------------------------------

class TypeTextRoutingTests(unittest.TestCase):
    """Text goes to a named window, or nothing is typed."""

    def executor(self, layout="gb"):
        ex = with_desktop(Executor(Config(dry_run=False)))
        ex._kb_layout = lambda: (layout, "")
        self.calls = []
        def shell(cmd, **kw):
            self.calls.append(cmd)
            return Result(True, "ok")
        ex._shell = shell
        return ex

    def test_the_window_reaches_the_dispatch(self):
        ex = self.executor()
        result = ex._tool_type_text("hi", "address:0xabc")
        self.assertTrue(result.ok, result.output)
        self.assertIn('window = "address:0xabc"', self.calls[0][-1])

    def test_one_batch_not_one_dispatch_per_key(self):
        """Measured 16.8 ms batched against 1104 ms individually, which is the
        difference between this approach being viable and not."""
        ex = self.executor()
        ex._tool_type_text("hello world")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][:2], ["hyprctl", "--batch"])
        # 11 characters, down and up for each.
        self.assertEqual(len(self.calls[0][-1].split(";")), 22)

    def test_a_capital_is_shift_not_a_capital_keysym(self):
        """A bare keysym ignores case: key="H" types "h". Measured against
        live Hyprland, and the reason the mapping exists at all."""
        ex = self.executor()
        ex._tool_type_text("H")
        self.assertIn('key = "h"', self.calls[0][-1])
        self.assertIn('mods = "SHIFT"', self.calls[0][-1])

    def test_the_layout_decides_the_pairing_not_a_table(self):
        """On gb, SHIFT+3 is £ and SHIFT+2 is "; on us the same keys give # and
        @. A hardcoded table would type the wrong character and report
        success, which is the failure this issue exists to remove."""
        ex = self.executor("gb")
        ex._tool_type_text("£")
        self.assertIn('key = "3"', self.calls[0][-1])
        self.assertIn('mods = "SHIFT"', self.calls[0][-1])

    def test_an_unmappable_character_types_nothing_at_all(self):
        """Whole-string refusal. Half of `rm -rf /tmp/x` is still a command,
        and it still runs."""
        ex = self.executor("gb")
        result = ex._tool_type_text("naïve")
        self.assertFalse(result.ok)
        self.assertIn("ï", result.output)
        self.assertEqual(self.calls, [])

    def test_an_unreadable_layout_refuses_rather_than_guessing_us(self):
        ex = self.executor("zzzz")
        result = ex._tool_type_text("hello")
        self.assertFalse(result.ok)
        self.assertEqual(self.calls, [])

    def test_a_window_that_vanishes_before_dispatch_is_a_failure(self):
        """hyprctl reports a missing window as a warning with exit 0, the trap
        _dispatch_lua already exists to close.

        The window has to EXIST for the #67 guard, which now catches an
        address that was never there and refuses earlier -- so what this
        exercises is the real race it was always about: a window present when
        the guard looked and gone by the time the keys were sent.
        """
        ex = self.executor()
        ex._shell = lambda cmd, **kw: Result(True, "warning: send_key_state: window not found")
        result = ex._tool_type_text("hi", "address:0xabc")
        self.assertFalse(result.ok)
        self.assertIn("not found", result.output)

    def test_an_address_that_never_existed_is_refused_before_dispatch(self):
        ex = self.executor()
        result = ex._tool_type_text("hi", "address:0xdead")
        self.assertFalse(result.ok)
        self.assertEqual(self.calls, [])


class ClickStalenessTests(unittest.TestCase):
    """The gap between reading the screen and clicking what was read."""

    def setUp(self):
        self.executor = with_desktop(Executor(Config(dry_run=False)))
        self.executor._target_geometry = lambda target="screen": ("0,0 800x600", None)
        self.dispatched = []
        self.executor._dispatch = lambda *a, **k: (self.dispatched.append(a)
                                                   or Result(True, "ok"))
        self.executor._press_button = lambda *a, **k: Result(True, "clicked")

    def reads(self, first, second):
        """First the full screen read, then the small verification re-read."""
        seen = iter([first, second])
        self.executor._ocr_words = lambda geometry: (next(seen), "")

    def test_a_target_that_is_still_there_is_clicked(self):
        button = [word("Continue", x=400, y=300)]
        self.reads(button, button)
        result = self.executor._tool_click_text("Continue")
        self.assertTrue(result.ok, result.output)
        self.assertTrue(self.dispatched)

    def test_a_target_that_moved_is_not_clicked(self):
        """The assertion that matters is that NO pointer event is sent. A
        message-level check passes on an implementation that clicks first and
        describes afterwards."""
        self.reads([word("Continue", x=400, y=300)],
                   [word("Something", x=400, y=300)])
        result = self.executor._tool_click_text("Continue")
        self.assertFalse(result.ok)
        self.assertIn("is not there now", result.output)
        self.assertEqual(self.dispatched, [])

    def test_a_failed_recheck_refuses_rather_than_trusting_the_old_read(self):
        seen = iter([([word("Continue", x=400, y=300)], ""), ([], "the session is locked")])
        self.executor._ocr_words = lambda geometry: next(seen)
        result = self.executor._tool_click_text("Continue")
        self.assertFalse(result.ok)
        self.assertEqual(self.dispatched, [])

    def test_the_recheck_reads_a_small_region_not_the_screen(self):
        """~187 ms against ~4050 ms. If this ever re-reads the full screen the
        tool doubles in cost to validate a stale read with another one."""
        seen, button = [], [word("Continue", x=400, y=300)]
        def ocr(geometry):
            seen.append(geometry)
            return (button, "")
        self.executor._ocr_words = ocr
        self.executor._tool_click_text("Continue")
        self.assertEqual(seen[0], "0,0 800x600")
        self.assertNotEqual(seen[1], "0,0 800x600")
        self.assertIn("300x60", seen[1])

    def test_a_vault_that_opens_between_the_read_and_the_recheck_stops_the_click(self):
        """The re-check's guards are the only ones dated after the OCR gap (#73).

        Sharing any guard answer between the two reads -- a per-call memo, a
        TTL -- hands the click a "safe" answer seconds old. So the real guard
        chain runs here, twice, and the vault appears while grim is capturing.
        """
        import json
        from omarchy_voice import tools

        ex = Executor(Config(dry_run=False))
        ex._target_geometry = lambda target="screen": ("0,0 800x600", None)
        dispatched = []
        ex._dispatch = lambda *a, **k: dispatched.append(a) or Result(True, "ok")
        ex._press_button = lambda *a, **k: Result(True, "clicked")
        ex._recorded_by_process = lambda: None  # a real recorder is not the subject
        vault = {"address": "0xvault", "class": "1Password", "title": "1Password",
                 "at": [0, 0], "size": [800, 600], "workspace": {"name": "1"},
                 "mapped": True, "focusHistoryID": 0}
        opened = []

        def shell(cmd, **kw):
            if cmd[:3] == ["omarchy-shell", "lock", "isLocked"]:
                return Result(True, "false")
            if cmd == ["hyprctl", "-j", "monitors"]:
                return Result(True, json.dumps([{"activeWorkspace": {"name": "1"},
                                                 "dpmsStatus": True}]))
            if cmd == ["hyprctl", "-j", "clients"]:
                return Result(True, json.dumps([vault] if opened else [ORDINARY_WINDOW]))
            return Result(False, f"unexpected {cmd}")
        ex._shell = shell

        tsv = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop"
               "\twidth\theight\tconf\ttext\n"
               "5\t1\t1\t1\t1\t1\t380\t290\t60\t14\t90\tContinue\n").encode()

        def run(argv, **kw):
            if argv[0] == "grim":
                opened.append(True)  # the vault opens during the OCR gap
                return mock.Mock(returncode=0, stdout=b"P6 frame")
            if argv[0] == "tesseract":
                return mock.Mock(returncode=0, stdout=tsv)
            if argv[0] == "pw-dump":
                return mock.Mock(returncode=0, stdout=b"[]")
            raise AssertionError(f"unexpected subprocess {argv}")

        with mock.patch.object(tools.subprocess, "run", run), \
                mock.patch.object(tools.shutil, "which", lambda name: f"/bin/{name}"):
            result = ex._tool_click_text("Continue")
        self.assertTrue(opened, "the first read never captured")
        self.assertFalse(result.ok, result.output)
        self.assertEqual(dispatched, [])
        self.assertIn("a password manager", result.output)


class NearMissNamesTheFailedWordTests(unittest.TestCase):
    """The near miss must name the word the caller got WRONG.

    Naming one they got right is true and useless: asked for "Files change"
    against a screen reading "Files changed", the refusal used to say "the
    closest text is 'Files'", which tells the caller nothing about what to
    call instead. Multi-word is the normal case for click_text, so this was
    most of the value of the message.
    """

    def words(self, *texts):
        return [word(t, x=100 + i * 80) for i, t in enumerate(texts)]

    def nearest(self, query, *screen):
        from omarchy_voice.tools import _nearest_word

        found = _nearest_word(self.words(*screen), query)
        return found["text"] if found else None

    def test_the_unmatched_word_is_named_not_the_matched_one(self):
        self.assertEqual(self.nearest("Files change", "Files", "changed"), "changed")

    def test_it_skips_past_several_words_that_matched(self):
        self.assertEqual(
            self.nearest("US and Iran trade strike",
                         "US", "and", "Iran", "trade", "strikes"),
            "strikes")

    def test_a_single_word_query_is_unaffected(self):
        self.assertEqual(self.nearest("Setting", "Settings"), "Settings")

    def test_nothing_close_is_still_nothing(self):
        self.assertIsNone(self.nearest("Aardvark", "Continue"))

    def test_a_word_that_only_matches_after_folding_is_not_reported(self):
        """#48's fold counts as a match, so "c1ose" is not the failed word."""
        self.assertEqual(self.nearest("close windo", "c1ose", "window"), "window")
