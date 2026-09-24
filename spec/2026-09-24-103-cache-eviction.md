---
status: approved
issue: 103
intent: intent/2026-09-24-103-cache-eviction.md
---

# Spec: writing a cache entry must not delete another process's entry

Closes #103. Line numbers are against `main` at `93c6dac`.

## The intent's open questions, answered

The intent was approved without answers to its five questions. Each one is
decided here, with the reasoning shown. Q4 is a claim about the installed
daemon, so it has a demonstration. **Any of these decisions can be rejected at
this gate.**

### Q4. The key does not move on a store upgrade: in scope, fixed here

Fixed in this change, not in a separate issue. Once the eviction is fixed, the
stale-manifest case becomes reachable (see the intent), so the two fixes ship
together.

The new key has three parts:

- **`capabilities.py`:** a sha256 of the file's bytes replaces its mtime.
- **Hyprland stub and Omarchy bindings directory:** the resolved path (`Path.resolve()`)
  is added next to the mtime. On NixOS both are symlinks into the store, so the
  resolved path changes whenever that package is rebuilt.
- **Omarchy and Hyprland versions:** unchanged.

Two candidates were rejected:

- **`importlib.metadata.version("omarchy-voice")`.** It is `0.3.0` in
  `pyproject.toml:7`, in the running daemon (`/nix/store/vy2ld06…-omarchy-voice-0.3.0`)
  and in every checkout. Rebuilds do not bump it, so it would not move either.
- **The resolved path of `__file__`.** It fixes the store case, but it gives every
  worktree its own key even when the file content is identical, and that is the
  contention this issue is about. A content hash does both jobs. Identical
  checkouts share a key, a `git switch` that only touches mtimes does not change
  it, and a store rebuild that changes the template does change it.

The bindings and stub have the same mtime-1 problem as `capabilities.py`:

```
$ stat -c '%y %n' $OMARCHY_PATH/default/hypr/bindings /run/current-system/sw/share/hypr/stubs/hl.meta.lua
1970-01-01 01:00:01.000000000 +0100 /nix/store/rp5i87d4…-nixarchy-omarchy-tree/default/hypr/bindings
1970-01-01 01:00:01.000000000 +0100 /run/current-system/sw/share/hypr/stubs/hl.meta.lua
```

As a result, an Omarchy tree rebuild that changes the bindings without changing
`omarchy version` currently serves stale `app_bindings`. The command index is
affected the same way, because `omarchy commands --json` comes from that tree.
Adding the resolved paths fixes both.

**Demonstration.** The real cache was only listed, never written. The package
was copied twice into the scratchpad as `store-aaa-omarchy-voice-0.3.0` and
`store-bbb-omarchy-voice-0.3.0`. In the second copy, one TEMPLATE heading was
changed to `## The rest of the Omarchy CLI (UPGRADED)`. Every file in both copies
got mtime 1, as in the store. Each copy ran `manifest()` in its own process,
with a shared temp `CACHE_DIR` and the real `omarchy`/`hyprctl` (read-only). The
"proposed" rows swap in the key above as a local function in the script. It is
not in the repo.

```
current   store-aaa-omarchy-voice-0.3.0: key=785110990b3dd980 upgraded_text=False files=['manifest-785110990b3dd980.md']
current   store-bbb-omarchy-voice-0.3.0: key=785110990b3dd980 upgraded_text=False files=['manifest-785110990b3dd980.md']
proposed  store-aaa-omarchy-voice-0.3.0: key=0febdde3becd5d17 upgraded_text=False files=['manifest-0febdde3becd5d17.md']
proposed  store-bbb-omarchy-voice-0.3.0: key=805342e7fde81165 upgraded_text=True files=['manifest-805342e7fde81165.md']
```

- **Current key.** The "upgraded" build computes `785110990b3dd980`, the running
  daemon's own key from the intent, and serves the old build's manifest
  (`upgraded_text=False`).
- **Proposed key.** It rebuilds (`upgraded_text=True`).

The file lists still show one file each, because the demo kept today's eviction.
The eviction is fixed separately below.

**Cost.** Measured in-process with `timeit` on this machine:

| Part | Per call |
| --- | --- |
| `system_versions()` (2 subprocesses, already in the key) | 31.1 ms |
| Today's three `stat()` calls | 12.6 µs |
| `resolve()` of stub and bindings (added) | 110.6 µs |
| sha256 of `capabilities.py`, 43 KB (added) | 35.6 µs |
| `os.utime` on a hit (added, see Q2) | 8.6 µs |

The additions come to about 0.15 ms against 31 ms. No subprocess is added.

