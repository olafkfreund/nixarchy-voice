---
status: draft
issue: 103
spec: spec/2026-09-24-103-cache-eviction.md
---

# Plan: writing a cache entry must not delete another process's entry

Closes #103. Base: `origin/main` at `93c6dac`, where
`nix develop -c pytest tests -q` gives 988 passed and
`nix develop -c python3 -m unittest discover -s tests` gives 988 OK. Every
`file:line` below was checked against `93c6dac`. The code change is in
`src/omarchy_voice/capabilities.py` only. There is one new test file,
`tests/test_cache.py`. No docs, `flake.nix` or packaging change.

## Approved decisions, carried over from the spec

The spec was approved as written. Its answers to the intent's five open
questions are the decisions below.

1. **The cache key uses the content of `capabilities.py`, not its mtime.**
   `_cache_key()` (`capabilities.py:868-886`) appends
   `hashlib.sha256(Path(__file__).read_bytes()).hexdigest()` in place of
   `Path(__file__).stat().st_mtime_ns`. Every file in the Nix store has mtime 1
   (1970-01-01 00:00:01). So under today's key, a rebuild that changes the
   template keeps the same key and serves the old manifest. The spec
   demonstrated this with two copies of the package at mtime 1: under the
   current key both give `785110990b3dd980`, the running daemon's key. With a
   content hash, identical checkouts share a key, a `git switch` that only
   touches mtimes keeps it, and a changed template moves it.
2. **The key also takes the resolved paths of the Hyprland stub and the Omarchy
   bindings directory.** For `_stub_path()` (`capabilities.py:138`) and
   `OMARCHY_PATH / "default/hypr/bindings"` (`OMARCHY_PATH` at
   `capabilities.py:33`), append `str(path.resolve())` and then `st_mtime_ns`,
   as today. Both have mtime 1 on NixOS, and both are symlinks into the store,
   so the resolved path moves when that package is rebuilt. That also fixes
   stale `app_bindings` and a stale command index after an Omarchy tree rebuild
   at the same `omarchy version`. `OSError` is skipped per path, as today.
   `system_versions()` (`capabilities.py:87`) stays in the key as it is.
3. **The package version is not used in the key.** It is `0.3.0` in
   `pyproject.toml:7`, in the running daemon's store path and in every
   checkout, and rebuilds do not bump it. The resolved `__file__` path is not
   used either: it would give every worktree its own key even when the file is
   identical, which is the contention this issue is about.
4. **Keep the 8 most recently used entries per kind.** After a write, the
   entries of that kind are sorted newest first by mtime and everything past
   `CACHE_KEEP = 8` is deleted. The entry just written is never deleted. The
   worst case is about 8 × 11 KB of manifests plus 8 × 12 KB of indexes, under
   200 KB.
5. **A hit refreshes the entry's mtime.** A hit calls `os.utime(cached)` (8.6 µs)
   under `contextlib.suppress(OSError)`, so a read-only directory still returns
   the hit. This makes the count bound least-recently-used. The daemon hits on
   every turn, so it is evicted only if 8 other keys are written between two of
   its turns.
6. **No age limit.** An age bound alone would evict a long-running daemon a day
   after it built its entry. On top of the count it adds a second setting that
   never takes effect.
7. **Atomic writes.** Write to
   `cached.with_name(f".{cached.name}.{os.getpid()}.tmp")`, then `os.replace()`
   it onto `cached`. A concurrent reader sees the old file or the whole new
   one, never a partial file. The dot-prefixed `.tmp` name matches neither
   glob, so a pruner never touches another process's file while it is being
   written.
8. **Reads tolerate a missing file.** An `OSError` from `read_text()` on a hit
   (another process pruned it between the check and the read) returns `None`
   and falls through to a rebuild. Pruning uses a `stat()` that skips
   `OSError` and `unlink(missing_ok=True)`. There is no lock file. The worst
   case is that one write keeps one entry more or fewer than 8.
9. **One policy for the manifest and the command index.** One `_load` and one
   `_store` serve both, with the same `CACHE_KEEP`. The glob patterns stay
   `"manifest-*.md"` and `f"*-{COMMAND_INDEX}"`, so pruning one kind never
   touches the other.
10. **No separate cache directory per checkout.** It would change the cache path
    that `doctor` reports (`cli.py:553`), leave directories behind that nothing
    prunes, and stop checkouts from reusing the daemon's entries.
