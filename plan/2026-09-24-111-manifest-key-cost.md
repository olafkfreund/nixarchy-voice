---
status: approved
issue: 111
spec: spec/2026-09-24-111-manifest-key-cost.md
---

# Plan: a cached manifest must be cheap to find and must name the agents actually installed

Closes #111. Base: `origin/main` `b73a3f4` (1126 tests collected, 13 of them
in `tests/test_cache.py`). Every `file:line` below was checked against
`b73a3f4`. Line numbers are in `src/omarchy_voice/capabilities.py` unless
another file is named. Only `src/omarchy_voice/capabilities.py` and
`tests/test_cache.py` change. `cli.py`, `planner.py`, `realtime.py`,
`mcp_server.py` and `flake.nix` do not change.

## Approved decisions, carried over from the spec

1. **The versions are read once per process.** An Omarchy or Hyprland
   upgrade takes effect at the next daemon restart, which is what the
   intent's constraint asks for ("rebuild plus daemon restart"). Using the
   binary's resolved path as a proxy was rejected: `omarchy` is a script on
   `PATH`, and off NixOS `hyprctl` is `/usr/bin/hyprctl`, so neither path
   moves on an upgrade. On NixOS the Hyprland stub and Omarchy's bindings
   already go into the key through their resolved paths (`:1131-1136`), so a
   rebuild moves the key within the same process there.
2. **Only the key's version input is memoised, not `manifest()`.** A memoised
   manifest would hide an agent install until restart. The on-disk cache
   already makes a hit cheap once the key is cheap (1.1 ms against 30.7 ms,
   measured for the spec).
3. **`shutil.which` runs at call time**, on every key. The four lookups cost
   about 0.9 ms. Sampling them less often would add state and could serve a
   stale agent list.
4. **`doctor` keeps calling `system_versions()` fresh** (`cli.py:546`).
   `system_versions` (`:90`) is not memoised. A new private function wraps
   it. `doctor` cannot see the daemon's memo, so showing a stale daemon view
   there is not attempted. `doctor`'s output does not change.
5. **The memo is a private `_versions()` next to `system_versions` (`:90`).**
   Stdlib only, all in `capabilities.py`. Callers treat the returned dict as
   read-only (both callers already do).
6. **`_cache_key` (`:1114-1141`)** calls `_versions()` instead of
   `system_versions()` (`:1129`). It also adds the installed agent set to
   the stamp:
   `stamp += ",".join(b for b, _, _ in CODING_AGENTS if shutil.which(b))`.
   This is the same test `coding_agents()` applies (`:1193`), so the key moves
   exactly when that manifest section would change. `CODING_AGENTS`
   (`:1173`) is defined below `_cache_key`, but it is read at call time, so
   that is fine. The docstring gets one sentence for each change.
7. **`manifest()` builds its text with `_versions()`** (`:1153`), so the bytes
   and the key come from the same reading. Otherwise, an upgrade inside a
   running daemon followed by an agent install (which forces a miss) would
   write new-version text under a key built from the old versions.
8. **`command_index()` (`:400`, key at `:410`) is not edited.** It gets the
   cheap key through `_cache_key()`. An agent install also moves its key, so
   it rebuilds `omarchy commands --json` once per install. That cost is
   accepted to keep a single key formula.
9. **The on-disk key format changes.** Every existing entry misses once and is
   then pruned by the LRU bound (`_store` `:381`, `CACHE_KEEP = 8` `:367`),
   as with #103. No clearing is needed. The existing T7 (`tests/test_cache.py:245`)
   covers this.
10. **Approved with (2026-09-24): a reading that contains `"unknown"` is not
    memoised.** The next call reads the versions again, so a `hyprctl` that
    failed while the compositor was starting recovers on the next turn, as it
    does today.
11. **Test isolation.** `CacheCase.setUp` (`tests/test_cache.py:37`) resets
    the memo, and the reset is undone at cleanup. No other test module
    asserts versions.