**Can the versions be cached?** Yes. The cost is 0.19 ms (`shutil.which` +
`realpath` of both binaries), against 31 ms per turn. `system_versions()` could
be memoized per process, keyed on the resolved path and mtime of `omarchy` and
`hyprctl`. **This is not in this change.** `hyprctl version` reports the
*running* compositor over its socket, not the binary on disk, and changing how
fresh every turn's key is deserves its own review. That makes it the bigger win
per turn (31 ms on every turn, against 50–65 ms per eviction), so I recommend
opening a follow-up issue.

### Q1. Bound the cache by count, 8 entries per kind, least recently used first

After each write, keep the 8 newest entries of that kind by mtime and delete the
rest. The entry just written is never deleted. The worst case is 8 manifests
(about 11 KB each) plus 8 indexes (about 12 KB each), under 200 KB in total.

There is no age bound. An age bound alone would evict a long-running daemon's
entry a day after it was built, and on top of the count bound it adds nothing
the count does not already bound. With the content-hash key, identical
checkouts share a key, so 8 slots are more than the number of distinct
`capabilities.py` versions normally in use at once.

### Q2. A hit refreshes the entry's mtime: yes

A hit calls `os.utime(cached)`, which costs 8.6 µs. That turns the count bound
into least-recently-used. The daemon hits on every turn, so its entry can only
be evicted if 8 *other* keys are written between two of its turns. A failed
`utime` (for example on a read-only directory) is ignored. The hit still
returns.

### Q3. One policy for both caches

One helper serves both caches, with the same N. The index costs more to rebuild
(443 ms), but it is also written far less often: only `omarchy_help` reaches
it, through `search_commands`, and the tests use a temp dir. With LRU and 8
slots it has more headroom than the manifest. A looser bound would be a second
constant for an eviction that does not happen.

### Q5. Separate cache directories for checkouts: no

Q1, Q2 and Q4 together remove the contention. Checkouts with the same
`capabilities.py` share entries, and the daemon's entry survives up to 7 other
keys. Separate directories would have three drawbacks:

- They would change the cache path that `doctor` reports (`cli.py:553`).
- A deleted worktree would leave a directory that nothing ever prunes.
- A checkout could never reuse a manifest the daemon already built for the same
  content.

## Design

All changes are in `src/omarchy_voice/capabilities.py`.

1. **`_cache_key()` (`:868-886`).** Keep `system_versions()`. For the stub and
   `OMARCHY_PATH / "default/hypr/bindings"`, append `str(path.resolve())` and
   `st_mtime_ns`. For `Path(__file__)`, append
   `hashlib.sha256(Path(__file__).read_bytes()).hexdigest()` instead of its
   mtime. `OSError` is skipped per path, as today. Rewrite the docstring: it
   should say that store files all have mtime 1, which is why the key uses the
   file's content and the resolved paths.

2. **New `_load(cached: Path) -> str | None`.** It calls `read_text()`, then
   `os.utime(cached)` under `contextlib.suppress(OSError)`. It returns `None` on
   `OSError` from the read, which happens when another process pruned the file
   between the check and the read. This replaces `cached.exists()` and
   `read_text()` in `command_index()` (`:374-381`) and `manifest()` (`:894-895`).
   A `None` result falls through to a rebuild. `refresh=True` still skips the
   read.

3. **New `_store(cached: Path, text: str, pattern: str) -> None`.** Replaces the
   unlink loops at `:397-399` and `:910-912`. It has two parts:
   - **Write atomically.** Write to `cached.with_name(f".{cached.name}.{os.getpid()}.tmp")`,
     then `os.replace()` it onto `cached`. A concurrent reader sees either the
     old file or the whole new one, never a partial file (a constraint in the
     intent). The dot-prefixed temp name does not match either glob, so a
     pruner never touches another process's in-flight file.
   - **Prune.** Collect `(st_mtime_ns, path)` for `CACHE_DIR.glob(pattern)`,
     skipping entries whose `stat()` raises `OSError`. Sort newest first. Unlink
     everything past `CACHE_KEEP` except `cached`, using
     `unlink(missing_ok=True)`.

   The patterns are `"manifest-*.md"` and `f"*-{COMMAND_INDEX}"`, as today, so
   pruning one kind never touches the other.

4. **Add `CACHE_KEEP = 8`** next to `COMMAND_INDEX` (`:360`), with a `ponytail:`
   comment that names the ceiling: more than 8 keys written between two daemon
   turns still evicts the daemon.

The manifest text and its formatting do not change, so a given key produces the
same bytes as before and the API prefix cache is unaffected.

### A manual step until this lands

The daemon serves the old manifest after every rebuild that changes
omarchy-voice. It also serves an old index after an Omarchy tree change that
keeps the same version. Until this lands, run this after such a rebuild:

```
rm -f ~/.cache/omarchy-voice/manifest-*.md ~/.cache/omarchy-voice/*-command-index.tsv
systemctl --user restart omarchy-voice
```

The restart makes the daemon run the new code. The `rm` makes the new code build
a fresh manifest. **Once this lands, neither step is needed.**

