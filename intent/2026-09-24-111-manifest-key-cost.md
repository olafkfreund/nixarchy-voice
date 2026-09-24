---
status: draft
issue: 111
author: olafkfreund
---

# Intent: a cached manifest must be cheap to find and must name the agents actually installed

Closes #111. Split out of #103 (merged in #115, which rewrote `_cache_key`).

## Problem

Two things are wrong with the key the manifest cache is looked up by. Code
references are to `src/omarchy_voice/capabilities.py` on `main` 6a9a3f5.

### 1. Finding a cached manifest costs two subprocesses every time

`_cache_key()` (`:1114-1141`) starts with `system_versions()` (`:90-94`),
which runs `omarchy version` and `hyprctl version` (`:92`, `:106`) through
`_run` (`:82-87`). `manifest()` (`:1144-1166`) computes the key before it
looks at the cache (`:1148`), so a cache *hit* pays for both. The versions
change only when the system is rebuilt; the daemon is long-lived.

`manifest()` has no in-process memo, and it is called per turn:

- `planner._system_prompt` (`planner.py:109-114`), used once per turn by the
  chat planner (`planner.py:162`) and once per session by the Claude backend
  (`claude_backend.py:564`);
- `realtime._instructions` (`realtime.py:509-513`), each time the realtime
  session's instructions are built;
- `omarchy-voice doctor` (`cli.py:545`), which then calls `system_versions()`
  again itself (`cli.py:546`).

`command_index()` (`:410`) also calls `_cache_key()`, but it sits behind
`functools.lru_cache` (`:110`), so it pays once per process, not per call.

Measured on p620 in the dev shell (`nix develop`, `DBUS_SESSION_BUS_ADDRESS`
pointed at a nonexistent socket), 20 runs each, warm cache:

| Call                                  | median  | min     | max      |
| ------------------------------------- | ------- | ------- | -------- |
| `_cache_key()`                        | 33.3 ms | 30.7 ms | 37.1 ms  |
| `system_versions()`                   | 32.5 ms | 28.8 ms | 35.1 ms  |
| `omarchy version` alone               | 16.0 ms | 15.0 ms | 21.3 ms  |
| `hyprctl version` alone               | 15.1 ms | 13.0 ms | 19.6 ms  |
| `manifest()`, cache hit               | 32.1 ms | 28.7 ms | 143.1 ms |
| `_cache_key()` with versions stubbed  | 0.2 ms  | 0.2 ms  | 0.3 ms   |
| `shutil.which` for all four agents    | 0.9 ms  | 0.8 ms  | 1.1 ms   |

A spy on `subprocess.run` during one `manifest()` cache hit recorded exactly
`['omarchy', 'version']` and `['hyprctl', 'version']`. So the issue's ~31 ms
holds, and nearly all of a cache hit is the two version subprocesses: without
them the key is 0.2 ms.

### 2. The key ignores which coding agents are installed

The manifest embeds `coding_agents()` (`:1164`, `:1185-1193`), which lists
each of `claude`, `codex`, `gh`, `ollama` (`CODING_AGENTS`, `:1173-1182`)
only if `shutil.which` finds it. Nothing about that set goes into
`_cache_key()`. Installing or removing one of them keeps the key, so the
cached manifest goes on naming an agent that is gone, or not naming one that
arrived, until something else moves the key (a rebuild of Omarchy or
Hyprland, or an edit to `capabilities.py`). `coding_agents()`'s own docstring
says why that matters: naming an absent binary "buys a failed command and a
confused turn". On p620 today all four are installed.

## Proposed outcome

- A cache hit on the manifest no longer runs a subprocess per call in a
  long-lived process; its cost is measurable with `trace_timings` (or the
  timing above) and is a small fraction of today's ~32 ms.
- After an Omarchy or Hyprland upgrade, the manifest still reflects the new
  versions. How promptly (next call, next daemon restart) is an open question.
- After installing or removing `claude`, `codex`, `gh` or `ollama`, the next
  manifest built or served describes the agents actually on `PATH`.
- `omarchy-voice doctor` reports the same versions it does today.

## Affected users and systems

- Everyone running the daemon: every chat-planner turn and every realtime
  instructions build pays today's ~32 ms before the model is called.
- Anyone who installs or removes a coding agent without rebuilding the
  system (e.g. a user profile install) sees a stale agent list.
- `src/omarchy_voice/capabilities.py` (`_cache_key`, `system_versions`,
  `manifest`, `coding_agents`), and its callers in `planner.py`,
  `realtime.py`, `claude_backend.py`, `cli.py`.
- The on-disk cache under `CACHE_DIR`: a key change invalidates entries once.

## Constraints

- Must keep #103's guarantees: the key moves when this file's content, the
  Hyprland stub or Omarchy's bindings change, including on NixOS where store
  mtimes are all 1; concurrent processes must not evict each other's entries.
- Must not make the manifest bytes change between turns when nothing on the
  machine changed: identical bytes are what the API prefix cache relies on.
- Must not serve a manifest for versions that are no longer installed after a
  rebuild plus daemon restart.
- `doctor` must keep showing real versions.
- Must work when `omarchy` or `hyprctl` is missing ("unknown" today).
- Measure before and after, as the issue asks.

## Open questions

1. How fresh must the versions be inside one running daemon? Options: once
   per process (an upgrade needs a daemon restart, which a NixOS rebuild of
   the user service normally does), or keyed on something free that moves on
   upgrade, such as the resolved store path of the `omarchy` and `hyprctl`
   binaries. Is a resolved binary path a trustworthy proxy for "the version
   changed" on non-NixOS installs, if those matter at all?
2. Should `manifest()` itself be memoised in-process (like `command_index`),
   or only the key's inputs? Memoising the manifest would also hide an agent
   install until restart, which conflicts with outcome 3 unless the agent set
   is checked on every call.
3. Is `shutil.which` at call time (0.9 ms for four) acceptable per turn, or
   should the agent set be sampled less often?
4. Should `doctor` keep calling `system_versions()` fresh (it is a one-shot
   command, so the cost does not matter there), so that it can show when the
   daemon's view is stale?
