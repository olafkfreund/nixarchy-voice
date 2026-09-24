---
status: draft
issue: 99
author: olafkfreund
---

# Intent: the test suite must not touch the user's real state, runtime or cache files

Closes #99.

## Problem

Running `nix develop -c pytest tests -q` writes into the live install's files.
The issue named one of them, the session log. Measured on `main` (72f9ab6),
it is three files, plus directory creation and real processes started:

| Real path | What the suite does | Test that does it | Why |
| --- | --- | --- | --- |
| `~/.local/state/omarchy-voice/session.log` | appends `held    not spoken aloud while listening: that did not go through` | `tests/test_feedback.py::SpeakingIntoAnOpenMicTests::test_an_open_mic_holds_it_back` | `Feedback.speak` logs at `feedback.py:134` to `LOG_FILE` (`feedback.py:208-210`). `_feedback()` at `tests/test_feedback.py:90-92` builds `Feedback` with no path patched |
| `$XDG_RUNTIME_DIR/omarchy-voice/level` (the orb overlay reads it) | writes `level.tmp`, then renames it over `level` | 9 tests in `tests/test_realtime.py`: `MicrophoneGateTests` (4, class at :819) and `SilenceGateTests` (5, class at :934) | `Feedback.level` writes `LEVEL_FILE` (`feedback.py:110-112`). `test_realtime.py` patches `LOG_FILE`, `STATE_FILE`, `STATE_DIR`, `RUNTIME_DIR` nine times (:78, :373, :454, :534, :621, :839, :948, :1032, :1094) but never `LEVEL_FILE` |
| `~/.cache/omarchy-voice/manifest-<key>.md` | **deletes** the daemon's cached manifest and writes its own | `tests/test_realtime_wire.py::WireTests::test_control_socket_toggles_and_quits` | `capabilities.manifest()` globs and unlinks every `manifest-*.md`, then writes a new one (`capabilities.py:872-892`). The key covers `capabilities.py`'s own mtime (`:860-863`), so a worktree's key never matches the installed daemon's. `WireTests` (`tests/test_realtime_wire.py:91-93`) patches `feedback`, `session` and `realtime` paths, not `capabilities.CACHE_DIR` |
| `~/.local/state/omarchy-voice/`, `$XDG_RUNTIME_DIR/omarchy-voice/`, `~/.cache/omarchy-voice/` | `mkdir(exist_ok=True)`. Nothing changes here, but on a machine with no install the suite creates them | `test_elevenlabs.py` (7), `test_feedback.py` (5), `test_local_engine.py` (4), `test_realtime.py` (2), `test_realtime_wire.py` (cache dir, 2) | `Feedback.__init__` (`feedback.py:76-77`), `manifest()` (`capabilities.py:872`) |

Processes started on the real desktop (read-only, but they read live state and
one of them makes sound):

- `pw-cat --playback` with three bytes of audio, from `test_realtime.py::RealtimeSessionTests::test_audio_bytes_reset_between_items` (:275).
- `hyprctl -j workspaces|monitors|clients|activewindow` from 6 tests in `test_realtime.py` and 2 in `test_realtime_wire.py`. `hyprctl version` and `omarchy version` from `test_realtime_wire.py`.
- `omarchy commands --json` from `test_config.py::UnreadableSourceTests::test_a_missing_omarchy_path_is_named`.

Left alone, checked: `notifications.jsonl` and `notifications-off-noticed`
(`test_notifications.py:96`, `:154` patch them), `notes.json` (not written),
`ids-migrated` (`test_migration_hook.py:44-49` runs the hook with a temp
`HOME` and `XDG_STATE_HOME`), `state.json`, `control.sock`, the command-index
cache (`test_find_apps.py:184` patches `CACHE_DIR`), and everything in
`~/.config/omarchy-voice` (`safety-id` is patched by `WireTests`). The whisper
and piper models are only read, through env vars.

Nothing else writes the log. `test_elevenlabs.py` also builds a bare
`Feedback`, which the issue named, but its fallback tests replace `mouth.log`
(`tests/test_elevenlabs.py:313`), so it only creates directories.

Why it matters: the daemon, `omarchy-voice log` and the #71 router's
`eval_router.py` all read that log, and now it holds events that never
happened. The orb shows a level nobody spoke. The daemon's manifest cache gets
deleted on every run.

### How this was measured

Nothing was written from outside the suite. Two methods:

