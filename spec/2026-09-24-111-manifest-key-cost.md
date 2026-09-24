---
status: approved
issue: 111
intent: intent/2026-09-24-111-manifest-key-cost.md
---

# Spec: a cached manifest must be cheap to find and must name the agents actually installed

Closes #111. Line numbers are in `src/omarchy_voice/capabilities.py` unless
another file is named. Written against `6a9a3f5` and re-verified on main
`b73a3f4`: `capabilities.py`, `planner.py`, `realtime.py`,
`claude_backend.py`, `cli.py` and `tests/test_cache.py` are unchanged between
the two, so every reference below still holds.

## Evidence

Measured on p620 in the dev shell with
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`, over 20 warm runs. The
script is `scratchpad/spec111.py` and is not committed. The "prototype" rows
monkeypatch the design below onto `main`.

| Call, cache hit      | main    | prototype |
| -------------------- | ------- | --------- |
| `manifest()`         | 30.7 ms | 1.1 ms    |
| `command_index()`    | 30.9 ms | 1.1 ms    |
| `_cache_key()`       | ~33 ms (intent) | 1.0 ms |

Re-measured on `b73a3f4`, same shell and host: `_cache_key()` median 31.4 ms
(min 28.1, max 35.4) over 20 runs; `omarchy version` 14.7 ms and
`hyprctl version` 13.7 ms, once each; `shutil.which` for all four agents
0.8 ms median. The numbers above hold.

One correction to the intent: `command_index()` (`:400`) is **not** behind
`lru_cache`. The decorator at `:110` is `_parse_stub`'s. So every
`omarchy_help` lookup also pays both version subprocesses today, and this
change fixes that path too.

## Decisions on the intent's open questions

The intent was approved with "approve all" and its questions unanswered, so
each one is decided here and can be rejected at this gate.

1. **Versions are read once per process.** An upgrade takes effect at the
   next daemon restart. The intent's constraint asks for exactly this: never
   serve old versions "after a rebuild plus daemon restart". The resolved
   binary path is rejected as a proxy. `omarchy` is a script on `PATH` and
   `hyprctl` on a non-NixOS install is `/usr/bin/hyprctl`, so neither path
   moves on an upgrade there. On NixOS the store paths of the Hyprland stub
   and Omarchy's bindings already go into the key through their resolved
   paths (`:1131-1136`), so a rebuild that changes either one already moves
   the key within the same process.
2. **Only the key's version input is memoised, not `manifest()`.** A memoised
   manifest would hide an agent install until restart (outcome 3). The
   on-disk cache already makes a hit cheap once the key is cheap: 1.1 ms in
   total.
3. **Yes, `shutil.which` runs at call time.** The four lookups cost 0.9 ms of
   the 1.1 ms. Sampling them less often would add state and could serve a
   stale list, which is the second bug this issue is about.
4. **`doctor` keeps calling `system_versions()` fresh** (`cli.py:546`).
   `system_versions` itself is not memoised; a new private function wraps it.
   `doctor` is its own short process, so its memo and its fresh read agree,
   and its output is the same as today. It cannot see the daemon's memo, so
   "show when the daemon's view is stale" is not possible from `doctor` and
   is not attempted.

## Design

Stdlib only, all in `capabilities.py`.

1. **The memo.** Add this next to `system_versions` (`:90`):

   ```python
   @functools.lru_cache(maxsize=1)
   def _versions() -> dict[str, str]:
       """system_versions, once per process: a cache hit ran two subprocesses (#111)."""
       return system_versions()
   ```

   `functools` is already imported (`:21`; `:110` is `_parse_stub`'s
   `lru_cache`). The callers must not mutate the
   dict. Both use it read-only.

2. **`_cache_key` (`:1114-1141`)** uses `_versions()` instead of
   `system_versions()` (`:1129`). It also adds the installed agent set to
   the stamp:

   ```python
   stamp += ",".join(b for b, _, _ in CODING_AGENTS if shutil.which(b))
   ```

   This is the same test `coding_agents()` applies (`:1193`), so the key
   moves exactly when that section of the manifest would change. The
   docstring gets one sentence on each change. `CODING_AGENTS` is defined
   below `_cache_key`, but it is only read at call time, so the order does
   not matter.

3. **`manifest()` builds with `_versions()`** (`:1153`), so the bytes and the
   key are built from the same reading. Without this, an upgrade inside a
   running daemon, followed by an agent install (which forces a miss), would
   write new-version text under a key built from the old versions.

4. `command_index()` changes nothing. It gets the cheap key through
   `_cache_key()`. An agent install moves its key too, which costs one
   `omarchy commands --json` rebuild per install. That is rare, and it is
   accepted so that there is one key rather than two.

The on-disk key format changes, so every existing entry misses once and is
then pruned by the LRU bound (`_store`, `CACHE_KEEP`), as with #103. No
clearing is needed, and T7 in `tests/test_cache.py` already covers that.

## Alternatives rejected

- **`lru_cache` on `system_versions` itself.** Rejected: `doctor` would lose
  its fresh read (Q4). The tests that call it with a mocked `_run` (T7,
  `tests/test_cache.py:248`) would also see a value memoised by an earlier
  test.
- **A time-to-live on the versions** (for example 60 s). Rejected: it adds a
  clock and a constant to tune, and a daemon restart already bounds how stale
  the versions can be.
- **Key on the binaries' resolved paths or mtimes** instead of running them.
  Rejected under Q1: this is not trustworthy off NixOS, and NixOS is already
  covered by the stub and bindings paths.
- **Memoise `manifest()` in-process.** Rejected under Q2.
- **Key the agent set into the manifest's filename only**, leaving the
  command index alone. Rejected: it gives two key formulas for one saving
  that happens about once per agent install.

## Risks

- **An Omarchy or Hyprland upgrade without a daemon restart** keeps the old
  versions in the manifest until the restart. Today a manifest cache hit
  would pick up the change on the next turn. On NixOS a home-manager switch
  restarts a changed user service, and a rebuild changes the stub and
  bindings store paths, which still move the key at once. Elsewhere,
  restarting the daemon is the documented step. Accepted: the intent's
  constraint names "rebuild plus daemon restart".
- **A failed first read is kept for the whole process.** If `hyprctl
  version` fails when `_versions()` first runs (the compositor socket is not
  up yet), `"unknown"` is memoised until the daemon restarts; today the next
  turn would recover. The unit starts after `graphical-session.target`, so
  this should be rare. Flagged for the reviewer, not decided here: one option
  is to not memoise a reading that contains `"unknown"`.
- **Where the NixOS claim in Q1 comes from.** The daemon's stub is the first
  candidate in `_stub_path()` (`:141-165`) that exists,
  `/run/current-system/sw/share/hypr/stubs/hl.meta.lua`, which resolves into
  the system's Hyprland store path (on p620 today,
  `hyprland-0.56.0+date=2026-09-23_e368c13`). So a Hyprland upgrade moves the
  key within the process there. The dev shell sets `OMARCHY_VOICE_HL_STUB` to
  this repo's pinned Hyprland, so in the dev shell it does not. Omarchy's
  bindings resolve into the `omarchy-<version>` store path.
- **The memo leaks between tests.** `CacheCase.setUp` in `tests/test_cache.py`
  calls `capabilities._versions.cache_clear()` and registers it again as a
  cleanup. Any other test module that builds the manifest does not assert
  versions, so it is unaffected.
- **Byte stability.** Nothing per-turn goes into the text. The agent list and
  the versions are the only machine inputs, and both are fixed while nothing
  is installed or upgraded. The API prefix cache keeps working.
- Hosts: all. There is no host-specific code.

## Verification

New tests in `tests/test_cache.py`, which uses the real `_cache_key` (not the
`keys()` patch). Each must fail on `main` first:

- `test_a_hit_runs_no_subprocess`: call `manifest()` once, then
  `capabilities._run.reset_mock()`, then call `manifest()` again.
  `_run.call_count == 0`. On main it is 2.
- `test_command_index_hit_runs_no_version_subprocess`: the same for
  `command_index()`. On main it is 2.
- `test_an_installed_agent_moves_the_key`: patch `capabilities.shutil.which`
  so it finds `{claude}` and then `{claude, codex}`. The two keys differ. On
  main they are equal.
- `test_installing_an_agent_rebuilds_the_manifest`: the same patch, end to
  end. The second `manifest()` contains `codex exec` and the first does not.
  Removing an agent works the other way round.

These guard the constraints and pass on main, and they must keep passing:

- `test_doctor_versions_stay_fresh`: `_run` answers `omarchy version` with
  `1.0` on the first `system_versions()` call and `2.0` on the second (each
  call also runs `hyprctl version`, so the side effect has four entries), and
  `system_versions()["omarchy"]` returns each value in turn. This turns red if
  someone memoises `system_versions`.
- The existing T2 (a template, stub or bindings change moves the key at mtime
  1), T1/T6 (keys coexist and there is no eviction) and T7 (an old entry is not
  served) stay green.

Mutation checks, each of which must turn a test red:

- Drop `@lru_cache` from `_versions`: the two "no subprocess" tests fail.
- Drop the agent line from the stamp: both agent tests fail.
- Put `lru_cache` on `system_versions` instead: the doctor test fails.

Timing: re-run `scratchpad/spec111.py`, adapted to the real code. A
`manifest()` hit should be around 1 ms against ~31 ms on main. Record the
numbers in the PR, as the issue asks.

Runs, all green, with `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`:

- `nix develop -c pytest tests -q`
- `nix develop -c python3 -m unittest discover -s tests`
- `nix flake check --no-write-lock-file`

`CacheCase` mocks `_run` and uses a temp `CACHE_DIR`, so no test runs a
version subprocess or touches `~/.cache/omarchy-voice`.


## Approved with (2026-09-24)

The owner approved this spec together with one decision on the flagged risk:

- **A version reading that contains `"unknown"` is not memoised.** The next call reads it again, so a `hyprctl` that failed while the compositor was starting recovers on the next turn, as it does today.
