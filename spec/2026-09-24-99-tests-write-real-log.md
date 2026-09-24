---
status: approved
issue: 99
intent: intent/2026-09-24-99-tests-write-real-log.md
---

# Spec: the test suite must not touch the user's real state, runtime or cache files

## The intent's open questions, answered

The intent was approved without answers to its five questions, so each one is
decided here, with the evidence. Any of these decisions can be rejected at
this gate.

### How the answers were measured

Everything below was prototyped in a scratch copy of the tree, first at
`72f9ab6` (this branch's base) and again at `origin/main` `35ef252`, which
merged #94 after this branch was cut. Nothing was committed from the scratch
copy. Two instruments were used:

- **An audit hook.** A `sys.addaudithook` module, loaded before the runner in
  the same process. It records every `open` for writing, `rename`, `remove`,
  `mkdir` and `rmtree` under the four omarchy-voice directories named by the
  *pre-run* `HOME` and `XDG_RUNTIME_DIR`. It also records every `Popen` of a
  desktop tool (`hyprctl`, `pw-cat`, `omarchy`, `notify-send`, `wtype`,
  `grim`, and others), with the `XDG_RUNTIME_DIR` and
  `DBUS_SESSION_BUS_ADDRESS` the child gets. The runners were started as
  `pytest.main(["tests", "-q"])` and as
  `unittest.main(module=None)` with `discover -s tests`, which is what
  `python3 -m unittest discover -s tests` does.
- **A fake home.** Each suite ran with `HOME` and `XDG_RUNTIME_DIR` pointed at
  empty scratch directories, and the `XDG_*_HOME` variables unset. Nothing
  else on the machine writes there, so what is in those directories after the
  run was put there by the suite. The real `session.log` cannot give that
  proof: while this spec was written, other agents' test runs appended a fake
  `held` line to it every 40 s (13:37:52, 13:38:32 and 13:39:12).

Control. `main` unchanged, fake home, both runners (930 tests, all passing):
63 audited events. The fake home ends up holding
`run/omarchy-voice/level`, `.cache/omarchy-voice/manifest-f34bd3625e05ef1f.md`,
`.local/state/omarchy-voice/session.log` and
`.local/state/omarchy-voice/notifications-off-noticed`. The same four spawns
happen under both runners: 37 × `hyprctl`, 4 × `omarchy`, 1 × `pw-cat` and
1 × `notify-send`, all with the caller's runtime directory. So the harness sees
the problem. It also finds two writes the intent did not list, below.

With the redirect in *Design*, fake home: 0 events. The fake home is empty
afterwards (not even the directories exist) under **both** runners, at both
commits. 932 tests at `72f9ab6`, 942 at `35ef252`, all passing. Every child
process gets the throwaway runtime dir and bus.

With the redirect, **real** home, while the daemon (pid 220215) was running,
both runners: 0 events under the real directories. A before/after snapshot of
the real directories did change: `session.log` grew, `level` was rewritten and a
manifest was replaced. That is the daemon and other worktrees' unfixed suites.
The audit hook shows none of it came from this process, and the fake-home runs
show the fixed suite writes nothing at all. No real file was edited or removed
to run any of this.

### Two writes the intent missed

- **`notifications-off-noticed`, a line in `session.log`, and a real
  desktop notification.** `tests/test_local_engine.py::WiringTests`
  (`:1032`, calling `cli.cmd_run` at `:1040` and `:1048`) reaches
  `cli.consent_notice` (`src/omarchy_voice/cli.py:196`, `:292-315`). That
  function appends to `LOG_FILE`, creates the marker, and runs `notify-send`
  when `Config.notify` is set, which it is by default (`config.py:449`). On the
  author's machine the marker already exists, so it returns early, which is
  why the intent's audit missed it. On a machine with no marker, and on every
  run once `HOME` is redirected (the throwaway home has no marker), it
  notifies.
- **The session bus is not under `XDG_RUNTIME_DIR`.**
  `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus` is an absolute
  address. Moving `XDG_RUNTIME_DIR` therefore does **not** cut `notify-send`
  off from the real desktop. Measured: `busctl --user list` with only the XDG
  variables redirected still lists the notification daemon, and with the
  address pointed at a missing socket it fails with `No such file or directory`.
  The four control and prototype runs made before this was found each spawned
  `notify-send` on the real bus. They probably put a "no longer records
  notification bodies" notification on the author's desktop, once per run. The
  design sets a dead bus address (see Design, point 1).

### 1. Scope: the three writes only, or the live processes too?

**Decided: both, through the same redirect, with no per-test stubs.** The
evidence is that the processes reach the desktop only through the runtime
directory and the session bus, and the redirect moves both:

| Child | Real env | Redirected env |
| --- | --- | --- |
| `hyprctl version` | `Hyprland 0.56.0 …`, rc 0 | `Couldn't connect to <tmp>/hypr/<sig>/.socket.sock`, rc 4 |
| `pw-cli info 0` (the socket `pw-cat` uses) | connects | `failed to connect: Host is down`, rc 255 |
| session bus (`notify-send`) | reachable | reachable if only the XDG variables move; fails once `DBUS_SESSION_BUS_ADDRESS` is dead |
| `omarchy version`, `omarchy commands --json` | output | identical output, rc 0 |

So `pw-cat` plays nothing, `hyprctl` sees no compositor, and `notify-send`
posts nothing. All 37 `hyprctl` calls and the one `pw-cat` now fail in the
child, and the suite still passes under both runners. The tests already
tolerate that, as they do in `nix flake check`, which has no compositor.
`WAYLAND_DISPLAY=wayland-1` is relative to the runtime dir, so it is cut off
too. No test spawns `wtype`, `grim` or `wl-copy` (audit: 0).

`omarchy` is left alone. It reads the installed Omarchy version and the
command list, which are machine-wide and read-only, and give the same output
whatever `HOME` is. It does not read session state. Stubbing it is a separate
test-quality question, not a question of touching the user's files.

### 2. Where the redirect lives

**Decided: a module every test file imports first (`tests/_isolated.py`). No
change to `flake.nix`.**

Why not the dev shell. It covers only runs inside `nix develop`. A bare-shell
`python3 -m unittest discover -s tests`, which `tests/test_environment.py`
exists to warn about, would still write the real log. It would also break the
thing the dev shell is for. `flake.nix:69-70` says the shell sets the wrapper's
environment "so `python -m omarchy_voice` in the shell behaves like the
installed binary". A redirected `HOME` would give that hand run no config, no
`env` file and no API keys. The approver therefore does not need to decide
whether the dev shell may set `HOME`/`XDG_*`, because it will not.

Why not a sitecustomize. It loads only if its directory is on `sys.path` at
interpreter start. Neither runner puts `tests/` there before start-up, so it
would need `PYTHONPATH`, which is again the dev shell.

Why not conftest or `tests/__init__.py`. `tests/test_environment.py:22-25`
records it, and it still holds: conftest is pytest-only, and
`discover -s tests` never loads `tests/__init__.py`.

Why a module works under both. Both runners put `tests/` on `sys.path` before
they import a test file: pytest's default `prepend` import mode does it for a
rootdir with no `__init__.py`, and `unittest discover -s tests` does it for
the top-level directory. Both import every test module before they run any
test. Every test file already has one line that must come before its first
`omarchy_voice` import: `sys.path.insert(0, … / "src")`. 30 of the 32 files use
it in exactly the same form, and `test_router.py:14` uses it as
`str(ROOT / "src")`. `test_migration_hook.py` never imports the package. The
redirect replaces that line, so the helper runs at the one point already known
to be early enough.

### 3. The guard

**Decided: no check on the real log's size. There are three cheaper guards
that never look at the user's files.**

The issue's check, "the real log does not change size during a run", races the
daemon and every other worktree's suite. That was measured above: three
foreign lines landed in 80 s. It would also have to read the user's file to
decide.

- **A path guard, as the intent suggested:**
  `tests/test_isolation.py::test_every_path_resolves_under_the_temp_root`. For
  every loaded `omarchy_voice.*` module, every absolute `Path` attribute that
  lies under the pre-run `HOME`, an `XDG_*_HOME` or the pre-run
  `XDG_RUNTIME_DIR` must lie under the throwaway root instead. This covers the
  copies-by-value (`feedback.py:24`, `capabilities.py:29`, …), not just
  `config.py`. Proven to bite: with the `XDG_RUNTIME_DIR` line removed from the
  helper, it fails and names `omarchy_voice.config.RUNTIME_DIR = …/run/omarchy-voice`
  and the rest.
- **An import-order guard:**
  `test_every_test_file_imports_it_before_the_package`. This is a static read
  of `tests/test_*.py`. Any file that mentions `omarchy_voice` must have
  `import _isolated` before the first mention. Proven: an added
  `test_zzz_forgot.py` fails it under both runners, and because the env was
  already redirected by earlier files, that run wrote nothing.
- **A stop at import.** If `omarchy_voice` is already in `sys.modules` when
  `_isolated` is imported, the paths are already real and the run must not go
  on. Under pytest the helper raises: a collection error, and pytest runs
  nothing ("Interrupted: 32 errors during collection", exit 2). Under unittest
  it writes the reason to stderr and calls `os._exit(2)`, because unittest's
  loader wraps each module import in a bare `except:`. A raise, even
  `SystemExit`, becomes one failed "test" there while every other module still
  runs with real paths. Proven with a forgetful `test_aaa_forgot.py` that sorts
  first: both runners exit 2 and the fake home stays empty.

Together these meet the intent's outcome, "A new test cannot bring the problem
back by accident": a forgetful file either stops the run before any test runs,
or fails the suite in a run that wrote nothing.

### 4. The fake lines already in the log

**Decided: the fix does not touch the file, and the spec only recommends.**
Observed read-only: the real `session.log` has 3023 lines. 343 of them are the
test's line, `held    not spoken aloud while listening: that did not go through`,
and the first is at line 131 (2026-09-11 11:33:17). All 343 `held` lines are
that exact text, so no real `held` event would be lost by removing them. That
string is the fixture at `tests/test_feedback.py:105`.

**Recommendation for the author, not done here:** after this fix is merged
**and** every other worktree has rebased onto it (until then they keep adding
lines), stop the daemon and drop lines with that exact text. For example,
`grep -vF 'held    not spoken aloud while listening: that did not go through'`
into a new file, checked and then moved over the old one. The consent-notice
line has not been written by tests on this machine (the marker predates them),
so it needs no cleanup.

### 5. `manifest()` deleting other keys' caches

**Decided: out of scope, and it deserves its own issue.** With the redirect, the
suite's `manifest()` deletes only the throwaway cache, so #99 no longer
triggers it. The remaining fight is between two real installs, such as a
worktree run by hand beside the daemon (`capabilities.py:890-891`). That is a
runtime behaviour change, which this intent rules out. Recommended: file it
separately. This spec does not file it.

## Design

A test-only change. Nothing in `src/` or `flake.nix` changes.

1. **New `tests/_isolated.py`**, as prototyped. It is not collected, because
   both runners match `test*.py` only. At import it does the following:
   - Stops the run if any `omarchy_voice` module is already imported: raises
     under pytest, and calls `os._exit(2)` with the reason on stderr otherwise
     (see Q3).
   - Records the pre-run roots in `REAL`: `Path.home()`, plus every
     `XDG_CONFIG/CACHE/STATE/DATA_HOME` and `XDG_RUNTIME_DIR` that is set.
   - Creates `ROOT = tempfile.mkdtemp(prefix="omarchy-voice-tests-")` and
     removes it through `atexit`.
   - Sets `HOME=ROOT`, unsets the four `XDG_*_HOME` variables, and sets
     `XDG_RUNTIME_DIR=ROOT/run` (mode 0700). With those settings every constant
     at `config.py:16-19`, `:65` and `:94-104` resolves under `ROOT`.
   - Sets `DBUS_SESSION_BUS_ADDRESS=unix:path=ROOT/run/bus`, a socket that does
     not exist. The address is not unset: unset, libnotify may autolaunch a
     bus over `$DISPLAY`.
   - Does the `sys.path.insert(0, <repo>/src)` that the test files did.

   Because the variables are set in `os.environ`, every child process inherits
   them. That is what cuts `hyprctl`, `pw-cat` and `notify-send` off from the
   desktop (Q1).
2. **Every test file that imports the package** (31 of them): the line
   `sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))`
   becomes `import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)`.
   In `tests/test_router.py:14`, `sys.path.insert(0, str(ROOT / "src"))` is
   replaced the same way. Its `tools` insert on `:15` stays.
   `tests/test_local_engine.py:22` also keeps its `tools` insert.
   `test_migration_hook.py` is untouched.
3. **New `tests/test_isolation.py`**: the path guard and the import-order guard
   from Q3.
4. **Unchanged:** the existing per-test patches (`EngineTestCase`,
   `test_realtime.py`, `WireTests`, `test_notifications.py`,
   `test_find_apps.py`). They still give each test its own directory inside
   `ROOT`, and the assertions that read `feedback.LOG_FILE` keep working. The
   prototype passed them all unchanged. `tests/test_environment.py` is
   unchanged.

## Alternatives rejected

- **`HOME`/`XDG_*` in the dev shell's `shellHook`.** Covers only `nix develop`,
  breaks `python -m omarchy_voice` in the shell, and leaves the bare-shell run
  writing (Q2).
- **sitecustomize, conftest, or `tests/__init__.py`.** Each one misses at
  least one runner, or needs the dev shell (Q2).
- **Patching each path constant per test, the way `test_realtime.py` does.**
  This is what failed: nine patch blocks and `LEVEL_FILE` is still missed. A
  new module that copies a constant would need every test to learn about it.
  An env redirect before import covers every copy at once.
- **Rewriting the path constants of already-imported modules** instead of
  stopping. It works for module attributes, but a test module can hold a
  `from … import LOG_FILE` copy that it cannot reach. A loud stop is simpler
  and honest.
- **A size check on the real `session.log`.** It races the daemon and other
  worktrees, measured at three foreign lines in 80 s (Q3).
- **Stubbing `hyprctl`/`pw-cat`/`notify-send` per test.** The redirect already
  makes them unreachable, and 40 call sites would need stubbing for nothing
  (Q1).

## Risks

- **The unittest stop is `os._exit(2)`.** It skips `atexit`, so one
  `omarchy-voice-tests-*` temp dir leaks, and it prints no test report, only
  the reason line. That is acceptable for a run that would otherwise write the
  user's files. It happens only when a test file breaks the import rule.
- **`--import-mode=importlib`** (not configured today) would not put `tests/`
  on `sys.path`. `import _isolated` would then raise `ModuleNotFoundError`:
  loud, not silent.
- **A test that relied on the user's real config or desktop** would now fail.
  None does: every such test already passes under `nix flake check`
  (`HOME=$TMPDIR`, `flake.nix:144`, no compositor), and the prototype passed in
  full under both runners.
- **Open branches that add tests.** `origin/fix/46-sensitive-capture` adds
  `tests/test_sensitive_capture.py` and modifies `tests/test_reach.py`. After
  this fix merges, the import-order guard will fail on that branch until its
  new file uses `import _isolated`. That is intended, and it is a one-line
  change. If it merges first, this branch must convert that file too.
  `fix/87-terminal-pane-hint`, `fix/88-desktop-suffix-in-id`,
  `fix/96-generic-id-part`, `fix/100-reads-are-not-actions` and
  `feat/31-a11y-before-ocr` have no test changes against `origin/main` yet. Any
  of them that adds a test file after this merges will be told the same way.
  There are no open PRs.
- **This branch is behind `origin/main`.** It is based on `72f9ab6`, and
  `origin/main` is `35ef252` (#94 changed `test_backend_choice.py`,
  `test_claude_backend.py` and `test_verify_gate.py`). All three still use the
  standard `sys.path` line (`:19`, `:20`, `:15` at `35ef252`), and the
  prototype passed on `35ef252` (942 tests, both runners). The plan should
  rebase first.
- **Other worktrees keep writing** until they rebase onto the fix. This is not
  a risk of the change, but it means the author's log keeps gaining fake lines
  for a while (Q4).

## Verification

1. **Both runners, fake home.** Run with `HOME` and `XDG_RUNTIME_DIR` set to
   empty temp dirs, inside `nix develop`:
   `python3 -m pytest tests -q` and `python3 -m unittest discover -s tests`.
   Both pass. The fake dirs are empty afterwards: nothing created, nothing
   written.
2. **Both runners, real home, with the audit hook.** Load the scratchpad
   audit-hook module in-process, as in this spec. Result: 0 write, rename,
   remove or mkdir events under the real `~/.local/state`, `~/.cache`,
   `~/.config` and runtime `omarchy-voice` dirs, and every desktop-tool child
   gets the throwaway `XDG_RUNTIME_DIR` and bus. The hook is a verification
   tool and is not committed.
3. **The guards bite, fake home, both runners:**
   - (a) A temporary `tests/test_aaa_forgot.py` that imports
     `omarchy_voice.feedback` without `_isolated`: exit 2, zero tests run.
   - (b) The same file renamed `test_zzz_forgot.py`:
     `test_every_test_file_imports_it_before_the_package` fails, and nothing is
     written.
   - (c) The helper's `XDG_RUNTIME_DIR` line removed: the path guard fails and
     names the runtime paths.

   Revert all three.
4. **`nix flake check`** still passes. The helper sits on top of
   `HOME=$TMPDIR`.
5. **Single-file runs** still work, for example `pytest tests/test_feedback.py`
   and `python3 -m unittest discover -s tests -p test_feedback.py`, because
   each file imports the helper itself.