12. **Rejected, and not to be reintroduced:** `lru_cache` on
    `system_versions` itself (doctor would lose its fresh read, and T7's
    `_old_key` (`tests/test_cache.py:246-254`) would see a value memoised by an
    earlier test). A time-to-live on the versions. Keying on the binaries'
    paths or mtimes instead of running them. Memoising `manifest()` in-process.
    Putting the agent set into the manifest's filename only.

## Spec ambiguities, resolved here (flagged for the reviewer)

- **A. `lru_cache` cannot skip a reading.** The spec's design is
  `@functools.lru_cache(maxsize=1)` on `_versions`, and its test isolation is
  `_versions.cache_clear()`. Decision 10, added at approval, needs a memo
  that does not store a reading containing `"unknown"`, and `lru_cache`
  always stores. Resolution: a module global `_VERSIONS: dict[str, str] |
  None = None`. `_versions()` returns it if it is set. Otherwise it calls
  `system_versions()`, stores the result only if `"unknown" not in
  result.values()`, and returns it. `CacheCase.setUp` uses
  `mock.patch.object(capabilities, "_VERSIONS", None)`, which restores the
  value by itself, instead of `cache_clear()`. The spec's mutation "drop
  `@lru_cache`" becomes "never store" (M1).
- **B. "Contains `unknown`" means a value equal to `"unknown"`.** That is the
  only way `system_versions` (`:90-94`) and `_hyprland_version` (`:97-107`)
  report a failure. A substring test would also skip a real version string
  that happened to contain the word, for no gain.
- **C. `CacheCase`'s default `_run` returns `""`** (`tests/test_cache.py:47`),
  so both versions read `"unknown"` and, under decision 10, are never
  memoised. The existing 13 tests therefore behave exactly as on main. The new
  "no subprocess" tests must give `_run` a fake that answers the two version
  commands with real-looking versions. Otherwise they would stay red after
  the fix.
- **D. The doctor test cannot fail on main.** Main has no memo, so
  `system_versions()` is always fresh there. It is a guard for decision 4 and
  is proven by mutation M3, as the spec says. The other five new tests fail
  on main.
- **E. The memo holds on hosts without Omarchy or Hyprland.** There,
  `"unknown"` is read on every call and the two subprocesses keep running.
  They fail fast (`FileNotFoundError`), which is cheap. This is a consequence
  of decision 10 and is accepted.

## Landing order

**#111 lands first** in this batch (#111, #110, #121, #79, #80). It is
based on `b73a3f4`, so it needs no rebase unless another PR merges first.

**Overlap with #121** (branch `docs/121-realtime-engine-future`, spec
approved at `03e072d`): #121 deletes `src/omarchy_voice/realtime.py`,
`tests/test_realtime.py`, `tests/test_realtime_wire.py` and
`tools/bench_realtime.py`, and edits `cli.py` (`cmd_run` `:188-201`, `:464`,
doctor `:504-506`). #111 edits none of these. `capabilities.py` has no
realtime mentions (`grep -n -i realtime` prints nothing). The only `openai`
hits are unrelated: the ChatGPT URL note (`:929`) and the Codex row in
`CODING_AGENTS` (`:1177`), which #111 reads and does not change.
`realtime.py:511` calls `capabilities.manifest()`, and #121 deletes that
caller. That removes one caller and changes nothing in #111. No file is
edited by both, so #121 rebases onto #111 without a conflict. Only its test
counts move: #111 adds 6, so #121's baseline is 1132 minus its deletions.

## Steps

0. **Baseline.** `git status` is clean on `fix/111-manifest-key-cost`, and
   `git merge-base HEAD origin/main` is `b73a3f4`. `gh issue view 111` shows
   OPEN. With `export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`, run
   `nix develop -c pytest tests -q` and
   `nix develop -c python3 -m unittest discover -s tests`
   → verify by both passing with **1126** tests, 13 of them in
   `tests/test_cache.py`. Record any failure that already exists before
   continuing.
