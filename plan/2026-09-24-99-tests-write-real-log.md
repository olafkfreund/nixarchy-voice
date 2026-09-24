---
status: approved
issue: 99
spec: spec/2026-09-24-99-tests-write-real-log.md
---

# Plan: redirect every test path to a throwaway home before the package is imported

Base: `origin/main` `35ef252` (940 tests passing). Every `file:line` below was
checked against `35ef252`. This change touches tests only. Nothing in `src/`
or `flake.nix` changes.

## Approved decisions, carried over from the spec

1. **One helper module, `tests/_isolated.py`.** It replaces the
   `sys.path.insert(0, … "src")` line in every test file that imports the
   package. Both runners put `tests/` on `sys.path`, and both import every test
   module before running any test: pytest through its default `prepend` import
   mode (there is no `tests/__init__.py`, no conftest and no pytest config in
   `pyproject.toml`), and `unittest discover -s tests` through its top-level
   directory. The helper is not collected, because both runners only match
   `test*.py`. Rejected alternatives: the dev shell's `shellHook` (it covers
   only `nix develop`, and it would break `python -m omarchy_voice` in the
   shell, `flake.nix:69-70`); sitecustomize (needs `PYTHONPATH`); conftest or
   `tests/__init__.py` (each misses one runner, `tests/test_environment.py:22-25`);
   patching the constants per test (this is the approach that already failed);
   rewriting the constants of modules that are already imported (a test module
   can hold a `from … import` copy that cannot be reached).
