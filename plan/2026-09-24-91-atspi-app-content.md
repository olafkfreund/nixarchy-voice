---
status: approved
issue: 91
spec: spec/2026-09-24-91-atspi-app-content.md
---

# Plan: read app content from the accessibility tree first, with OCR as the fallback

Closes #91. Base: `origin/main` `50dcf99` (1085 tests collected). Every
`file:line` below was checked against `50dcf99`. This lands **third**, after
#77 and #83, and all three edit `src/omarchy_voice/tools.py`, so step 1
rebases and re-checks every line number. Files that change:
`src/omarchy_voice/a11y.py` (new), `src/omarchy_voice/tools.py`,
`src/omarchy_voice/trace.py`, `tests/test_a11y.py` (new),
`tools/verify_a11y.py` (new), `nix/package.nix`, `flake.nix`, `README.md`.
No existing test is edited.

## Approved decisions, carried over from the spec

1. **The Chrome flag is the user's to set, in their NixOS config.** This
   repo documents `--force-renderer-accessibility` and never sets it. There
   is no `programs.omarchy-voice.chromeAccessibility` option in
   `nix/hm-module.nix`: `p620_home.nix:93-110` sets
   `programs.chromium.commandLineArgs = lib.mkForce [ … ]`, which would drop
   it without a word, and a voice package should not override browser flags.
   The web apps (`omarchy-launch-webapp`) are expected to inherit the flag
   from the same binary. That is not verified, and the live check reports it
   per process. Opening a tracking issue in the NixOS-config repo is the
   lead's job. This task opens nothing.
2. **The flag's cost is not measured here.** Measuring it means relaunching
   Chrome, which this task must not do. The README says the cost is
   unmeasured and gives the command to measure it: `ps -o rss= -C chrome`
   summed, same tabs, before and after one restart.
3. **Ship the reader now, for every app that already exposes a tree.** GTK
   apps (Nautilus, spotifast) answer today. Chrome answers as soon as the
   flag is set, with no second change. The acceptance check for Chrome is a
   later run of `tools/verify_a11y.py` that shows the flag in the browser
   pid's `/proc/<pid>/cmdline`, not this merge.
4. **Privacy scope: only what OCR could see.** A node is read only if it is
   `SHOWING`, its rectangle (after adding the window origin) intersects the
   capture rectangle, and it is not a password field. The guards run first,
   and the tree is never touched when they refuse. Reading the whole document
   was rejected.
5. **Electron apps are covered by the same code.** Vesktop and claude-desktop
   answer once launched with the flag. The README line names them. There is
   no per-app code.
6. **One seam, and no caller changes.** The tree is tried inside
   `_ocr_region` (`tools.py:2814`) and `_ocr_words` (`:2919`), **after** the
   guard line `self._screen_unavailable() or self._capture_refused(geometry)`
   (`:2819`, `:2930`) and before `grim`: guard, then tree answer, otherwise
   grim + tesseract unchanged. Callers stay as they are: `_read_screen_text`
   (`:3312`/`:3316`), `_await_paint` (`:4142`, `:4151`), `_tool_click_text`
   (`:3121`), `_target_moved` (`:3094`), `_tool_wait_for` (`:4414`). The names
   `_ocr_*` stay (36 test references in 8 files), with a `ponytail:` comment
   on each saying the name is historical. `_tool_screenshot` (`:2794`,
   guard `:2807`) returns pixels and gets no tree path.
7. **The grim/tesseract `which` checks move below the tree attempt**
   (today at `:2816-2818` and `:2927-2929`, before the guard). A tree answer
   does not need tesseract. A missing tool still gives today's message when
   the fallback runs.
8. **New module `src/omarchy_voice/a11y.py`, about 150 lines.** It holds
   pure functions over any Atspi-like object, so tests pass in fakes. It is
   the only file under `src/` that imports `gi`.