1. **Precondition: nothing in the batch has merged ahead of #111.**
   `git fetch origin` and `git log --oneline b73a3f4..origin/main`. If it
   is empty, continue. If not, `git rebase origin/main`, repeat step 0 and
   re-check every `file:line` in this plan
   → verify by a clean rebase and a green baseline, with the line numbers
   confirmed or corrected in this file (in the same commit as the code).
2. **`tests/test_cache.py`: add the fakes and the new tests, and see them
   fail on `main`.** Add them before any `src/` change, after `T8VanishedEntry`
   (`:269`). They use the real `_cache_key`, never `self.keys()`.
   - *Version-aware `_run`*: a `side_effect` that answers
     `["omarchy", "version"]` with `3.1.0`,
     `["hyprctl", "version"]` with
     `Hyprland 0.56.0 built from branch unknown at commit abc`, and anything
     else with `""`. A helper `version_calls()` counts only the calls in
     `capabilities._run.call_args_list` whose argv is one of those two.
     Tests count only these, so other `_run` calls (such as
     `omarchy commands --json`) do not change the counts.
   - *Installed agents*: a mutable set `self.installed`, and
     `mock.patch.object(capabilities.shutil, "which", side_effect=lambda b:
     f"/bin/{b}" if b in self.installed else None)`. The tests change the set
     between calls. Nothing is timed. There is no real subprocess and no real
     `which`.
   - `T9VersionsReadOncePerProcess(CacheCase)`:
     1. `test_a_hit_runs_no_subprocess`: `manifest()` on an empty cache gives
        `version_calls() == 2` (the miss reads once; main gives 4, two for
        the key and two for the text). Then `_run.reset_mock()`, `manifest()`
        again: `version_calls() == 0` (main gives 2).
     2. `test_command_index_hit_runs_no_version_subprocess`: `manifest()`
        (to take the reading), then `_run.reset_mock()`, then
        `command_index()` twice. `version_calls() == 0` (main gives 4).
     3. `test_an_unknown_reading_is_read_again`: the `hyprctl` answer is `""`
        on its first call and the real line after that. Call `_cache_key()`
        three times. The first two keys differ (the first has
        `"hyprland": "unknown"`). `version_calls() == 4` after the third
        call: the unknown reading was re-read, and the good one was kept
        (main gives 6).
     4. `test_doctor_versions_stay_fresh`: `_run` answers `omarchy version`
        with `1.0` on its first call and `2.0` on its second, and `hyprctl
        version` with the real line each time (a function side effect, not a
        four-entry list, so the order of the two commands does not matter).
        Two `system_versions()` calls return `omarchy` `1.0` and then `2.0`.
        Passes on main. It is a guard (ambiguity D).
   - `T10InstalledAgentsMoveTheKey(CacheCase)`:
     5. `test_an_installed_agent_moves_the_key`: `self.installed =
        {"claude"}`, `_cache_key()`; then `{"claude", "codex"}`,
        `_cache_key()`. The keys differ (main: equal).
     6. `test_installing_an_agent_rebuilds_the_manifest`: `{"claude"}` →
        `manifest()` does not contain `codex exec`. `{"claude", "codex"}` →
        `manifest()` contains it. Back to `{"claude"}` → `manifest()` does not
        contain it again (removal). `version_calls() == 2` over all three:
        the rebuilds reuse the one reading. On main, the second call is a hit
        and returns the first text.
   → verify by `nix develop -c pytest tests/test_cache.py -q -k "T9 or T10"`
   on the unchanged `src/`: **tests 1, 2, 3, 5 and 6 FAIL**, each on the
   count or key or text named above, not on an import or attribute error.
   Test 4 passes.
3. **`capabilities.py` `:90`: add the memo. `tests/test_cache.py`
   `CacheCase.setUp` (`:37-55`): reset it.** Below `system_versions`, add
   `_VERSIONS: dict[str, str] | None = None` and `_versions()` as in
   ambiguity A, with a one-line docstring that names #111 and the
   `"unknown"` rule. In `setUp`, add `("_VERSIONS", {"new": None})` to the
   patch list.
   → verify by tests 3 and 4 passing, and by the existing 13 tests passing
   unchanged.
