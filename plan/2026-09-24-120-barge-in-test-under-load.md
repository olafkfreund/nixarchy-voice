---
status: approved
issue: 120
spec: spec/2026-09-24-120-barge-in-test-under-load.md
---

# Plan: the microphone gate tests wait on events, and a failed turn no longer ends listening

Closes #120. Branch `fix/120-barge-in-test-under-load`, based on
`origin/main` at fc33ae2 (1075 passed, 709 subtests; `unittest` 1075 OK,
re-measured when this plan was written).

Every `file:line` below was checked against `origin/main` (fc33ae2). The
branch carries only the intent and spec commits, so `src/` and `tests/`
are identical to main.

Every run in this plan exports
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`.

## Approved decisions, carried over from the spec

You can implement from this list without opening the intent or the spec.

1. **Waits are polled on the event loop, never on the thread pool.**
   `until(predicate, seconds)`, now at `tests/test_local_engine.py:1254-1258`
   in `WatchAnnounceTests`, moves unchanged up to `EngineTestCase` (`:167`).
   `WatchAnnounceTests` inherits it. It reads `loop.time()`, which
   `SpokenConsentTests`' stepped clock does not touch, because that clock
   patches only `local_engine.time` (`:640-645`). No test wait in
   `MicrophoneGateTests` calls `asyncio.to_thread(...wait)`.
2. **All three `MicrophoneGateTests` are rewritten, and `Mouth.wait_until_speaking`
   changes for every caller.** `wait_until_speaking(case, why=…)` (`:123-126`)
   becomes `case.until(self.started.is_set, 30)`, with `why()` appended to
   its message. `SpokenConsentTests` (`:728`) picks it up unchanged.
   Out of scope: the other `to_thread(...wait)` and `sleep` waits (`:856`,
   `:1234`, `:1384`). They have no reported failure.
3. **`Mouth` gains `holding`** (`:99`). It is set on entry to `__call__`
   (`:116`) and cleared on exit (in a `finally`).
4. **The fake microphone flags a reopen mid-sentence and then blocks.**
   `Ears` (`:129`) gains `over_her = threading.Event()` and `shut = None`.
   With `shut` left at `None`, nothing changes, so `TurnTests`, `OnsetEars`
   and `SpokenConsentTests` are unaffected. With `shut` set, from the second
   capture on:
   - if `case.mouth.holding`: set `over_her`, then `shut.wait(30)`, then
     return `b""`;
   - otherwise: `threading.Event().wait(0.01)` and return `b""` (a quiet
     room, as `WatchAnnounceTests.quiet` at `:1261` does).

   `again` is still set on every reopening.
5. **`MicrophoneGateTests.running`** (`:288`) sets `self.ears.shut =
   threading.Event()`, releases it in cleanup, stores the task on the test
   as `self.loop` (not the same thing as `session.loop`, which `build` sets
   at `:191`), and the class gains `why()` (decision 11).
6. **Shut test** (`:294`): `until(ears.again.is_set, 1.5)` must be False,
   then after `mouth.release.set()`, `until(ears.again.is_set, 30)` must be
   True. The negative check also fails if `over_her` was ever set. The 1.5 s
   stays: load can only make it more true.
7. **Barge-in test** (`:326`) waits on `until(ears.over_her.is_set, 30)`,
   not on `again`. It keeps `mouth.spoken == []`.
8. **Toggle test** (`:350`): `asyncio.sleep(0.05)` (`:366`) becomes a queue
   wait, `await session._speech.join()`. When it returns, "One." has been
   spoken and the queue is empty, so "Two. was not spoken" is proven, not
   sampled.
9. **`_listen_loop` recovers from a turn that raises**
   (`src/omarchy_voice/local_engine.py:564-584`). The body of its `while`
   goes inside `try`, with `except asyncio.CancelledError: raise` (as
   `_wake_turn` does at `:384-385`) and then `except Exception as exc`:
   - log `error   turn: <Type>: <msg>`;
   - count failures in a row. A pass through the body that completes resets
     the count to 0;
   - below `TURN_FAILURES_TO_MUTE = 3`: `await self._say("Something went
     wrong with that.")`. This is the existing line from `_answer` (`:480`);
   - at 3: `await self._set_active(False)` **first**, then `await
     self._say("Listening keeps failing, so I have stopped. The log says
     why.")`. Muting drops queued speech (`_set_active` →
     `_drop_queued_speech`, `:653`). With `barge_in` on, the old order
     (say, then mute) drops the line unspoken;
   - always `await asyncio.sleep(TURN_FAILURE_PAUSE)`, 2.0 s, the
     `_wake_turn` value (`:391`). A repeating failure can never spin hot,
     even after the mute.

   `TURN_FAILURES_TO_MUTE = 3` and `TURN_FAILURE_PAUSE = 2.0` are module
   constants next to `NOT_CAUGHT` (`:94`), so tests can patch them. There is
   no config knob. `run()`, `_turn` and `_answer` do not change.
10. **Two new tests in `MicrophoneGateTests`**, with `TURN_FAILURE_PAUSE`
    patched to 0.01:
    - *a turn that raises does not end listening*: the first `transcribe`
      raises `RuntimeError`. The loop stays alive, the mouth says "Something
      went wrong with that." and then the brain's reply, the brain is asked
      the second utterance, and the log has `error   turn: RuntimeError: …`.
    - *a turn that always raises mutes*: after 3 failures in a row,
      `session.active` is False and the last line spoken is the mute line.
      This runs as two subtests, `barge_in` off and on.
11. **Failure diagnostics.** Every gate-test failure message ends with
    `[captures=N, mouth started=…, mouth holding=…, listen loop running |
    cancelled | died: <exception>]`, built by `MicrophoneGateTests.why()`
    from `self.ears`, `self.mouth` and `self.loop`.
12. **The measured table stands, including a failure at 1 worker that is by
    design.** With the pool capped at 1, the barge-in test fails 20/20 after
    about 30 s with `the microphone never reopened while she was speaking …
    [captures=3–4, mouth started=True, mouth holding=False, listen loop
    running]`. It must not pass, and it must not fail with "never handed to
    the mouth". Barge-in needs two workers at once (mouth and recorder), and
    so does the real engine (`_speech_loop` → `to_thread`, `:232`).
    Production owns a pool of 8, and asyncio's default is `min(32, cpus+4)`
    ≥ 5. The failure message names this, so nobody "fixes" it later:
    `the microphone never reopened while she was speaking — barge_in is not
    letting the turn move on (this needs 2 free worker threads: 1 fails by
    design, see #120)`. The other three tests pass 20/20 at 1 worker.

    | Condition | barge-in | stays shut | toggle drops | turn raises → recovers |
    | --- | --- | --- | --- | --- |
    | default pool, unpinned | 20/20 | 20/20 | 20/20 | 20/20 |
    | pool capped at 2 | 20/20 | 20/20 | 20/20 | 20/20 |
    | pool capped at 1 | **0/20**, fails by design | 20/20 | 20/20 | 20/20 |
    | 2 CPUs + 16 spinners | 20/20 | 20/20 | 20/20 | 20/20 |
    | 1 CPU + 8 spinners | 20/20 | 20/20 | 20/20 | 20/20 |

13. **Landing order: #120 lands first, then #114 rebases onto it.** #114
    (`fix/114-every-voice-gates-the-mic`, spec approved at e577eae, local
    only, not merged) edits these parts of `local_engine.py`: `_say`
    (`:244-256`), `_record` (`:271`), `_turn` (`:301-329`) and the tail
    sleep in `_answer` (`:506`). This plan re-indents `_listen_loop`'s body
    (`:571-584`) and adds two constants at `:94`. The hunks are close
    together but do not overlap, so the rebase conflicts will be mechanical.
    Both PRs add tests to `tests/test_local_engine.py` and reuse
    `Mouth`/`Ears`, so #114 rebases onto the new `Ears(shut, over_her)`.
    After the rebase, #114 re-runs the shut and barge-in tests unchanged
    (step 9). #114 must not edit the barge-in test.

### Resolutions the spec left open (flagged for the reviewer)

- **A. `why()` in the toggle test.** The toggle test never calls
  `running`, so there is no `self.loop`. `why()` reports `listen loop not
  started` in that case (`getattr(self, "loop", None)`).
- **B. The toggle test's `join` is bounded.** A bare `join()` hangs the
  suite if a drop ever forgets `task_done`. It is written
  `await asyncio.wait_for(session._speech.join(), 30)`, the same 30 s bound
  as the mouth's guard. It also asserts `mouth.spoken == ["One."]`, so it
  can no longer pass when nothing was spoken at all, which was the spec's
  complaint about the sleep.
- **C. The recovery test also checks the count reset (decision 9).** None of
  the spec's tests catches "a completed pass resets the count". The fake
  `transcribe` in *a turn that raises does not end listening* raises on
  calls 1, 2 and 4 and returns `self.heard` otherwise. Its first call still
  raises, as the spec says. Without the reset, call 4 would be the third
  failure and would mute. The test waits for 5 captures and asserts
  `session.active` is still True.
- **D. The mute test checks the pause.** It patches `TURN_FAILURE_PAUSE`
  to 0.05 and asserts at least 0.1 s of `loop.time()` between the start and
  the mute (two pauses happen before the third failure). Load can only make
  that longer. Without this, "always pause" has no test. It also asserts
  `ears.captures == 3` once muted (the loop then waits for the toggle and
  records nothing), so the threshold is pinned rather than "eventually".
- **E. The mute test asserts only `spoken[-1]`** plus that the error line
  was said at least once. It does not assert "error line exactly twice".
  With `barge_in` on, the mute at failure 3 may drop an error line that is
  still queued, and that timing is not something the test controls.

## Steps

0. **Baseline.** `git switch fix/120-barge-in-test-under-load && git rebase
   origin/main`. Then run `nix develop -c pytest tests -q` and
   `nix develop -c python3 -m unittest discover -s tests` → verify by 1075
   passed with 709 subtests, and `Ran 1075 … OK`. `git diff origin/main --
   src tests` is empty.
1. `tests/test_local_engine.py`: move `until` from `WatchAnnounceTests`
   (`:1254-1258`) to `EngineTestCase`, unchanged (decision 1) → verify by
   `pytest tests/test_local_engine.py -q -k WatchAnnounce` green and
   `grep -c "def until" tests/test_local_engine.py` = 1.
2. `tests/test_local_engine.py`: in `Mouth`, add `holding` and set it in
   `__call__` with try/finally. `wait_until_speaking(case, why=lambda: "")`
   polls `case.until(self.started.is_set, 30)` and appends `why()` to the
   message (decisions 2 and 3) → verify by `pytest tests/test_local_engine.py
   -q` green, including `SpokenConsentTests::test_e3_barge_in_echo_while_playing`.
3. `tests/test_local_engine.py`: in `Ears`, add `over_her` and `shut` and the
   shut-only branch (decision 4). Then, in `MicrophoneGateTests`: `running`
   sets `ears.shut` and stores `self.loop`, plus `why()` (decisions 5 and 11,
   and resolution A) → verify by `pytest tests/test_local_engine.py -q`
   green. With `shut` unset nothing changes.
4. `tests/test_local_engine.py`: rewrite the shut, barge-in and toggle tests
   (decisions 6, 7, 8 and 12, and resolution B), each message ending
   `+ self.why()` → verify by `pytest -k MicrophoneGate` 3 passed, then the
   20× loop (Tests §2) 20/20.
5. `tests/test_local_engine.py`: add the two new tests (decision 10, and
   resolutions C, D, E), patching `local_engine.TURN_FAILURE_PAUSE` with
   `mock.patch.object(..., create=True)` so they can run before the
   constant exists → verify **red on main's engine**: the recovery test
   fails with `listen loop died: RuntimeError(...)` in its message. The mute
   test fails too. Commit nothing yet.
6. `src/omarchy_voice/local_engine.py`: add `TURN_FAILURES_TO_MUTE` and
   `TURN_FAILURE_PAUSE` after `NOT_CAUGHT` (`:94`). Wrap the body of
   `_listen_loop` (`:571-584`) as in decision 9, mute before the last line.
   Then remove `create=True` from step 5 → verify by
   `pytest -k MicrophoneGate` 5 passed, and by `git diff --stat` showing
   about 32 engine lines.
7. Full suites, both runners, then `nix flake check --no-write-lock-file`
   **twice** (Tests §1 and §5) → verify by all green, both flake runs.
8. Mutation checks (Tests §4). Each mutation is applied, run and reverted
   with `git checkout -- src tests` → verify by every mutation giving the
   expected red, and `git diff` being empty afterwards except for the real
   change.
9. After #114 rebases onto merged #120, the #114 implementer runs
   `pytest -k "MicrophoneGate"` on the rebased branch → verify by the shut
   and barge-in tests passing unchanged. This is recorded in #114's PR, not
   in this one.

Commit each step's code together with its tests, as
`test(local): …` for steps 1–5 and `fix(local): …` for step 6, all `(#120)`.
The PR description links the intent, spec and plan.

## Tests

1. **Whole suite, both runners:**
   `nix develop -c pytest tests -q` → 1077 passed (1075 + 2 new), 709+
   subtests, and
   `nix develop -c python3 -m unittest discover -s tests` → `Ran 1077`, OK.
2. **Repeat loop, default pool, 20/20:**
   `for i in $(seq 20); do nix develop -c pytest -q tests/test_local_engine.py
   -k MicrophoneGate || echo FAIL $i; done` → no `FAIL`, 5 passed each run.
   Then again under load: `taskset -c 0,1` on the pytest command, with 16
   `timeout 90 taskset -c 0,1 sh -c 'while :; do :; done' &` spinners killed
   after the run → 20/20. Check `ps` for leftover spinners.
3. **Pool capped at 1 (decision 12).** Use a scratch plugin in the
   scratchpad, never committed, as `pool_cap.py`:

   ```python
   import concurrent.futures as cf, os
   _init = cf.ThreadPoolExecutor.__init__
   def init(self, max_workers=None, *a, **k):
       if k.get("thread_name_prefix") == "asyncio":
           max_workers = int(os.environ["POOL_CAP"])
       _init(self, max_workers, *a, **k)
   cf.ThreadPoolExecutor.__init__ = init
   ```

   `nix develop -c env PYTHONPATH=<scratch> POOL_CAP=1 python3 -m pytest -p
   pool_cap -q tests/test_local_engine.py -k MicrophoneGate` → the barge-in
   test fails in about 30 s with "never reopened while she was speaking",
   the 2-worker note and `listen loop running`. It does **not** fail with
   "never handed to the mouth". The other 4 pass. With `POOL_CAP=2` → 5
   passed. This is not a CI test. It is recorded in the PR.
4. **Mutation checks, one per decision.** Each is reverted after.

   | Decision | Mutation | Expected |
   | --- | --- | --- |
   | 1, 2 (waits off the pool) | `wait_until_speaking` back to `to_thread(self.started.wait, 30)` | at `POOL_CAP=1` (`--durations=0`), the shut and toggle tests fail or take ≥ 30 s: the wait and the mouth queue behind each other for the one worker. Rewritten, each takes < 5 s |
   | 3, 4, 7 (`over_her`) | barge-in test waits on `ears.again` instead | at `POOL_CAP=1` the barge-in test **passes** after about 30 s. That false pass is why `over_her` exists |
   | 4, 6 (shut test) | `_say`: drop `if not self.config.barge_in:` so it never joins | shut test fails: reopened while speaking, `over_her` set |
   | 7 (barge-in) | `_say`: always `join()`, ignoring `barge_in` | barge-in test fails with the `over_her` message after 30 s |
   | 8 (toggle join) | `_drop_queued_speech`: delete `self._speech.task_done()` | toggle test fails on `wait_for` after 30 s, and the suite does not hang |
   | 9 (recovery) | engine as on main (no `try`) | recovery test fails: `listen loop died: RuntimeError` (step 5) |
   | 9 (count reset) | never reset the count on a completed pass | recovery test fails: muted after capture 4 |
   | 9 (mute order) | `_say` the mute line, then `_set_active(False)` | mute test, `barge_in=True` subtest fails: last line is not the mute line |
   | 9 (pause) | delete the `asyncio.sleep(TURN_FAILURE_PAUSE)` | mute test fails the ≥ 0.1 s check |
   | 9 (threshold) | `TURN_FAILURES_TO_MUTE = 4` | mute test fails: `captures == 4`, not 3 (resolution D) |
   | 11 (diagnostics) | none. Read the message from Tests §3 | it contains `captures=`, `mouth holding=False`, `listen loop running` |

   `except asyncio.CancelledError: raise` has no mutation that a test
   catches, because `except Exception` does not catch `CancelledError`. It
   stays to mirror `_wake_turn` and `_speech_loop`, and to be explicit.
5. **`nix flake check --no-write-lock-file`, run twice.** This was the
   flaky signal in #120. Both runs must be green. A single green run proves
   nothing about a flake.

## Deviation found while implementing (2026-09-24)

- **Tests §4, the `over_her` mutation.** At `POOL_CAP=1`, the barge-in test
  waiting on `ears.again` passes in about 0.03 s, not "after about 30 s".
  The false pass the row exists to show is confirmed. Only the timing in
  the table was wrong.
- **The recovery test fails fast when it mutes.** Its wait also ends when
  `session.active` goes False, so the count-reset mutation now fails at
  once with "failures that were not in a row muted listening" and
  `captures=4`, not after the 30 s bound with the generic message. It still
  asserts 5 captures, a live loop and `session.active`.
- **`running(session, shut=False)`.** The two new tests need a turn from
  every capture, so `running` takes `shut=False` and then leaves `ears.shut`
  at `None`. The gate tests call it as before. This was not in decision 5.
  It landed in the step 5–6 commit without this note.

## Rollback

- Before merge: drop the branch. Only `tests/test_local_engine.py`,
  `src/omarchy_voice/local_engine.py` and the three docs change.
- After merge: `git revert` the merge commit. The engine change and the test
  rewrite are separate commits, so the recovery fix (step 6 and its tests)
  can be reverted alone, which restores "exit 1 and restart muted", without
  losing the event-driven gate tests.
- If #114 has already rebased onto this, revert #114 first or resolve the
  `_listen_loop` hunk by hand. The textual overlap is only that hunk and
  the shared `Ears`/`Mouth` helpers.
- No config, no Nix module and no state files change, so nothing needs
  migrating on p620 or razer.