9. **`a11y.desktop()`** returns the Atspi desktop, or `None` when `gi` or the
   `Atspi-2.0` typelib is missing (ImportError/ValueError), when
   `org.a11y.Status.IsEnabled` is not `true`, or when reading it fails for
   any reason. `IsEnabled` is read with one `Gio` `Properties.Get` on the
   session bus to `org.a11y.Bus`, with a 1 s timeout, **before** anything
   touches Atspi. Nothing is ever written: no `Set`, no `busctl
   set-property`. On success it calls `Atspi.set_timeout(500, 2000)`. Only
   `IsEnabled` gates. `ScreenReaderEnabled` is not read by the reader.
10. **`a11y.window_nodes(desktop, client)`** matches the application's
    `get_process_id()` to the client's `pid`. One frame for that pid means
    that frame. Several frames means the one whose name equals the client's
    title or starts with it. None or more than one match returns `None`
    (unknown, so OCR).
11. **`a11y.collect(frame, origin, rect, deadline, cap=5000)`** walks
    depth-first and returns `[(text, x, y, w, h, actionable)]`, or `None`:
    - it descends only into `SHOWING` children;
    - extents are read with `CoordType.WINDOW`, and `origin` (the client's
      `at`) is added;
    - a node is kept if its screen rectangle intersects `rect`. Its text is
      the Text interface content, or else its name, capped at 2000
      characters. Consecutive duplicates are dropped;
    - a node whose role name, lower-cased with spaces, `-` and `_` removed,
      contains `password` is skipped. This is the only role test. Reading
      filters by no other role, so `button` and `push button` behave the
      same;
    - `actionable` is true when the node has an Action interface with at
      least one action;
    - passing `cap` or `deadline` returns `None`. A partial tree is unknown.
12. **A module-level `threading.Lock` wraps every walk.** Tools run through
    `asyncio.to_thread` (`mcp_server.py:169`), and libatspi is not
    thread-safe.
13. **`_tree_nodes(geometry)` in `tools.py` decides whether the tree
    answered.** It returns the node list, or `None` for OCR. It returns
    `None` unless every rule holds:
    1. `a11y.desktop()` is not `None`;
    2. `_windows_in(geometry)` (`:2709`) is called again and is not empty.
       If it raises `_CannotSee` (`:2507`, raised at `:2734`), the answer is
       `None`;
    3. no covering client is `xwayland: true`;
    4. no two covering windows overlap each other inside `geometry`;
    5. every covering window has a frame (`window_nodes`), and `collect`
       returns at least one node whose text is neither the frame's name nor
       the client's title;
    6. everything finishes inside a 1.0 s `deadline`.
14. **The three ways to get nothing stay separate.** Accessibility off or
    unreadable is rule 1. A tree with frames only (Chrome without the flag,
    or a toolkit that only saw `IsEnabled`) is rule 5. A role mismatch cannot
    happen, because only the password skip looks at roles. All three fall
    back to OCR. The live check prints which one it saw for each window.
15. **A region where one covering window has no tree goes to OCR whole.**
    A `ponytail:` comment names the upgrade path: OCR only the treeless
    windows and merge, if the live check shows mixed regions are common.
16. **The trace gets a phase `A11Y = "a11y"`** in `trace.py`, next to
    `CAPTURE`/`OCR` (`:42-43`), around the walk. It records the phase name
    only, never text.
17. **`_ocr_region` from the tree** returns node texts in walk order, joined
    by newlines and cut at `OCR_LIMIT` (`:266`) with the same
    "… [more text on screen, not read]" tail. An empty result means OCR
    runs. It is never "no readable text".
18. **`_ocr_words` from the tree** returns one word dict per whitespace
    token, each carrying its node's screen rectangle and `conf: 100.0`.
    Actionable nodes come first. `_find_phrase` (`:2972`) is unchanged: a
    run inside one node clicks that node's centre. `_target_moved` (`:3080`)
    re-reads through the same method, so #60's re-check now costs one walk.
19. **`read_screen`'s description** (`:1104-1112`, "OCR is imperfect" at
    `:1109`) gets one clause: text comes from the app's accessibility tree
    when it exposes one, and from OCR otherwise. The result is the same shape
    either way, and the model is not told which one answered.