- **Later upgrades.** A rebuild that changes `capabilities.py`, the Omarchy
  tree or the Hyprland package moves the key.
- **The upgrade that brings in this fix.** It needs no step either. The new key
  formula cannot equal an old key, so the first run of the new code rebuilds.
- **Old-format files.** They match the globs and are pruned as least-recently-used.

## Alternatives rejected

- **Delete nothing.** This trades churn for unbounded growth, which the intent
  rules out.
- **Age bound, alone or on top of the count.** See Q1. It evicts entries that are
  still in use, or adds a second knob that does nothing.
- **Package version or resolved `__file__` in the key.** See Q4. The version does
  not move, and the path splits identical checkouts.
- **Hash every file the manifest reads, such as the bindings `*.lua` and the
  stub.** The resolved store path already captures a change on NixOS, and mtime
  captures an edit elsewhere. Hashing would add file reads to every turn for no
  case that the path and mtime miss.
- **Per-checkout cache directories.** See Q5.
- **A lock file around write and prune.** An atomic `os.replace()`, a tolerant
  `stat()` and `unlink(missing_ok=True)` cover every race with no lock. The
  worst case is a prune that keeps one entry more or fewer than 8 for one write.

## Risks

- **One rebuild after each system rebuild.** A system rebuild that rebuilds
  Hyprland or the Omarchy tree at the same version now changes the key, through
  the resolved stub or bindings path. The next turn rebuilds the manifest once
  (about 80 ms). This is intended, but it partly undoes one goal in the
  `_hyprland_version` docstring (`:94-104`): not moving the key on same-version
  rebuilds. Stripping the "built from" line still matters for the manifest bytes.
- **More than 8 live keys.** If 8 other keys are written between two daemon
  turns, the daemon is evicted again. That needs 8 distinct `capabilities.py`
  contents or system states in use at once, so it is far rarer than today, where
  any second key evicts.
- **Parts of the key that are still missing.** The key still hashes only
  `capabilities.py`. The manifest also depends on `coding_agents()` (`:931`,
  through `shutil.which`) and on anything imported from other modules. This is
  the same gap as today and is not widened here.
- **Stray temp files.** A crash between the write and `os.replace()` leaves a
  `.manifest-….tmp` or `.…-command-index.tsv.….tmp` file that nothing prunes.
  It is small, and it can only happen at that exact moment, so it is accepted.
- **Where it runs.** Every install is affected: `~/.cache/omarchy-voice/` on the
  daemon's host (p620 here) and on any machine that runs `say`, `doctor` or
  `manifest` from a checkout. No host needs special approval, because only a
  per-user cache directory is written.

## Verification

New `tests/test_cache.py`. It imports `_isolated` first, patches
`capabilities.CACHE_DIR` to a `tempfile.mkdtemp()`, and mocks `_run` so the tests
do not shell out. The real `~/.cache/omarchy-voice` is never touched (#99).

1. **Two keys coexist.** Patch `_cache_key` to `"a"*16`, call `manifest()`, then
   `"b"*16`, then `"a"*16` again. Both files exist. The third call is a hit:
   `essentials` is patched with a mock that is not called. Do the same for
   `command_index()`: `_run` is called once per key, not three times.
2. **The stale upgrade rebuilds.**
   - Point `capabilities.__file__` at a temp copy of `capabilities.py` with
     mtime 1, and record `_cache_key()`.
   - Change one TEMPLATE line, set the mtime back to 1, and assert the key
     differs. Under today's `_cache_key` this assertion fails, which is the bug.
   - Make a byte-identical copy at another path and assert the key is equal.
     Identical checkouts share a key.
   - Point a symlink stub at another target with the same mtime and assert the
     key differs.
3. **The bound evicts only old entries.**
   - Write 12 manifests through `_store` with ascending mtimes, calling
     `os.utime` on the first one after the sixth write, as the daemon's hit
     would. Exactly 8 remain, the touched one is among them, and the 4 oldest
     untouched ones are gone.
   - A `*-command-index.tsv` in the same directory survives manifest pruning.
   - No `.tmp` file remains.
4. **A vanished entry does not raise.** Make `read_text` raise
   `FileNotFoundError` on a hit. `manifest()` returns a rebuilt text.
5. **The existing suite still passes.** `pytest tests -q`: all pass, including
   `ManifestTests` in `tests/test_find_apps.py:323`.
6. **Flake checks.** `nix flake check --no-write-lock-file` passes (it runs
   `pytest tests -q`, `flake.nix:145`).
7. **Runtime check after install (read-only).** List `~/.cache/omarchy-voice/`
   after running `omarchy-voice manifest` from a checkout. The daemon's
   `manifest-<key>.md` is still there next to the checkout's entry, and its
   mtime advances with each voice turn.
