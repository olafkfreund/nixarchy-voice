---
status: approved
issue: 183
spec: spec/2026-10-12-183-gate-self-approval-secrets-hold-ttl.md
---

# Plan: close self-approval, secret-read and stale-hold paths in the gate

## Approved decisions (from the spec — implement exactly these)

1. **No self-release.** A hard-coded, non-removable list `_SELF_RELEASE` in
   `tools.py`, checked first in `Policy.check`, ahead of the configured deny
   rules and ahead of the `read` early return. Patterns:
   `r"\bomarchy[-\s]+voice[-\s]+(confirm|say)\b"` and
   `r"omarchy-voice/control\.sock\b"`. A match raises
   `Denied("only the user can confirm a held action")`. `voice cancel`,
   `start`, `stop` and `announce` stay allowed.
2. **Secrets.**
   - Add the deny rule `"secret-proc-environ": r"/proc/(\S*/)?(environ|mem)\b"`.
   - Every secret rule in `DEFAULT_DENY_RULES` that starts with `/\.` instead
     starts with the prefix `_AT = r"""(^|[\s/"'=~])"""` followed by `\.`.
     That is 13 rules: `secret-ssh-dir`, `-gnupg`, `-login-stores`, `-aws`,
     `-gh-token`, `-claude-login`, `-pass-store`, `-keyrings`, `-codex`,
     `-gemini`, `-copilot`, `-claude-desktop`, `-opencode`. Rule names do not
     change.
3. **Holds lapse.**
   - `HOLD_SECONDS = 60` is a constant in `tools.py`, not a config key.
   - The engine runs a timer per hold, guarded by a generation counter.
   - The 60 s count from when she stops speaking (`_voice_until`).
   - When a hold lapses, she drops it, says "That timed out, so I didn't do
     it.", logs `lapse  <held>`, calls `brain.note(...)`, and calls `_settle()`.
   - The MCP `_confirm` drops a hold older than `HOLD_SECONDS`.