20. **Packaging.** `nix/package.nix:57` adds `pygobject3` to
    `dependencies`. `postFixup` (`:95`) adds `--prefix GI_TYPELIB_PATH :
    ${lib.makeSearchPath "lib/girepository-1.0" [ at-spi2-core glib.out
    gobject-introspection ]}`. The `flake.nix` dev shell (`:61`) and check
    (`:121`) add `pygobject3` and the same `GI_TYPELIB_PATH`.
21. **README.** One section of about 15 lines after the tool table under
    `### Going after a goal` (`README.md:580`, table `:586-595`): what reads
    from the tree; that Chrome, its web apps and Electron apps need
    `--force-renderer-accessibility` in the user's own
    `programs.chromium.commandLineArgs` (merged into a `mkForce` list if they
    have one); how to confirm it took; the unmeasured cost and the RSS
    command; and that nothing changes until then.
22. **Rejected, and not to be reintroduced:** an HM option for the flag;
    relaunching Chrome, or writing `IsEnabled`/`ScreenReaderEnabled`; reusing
    ai-mirror's `a11y.py` or CLI (its `enable_bus`, `ai-mirror/src/ai_mirror/
    a11y.py:62`, writes the bus); a separate `read_tree` tool or a source
    router; a role allow-list; reading the whole document; per-window mixing
    of tree and OCR; a config switch to turn tree reads off; waiting for the
    flag before shipping.

## Landing order

**Third: #77, then #83, then #91.** `refactor/77-engine-duplication` and
`feat/83-services-and-mcp` both edit `tools.py`. At `50dcf99` neither has
merged. Step 1 is a hard precondition. After the rebase, every `tools.py`
line above is re-checked, and corrected in this file in the same commit as
the code.

## Steps

0. **Baseline.** `git fetch origin`. `gh issue view 91` shows OPEN. With
   `export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`, run
   `nix develop -c python3 -m pytest -q` and
   `nix develop -c python3 -m unittest discover -s tests`
   → verify by both passing. Record the count (1085 at `50dcf99`) and any
   failure that already exists.
1. **Precondition: #77 and #83 have merged.** `gh pr list --state merged
   --search 77` and `--search 83`, and `git log origin/main --oneline | grep
   -E '#77|#83'`. If either is missing, **stop and report. Do not
   implement.** Otherwise `git rebase origin/main`, repeat step 0, and
   re-grep every symbol in decisions 6, 7, 13 and 16-19
   → verify by a clean rebase, a green baseline, and line numbers confirmed
   or corrected here.
2. **`tests/test_a11y.py`: fakes and tests, red on `main` first.** The file
   imports `_isolated` first. No test reaches a real bus or a real Atspi
   tree, except T16, which only imports the typelib.
   - *Fake tree*: small classes with `get_name`, `get_role_name`,
     `get_process_id`, `get_state_set().contains`,
     `get_component_iface().get_extents` (returns window-relative extents,
     whatever coord type is asked), `get_text_iface`, `get_action_iface`,
     `get_child_count`/`get_child_at_index`. A builder takes a nested tuple
     `(role, name, (x, y, w, h), showing, actionable, children)`.
   - *Desktop*: patch `omarchy_voice.a11y.desktop` to return the fake
     desktop (a `mock.Mock` wrapping it, so calls are counted), or `None`.
   - *Windows*: as in `SensitiveWindows` (`tests/test_policy.py:217-231`),
     set `ex._query_rows = lambda kind: (clients, None)` and patch
     `_visible_workspaces`, `_screen_unavailable` and `_screen_is_recorded`.
     Clients carry `pid`, `at`, `size`, `title`, `class`, `xwayland`.
   - *OCR*: patch `omarchy_voice.tools.subprocess.run`. In tree tests it
     raises if called. In fallback tests it returns canned grim bytes and
     tesseract text/tsv, and the test asserts it was called.
   - Tests (window at `at=[1000, 50]`, size 800×600, geometry
     `"1000,50 800x600"` unless stated):
     - T1 `test_a_gtk_tree_answers_read_screen_without_ocr`.
     - T2 `test_frames_only_falls_back_to_ocr`: a frame named like the title
       and nothing else → OCR text.
     - T3 `test_no_accessibility_falls_back_to_ocr`: `desktop()` is `None`
       → OCR text.
     - T4 `test_is_enabled_false_is_none_and_only_get_is_called`: a fake
       Gio connection returning `IsEnabled=false`; the real `desktop()`
       returns `None`, the only D-Bus method called is `Get`, and Atspi is
       never touched.
     - T5 `test_an_unreachable_bus_is_none`: the real `desktop()` under
       `_isolated`'s dead bus address returns `None` within 1.5 s.
     - T6 `test_the_guards_refuse_before_the_tree`: subTests for a
       sensitive window (#46), the window list failing (#51), a recording
       running (#52), and DPMS off or the lock screen (#67), each through
       both `_ocr_region` and `_ocr_words`. The refusal text equals what
       `main` returns, and `a11y.desktop` is **never called**.
     - T7 `test_window_relative_extents_are_shifted_by_the_window_origin`:
       a button at (10, 20, 80, 30) → `click_text` clicks (1050, 85).
     - T8 `test_button_and_push_button_are_both_clickable`: a Chromium
       `button` and an ATK `push button` each answer `click_text`, with no
       OCR.
     - T9 `test_password_fields_are_never_read_or_matched`: roles
       `password text`, `Password_Text` and `passwordtext` holding
       "hunter2" beside a label. `read_screen` omits "hunter2", and
       `click_text("hunter2")` finds nothing from the tree.
     - T10 `test_only_showing_nodes_inside_the_rect_are_read`: a
       non-`SHOWING` node, a `SHOWING` child under a non-`SHOWING` parent,
       and a `SHOWING` node whose shifted rectangle is outside the geometry
       (it would be inside without the origin add) are neither read nor
       matched.
     - T11 `test_tree_off_frames_only_and_role_mismatch_are_told_apart`:
       three fixtures, three different call patterns. Tree off: the walk is
       never entered, OCR runs. Frames only: the walk runs, OCR runs.
       Chromium roles only (`button`, `link`, `paragraph`, no ATK spelling):
       the tree answers and OCR is not called.
     - T12 `test_xwayland_overlap_or_ambiguous_frames_fall_back`: subTests
       for an `xwayland: true` client, two covering windows that overlap,
       and one pid with two frames where no name matches the title.
     - T13 `test_second_window_list_failure_falls_back_to_ocr`:
       `_query_rows` succeeds on the guard's call and fails on the next one
       → OCR text, not a refusal and not a tree answer.
     - T14 `test_a_partial_walk_falls_back`: subTests for `cap` exceeded
       and the deadline passed (a fake `time.monotonic`).
     - T15 `test_one_treeless_window_sends_the_region_to_ocr`: a tree
       window beside a terminal with no frame.
     - T16 `test_the_atspi_typelib_imports`: real `gi.require_version
       ("Atspi", "2.0")`; `Atspi.set_timeout`, `Atspi.CoordType.WINDOW`
       and `Atspi.StateType.SHOWING` exist. No bus call.
     - T17 `test_a_tree_answer_needs_no_tesseract`: `shutil.which` returns
       `None`. With a tree, `read_screen` answers. Without one, it returns
       today's install hint.
     - T18 `test_a_button_beats_body_text`: a paragraph "Sign in" and a
       `button` "Sign in" → the click lands on the button's centre.
     - T19 `test_the_trace_names_a11y_and_holds_no_text`: the trace has an
       `a11y` phase, and no node text appears in the trace output.
     - T20 `test_wait_for_and_target_moved_read_the_tree`:
       `wait_for(text)` succeeds, and the `_target_moved` re-check passes,
       with the OCR stub never called.
     - T21 `test_the_tree_text_is_capped_like_ocr`: 7000 characters of node
       text → `OCR_LIMIT` plus the same tail.
   → verify by `nix develop -c python3 -m pytest -q tests/test_a11y.py` on
   the unchanged `src/`: every test that needs `omarchy_voice.a11y` or a tree
   answer FAILS (ImportError, or the OCR stub raising). The fallback tests
   (T2, T3, T12-T15) and T6 fail on the missing `a11y` patch target, and pass
   once it exists. Record the red run in the PR.
3. **`flake.nix` (`:61`, `:121`) and `nix/package.nix` (`:57`, `:95`):
   pygobject3 and `GI_TYPELIB_PATH`** (decision 20)
   → verify by `nix develop -c python3 -c 'import gi;
   gi.require_version("Atspi","2.0"); from gi.repository import Atspi'`
   exiting 0, and `nix build .#default` succeeding.