2. **HOME and XDG_\* point at a temp dir.** At import the helper creates
   `ROOT = tempfile.mkdtemp(prefix="omarchy-voice-tests-")` and removes it
   through `atexit`. It records the pre-run roots in `REAL`: `Path.home()` plus
   every one of `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `XDG_STATE_HOME`,
   `XDG_DATA_HOME` and `XDG_RUNTIME_DIR` that is set. It then sets `HOME=ROOT`,
   unsets the four `XDG_*_HOME` variables, and sets `XDG_RUNTIME_DIR=ROOT/run`
   (created with mode 0700). With that, every constant at
   `src/omarchy_voice/config.py:16-19`, `:65` and `:94-104` resolves under
   `ROOT`, and so do the copies other modules take of them by value
   (`feedback.py:24`, `capabilities.py:29`). Last, it does the
   `sys.path.insert(0, <repo>/src)` that the test files used to do. The
   variables are set in `os.environ`, so every child process inherits them.
3. **A dead session-bus address.** The helper sets
   `DBUS_SESSION_BUS_ADDRESS=unix:path=ROOT/run/bus`, a socket that never
   exists. It does not unset the variable, because an unset address lets
   libnotify try to autolaunch a bus over `$DISPLAY`. The address is absolute
   (`unix:path=/run/user/1000/bus`), so moving `XDG_RUNTIME_DIR` alone does
   **not** cut `notify-send` off from the desktop. This covers
   `cli.consent_notice` (`cli.py:292-315`, which calls `notify-send` at `:310`
   because `Config.notify` defaults to true at `config.py:449`, and is reached
   from `tests/test_local_engine.py::WiringTests` at `:1032`, `:1040`, `:1048`),
   `feedback.py:121`, and `busctl --user monitor` at `notifications.py:130`.
4. **Guard 1, the path guard.**
   `tests/test_isolation.py::test_every_path_resolves_under_the_temp_root`.
   For every loaded `omarchy_voice.*` module, every absolute `Path` attribute
   that lies under a root in `_isolated.REAL` must also lie under
   `_isolated.ROOT`. The failure message names each `module.ATTR = path`.
5. **Guard 2, the import-order check.**
   `tests/test_isolation.py::test_every_test_file_imports_it_before_the_package`.
   This is a static read of `tests/test_*.py`. Any file that mentions
   `omarchy_voice` must have an `import _isolated` line before the first
   mention. The failure message names the file.
6. **Guard 3, the hard stop at import.** If any `omarchy_voice` module is
   already in `sys.modules` when `_isolated` is imported, the paths are
   already real and the run must not continue. Under pytest
   (`"pytest" in sys.modules`) the helper raises `RuntimeError`, which gives a
   collection error and no tests run (exit 2). Otherwise it writes the reason
   to stderr and calls `os._exit(2)`, because unittest's loader wraps each
   module import in a bare `except:` and would turn a raise into one failed
   "test" while the rest ran with real paths. There is no size check on the
   real `session.log`: it races the daemon and other worktrees (three foreign
   lines in 80 s were measured), and it would have to read the user's file.
7. **`omarchy` reads stay in scope.** `omarchy version` and
   `omarchy commands --json` read machine-wide, read-only data that is the same
   whatever `HOME` is. They are not stubbed. `hyprctl`, `pw-cat` and
   `notify-send` are not stubbed per test either: the redirect already makes
   them unreachable (no compositor socket, `Host is down`, dead bus), and the
   tests already tolerate that, as they do in `nix flake check`.
8. **The 343 fixture lines stay in the real log.** The fix does not touch the
   user's `session.log`. Its 343 lines reading
   `held    not spoken aloud while listening: that did not go through` come
   from the fixture at `tests/test_feedback.py:105`. Cleaning them is left to
   the user, once this change is merged **and** every other worktree has
   rebased onto it: stop the daemon, then
   `grep -vF 'held    not spoken aloud while listening: that did not go through'`
   into a new file, check it, and move it over the old one. No step in this
   plan reads or edits that file.
9. **Q5 is split out as #103** ("Writing a manifest deletes every other
   manifest in the cache", open). It is `capabilities.py:890-891`, a runtime
   change, and out of scope here. With the redirect, the suite's `manifest()`
   only deletes files in the throwaway cache.
10. **Unchanged:** `tests/test_environment.py`, `tests/test_migration_hook.py`
    (it never imports the package), the existing per-test path patches
    (`EngineTestCase`, `test_realtime.py`, `WireTests`,
    `test_notifications.py`, `test_find_apps.py`), and the `tools` inserts at
    `tests/test_router.py:15` and `tests/test_local_engine.py:22`.

## The test files, recounted on `35ef252`

`tests/` has 32 `test_*.py` files. **31 import the package** and get the
change. `test_migration_hook.py` does not. No file mentions `omarchy_voice`
above its `sys.path.insert` line (checked for each file), so replacing that
line satisfies guard 2 everywhere.

The standard line is
`sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))`
at these lines:

| File | Line | File | Line |
| --- | --- | --- | --- |
| test_backend_choice.py | 19 | test_matching.py | 14 |
| test_claude_backend.py | 20 | test_mcp.py | 17 |
| test_compose.py | 15 | test_media.py | 15 |
| test_config.py | 9 | test_notifications.py | 16 |
| test_dispatcher_source.py | 21 | test_planner.py | 16 |
| test_elevenlabs.py | 22 | test_policy.py | 15 |
| test_environment.py | 33 | test_reach.py | 18 |
| test_feedback.py | 20 | test_realtime.py | 24 |
| test_find_apps.py | 22 | test_realtime_wire.py | 24 |
| test_hypr_events.py | 22 | test_screenshot.py | 17 |
| test_input_guard.py | 20 | test_terminal.py | 19 |
| test_keys.py | 15 | test_trace.py | 7 |
| test_listen_local.py | 17 | test_verify_gate.py | 15 |
| test_live_state.py | 15 | test_virtual_input.py | 14 |
| test_local_engine.py | 21 (22 keeps `tools`) | test_web.py | 26 |

That is 30 files. The odd one out is `test_router.py:14`,
`sys.path.insert(0, str(ROOT / "src"))`. `:15` (`tools`) stays, and so does
its local `ROOT` on `:13`, which `:15` still uses.

On `test_environment.py`: its own line is replaced like the rest. Decision 1
leaves its docstring and its warning unchanged.

## Shell used by every run in this plan

Set up **before any test run, including the baseline**. The dead bus address
is exported first, so no step here can reach the real notification daemon,
even before the helper exists. The environment is changed *inside*
`nix develop` so that nix itself keeps the real `HOME`.

```sh
S=$(mktemp -d -t p99-XXXXXX)
export DBUS_SESSION_BUS_ADDRESS=unix:path=$S/dead-bus   # first, before anything runs
mkdir -p "$S/home" "$S/run" && chmod 700 "$S/run"
busctl --user list >/dev/null 2>&1 && { echo "bus reachable: STOP"; exit 1; } || echo "bus dead: ok"
fake() {  # fake home, fake runtime dir, dead bus
  nix develop --no-write-lock-file -c env HOME="$S/home" XDG_RUNTIME_DIR="$S/run" \
    -u XDG_CONFIG_HOME -u XDG_CACHE_HOME -u XDG_STATE_HOME -u XDG_DATA_HOME \
    DBUS_SESSION_BUS_ADDRESS="unix:path=$S/dead-bus" "$@"
}
empty() { test -z "$(find "$S/home" "$S/run" -mindepth 1 -print -quit)" && echo EMPTY || { find "$S/home" "$S/run" -mindepth 1; echo NOT-EMPTY; }; }
```

`$S/dead-bus` is outside `$S/home` and `$S/run`, so `empty` checks only what
the suite could write.

## Steps

0. **Baseline.** `git status` is clean on `fix/99-tests-write-real-log`,
   rebased onto `origin/main` `35ef252` (only docs commits on top). Run the shell
   block above, then `fake python3 -m pytest tests -q` → verify by
   `940 passed`, and `empty` → `NOT-EMPTY`, listing at least
   `.local/state/omarchy-voice/session.log`, `run/omarchy-voice/level` and a
   `.cache/omarchy-voice/manifest-*.md`. That shows the harness can see the bug.
   Then `rm -rf "$S/home"/* "$S/home"/.[!.]* "$S/run"/*`, followed by `empty` →
   `EMPTY`. Also check
   `grep -c 'import _isolated' tests/*.py | grep -v ':0' | wc -l` → `0`.
1. **`tests/_isolated.py`: new file (decisions 2, 3, 6)**, in this order:
   (a) the stop-at-import check, which runs **before** `mkdtemp` so a stopped
   run leaks nothing; (b) build `REAL`; (c) `ROOT` via `mkdtemp` plus an
   `atexit` `shutil.rmtree(ROOT, ignore_errors=True)`; (d) set `HOME`, unset the
   four `XDG_*_HOME`, create `ROOT/run` with mode 0700 and set
   `XDG_RUNTIME_DIR`; (e) set `DBUS_SESSION_BUS_ADDRESS=unix:path=ROOT/run/bus`;
   (f) `sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))`.
   Module docstring: why it exists (#99), why it is a module and not a
   conftest, and that it must be imported before any `omarchy_voice` import.
   → Verify by
   `fake python3 -c 'import sys; sys.path.insert(0,"tests"); import _isolated as i, os; print(i.ROOT, os.environ["HOME"], os.environ["XDG_RUNTIME_DIR"], os.environ["DBUS_SESSION_BUS_ADDRESS"], sorted(map(str,i.REAL)))'`:
   all three variables are under `ROOT`, `REAL` holds `$S/home` and `$S/run`,
   `ROOT` is gone after the process exits, and `empty` → `EMPTY`.
2. **The 31 test files in the table: the `src` insert becomes
   `import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)`**
   (decision 1). Use a literal, line-anchored substitution of the standard
   line in the 30 files, and a hand edit at `test_router.py:14`. Do not
   touch any other line. → Verify by:
   `grep -l '/ "src"))' tests/*.py` → nothing;
   `grep -c '^import _isolated' tests/test_*.py | grep -c ':1$'` → `31`;
   `test_migration_hook.py` is not in `git diff --name-only`;
   `grep -n 'tools"))' tests/test_router.py tests/test_local_engine.py` → the
   two `tools` lines are still there; `fake python3 -m pytest tests -q` →
   `940 passed`; `empty` → `EMPTY`.
3. **`tests/test_isolation.py`: new file with the two guards (decisions 4
   and 5).** It starts with `import _isolated` itself, so guard 2 also checks
   this file. Guard 1 walks `sys.modules` for names that start with
   `omarchy_voice`, and each module's `vars()` for absolute `Path` values
   (not in lists or dicts; decision 4 says attributes). It fails when a value
   is under a `REAL` root and not under `ROOT`. Guard 1 imports
   `omarchy_voice.config`, `.feedback` and `.capabilities` first, so it does not
   depend on which other files were loaded (single-file runs). Guard 2 is a
   plain line scan with no AST, and skips `_isolated.py`, which does not match
   `test_*.py` anyway. → Verify by `fake python3 -m pytest tests/test_isolation.py -q`
   → `2 passed`, and `empty` → `EMPTY`.
4. **Proof, both runners, fake home, dead bus.** → Verify by running each of
   these, then `empty` → `EMPTY` after each one, before the next:
   `fake python3 -m pytest tests -q` → `942 passed`;
   `fake python3 -m unittest discover -s tests` → `Ran 942 tests … OK`.
   Also `ls /tmp | grep -c omarchy-voice-tests-` is the same before and after
   (the `atexit` cleanup works).
5. **Proof that no D-Bus call or desktop tool reaches the real session.** Write
   an audit hook to `$S/audit.py`. It is a verification tool and is never
   committed. It uses `sys.addaudithook`, keeps its events in a list and dumps
   them to `$S/audit.jsonl` at `atexit`. It records:
   - every `subprocess.Popen` event whose executable basename is one of
     `notify-send`, `busctl`, `gdbus`, `dbus-send`, `hyprctl`, `pw-cat`,
     `pw-cli`, `wtype`, `grim`, `wl-copy` or `omarchy`, with the
     `DBUS_SESSION_BUS_ADDRESS` and `XDG_RUNTIME_DIR` the child gets (from the
     `env` argument, or `os.environ` when that is `None`);
   - every write-mode `open`, `os.rename`, `os.replace`, `os.remove`,
     `os.mkdir` or `shutil.rmtree` under the four `omarchy-voice` directories
     named by the **pre-run** `HOME` and `XDG_RUNTIME_DIR`.

   Run it under both runners with the fake home first:
   `fake python3 -c 'import sys; sys.path.insert(0,"'$S'"); import audit, pytest; sys.exit(pytest.main(["tests","-q"]))'`
   and
   `fake python3 -c 'import sys; sys.path.insert(0,"'$S'"); import audit, unittest; unittest.main(module=None, argv=["u","discover","-s","tests"])'`.
   Then, only if both show 0 path events and `empty` → `EMPTY`, run the same
   two commands with the **real** `HOME` and `XDG_RUNTIME_DIR`. Use
   `nix develop --no-write-lock-file -c env DBUS_SESSION_BUS_ADDRESS="unix:path=$S/dead-bus" python3 -c …`,
   so the outer dead bus is still set. → Verify, for all four runs:
   0 path events; at least one `notify-send` spawn is recorded (the consent
   notice fires, because the throwaway home has no marker), and every recorded
   spawn has `DBUS_SESSION_BUS_ADDRESS` equal to
   `unix:path=<ROOT>/run/bus` and `XDG_RUNTIME_DIR` equal to `<ROOT>/run`.
   Neither `$S/dead-bus` nor `/run/user/$UID/bus` appears in any event. As a
   final check, no new "omarchy-voice" notification appeared on the desktop
   during the plan. Two dead addresses stand between the suite and the real
   bus, so it cannot.
6. **Mutation checks: each guard fails when it should.** Each is made in the
   working tree, run with `fake`, and reverted with `git checkout -- <file>` or
   `rm`. After each one, `empty` → `EMPTY` and `git status` shows only the
   planned files. Clean `$S/home` and `$S/run` between runs if a mutation
   writes there on purpose (6c, 6d).
   - **6a, guard 3 (stop at import).** Add a temporary
     `tests/test_aaa_forgot.py` that contains only
     `import omarchy_voice.feedback` after the old `sys.path` line. →
     `fake python3 -m pytest tests -q` exits 2 with `errors during collection`
     and 0 tests run; `fake python3 -m unittest discover -s tests` exits 2
     after printing the reason line, with no test report; `empty` → `EMPTY`.
   - **6b, guard 2 (import order).** Rename the file to
     `tests/test_zzz_forgot.py`. → Under both runners,
     `test_every_test_file_imports_it_before_the_package` fails and names
     `test_zzz_forgot.py`; `empty` → `EMPTY`, because earlier files already
     redirected the environment.
   - **6c, guard 1 (paths).** Remove the `XDG_RUNTIME_DIR` line from
     `_isolated.py`. → `fake python3 -m pytest tests/test_isolation.py -q`
     fails and names `omarchy_voice.config.RUNTIME_DIR`, `LEVEL_FILE`,
     `SOCKET_PATH` and `STATE_FILE`, plus `feedback`'s copies. Repeat with
     the `HOME` line removed instead: it names `STATE_DIR`, `LOG_FILE` and
     `CACHE_DIR`. The writes land in `$S`, never in the real home, because
     `fake` sets the outer environment.
   - **6d, the bus (decision 3).** Remove the `DBUS_SESSION_BUS_ADDRESS` line
     from `_isolated.py` and rerun the fake-home audit from step 5 under
     pytest. → Every `notify-send`/`busctl` spawn now carries
     `unix:path=$S/dead-bus`, not `<ROOT>/run/bus`, so the step 5 check fails.
     The real bus is still unreachable because of the outer dead address.
     That is why the outer address is exported before step 0.
7. **Single-file runs.** → `fake python3 -m pytest tests/test_feedback.py -q`
   and `fake python3 -m unittest discover -s tests -p test_feedback.py` both
   pass, and `empty` → `EMPTY`.
8. **`nix flake check --no-write-lock-file`** → passes. The `tests` check
   (`flake.nix:140-147`, `export PYTHONPATH=$PWD/src HOME=$TMPDIR` at `:144`)
   runs `pytest tests -q` with the helper on top.
9. **Diff review.** `git diff --stat origin/main` → 2 new files
   (`tests/_isolated.py`, `tests/test_isolation.py`) and 31 changed files, one
   line each. There are no changes under `src/`, and `flake.nix` is unchanged.
   Commit: `fix(tests): redirect HOME, XDG and the session bus before the
   package is imported (#99)`.

## Tests

| Command (under `fake`, dead bus exported first) | Expected |
| --- | --- |
| `python3 -m pytest tests -q` | `942 passed`, `$S/home` and `$S/run` empty |
| `python3 -m unittest discover -s tests` | `Ran 942 tests`, `OK`, both dirs empty |
| audit hook, both runners, fake home then real home | 0 path events; every desktop-tool child on `<ROOT>/run` and `<ROOT>/run/bus` |
| mutations 6a-6d | each fails as described; both dirs empty |
| single-file runs | pass |
| `nix flake check --no-write-lock-file` | passes |

## Overlap with open branches

After this change merges, guard 2 fails on any branch whose new or changed
test file imports `omarchy_voice` without `import _isolated` first. That is
intended. The fix is one line: replace the `src` insert with
`import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)`.

- `fix/87-terminal-pane-hint`: being implemented now. It has no test diff
  against `origin/main` yet, and it will very likely add one.
- `fix/96-generic-id-part`, `fix/100-reads-are-not-actions`,
  `fix/88-desktop-suffix-in-id`, `feat/31-a11y-before-ocr`: no test diff yet.
  Any test file they add needs the import.
- `origin/fix/46-sensitive-capture`: adds `tests/test_sensitive_capture.py`
  and modifies `tests/test_reach.py`. Line 18 changes in this plan, so a
  textual conflict is possible. Keep `import _isolated`.

**Landing order: land #99 early**, before those branches. Each of them then
rebases onto it and adds the import to any test file it adds. If one of them
lands first, this branch rebases and converts that branch's new test files in
its own step 2, and the count in the table goes up to match.

## Deviations recorded during implementation

None of these changes an approved spec decision.

- **The shell block's `fake()` does not run as written.** `env` treats the
  first `NAME=VALUE` as the end of its options, so `-u …` after `HOME=…` fails
  with `env: '-u': No such file or directory`. The `-u` options go first:
  `env -u XDG_CONFIG_HOME -u XDG_CACHE_HOME -u XDG_STATE_HOME -u XDG_DATA_HOME HOME=… XDG_RUNTIME_DIR=… DBUS_SESSION_BUS_ADDRESS=… "$@"`.
  The implementer's sandbox also refused shell functions and `source`, so
  `fake`, `empty` and the step 5 runners were small scripts in a fixed scratch
  dir instead. The environment they set is the one above.
- **Step 2's `grep -l '/ "src"))' tests/*.py` lists `tests/_isolated.py`.** That
  is where the line now lives (decision 2, step 1f). No `test_*.py` matches.
- **Step 4's `/tmp` leftover check** also looks in `/tmp/nix-shell.*/`, because
  `nix develop` sets `TMPDIR` there and `mkdtemp` follows it. Both were 0.
- **Step 5's "no `/run/user/$UID/bus`" check** reads the event lines only. The
  dump's first line lists the watched dirs, and with the real
  `XDG_RUNTIME_DIR` one of them is `/run/user/$UID/omarchy-voice`.
- **`origin/main` moved to `5ba5a94` (#87) during implementation.** It changes
  `tests/test_compose.py` and `tests/test_terminal.py`, both already in the
  table, and adds no test file. The branch was rebased onto it (landing-order
  section), so the file count stays 31.
- **Commit subject** follows the lead's wording:
  `fix(tests): redirect every test path to a throwaway home (#99)`.

## Rollback

The code is one implementation commit. `git revert <sha>` restores the 31
`sys.path` lines and removes both new files. No data, config or `src/` code
changes, so nothing else needs undoing. After a revert, the suite writes the
real `session.log` again. If a run is killed hard (for example by guard 3's
`os._exit(2)` after a partial run, or `SIGKILL`), an
`omarchy-voice-tests-*` dir can be left in `$TMPDIR`. It holds only test
output and is safe to `rm -rf`. Branches that already added
`import _isolated` would fail to import after a revert, so they switch back to
the `sys.path` line in the same way.