4. **`capabilities.py` `_cache_key` (`:1114-1141`): the cheap key, with the
   agents in it.** `:1129` becomes `versions = _versions()`. Right after the
   `json.dumps` stamp line (`:1130`), add the agent line from decision 6. Add
   one docstring sentence for each change: the versions are read once per
   process (an upgrade needs a daemon restart, and an unknown reading is read
   again), and the installed coding agents are part of the key.
   → verify by tests 2, 5 and 6 passing.
5. **`capabilities.py` `manifest()` `:1153`: the same reading.**
   `versions = system_versions()` becomes `versions = _versions()`.
   → verify by test 1 passing (the miss reads the versions once).
6. **Scope check.** `grep -n "system_versions()" src/omarchy_voice/*.py`
   prints only the `def` line, the call inside `_versions` and `cli.py:546`. `git diff --stat` lists only `capabilities.py` and
   `tests/test_cache.py`
   → verify by that output.
7. **Mutation checks.** Apply each one alone, run
   `nix develop -c pytest tests/test_cache.py -q`, and revert with
   `git checkout -- src/`:
   - M1 (decisions 1, 5): `_versions()` never stores (always
     `return system_versions()`) → tests 1, 2 and 3 fail.
   - M2 (decisions 3, 6): drop the agent line from the stamp → tests 5 and
     6 fail.
   - M3 (decision 4): wrap `system_versions` itself in
     `functools.lru_cache(maxsize=1)` → test 4 fails.
   - M4 (decision 7): revert `:1153` to `system_versions()` → test 1 fails
     on the miss count (4, not 2), and test 6 fails on its count.
   - M5 (decision 10): store the reading even when it contains `"unknown"`
     → test 3 fails.
   - M6 (decision 2): memoise `manifest()` with `functools.lru_cache` →
     test 6 fails, since the install is hidden.
   - M7 (decision 3): compute the agent string once at import and reuse it
     in the stamp → tests 5 and 6 fail.
   → verify by every mutation turning its tests red, and by `git diff
   --stat` showing only the two intended files after each revert.
8. **Timing, for the PR.** Re-run the spec's scratch script
   (`scratchpad/spec111.py`, not committed), adapted to the real code, with
   the `DBUS_SESSION_BUS_ADDRESS` export, over 20 warm runs
   → verify by a `manifest()` hit near 1 ms against about 31 ms on main.
   Record the median, minimum and maximum in the PR, as the issue asks.
9. **Full suites and the flake.** With
   `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`, run
   `nix develop -c pytest tests -q`,
   `nix develop -c python3 -m unittest discover -s tests` and
   `nix flake check --no-write-lock-file`
   → verify by all three passing with **1132** tests (1126 + 6), 19 of them
   in `tests/test_cache.py`. T1 to T8 pass with no edits, and so does
   `tests/test_find_commands.py:314` (its `_run` returns `""`, so nothing is
   memoised).

## Tests

```
export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent
nix develop -c pytest tests/test_cache.py -q -k "T9 or T10"   # 6 passed (1, 2, 3, 5, 6 fail before step 3)
nix develop -c pytest tests/test_cache.py -q                  # 19 passed
nix develop -c pytest tests -q                                # 1132 passed
nix develop -c python3 -m unittest discover -s tests          # Ran 1132 tests, OK
nix flake check --no-write-lock-file                          # passes
```

No test runs a version subprocess, calls the real `shutil.which` for an
agent, or touches `~/.cache/omarchy-voice`. No assertion depends on time.
Nothing runs in the background.

## Rollback

The change is one commit on `fix/111-manifest-key-cost`. Before merge, drop
the branch. After merge, `git revert <sha>` restores the old key. The
key format changes both ways, so each direction misses once and the LRU bound
prunes the entries of the other formula. No cache clearing, config or
migration is needed. On a host (p620 or razer), the daemon picks up either
direction at the next `omarchy-voice` rebuild or restart.