4. **`src/omarchy_voice/trace.py:42-43`: add `A11Y = "a11y"`**
   → verify by `grep -n 'A11Y' src/omarchy_voice/trace.py`.
5. **`src/omarchy_voice/a11y.py`: `desktop`, `window_nodes`, `collect`,
   the lock** (decisions 8-12). Only this file imports `gi`, lazily inside
   `desktop()`, so importing the module never touches the bus
   → verify by T4, T5, T9, T10, T14 and T16 passing, and by
   `grep -rn "import gi\|from gi" src/` listing only `a11y.py`.
6. **`src/omarchy_voice/tools.py`: `_tree_nodes`, and the tree attempt in
   `_ocr_region`/`_ocr_words`** (decisions 6, 7, 13-18). Add `_tree_nodes`
   beside `_windows_in` (`:2709`). In both methods, keep the guard line
   first, then try the tree inside `self.trace.mark(trace_mod.A11Y)`, then
   run the moved `which` checks, then grim + tesseract unchanged. Add the
   `ponytail:` comments (historical names; whole-region fallback)
   → verify by all of `tests/test_a11y.py` passing, and by
   `git diff src/omarchy_voice/tools.py` showing no caller changed and the
   guard line still the first check in both methods.
7. **`tools.py:1104-1112`: one clause in `read_screen`'s description**
   (decision 19) → verify by the existing tool-schema tests passing
   unchanged.