4. **Out of scope:** taking the keys out of the CLI's environment, the MCP
   agent-chosen phrase (#4), and the 400-character cut (#10). Each gets a
   follow-up issue (step 6).

Run tests with `nix develop -c python -m pytest -q tests/<file>`. Plain
`python` has no pytest. Baseline on `main`: 1368 passed, 1 skipped.

## Steps

1. **`src/omarchy_voice/tools.py:221-235` (`Policy`): the self-release block.**
   - Add `_SELF_RELEASE` as a module-level tuple just above `class Policy`, with
     a one-line comment explaining why it is in code and not in
     `DEFAULT_DENY_RULES`: users can drop those rules.
   - In `Policy.check`, before the `deny_patterns` loop, raise `Denied` if
     `re.search(p, description, re.IGNORECASE)` matches any pattern in it.
   - Add `HOLD_SECONDS = 60` next to it at module level, with a one-line comment
     pointing at #183. Steps 3 and 5 import it.

   Tests, in `tests/test_policy.py`, a new `SelfReleaseTests` class using
   `Policy(Config())`:
   - Denied: `omarchy voice confirm`, `omarchy voice say yes`,
     `omarchy-voice say yes`,
     `/nix/store/x-omarchy-voice/bin/omarchy-voice-confirm`,
     `socat - UNIX:/run/user/1000/omarchy-voice/control.sock`.
   - Also denied with `read=True`.
   - Also denied with `Policy(Config(deny_patterns=[], deny_patterns_replace=True))`.
   - Not denied: `omarchy voice cancel`, `omarchy voice stop`.

   In `tests/test_claude_backend.py`, using the existing `gate(...)` helper
   (line ~55): `gate(subject, "Bash", {"command": "omarchy-voice say yes"}).behavior == "deny"`.

   → verify by `nix develop -c python -m pytest -q tests/test_policy.py tests/test_claude_backend.py`.
   The new tests fail before the edit and pass after it.

   Traps:
   - The check must come before `if read: return`. Otherwise a `Read` or
     `Grep` call can reach the socket path.
   - Do not add these patterns to `DEFAULT_DENY_RULES`.

2. **`src/omarchy_voice/config.py:137-186` (`DEFAULT_DENY_RULES`): secret paths.**
   - Define `_AT` just above the dict, with a one-line comment: "absolute, `~`
     or relative (#183)".
   - Rewrite the 13 `/\.`-prefixed rules as `_AT + r"\.…"`, keeping each rule's
     tail exactly as it is.
   - Add `"secret-proc-environ"`, with a comment: "the daemon's API keys, via the
     CLI's inherited env".

   Tests in `tests/test_policy.py`. Denied:
   - `read /proc/self/environ`
   - `read /proc/42/environ`
   - `read /proc/self/task/5/environ`
   - `read .aws/credentials`
   - `cat ~/.netrc`
   - `read .config/gh/hosts.yml`
   - `read /home/u/.aws/credentials` (absolute, still denied)

   Not denied: `read /proc/meminfo`.

   In `tests/test_claude_backend.py`:
   `gate(subject, "Read", {"file_path": "/proc/self/environ"}).behavior == "deny"`.

   → verify by `nix develop -c python -m pytest -q tests/test_policy.py tests/test_claude_backend.py tests/test_config.py`.
   Every existing non-match test (`.env.example`, `.envrc`, …) still passes.

   Traps:
   - `_DEFAULT_DENY_NAMES` maps pattern → name, and is built from the dict.
     Check it still resolves, so a refusal names `secret-aws`.
   - `test_config.py` may pin the rule count or the exact pattern strings.
     Update those pins; do not weaken them.

3. **`src/omarchy_voice/local_engine.py`: the hold timer.**
   - Import `HOLD_SECONDS` from `.tools`.
   - In `__init__`, near `self._shown_hold` (~line 245), add
     `self._hold_gen = 0`.
   - **`_show_hold` (~349-361).** After the existing body:
     - Increment `self._hold_gen`, and capture it as `gen`.
     - If `self.loop is not None`, call
       `self.loop.call_soon_threadsafe(self.loop.call_later, HOLD_SECONDS, self._spawn_lapse, gen)`.
   - **Extract `_drop_hold()`** from `_local_cancel` (~1090-1101). It holds
     the `brain_holds` and `drop_pending() or brain.cancel()` logic and returns
     `held | None`. `_local_cancel` calls it, and its behaviour and its "cancel"
     log line stay the same.
   - **`_spawn_lapse(gen)`** is a plain function: `self._spawn(self._lapse(gen))`.
     `call_later` cannot take a coroutine.
   - **`async def _lapse(gen)`:**
     - Return if `gen != self._hold_gen` or `self._held() is None`.
     - Compute `remaining = self._voice_until + HOLD_SECONDS - time.monotonic()`.
     - If `remaining > 0`, run
       `self.loop.call_later(min(remaining, HOLD_SECONDS), self._spawn_lapse, gen)`
       and return.
     - Otherwise:
       - `held = await asyncio.to_thread(self._drop_hold)`. Return if it is None.
       - `self.feedback.log(f"lapse   {held}")`
       - `self.brain.note(f"{held} timed out unconfirmed and was not run")`
       - `await self._say("That timed out, so I didn't do it.")`
       - `self._settle()`

   Tests: a new `HoldLapseTests(EngineTestCase)` in `tests/test_local_engine.py`.
   Patch `local_engine.HOLD_SECONDS` to `0.05`.
   - **Executor hold lapses:** `session.executor.call("omarchy_cli", {"command": "reboot"})`,
     then `await self.until(lambda: session._held() is None)`. The lapse line is
     in `self.mouth.spoken`. `brain.notes` has the note. After that,
     `await session._consent("yes", None)` returns False.
   - **Brain hold lapses:**
     `FakeBrain(["I need a yes for that."], hold="omarchy reboot")`, then
     `_turn()`, then wait. `brain.pending` is None.
   - **Waits for her voice:** set `session._voice_until = math.inf`, make the
     hold, `await asyncio.sleep(0.2)`, and assert it is still held. Then set
     `session._voice_until = time.monotonic()` and assert it lapses.
   - **Confirmed is not lapsed:** make the hold, `await session._local_confirm()`,
     sleep past the timeout. Nothing is spoken about a timeout and nothing
     is logged as a lapse.
   - **A replaced hold's timer is stale:** hold, `_local_cancel`, hold again
     right away. The second hold survives the first timer, and lapses only on
     its own. *(Deviation, done in implementation: the first timer is fired
     by calling `await session._lapse(first_gen)` directly, not by sleeping,
     so the test cannot race under load.)*

   → verify by `nix develop -c python -m pytest -q tests/test_local_engine.py`.

   Traps:
   - `_voice_until` is `math.inf` while she speaks (line 455).
     `call_later(inf)` breaks, hence the `min(remaining, HOLD_SECONDS)`.
   - Use `time.monotonic()` like line 439, not `loop.time()`.
   - `_show_hold` is also called from `_settle` when the shown hold changes.
     The generation bump there is intended: it restarts the clock for the hold
     now on screen.
   - FakeBrain with `hold_at=None` sets `pending` without calling `on_hold`.
     Its hold reaches `_show_hold` through `_settle` after the turn, and the
     test must allow for that.
   - Do not take `Executor._lock` on the loop thread. That is why the drop
     runs under `to_thread`.
   - Existing hold tests must not start lapsing. The real `HOLD_SECONDS` is
     60, so only the new class patches it.

4. **`src/omarchy_voice/mcp_server.py:127-145` (`_confirm`): MCP timeout.**
   - Import `HOLD_SECONDS`.
   - After `waited` is computed, and before the `CONFIRM_DELAY` check: if
     `waited > HOLD_SECONDS`, run `executor.drop_pending()` and return
     `f"ERROR: {held} timed out and was dropped. Ask the user again if they still want it."`.

   Test in `tests/test_mcp.py`, following `test_the_users_word_releases_it`
   (~line 156):
   - Call `self.hold()`.
   - Set `self.executor.pending_since -= HOLD_SECONDS + 1`.
   - `call("confirm_last", {"phrase": "confirm"})` contains "timed out".
   - `self.executor.pending` is None.

   → verify by `nix develop -c python -m pytest -q tests/test_mcp.py`.

   Traps:
   - `pending_since` can be None (the `or 0.0` already there). With None,
     `waited` is huge, but `pending` is checked first, so that cannot happen
     in practice. Leave the existing guard as it is.

5. **Whole suite and flake.**
   - `nix develop -c python -m pytest -q tests` should give 1368 + the new
     tests passed, 1 skipped, 0 failed.
   - `nix flake check` should pass.

   Traps: none.

6. **(Session model, not coder.) Follow-ups and PR.**
   - Open three issues: removing the keys from the CLI's environment, MCP
     out-of-band consent (#4), and the deny check seeing uncut input (#10).
   - Run the fresh-agent Opus review against this plan and `git diff main`.
   - Open the PR linking intent, spec and plan, and say which steps the coder
     did.
   - Manual check on the running daemon (dry-run): hold a `reboot`, wait 60 s,
     then hear the lapse line and see the card close.

## Tests

- Run `nix develop -c python -m pytest -q tests`.
  - Expected: all pass, with the new tests in `test_policy.py`,
    `test_claude_backend.py`, `test_local_engine.py` and `test_mcp.py`.
  - Each new test fails when run against `main`: spot-check one per step with
    `git stash`.
- Run `nix flake check`. It should pass.

## Rollback

- Each step is its own commit. Revert in reverse order.
- Revert step 2 alone if the wider secret prefix denies something users rely
  on. A user can also drop a single rule by name with `deny_patterns_remove`
  without any code change.
- Revert step 3 alone if the timer misbehaves. Steps 1, 2 and 4 do not depend
  on it, except that they import `HOLD_SECONDS`, which step 1 defines.
