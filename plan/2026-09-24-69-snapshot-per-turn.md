---
status: approved
issue: 69
spec: spec/2026-09-24-69-snapshot-per-turn.md
---

# Plan: the voice engine must see the desktop as it is now, not as it was at start

## Approved decisions, carried over from the spec

1. **The snapshot rides in the user message on every Claude turn.** This
   covers the warm voice brain and the cold `say` brain alike. It does not go
   into the warm-up turn.
2. **It leaves the Claude system prompt.** `planner._system_prompt(live=True)`
   gains the keyword. `ClaudeBrain._options()` passes `live=False`. The
   Planner (default) and the realtime engine are untouched.
3. **The `live_state` half of #75 is folded in.** A failed query reads
   `unknown (hyprctl did not answer)`, an empty one reads `none`, and any
   unknown section adds one closing line telling the model to call
   `hypr_query`. The `_await_new_window` half stays in #75.
4. **No history bound in this change.** Ceiling: about 560 tokens per turn,
   measured at 2,235 characters and 56 ms. Upgrade trigger: the `usage`
   tally shows uncached input growing turn over turn. The upgrade path:
   restart the warm session when idle-stop fires. The ceiling goes into a
   `ponytail:` comment at the helper.
5. **The heading is `# The desktop right now`**, the one the persona already
   names (`persona.py:50`), labelled "current as of this turn; supersedes
   every earlier snapshot". The persona text is unchanged.
6. **The snapshot is fetched on the critical path, with no prefetch.** In the
   warm path it runs through `asyncio.to_thread`.

## Steps

0. **Baseline.** `nix develop -c pytest tests -q` → record the pass count
   before any edit, so a later failure can be told apart from one that was
   already there.

1. **`src/omarchy_voice/capabilities.py`, `live_state()` (:455-486): unknown
   versus none.** Replace each `query(...) or []` / `or {}` with a check for
   `None`:

   ```python
   UNKNOWN = "unknown (hyprctl did not answer)"
   monitors = query("monitors")
   if monitors is None:
       parts.append(f"Monitors: {UNKNOWN}")
   else:
       parts.append("Monitors: " + (", ".join(...) or "none"))
   ```

   Do the same for workspaces (`none` when no workspace has an id above 0)
   and clients (`Open windows: none` when the list is empty or all hidden).
   For `activewindow`, `None` gives `Focused window: <UNKNOWN>` and `{}` or
   no class omits the line, as now. If any section was unknown, append
   `Part of this snapshot is unknown; call hypr_query before acting on what is
   missing.` The healthy output stays byte-identical, apart from `none`
   replacing what used to be an empty string after the label.
   → verify by step 5's `live_state` tests.

2. **`src/omarchy_voice/planner.py`, `_system_prompt()` (:109-114).** Change
   the signature to `_system_prompt(live: bool = True)`, and add the
   `# The desktop right now` part only when `live` is true.
   `planner.py:162` stays as it is. → verify by step 5.

3. **`src/omarchy_voice/claude_backend.py`.**
   - Add `capabilities` to the import on line 34:
     `from . import capabilities, mcp_server, planner`.
   - `_options()` (:436): `prompt = planner._system_prompt(live=False)`.
   - Add a module-level `_with_desktop(text: str) -> str`, as in the spec,
     with the decision 4 `ponytail:` comment.
   - `_ask` (:498): `await client.query(_with_desktop(text))`.
   - `_turn` (:725): `await self._client.query(await
     asyncio.to_thread(_with_desktop, text))`.
   - `_drain_query` is left alone, so the warm-up carries no desktop.
   → verify by step 5.

4. **`tests/test_claude_backend.py`: keep the existing suite honest.**
   `FakeClient` answers by the exact text sent (`script.get(text)`, :617), so
   a prefix would break every scripted warm test and would run the real
   `hyprctl`. In `WarmBrainTests.setUp` (:633), add
   `self.enterContext(mock.patch.object(claude_backend, "_with_desktop",
   side_effect=lambda t: t))`. The new tests below undo that inside their own
   `with` block. → verify: the existing suite passes unchanged.

5. **Tests (new).**
   - `tests/test_live_state.py` (new, since there is no module for it): patch
     `capabilities._run`.
     (a) Healthy JSON gives monitors, workspaces, a focused window and open
     windows, with no "unknown".
     (b) `_run` returns `""` for `clients` only: `Open windows: unknown …`
     plus the closing line, and the monitors line is still correct.
     (c) Every query returns `"[]"` (and `"{}"` for activewindow): `none` in
     each section, no "unknown", no closing line.
     (d) Invalid JSON is treated as unknown.
   - `tests/test_planner.py`: `_system_prompt()` contains
     `# The desktop right now`, and `_system_prompt(live=False)` does not.
     `capabilities.manifest` and `live_state` are patched.
   - `tests/test_claude_backend.py`:
     (e) `options_of(brain())` with `_system_prompt` patched as a mock asserts
     it was called with `live=False`.
     (f) `WarmBrainTests`: patch `capabilities.live_state` with
     `side_effect=["A", "B"]` and restore the real `_with_desktop`. Across two
     turns, `client.asked[1:]` holds a query containing `A` before the first
     utterance, then one containing `B` before the second.
     (g) `client.asked[0] == WARM_UP` exactly, with no snapshot.
     (h) Cold `_ask`: a stand-in `claude_agent_sdk` in `sys.modules`, whose
     `ClaudeSDKClient` is a `FakeClient` given `__aenter__`/`__aexit__`, and
     `ClaudeBrain._options` patched. `asyncio.run(subject._ask("hi",
     Turn("hi")))` leaves one query that starts with
     `# The desktop right now` and ends with `hi`.
   → verify: `nix develop -c pytest tests -q` equals the baseline count plus
   the new tests, with no new failures.

6. **Whole check.** `nix flake check --no-write-lock-file`, as CI runs it →
   it passes.

7. **Live check on this machine.** Rebuild or restart so the service runs the
   branch. The method depends on how the flake input is pinned for this
   host; state which one was used in the PR. Then restart it:
   `systemctl --user restart omarchy-voice`. Wait for "brain ready" in
   `omarchy-voice log`, open a new window, wait more than a minute, and ask
   "what's open?" by voice. The answer names the new window, and the log
   shows no `hypr_query` call for that turn. The user runs the voice part,
   since that needs the microphone.

8. **PR.** Push the branch and open a PR that closes #69, references #75 (for
   the `live_state` half), and links the intent, spec and plan. Mark the #75
   checklist or comment on it saying which half is done.

## Tests

```
nix develop -c pytest tests -q                      # baseline + new, 0 new failures
nix develop -c pytest tests/test_live_state.py tests/test_planner.py tests/test_claude_backend.py -q
nix flake check --no-write-lock-file                # CI parity
```

Plus the voice check in step 7.

## Rollback

It is a single squash-merged PR touching three source files and three test
files, with no config, Nix or schema change. `git revert <merge>` restores
the snapshot in the system prompt and `live_state`'s empty-on-failure
behaviour. Nothing persisted needs undoing. If the service is already running
the new code, restart it after the revert.