1. A size, mtime and sha256 snapshot of `~/.local/state/omarchy-voice`,
   `~/.cache/omarchy-voice`, `~/.config/omarchy-voice` and
   `/run/user/1000/omarchy-voice`, taken before and after a full suite run
   (930 passed). Diff: `session.log` +348 bytes, `level` rewritten,
   `manifest-c64ed5a52ad58950.md` removed and `manifest-ac5f0be499ebc282.md`
   added (identical content). The daemon (pid 220215) was running and other
   agents' test runs were writing too, so the snapshot alone does not say
   which process did what. Of the four lines it gained, one was
   written 2 s after this run ended.
2. So attribution comes from a `sys.addaudithook` pytest plugin, loaded from
   the scratchpad with `-p`, and not committed. It records every `open` for
   writing, rename, remove and mkdir under those directories, plus every
   `subprocess.Popen` of a desktop tool, tagged with the running test's node
   id. The table above is its output. It sees only this process, but no test
   starts a Python child that imports the package.

## Proposed outcome

- A full run under `pytest` **and** under `python3 -m unittest discover -s tests`
  leaves every file under the four directories above byte-identical,
  and creates none of them.
- A test that forgets to redirect a path fails in the suite, and does not
  silently write to the user's files. A new test cannot bring the problem back
  by accident.
- The session log only holds events that happened.

## Affected users and systems

- Anyone running the suite on a machine with omarchy-voice installed: the
  author's p620 and razer, and every agent worktree on them.
- The running daemon: its manifest cache and orb level.
- Readers of `session.log`: `omarchy-voice log`, `eval_router.py` (#71).
- `nix flake check` is **not** affected. It runs `pytest` with
  `HOME=$TMPDIR` (`flake.nix:144-145`) in a sandbox with no `XDG_*`, so all
  paths resolve under the build's temp dir. The problem is only in the dev shell
  (`flake.nix:74-77` sets no paths).

## Constraints

- Must work under both runners. `tests/test_environment.py:23-26` records that
  `unittest discover -s tests` loads neither `conftest.py` nor
  `tests/__init__.py`. Neither exists today. A fix placed in a conftest alone
  covers pytest only.
- The paths are module constants resolved at import from `XDG_*` and `HOME`
  (`config.py:16-19`, `:55-65`, `:94-104`). Each module copies them by value
  at its own import: `feedback.py:24`, `capabilities.py:29`,
  `notifications.py:38`, `session.py:18`, `realtime.py:39`. Two consequences.
  Setting env vars works only before the first `omarchy_voice` import. And
  patching `config.X` does not reach the copies, so each consumer module has
  to be patched.
- Existing per-test patches (`EngineTestCase` at `tests/test_local_engine.py:149-160`,
  which patches all five `feedback` names; `test_realtime.py`,
  `test_realtime_wire.py`, `test_notifications.py`, `test_find_apps.py`) and
  assertions that read `feedback.LOG_FILE` (`test_local_engine.py:693`, `:809`,
  `:816`, `:931`, `:1020`) must keep working.
- Never remove or rewrite the user's real files while fixing or verifying this.
- No change to runtime behaviour of the daemon. This is a test-only fix unless
  the approver decides otherwise (see open questions).

## Open questions

1. **Scope.** Is this only the three writes and the directory creation? Or
   should the `pw-cat` playback and the live `hyprctl`/`omarchy` reads go too?
   They write nothing, but a test that plays sound or depends on the current
   desktop is not isolated.
2. **Where the redirect lives.** It has to be one place that both runners run
   before any `omarchy_voice` import. Options are a module every test file
   imports, env vars set in the dev shell (`flake.nix` `shellHook`, as the flake
   check already does with `HOME`), or both. The spec picks one, so the
   approver only needs to say whether the dev shell may set `HOME`/`XDG_*` for
   everything run inside it. That includes `python3 -m omarchy_voice` by hand,
   which would then stop seeing the real config.
3. **Guard test.** The issue asks for a test that fails if the real log changes
   size during a run. That races the live daemon, which writes the same log
   (see the four-line diff above). Would a guard that fails if any
   `omarchy_voice` path resolves outside a temp dir do instead? It never looks
   at the user's files.
4. **The polluted log.** `session.log` already holds invented `held` lines from
   earlier runs (at least 3000 → 3001 in the issue, and more today). Leave them
   alone, or should the fix ship a note on how to spot them? This intent does not
   propose touching the file.
5. **`manifest()` deleting other keys' caches** (`capabilities.py:890-891`) is
   also how two installs with different keys (a worktree run by hand next to
   the daemon) fight over one cache. Out of scope here, or a separate issue?