8. **`README.md`: the section after `:595`** (decisions 1, 2, 3, 5, 21)
   → verify by reading it: it has the flag line, the `mkForce` note, the
   `/proc/<pid>/cmdline` confirmation, the RSS command, Electron, and no HM
   option.
9. **`tools/verify_a11y.py`: the read-only live check**, shaped like
   `tools/verify_find.py`. It reads `IsEnabled`/`ScreenReaderEnabled` and
   stops with one line if accessibility is off. For each visible client it
   prints class, pid, xwayland, whether `--force-renderer-accessibility` is
   in `/proc/<pid>/cmdline`, node count, answer or fall back and which of
   the three reasons (decision 14), and walk time. For up to five named,
   actionable nodes per answering window, it prints the computed screen
   rectangle and whether OCR of exactly that rectangle contains the name,
   only where `_capture_refused` passes. It never sets a property, clicks,
   types, launches or relaunches anything
   → verify by `grep -nE "Set\b|set_property|busctl|wtype|click|
   ai-mirror-input|Popen" tools/verify_a11y.py` printing no call.
10. **Optional live check on this desktop (read-only).** Accessibility may
    be on or off here, so check first, read-only:
    `busctl --user get-property org.a11y.Bus /org/a11y/bus
    org.a11y.Status IsEnabled`. Then, **without** the dead-bus export,
    `nix develop -c python3 tools/verify_a11y.py`. Do not click, type, use
    ai-mirror input, or relaunch any app. If accessibility is off, record
    that and skip. It is not turned on for this check
    → verify by the output matching the spec's expectation: GTK apps
    (spotifast/Nautilus) answer with their rectangles confirmed by OCR, and
    Chrome/Chromium/Electron report "frames only, flag absent → OCR". Paste
    the output in the PR. Any rectangle that OCR does not confirm is a
    finding against decision 11. Report it and do not merge.
