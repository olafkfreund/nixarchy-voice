---
status: approved
issue: 103
author: olafkfreund
---

# Intent: writing a cache entry must not delete another process's entry

Closes #103.

## Problem

Both disk caches in `src/omarchy_voice/capabilities.py` delete every other
entry when they write one:

- `manifest()` writes `manifest-<key>.md` after deleting every
  `manifest-*.md` in `CACHE_DIR` (`capabilities.py:910-912`, on `main` 93c6dac).
- `command_index()` writes `<key>-command-index.tsv` after deleting every
  `*-command-index.tsv` (`capabilities.py:397-399`).

The key (`_cache_key`, `capabilities.py:868-886`) hashes the Omarchy and
Hyprland versions plus the mtimes of the Hyprland stub, Omarchy's bindings
directory and `capabilities.py` itself. Any two processes whose files differ in
mtime have different keys, and each build by one deletes the other's entry.
The next call from the other process misses and rebuilds, and deletes in turn.

The daemon calls `manifest()` on every turn: `planner._system_prompt`
(`planner.py:111`, used at `:162` and by `claude_backend.py:547`) and
`realtime._instructions` (`realtime.py:510-513`). So after an eviction the
daemon's next turn pays the rebuild.

The eviction came in with the initial import (`git log -S` on both lines finds
only `6034269`, "omarchy-voice 0.3.0"). Neither the commit nor the code says
why. The likely reason is to keep the cache from growing a file per key
forever; that is an inference, not a record.

### It is happening on this machine

Measured read-only. The real cache was listed, never written.

| Process | Code it runs | `_cache_key()` |
| --- | --- | --- |
| Running daemon (PID 220215, up since 2026-09-22 23:47) | `/nix/store/vy2ld06…-omarchy-voice-0.3.0` | `785110990b3dd980` |
| Main checkout `nixarchy-voice/src` | working tree | `f842c8bd9345c5b9` |
| Six agent worktrees under `.claude/worktrees/` | working trees | six different keys, none equal to the above |

`~/.cache/omarchy-voice/` at 14:4x on 2026-09-24:

```
12286  11:59:19  785110990b3dd980-command-index.tsv   <- the daemon's key
 9539  14:27:15  manifest-5df3f50cbe0b4183.md         <- nobody's current key
```

