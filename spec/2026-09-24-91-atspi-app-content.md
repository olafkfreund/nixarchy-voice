---
status: approved
issue: 91
intent: intent/2026-09-24-91-atspi-app-content.md
---

# Spec: read app content from the accessibility tree first, with OCR as the fallback

Line numbers are for `main` at `50dcf99` (after #121, #110, #111, #79, #80,
#114 and #138). The intent's line numbers are for `2172c4f` and have drifted
(about +18 in `tools.py` from `_sensitive_kind` on); where the two differ,
this file is current.

## Decisions on the intent's open questions

The intent was approved with a bare "approve all", so each question is
decided here, with the reason and the evidence.

1. **Who sets the flag, and where: the user's NixOS config. This repo
   documents it and does not set it. This issue does not wait for it.**
   Evidence: `p620_home.nix:93-110` sets
   `programs.chromium.commandLineArgs = lib.mkForce [ … ]`. A `mkForce`
   list discards every definition at normal priority, so a
   `programs.omarchy-voice.chromeAccessibility` option in `nix/hm-module.nix`
   that appended the flag would be dropped without a word on p620. That is
   the host this issue is for. The option would also have to raise its own
   priority to beat the user, and a voice package overriding the browser's
   flags is a surprising side effect for other users. `nix/hm-module.nix`
   has no browser settings today (checked: no `chrom` anywhere in it). The
   web apps are covered by the same change: `omarchy-launch-webapp` execs
   the `Exec=` binary of the default browser's desktop entry, which is the
   binary `programs.chromium` builds with `commandLineArgs`. That is expected
   but not verified, and the live check reports it per process (see
   Verification). The README gets a short section with the one line to add
   and how to confirm it took. A tracking issue in the NixOS-config repo is
   the lead's to open. This spec opens nothing.

2. **The flag's cost: measured by the user before they turn it on, not
   blocking here.** This repo never turns it on, so the cost lands only when
   the user adds the line. The README section says the cost is unmeasured
   and gives a way to measure it: Chrome's summed RSS
   (`ps -o rss= -C chrome`) with the same tabs open, before and after one
   restart. Measuring it now would mean relaunching Chrome with the flag,
   which this task must not do.

3. **Scope before the flag lands: ship the reader now, for every app that
   already exposes a tree.** The code does not depend on which app answers,
   and Chrome starts using it the moment the flag is set, with no second
   change. #73 declined because the log showed no GTK app
   (`spec/2026-09-24-73-read-before-ocr.md:32-46`). That still holds, so the
   value before the flag is small. But the reader is what proves the flag
   pays off, and without it nobody can measure the gain. GTK trees (Nautilus
   291 nodes, spotifast 190) are also what the live check uses to prove the
   coordinate arithmetic on this desktop today, before Chrome changes.
   **Chrome is revisited** when `tools/verify_a11y.py` shows
   `--force-renderer-accessibility` in the Chrome browser pid's
   `/proc/<pid>/cmdline`. That run, not this merge, is the acceptance check
   for Chrome (see Verification). No code change is planned for that day.

4. **Privacy scope: only what OCR could see.** A node is read only if it is
   `SHOWING` and its rectangle (after adding the window origin) intersects
   the capture rectangle, and it is not a password field. Off-screen rows,
   collapsed menus and hidden inputs are skipped. The guards run first, at
   the same seam as today, and the tree is never touched when they refuse.
   Reading the whole document was rejected: a voice turn that asks "what is
   on screen" should not hand the model the rest of a mail thread it cannot
   see.

5. **Electron apps: supported by the same code, configured by nobody here.**
   Electron honours `--force-renderer-accessibility` the way Chrome does, so
   Vesktop and claude-desktop start answering from the tree if the user
   launches them with it. The README line mentions them. There is no
   per-app work in this repo.

## Design

### One seam, unchanged callers

Every text read already goes through two methods, and each one runs the
guards first:

- `_ocr_region(geometry)` (`src/omarchy_voice/tools.py:2814`, guard at
  `:2819`). Used by `_read_screen_text` (`:3312-3316`, which serves
  `read_screen`) and by `_await_paint` (`:4142`, `:4151`, the web-page read).
- `_ocr_words(geometry)` (`:2919`, guard at `:2930`). Used by
  `_tool_click_text` (`:3121`), `_target_moved` (`:3094`) and
  `_tool_wait_for(text)` (`:4414`).

The guard line is `self._screen_unavailable() or self._capture_refused(geometry)`:
DPMS off and the lock screen (#67), then sensitive windows (#46), an
unreadable window list failing closed (#51) and a running screencast (#52).
The tree attempt sits below that one line, so all of them refuse a tree read
exactly as they refuse a capture. The only other capture seam,
`_tool_screenshot` (`:2794`, same guard at `:2807`), returns pixels, not
text, and does not get a tree path.

The tree is tried inside these two methods, **after** the guard line and
before `grim`:

```
guard (unchanged) → tree answer? → return it
                  → otherwise     → grim + tesseract (unchanged)
```

No caller changes, the guard stays where #46/#51/#67 put it, and a new read
path can only be added below the guard. Keeping the names `_ocr_*` avoids
churn across the 36 test references to them (8 test files). A `ponytail:` comment on each
says the name is historical and the method reads the tree first. Tests that
stub `_ocr_words` or `_ocr_region` bypass the tree, as they bypass the
capture today.

The `grim`/`tesseract` availability check at `:2816-2818` and `:2927-2929`
(today it runs *before* the guard) moves below the tree attempt. That way a tree answer does not need tesseract
installed, and a missing tesseract still gives the same install hint when the
fallback runs.

### New module `src/omarchy_voice/a11y.py` (about 150 lines)

It has pure functions over any Atspi-like object, so tests can pass in fake
trees. It is the only file that imports `gi`.

- `desktop()` returns the Atspi desktop, or `None` when:
  - `gi` or the `Atspi-2.0` typelib is missing (ImportError/ValueError);
  - `org.a11y.Status.IsEnabled` is not `true`. This is read with one
    `Gio` `Properties.Get` on the session bus to `org.a11y.Bus`, with a 1 s
    timeout, **before** anything touches Atspi. That avoids activating the
    a11y bus launcher when accessibility is off. Nothing is ever written: no
    `Set`, no `busctl set-property`. That is the difference from ai-mirror's
    `enable_bus` (`ai-mirror/src/ai_mirror/a11y.py:60-72`);
  - the property read fails for any reason (bus unreachable, which is what
    every test sees, because `tests/_isolated.py` points
    `DBUS_SESSION_BUS_ADDRESS` at a path that does not exist).

  On success it calls `Atspi.set_timeout(500, 2000)`, so that one hung app
  costs at most half a second per call, not the 25 s default.
  Only `IsEnabled` gates. With `IsEnabled` true and `ScreenReaderEnabled`
  false, toolkits may publish frames with nothing under them (ai-mirror's
  #12, `a11y.py:63-65`). That is the same shape as unflagged Chrome and is
  caught by rule 5 below, not here.
- `window_nodes(desktop, client)` finds the frames that belong to one
  `hyprctl` client. It matches the application's `get_process_id()` to the
  client's `pid` (Chrome's frames live in the browser process, which is the
  client pid). With one frame for that pid, it takes that frame. With
  several (two Chrome windows), it takes the frame whose name equals the
  client's title, or starts with it. If none or more than one match, the
  answer is `None`, which means unknown and falls back to OCR.
- `collect(frame, origin, rect, deadline, cap=5000)` walks depth-first and
  returns `[(text, x, y, w, h, actionable)]`, or `None` for unknown:
  - It descends only into `SHOWING` children (ai-mirror `_showing`,
    `a11y.py:139-141`).
  - Extents are read with `CoordType.WINDOW` and then `origin` (the
    client's `at`) is added. This is the fact from #73 and ai-mirror:
    extents come back window-relative even when SCREEN is asked for. Asking
    for WINDOW says what we actually get.
  - A node is kept if its screen rectangle intersects `rect`. Its text is
    the Text interface content if there is any, otherwise its name. It is
    capped at 2000 characters per node, and consecutive duplicates (a label
    whose name equals its text) are dropped.
  - Password fields are skipped whatever the role spelling. The role name
    is lower-cased with spaces, `-` and `_` removed, and a node whose role
    contains `password` is skipped. This is the only role test anywhere.
    Reading does not filter by role at all, so Chromium's `button` and ATK's
    `push button` behave the same, and there is no role string that can
    silently match nothing.
  - `actionable` is true when the node has an Action interface with at
    least one action. Clicks prefer actionable nodes and never require one.
  - Passing the node cap or the deadline returns `None`. A partial tree is
    unknown, not a short page.
- A module-level `threading.Lock` around every walk. Tools run through
  `asyncio.to_thread` (`mcp_server.py:169`), and libatspi is not
  thread-safe.

### The rule for "the tree answered" (in tools.py)

It is one helper, `_tree_nodes(geometry)`, which both `_ocr_*` methods call.
It returns the node list, or `None` meaning fall back to OCR. It returns
`None` unless **every** one of these holds:

1. `a11y.desktop()` is not `None`.
2. `_windows_in(geometry)` (`:2709`) is not empty. It is already fetched by
   the guard, and the helper calls it again rather than threading it
   through, which costs one hyprctl query of about 13 ms. If that second
   call raises `_CannotSee` (the window list failed), the answer is `None`.
3. No covering client is `xwayland: true`. X11 apps report X-root
   coordinates, not window-relative ones, and mixing the two conventions is
   how a click lands in the wrong place. OCR serves them as it does now.
4. No two covering windows overlap each other inside `geometry`. With a
   floating window over a tiled one, the tree of the lower window holds text
   that is painted over. OCR could not see it, so neither should the tree.
5. Every covering window has frames (`window_nodes`) and `collect` returns
   at least one node with text **other than** the frame's own name and the
   client's title. A Chrome window without the flag (9 nodes: frames, the
   title and not much else) fails this, so it falls back to OCR instead of
   answering "nothing on screen".
6. The whole thing finishes inside 1.0 s (the `deadline`). A 0.086 s
   whole-desktop walk leaves a margin of more than ten times.

The three ways to get nothing are kept apart, because they look the same
from outside ("tree off" versus "role mismatch"): accessibility off or
unreadable is `desktop() is None` (rule 1); a tree with frames only, whether
Chrome without the flag or a toolkit that saw only `IsEnabled`, is rule 5;
and a role-string mismatch cannot happen, because nothing filters by role
except the password skip. All three fall back to OCR at run time. The live
check prints which of the three it saw per window.

If one covering window has no tree (a terminal beside Chrome), the whole
region goes to OCR. `ponytail:` the upgrade path is to OCR only the windows
without a tree and merge the text. That is worth doing only if the live
check shows mixed regions are common.

The trace records a new phase name, `trace_mod.A11Y = "a11y"` (next to
`CAPTURE`/`OCR`, `trace.py:42-43`), around the walk, so a tree answer and an
OCR answer show up in the trace timings. Only the phase name is recorded,
never text (the intent's constraint).

### What each method returns from the tree

- `_ocr_region`: the node texts in walk order, joined by newlines, cut at
  `OCR_LIMIT` (`:266`) with the same "… [more text on screen, not read]"
  tail. Empty after all that means `None`, so OCR runs, never "no readable
  text".
- `_ocr_words`: one word dict per whitespace-split token, each carrying the
  node's screen rectangle and `conf: 100.0`. `_find_phrase` (`:2972`)
  already takes the centre of the matched run's bounding box, so a run
  inside one node clicks that node's centre. That is the "clicks land at the
  element's real position" outcome, with no change to matching. Actionable
  nodes are listed first, so a tie between the word "Sign in" in body text
  and a `Sign in` button goes to the button.
- `_target_moved` (`:3080`) re-reads its 300×60 box through `_ocr_words`.
  From the tree that re-check costs one walk (tens of ms) instead of about
  187 ms of OCR, and it keeps the #60 protection.

`read_screen`'s tool description (`:1104-1112`, "OCR is imperfect" at `:1109`) gets one clause: text comes from
the app's accessibility tree when it exposes one, and from OCR otherwise. The
model is not told which one answered. The result text is the same shape
either way.

### Packaging

- `nix/package.nix:57` adds `pygobject3` to `dependencies`. `postFixup`
  (`:95`) adds `--prefix GI_TYPELIB_PATH : ${lib.makeSearchPath
  "lib/girepository-1.0" [ at-spi2-core glib.out gobject-introspection ]}`.
  That is the same list ai-mirror uses (`ai-mirror/flake.nix:51`). Checked
  here: the import works with `at-spi2-core` plus `gobject-introspection`
  (`at-spi2-core` alone fails with "Typelib file for namespace 'DBus'"), and
  `Atspi.set_timeout`, `CoordType.WINDOW` and `StateType.SHOWING` all exist
  in at-spi2-core 2.60.6.
- `flake.nix` dev shell (`:61`) and check (`:121`) add `pygobject3` and the
  same `GI_TYPELIB_PATH`. The live-check script needs them, and the check
  proves the wrapper's import path works. The tests still reach no bus.

### README

One section under `### Going after a goal` (`README.md:580`), after the
table whose rows cover `read_screen` and `click_text` (`:586-595`), about 15
lines: what reads from the tree, that Chrome, the Chrome web apps and
Electron apps need `--force-renderer-accessibility` in the user's own
`programs.chromium.commandLineArgs` (merged into a `mkForce` list if they
have one), how to confirm the flag took, the unmeasured cost with the RSS
command, and that nothing changes until then.

## Alternatives rejected

- **An HM option in `nix/hm-module.nix` that sets the Chrome flag.** A
  `mkForce` on p620 drops it silently (Decision 1). It also puts a browser
  setting in a voice package.
- **Oma relaunching Chrome with the flag, or setting `IsEnabled` /
  `ScreenReaderEnabled`.** The intent forbids both: no bus writes, and no
  relaunching.
- **Reusing ai-mirror's `a11y.py` or its CLI.** It enables the bus as a
  side effect (`a11y.py:60-72`, `:152-153`), and it is a separate package
  and process. The intent asks for a small in-repo reader. Its walk, its
  `SHOWING` filter and its `_call` error wrapper are prior art that is
  copied in idea.
- **A separate `read_tree` tool, or a source router in front of the
  tools.** That would be a second path the guards would have to be wired
  into, and a choice the model would have to make. The seam inside `_ocr_*`
  gives both for free.
- **Matching roles against a list (`push button`, `link`, …).** Chromium
  spells them differently, and a miss returns `[]`, which looks like "no
  accessibility". Text matching with an actionable preference needs no role
  list.
- **Reading the whole document once the window guard passes.** Rejected in
  Decision 4.
- **Per-window mixing of tree and OCR in one region.** More code, for a case
  (a Chrome window and a terminal side by side) with no measured frequency.
  The whole-region fallback is correct, only slower.
- **A config switch to turn tree reads off.** Nothing needs it. The tree
  reads what OCR reads, through the same guards, and the fallback covers
  every failure. It can be added if a user asks.
- **Blocking until the flag lands (#73's call).** Rejected in Decision 3.

## Risks

- **Coordinates off by the decoration or scale** (p620, razer). Hyprland's
  `at` is the surface origin. Chrome with `WaylandWindowDecorations` draws
  its own title bar inside the surface, so window-relative extents should
  already include it. A fractionally scaled monitor could make extents
  logical while `at` is also logical, which is consistent, but that is not
  measured. Mitigation: the live check compares every sampled node's
  computed rectangle against OCR of that same rectangle (read-only). The
  `_target_moved` re-check also refuses a click whose text is not at the
  computed point.
- **A tree that lies.** Stale `SHOWING` states, or a web app that keeps
  off-screen DOM `SHOWING`. The rectangle intersection is the second filter,
  and it does not trust states.
- **A hung app blocks a read.** `Atspi.set_timeout(500, …)` plus the 1 s
  deadline bound it, and past the deadline the read falls back to OCR.
- **The threading lock serialises reads.** Two concurrent tool calls wait
  for each other for at most about 1 s. That is acceptable. OCR already
  costs more.
- **Closure growth.** pygobject3 plus at-spi2-core typelibs are a few MiB.
  The wrapper already carries tesseract.
- **Privacy surface.** The tree could still expose text inside the rectangle
  that the pixels do not show, such as a `SHOWING` but visually clipped
  node. This is bounded by the rectangle test. Password fields are excluded
  by role. A field that is sensitive but not a password field (a card
  number in a plain text input) is read, exactly as OCR reads it today.
- **Chrome flag never set.** Then the reader serves only GTK apps and the
  gain is small (Decision 3 accepts this).

## Verification

### Unit tests, `tests/test_a11y.py`, fake trees only

The fakes are small Python classes with `get_name`, `get_role_name`,
`get_process_id`, `get_state_set().contains`,
`get_component_iface().get_extents`, `get_text_iface`, `get_action_iface`
and `get_child_count`/`get_child_at_index`. Tests patch
`omarchy_voice.a11y.desktop` and `subprocess.run`. `grim`/`tesseract` stubs
raise if called when the test expects a tree answer. Each test below fails on
`origin/main` first (no `a11y` module, or OCR is called), and that is shown
in the implementation PR:

1. A GTK-style tree answers `read_screen`: the text is returned and
   grim/tesseract are never run.
2. Frames only (the unflagged Chrome shape: a frame named like the title,
   nothing else) falls back to OCR.
3. `desktop()` is `None` (bus disabled or unreadable) falls back to OCR, and
   no `Set` or `set-property` is attempted. A test with a fake Gio
   connection shows that `IsEnabled=false` returns `None`, and that the only
   method called is `Get`.
4. The guards run first. With a sensitive window in the region, the session
   locked, or a recording running, the read is refused with today's text,
   and `a11y.desktop` is **never called**.
5. Click coordinates are `client.at` plus the extent centre. A button at
   window-relative (10, 20, 80, 30) in a window at (1000, 50) is clicked at
   (1050, 85).
6. Role leniency: `button` and `push button` are both clickable. `password
   text`, `Password_Text` and `passwordtext` are never read or matched.
7. Visibility: a node that is not `SHOWING`, or whose rectangle falls
   outside the capture rectangle, is neither read nor matched.
8. An `xwayland: true` client, overlapping covering windows, or a pid with
   two frames and no title match each fall back to OCR.
9. Passing the node cap or the deadline (a fake clock) falls back to OCR.
10. A region with one window that has a tree and one that has none falls
    back to OCR for the whole region.
11. Actionable preference: body text "Sign in" and a `Sign in` button, and
    the click goes to the button.
12. The trace gets the `a11y` phase name, and no node text appears in the
    trace output.
13. `wait_for(text)` succeeds from the tree, and `_target_moved` re-checks
    through the tree (OCR stub not called).

**Mutation checks** (each is undone after it is run, and the result is
recorded in the PR): delete the guard line above the tree attempt → test 4
fails. Drop the origin add → test 5 fails. Drop the `SHOWING` filter or the
rectangle test → test 7 fails. Accept frames-only trees → test 2 fails. Skip
the `IsEnabled` read → test 3 fails. Remove the password skip → test 6
fails. Treat a partial tree as an answer → test 9 fails.

### Runners and build

With `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` exported:

```
nix develop -c pytest tests -q
nix develop -c python3 -m unittest discover -s tests
nix flake check --no-write-lock-file
```

All three must be green, and the existing tests must be unchanged (none are
edited to pass). One more test imports `gi` and requires `Atspi 2.0`. It
runs in the check's environment, which has the same `GI_TYPELIB_PATH` as
the wrapper, so a missing typelib fails the check rather than turning into
a silent fall back to OCR on the desktop. A plain `nix build .#default` must
succeed as well.

### Live check, `tools/verify_a11y.py` (read-only)

Shaped like `tools/verify_find.py`: it prints and changes nothing. It never
sets a bus property, never clicks, types or launches, and never relaunches
an app. Run as `nix develop -c python3 tools/verify_a11y.py`, it prints:

- `IsEnabled` / `ScreenReaderEnabled` (read), and stops with a clear line if
  accessibility is off;
- for every visible client: class, pid, xwayland, whether
  `--force-renderer-accessibility` is in `/proc/<pid>/cmdline` (for Chrome
  and Electron, including the web-app processes, which answers the
  "web apps inherit the flag" question in Decision 1), node count, whether
  it would answer or fall back and why, and walk time;
- for up to five named, actionable nodes per answering window: the computed
  screen rectangle, and whether OCR of exactly that rectangle (grim +
  tesseract, read-only) contains the node's name. This is the coordinate
  proof, with no click. It runs only where `_capture_refused` passes, so it
  prints no sensitive window's content.

Expected on p620 today: spotifast/Nautilus answer and their rectangles are
confirmed by OCR. Chrome, Chromium and Electron report "frames only, flag
absent → OCR". After the user adds the flag and restarts Chrome, the same run
shows Chrome answering. That run is the acceptance check for the intent's
outcome, and it happens outside this repo's merge.