11. **Caching the version calls is left to #111.** `system_versions()` is about
    31 ms per call (two subprocesses), which is more than anything this change
    touches. `hyprctl version` reports the running compositor, so how fresh the
    key must be deserves its own review. #111 is open for it, and it also adds
    the installed coding agents to the key.
12. **Measured costs** (spec, in-process `timeit` on p620). Per call:
    `system_versions()` 31.1 ms (unchanged); today's three `stat()` calls
    12.6 µs; `resolve()` of the stub and bindings 110.6 µs (added); sha256 of the
    43 KB `capabilities.py` 35.6 µs (added); `os.utime` on a hit 8.6 µs (added).
    The additions are about 0.15 ms. No subprocess is added.
13. **The manifest text does not change.** A given key produces the same bytes
    as before, so the API prefix cache is unaffected.
14. **Accepted risks.** A system rebuild that rebuilds Hyprland or the Omarchy
    tree at the same version now moves the key once, through the resolved path
    (one rebuild, about 80 ms). This partly undoes the goal stated in the
    `_hyprland_version` docstring (`capabilities.py:94-104`), but stripping the
    "built from" line still matters for the manifest bytes. More than 8 live
    keys between two daemon turns still evicts the daemon. The key still does
    not cover `coding_agents()` (`capabilities.py:931`) or other modules
    (#111). A crash between the write and `os.replace()` leaves a stray `.tmp`
    file that nothing prunes.

### The user-facing note (goes in the PR description)

Until this lands, a rebuild that changes omarchy-voice (or the Omarchy tree
without a version bump) needs:

```
rm -f ~/.cache/omarchy-voice/manifest-*.md ~/.cache/omarchy-voice/*-command-index.tsv
systemctl --user restart omarchy-voice
```

After this lands, clearing the cache after a rebuild is no longer needed. That
includes the upgrade that brings the fix in: the new key formula cannot equal
an old key, so the first run of the new code rebuilds, and the old files are
pruned as least-recently-used. The restart is still what makes the daemon run
new code. This note is checked by tests T2 and T7 below.

## Overlaps and landing order

- **`feat/82-installed-commands` (#82, spec approved, no code yet).** Its spec
  adds a PATH/man/tldr index to `capabilities.py` and names its function
  `command_index() -> dict[str, dict]`. That clashes with the existing
  `command_index()` at `capabilities.py:364`, which this plan edits. The clash
  is already on `main` and does not come from #103, but whichever branch lands
  second must resolve it. #82's index is held in memory, not on disk, so it
  does not use `_store`/`_load`. After #103, its manifest sentence (spec §3)
  moves the cache key through the content hash, which is the correct behaviour.
- **#111 (open).** It rewrites `_cache_key()` again to memoise the versions and
  add the coding-agent set. Its body says "Lands after #103".
- **Landing order: #103, then #111, then #82.** #82 can also land before #111,
  because they touch different functions. It must rebase onto #103 and rename
  its `command_index` (for example to `path_commands`). That is flagged for the
  #82 plan, not done here.

## Steps

Each step says what it changes and how it is verified. Run all Python with
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` exported. Never read from or
write to the real `~/.cache/omarchy-voice`.

0. **Baseline.** Run `git switch fix/103-cache-eviction && git rebase origin/main`,
   then `nix develop -c pytest tests -q` and
   `nix develop -c python3 -m unittest discover -s tests` → verify by: 988
   passed and 988 OK. Record `ls ~/.cache/omarchy-voice/` (read-only) for the
   runtime check in step 8.

1. **`tests/test_cache.py`: new file with the tests below, written before any
   `src/` change.**
   - It starts with `import _isolated  # noqa: F401` before
     `from omarchy_voice import capabilities`, as in `tests/test_find_apps.py:22-24`.
   - Each test patches `capabilities.CACHE_DIR` to its own `tempfile.mkdtemp()`
     (removed in `addCleanup`) and `capabilities._run` to `return_value=""`, so
     nothing shells out.
   - A test that tracks rebuilds patches `capabilities.essentials` with
     `wraps=capabilities.essentials` and counts the calls.
   - T1–T8 are listed under **Tests**.

   → verify by: on unmodified `src/`, T2 **fails** on its "changed template
   → different key" assertion, which is the bug. T1 fails, because the second
   write deletes the first entry. T3–T6 and T8 error, because `_store`/`_load`
   do not exist yet. Record the failing output in the PR.

2. **`capabilities.py:19-27`: add `import contextlib`** (it is not imported today;
   `hashlib`, `os` and `Path` already are) → verify by: `python3 -c "import omarchy_voice.capabilities"`
   inside `nix develop`.

3. **`capabilities.py:361`: add `CACHE_KEEP = 8` below `COMMAND_INDEX`**, with
   `# ponytail: count bound, LRU via utime on hit; >8 keys written between two
   daemon turns still evicts the daemon.` → verify by: grep.

4. **`capabilities.py`, after `CACHE_KEEP`: add `_load(cached) -> str | None`
   and `_store(cached, text, pattern) -> None`.**
   - `_load` does `try: text = cached.read_text() except OSError: return None`,
     then `with contextlib.suppress(OSError): os.utime(cached)`, and returns
     `text`.
   - `_store` writes the temp file named in decision 7, calls
     `os.replace(tmp, cached)`, then collects `(st_mtime_ns, p)` for
     `CACHE_DIR.glob(pattern)`, skipping an `OSError` from `stat()`. It sorts
     newest first and calls `unlink(missing_ok=True)` on every `p != cached`
     past index `CACHE_KEEP`.

   → verify by: T3, T4, T5 pass.

5. **`capabilities.py:364-400` `command_index()`: use the helpers.**
   - Replace `if cached.exists() and not refresh: … cached.read_text()`
     (`:375-381`) with `text = None if refresh else _load(cached)`, then
     `if text is not None:` and parse `text` as today.
   - Replace the unlink loop and `write_text` (`:397-399`) with
     `_store(cached, "\n".join(…), f"*-{COMMAND_INDEX}")`.

   → verify by: T1 (index half) and T6 pass, and `tests/test_compose.py`
   still passes.

6. **`capabilities.py:889-913` `manifest()`: the same change.**
   - Replace `:894-895` with `_load`. A `None` falls through to the rebuild.
   - Replace `:910-912` with `_store(cached, text, "manifest-*.md")`.

   → verify by: T1 (manifest half) and T8 pass, and `ManifestTests`
   (`tests/test_find_apps.py:323`) still passes.

7. **`capabilities.py:868-886` `_cache_key()`: the new key (decisions 1 and 2).**
   - Build the list as today. For the stub and bindings, append
     `str(path.resolve())` and then `str(path.stat().st_mtime_ns)`. For
     `Path(__file__)`, append the sha256 of its bytes. Each path is in its own
     `try/except OSError: pass`.
   - Rewrite the docstring. Keep the "an hour of wondering" history, and add
     that store files all have mtime 1, which is why the key uses the file's
     content and the resolved paths, not mtimes alone.

   → verify by: T2 and T7 pass.

8. **Whole suite, both runners, the flake, and mutation checks.**
   - `nix develop -c pytest tests -q` and
     `nix develop -c python3 -m unittest discover -s tests`: 988 plus the new
     tests, all passing, under both runners.
   - `nix flake check --no-write-lock-file`: passes (it runs `pytest tests -q`,
     `flake.nix:145`).
   - Then run the mutation checks under **Tests** and revert each one.
   - After install on p620, the runtime check (read-only): run
     `omarchy-voice manifest` from a checkout, then `ls -l --time-style=full-iso ~/.cache/omarchy-voice/`.
     The daemon's `manifest-<key>.md` is still there next to the checkout's
     entry, and its mtime moves forward after a voice turn.

9. **Commit** code and tests together as
   `fix(capabilities): cache entries are kept LRU and keyed on content (#103)`.
   The PR links intent, spec and plan, carries the user-facing note above,
   and says "Closes #103". If implementation deviates from a step, update this
   file in the same commit.

## Tests

All tests are in `tests/test_cache.py`. They use a temp `CACHE_DIR` and a
mocked `_run`.

- **T1 Two keys coexist.** Patch `_cache_key` to return `"a"*16`, then
  `"b"*16`, then `"a"*16`, calling `manifest()` each time. Both
  `manifest-aaaa….md` and `manifest-bbbb….md` exist, and `essentials` is
  called twice, not three times: the third call is a hit. Do the same with
  `command_index()`: `_run` is called once per key (2 calls, not 3).
- **T2 A stale upgrade rebuilds (must fail on `main` first).**
  - Copy `capabilities.py` to `tmp/a/capabilities.py`, set its mtime to 1
    (`os.utime(p, ns=(1_000_000_000,)*2)`), patch `capabilities.__file__` to
    it, and record `k1 = _cache_key()`.
  - Change one TEMPLATE line in the copy, set the mtime back to 1, and assert
    `_cache_key() != k1`.
  - A byte-identical copy at `tmp/b/` with mtime 1 gives the same key as the
    one before it: identical checkouts share a key.
  - Patch `_stub_path` to a symlink, point the symlink at a second target with
    the same mtime, and assert the key changes. Do the same for the bindings
    directory, with `OMARCHY_PATH` patched to a temp tree.
  - End to end: `manifest()` on the first copy, then on the changed copy.
    `essentials` is called twice, and two manifest files exist.
- **T3 The bound evicts only the least recently used.**
  - Write 12 entries through `_store` into a temp dir. After each write, set
    that entry's mtime to `i` seconds (`i = 1..12`), so "now" is always
    newer than all of them.
  - After the sixth write, `os.utime(first)` with no arguments, as a hit
    does.
  - Exactly 8 `manifest-*.md` remain: the touched first entry plus entries
    6–12. Entries 2–5 are gone.
  - A `x-command-index.tsv` placed in the same dir survives all of it.
- **T4 A hit refreshes the mtime.** Put an entry at the patched key with mtime
  1 and call `manifest()`. It returns the entry's text, `essentials` is not
  called, and `st_mtime_ns > 1e9`. The same for `command_index()`. With
  `os.utime` patched to raise `PermissionError`, the hit still returns the
  text.
- **T5 An atomic write leaves no partial file.**
  - Put `cached` in place with the text `"old"`. Wrap `capabilities.os.replace`
    so that, before it calls the real one, it asserts that `cached` still reads
    `"old"` and that the temp file (a dot-prefixed name ending in `.tmp`)
    holds the whole new text.
  - After `_store`, `cached` holds the new text, `os.replace` was called once,
    and no `.*.tmp` file remains in the dir.
- **T6 The command index follows the same policy.** Nine `command_index()`
  calls under nine patched keys leave exactly 8 `*-command-index.tsv`, and the
  oldest is gone. A `manifest-*.md` in the same dir is untouched. Then T4's
  hit-refresh check again, for the index.
- **T7 The upgrade that brings the fix needs no cache clearing.** Compute a
  key with today's formula, inlined in the test: sha256 of
  `json.dumps(system_versions(), sort_keys=True)` plus the three
  `st_mtime_ns`, truncated to 16. Write `manifest-<oldkey>.md` containing
  `"STALE"`. `manifest()` does not return `"STALE"` and `essentials` is
  called. The old file then counts as the least recently used. After 8 more
  keys, it is pruned.
- **T8 A vanished entry does not raise.** Create the entry, and patch
  `Path.read_text` for that path to raise `FileNotFoundError`. `manifest()`
  returns a rebuilt text.

Mutation checks. Apply each one to `capabilities.py`, run
`nix develop -c pytest tests/test_cache.py -q`, confirm the named test fails,
then revert:

| Mutation | Must fail |
| --- | --- |
| `_cache_key` back to `st_mtime_ns` for `__file__` | T2, T7 |
| drop `resolve()` for the stub and bindings | T2 (symlink part) |
| `_store` prunes every other entry (today's loop) | T1, T3, T6 |
| `_store` sorts oldest first | T3 |
| drop `os.utime` in `_load` | T4 |
| `_store` uses `cached.write_text(text)` directly | T5 |
| `_load` lets `OSError` propagate | T8 |
| `_store` glob `"*"` in place of `pattern` | T3 (index survives), T6 |

Expected final result: both runners report 988 plus the new test count, all
passing, and `nix flake check --no-write-lock-file` exits 0.

## Rollback

`git revert` the implementation commit. No migration is needed. Files written
under the new keys match the old globs, so the old code's unlink loop deletes
them on its first write. After a rollback, the manual `rm` + restart from the
user-facing note is needed again after rebuilds.

## Spec points this plan settles

- **Line citations.** The spec puts `COMMAND_INDEX` at `:360`. It is at `:361`
  on `93c6dac`. Every other citation in the spec matched.
- **`contextlib` is not imported today.** Step 2 adds it.
- **T3 mtimes.** The spec says "ascending mtimes". Real write times can tie at
  filesystem resolution, so T3 sets explicit mtimes of 1–12 s after each write.
  A hit's `os.utime()` with no arguments is then always the newest.
- **The upgrade claim.** The spec states it but does not list a test for it.
  T7 tests it.