The only manifest on disk belongs to a key that neither the daemon nor any
current checkout produces. It was written at 14:27 by a process run from a
checkout whose `capabilities.py` has since changed mtime, and that write deleted
the daemon's manifest. The daemon's next turn rebuilds and deletes this one.
That then happened while this intent was being written. By 14:45:20 the
listing was `manifest-785110990b3dd980.md` (the daemon's key, 10904 bytes),
and `manifest-5df3f50cbe0b4183.md` was gone. None of the probes here wrote to
the real cache: they used a temp `CACHE_DIR` or stopped after computing the key.

A worktree's key changes whenever git rewrites `capabilities.py`: a `git switch`
in this worktree at 14:42 moved its mtime and its key. Every branch switch,
worktree, test run that reaches the real cache (#99), `omarchy-voice doctor`
(`cli.py:516`) or `omarchy-voice manifest` (`cli.py:239`) from a checkout is a
new key and an eviction.

### What an eviction costs

Timed with the real system calls (read-only commands only) against a temp
`CACHE_DIR`, from this worktree:

| Step | Time |
| --- | --- |
| `_cache_key()` (runs `omarchy version`, `hyprctl version`) | 30 ms |
| `hyprctl repl` dispatcher walk (once per process, `lru_cache`) | 15 ms |
| Every file scrape (`app_bindings`, `dispatch_examples`, ...) | about 2 ms |
| `manifest()` hit | 30 ms |
| `manifest()` rebuild, new-process conditions | 77-95 ms |
| `command_index()` hit | 30 ms |
| `command_index()` rebuild (`omarchy commands --json`) | 443 ms |

So a manifest eviction costs about 50-65 ms on the daemon's next turn, and an
index eviction about 410 ms on its next `omarchy_help` call. Nearly all of a
manifest *hit* is the cache key's two subprocesses, not the file read.

The rebuilt text is the same bytes when nothing changed, so the API-side
prefix cache is not affected. The cost is local latency on a voice turn, and a
cache that does not do its job whenever a second process exists.

### Eviction demonstrated

In a temp `CACHE_DIR`, with `_cache_key` pinned to `aaaa…` then `bbbb…`:

```
after A:        ['manifest-aaaaaaaaaaaaaaaa.md']
after B:        ['manifest-bbbbbbbbbbbbbbbb.md']
A cache hit possible? False
after A again:  ['manifest-aaaaaaaaaaaaaaaa.md']
index after A,B: ['bbbbbbbbbbbbbbbb-command-index.tsv']
```

### Found alongside: the key does not move when the installed package changes

Every file in the Nix store has mtime 1970-01-01 00:00:01. Measured on the
daemon's `capabilities.py` and on `/run/current-system/sw/share/hypr/stubs/hl.meta.lua`.
For an installed daemon the mtime part of `_cache_key` is a constant, and the
key is the two version strings alone. An upgrade of omarchy-voice that changes
`TEMPLATE` or `ESSENTIALS`, with Omarchy and Hyprland unchanged, keeps the same
key, and the daemon serves the old manifest from `~/.cache`. The docstring at
`capabilities.py:869-875` says the file's mtime is in the key to prevent exactly
that. Today the eviction hides this: some other process usually deletes the old
file first. Fixing the eviction alone makes it reachable.

## Proposed outcome

- Two processes with different keys can both hit their own cache entry. The
  daemon's manifest and command index survive a `say`, a `doctor`, a test run or
  a second checkout.
- The cache directory still stays bounded. It does not grow a file per branch
  switch forever.
- After the installed package changes, the daemon does not serve a manifest
  built by the previous version.

## Affected users and systems

- `src/omarchy_voice/capabilities.py`: `manifest()`, `command_index()`,
  `_cache_key()`.
- The running daemon (realtime and planner paths, both call `manifest()` per turn).
- `omarchy-voice doctor`, `omarchy-voice manifest`, `say`, and anyone developing
  from a checkout or worktree on a machine with the daemon installed.
- `~/.cache/omarchy-voice/` on every install.

## Constraints

- Must keep the cache bounded. Removing the deletion outright trades churn for
  unbounded growth.
- Must not change the manifest's bytes for a given key: the API prefix cache
  depends on them.
- Must not add a subprocess to the per-turn path. `_cache_key` already costs
  30 ms per turn.
- Must be safe with two processes writing the directory at once (daemon and a
  checkout). No partially written file served as a hit.
- Tests must keep using a temp `CACHE_DIR` (#99), never the real one.

## Open questions

1. Bound by age, count, or both? The issue suggests "older than a day" or "the
   newest N". Age alone lets a flurry of branch switches pile up for a day; count
   alone (say 8) can still evict the daemon under enough worktrees. Both, with the
   entry just read or written always kept?
2. Should a hit refresh the entry's mtime (touch), so a long-running daemon's
   entry is never the oldest? Without it, an age bound evicts the daemon's
   manifest a day after it was built even while it is in use.
3. Do the manifest and the command index share one policy, or does the index
   (443 ms to rebuild, used rarely) deserve a looser bound?
4. The store-mtime finding: should `_cache_key` use the resolved path of
   `capabilities.py` (the store hash changes per build) instead of, or as well as,
   its mtime? Fix it here or in a separate issue? Fixing only the eviction makes
   the stale-manifest-after-upgrade case reachable, so it should not ship later
   than this.
5. Should checkouts use a separate cache directory from the installed daemon
   (e.g. keyed by `Path(__file__).parent`), removing the contention instead of
   bounding it? That changes where `doctor` from a checkout looks.