11. **Mutation checks.** Apply each one alone, run `tests/test_a11y.py`,
    then revert with `git checkout -- src/`. One per decision:
    - D1, D2, D3, D5, D21 (README only): no code to mutate. Checked in
      step 8, and `grep -n chrom nix/hm-module.nix` prints nothing.
    - D4 and D11 (`SHOWING`): descend into non-`SHOWING` children → T10
      fails. D4 and D11 (rectangle): drop the intersection test → T10
      fails.
    - D6: move the tree attempt above the guard line → T6 fails.
    - D7: put the `which` checks back above the tree → T17 fails.
    - D8: import `gi` at the top of `tools.py` → the `grep` in step 5 lists
      it (read, not a test).
    - D9: skip the `IsEnabled` read and go straight to Atspi → T4 fails.
    - D10: take the first frame when several match the pid → T12 fails.
    - D11 (origin): drop the `origin` add → T7 fails. D11 (password): remove
      the skip → T9 fails. D11 (partial): return the partial list at the
      cap → T14 fails.
    - D12: remove the lock → no unit test (thread timing). Checked by
      reading the diff.
    - D13 rule 2: swallow `_CannotSee` as "no windows" and read the tree →
      T13 fails. Rule 3: drop the xwayland test → T12 fails. Rule 4: drop
      the overlap test → T12 fails. Rule 5: accept frames-only → T2 and T11
      fail. Rule 6: no deadline → T14 fails.
    - D14: add a role allow-list (`push button`, `link`) → T8 and T11 fail.
    - D15: return the tree windows' text and skip the treeless one → T15
      fails.
    - D16: pass node text into the trace mark → T19 fails.
    - D17: drop the `OCR_LIMIT` cut on the tree path → T21 fails. Return
      "no readable text" for an empty tree → T2 fails.
    - D18: do not sort actionable first → T18 fails.
    - D19: description only. Checked by reading it.
    - D20: remove `GI_TYPELIB_PATH` from the check → T16 fails in
      `nix flake check`.
    - D22: no code to mutate. Checked by reading the diff.
    → verify by each mutation turning its test red, and by `git diff --stat`
    showing only the intended files after each revert.
12. **Full suites and the flake.** With
    `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`, run both runners and
    `nix flake check --no-write-lock-file`
    → verify by all three passing, and the count equal to the step 0
    (post-rebase) baseline plus 21. No existing test is edited.

## Tests

```
export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent
nix develop -c python3 -m pytest -q tests/test_a11y.py     # 21 passed (red before step 5)
nix develop -c python3 -m pytest -q                        # baseline + 21, all pass
nix develop -c python3 -m unittest discover -s tests       # same count, OK
nix flake check --no-write-lock-file                       # passes, T16 included
nix build .#default                                        # succeeds
nix develop -c python3 tools/verify_a11y.py                # optional, real bus, read-only
```

No unit test reaches D-Bus, walks a real accessibility tree, captures the
screen or runs tesseract. Nothing in any step clicks or types on the desktop.

## Rollback

One PR on `feat/91-atspi-app-content`. Before merge, drop the branch. After
merge, `git revert <sha>` removes `a11y.py`, the tree attempt, the trace
phase, the README section and the pygobject3 dependency. The `_ocr_*` methods
go back to OCR-only. There is no config, state or migration. On a running
host the tree path can also be disabled with no revert, by accessibility
being off (`IsEnabled` false), because `desktop()` then returns `None`. The
daemon picks up either change on the next `omarchy-voice` rebuild or restart.

## Spec ambiguities, resolved here

- **"The only file that imports `gi`" vs "one more test imports `gi`".**
  Read as the only file under `src/`. T16 imports `gi` directly so that a
  missing typelib fails the check (spec: "rather than a silent fall back").
- **"Three nothings told apart" in unit tests.** The spec puts the reason
  string only in the live check and gives `_tree_nodes` a plain
  list-or-`None` return. The plan does not add a reason API. T11 tells the
  three apart by what is called (walk entered or not, OCR called or not).
- **"Second window-list call fails" had no numbered test** (rule 2). T13 adds
  it.
- **Spec test count.** The spec lists 13 tests. This plan splits them into
  21 so that each decision has a test its mutation turns red.
