---
status: approved
issue: 120
intent: intent/2026-09-24-120-barge-in-test-under-load.md
---

# Spec: the microphone gate tests wait on events, and a failed turn no longer ends listening

Closes #120.

## The intent's open questions, answered

The intent was approved without answers to its four questions. Each one is
decided here, with the reason. **Any of them can be rejected at this gate**,
and the rest of the spec changes with it.

1. **Test rewrite shape: events, not a stepped clock.** Nothing in these
   tests is about time. #86's stepped clock (`SpokenConsentTests`,
   `tests/test_local_engine.py:624`) exists because consent reads
   `time.monotonic()`. The gate tests ask "did the recorder reopen while the
   mouth was inside a sentence?", which is an ordering question. Two changes
   answer it without a clock and without the thread pool:
   - The test's waits are polled from the event loop (`until`, which already
     exists at `:1254` in `WatchAnnounceTests`). They never take a worker, so
     they cannot compete with the code under test for one.
   - The fake microphone records the answer itself, at the moment it
     reopens: it reads the fake mouth's `holding` flag and sets an event
     `over_her`. Then it blocks until cleanup, so the loop cannot spin. The
     test asserts that event. It no longer checks a flag later, at a moment
     that depends on scheduling.

   The prototype found that "the recorder reopened" is not the same as
   "the recorder reopened while she was speaking". With 2 workers the
   recorder often reopens *before* the mouth starts. With 1 worker it reopens
   only *after* the mouth's 30 s deadlock guard gave up. With only the waits
   moved off the pool, the barge-in test passed both ways. The 1-worker case
   was observed passing after 30 s, because a mouth that gave up also
   appends nothing, so `spoken == []` still held. The `over_her` event
   closes that gap.
2. **Scope: all three `MicrophoneGateTests`, plus `Mouth.wait_until_speaking`
   for every caller.** All three use `wait_until_speaking` (`:123`), which is
   the `to_thread(started.wait, 30)` the intent measured (`:125`). The shut
   test also uses `to_thread(again.wait, …)` (`:311`, `:318`). The toggle
   test ends on `asyncio.sleep(0.05)` (`:366`). That sleep is a clock bet,
   and it passes even when nothing was spoken at all. `SpokenConsentTests`
   calls `wait_until_speaking` too (`:728`), so it gets the pool-free wait
   for free. Out of scope: the other `to_thread(...wait)` and `sleep` waits
   in `WatchAnnounceTests` and elsewhere (`:856`, `:1234`, `:1384`). They
   have no reported failure and do not share the spinning fake microphone.
   They can be filed if anyone wants them.
3. **Real bug: fix it here.** The intent says an exception in `_turn` "ends
   listening silently". Reading `run()` corrects that. The exception leaves
   `_listen_loop` (`src/omarchy_voice/local_engine.py:564-584`, no `try`),
   and `run()` catches it (`:833-837`). `run()` logs `error`, sets the state
   to error and exits 1. systemd then restarts the daemon
   (`nix/hm-module.nix:305-306`, `Restart = "on-failure"`, 3 s). So it is
   not silent, but it is worse than silent. One bad transcription, for
   example a whisper error that is not `Unavailable`, costs the warm Claude
   session (6.5 s to rebuild, and the conversation's context is lost). It
   also leaves the daemon **muted** after the restart, so the user has to
   press the toggle again. `_wake_turn` already survives this with a log and
   a 2 s pause (`:389-391`). `_listen_loop` should do the same. The change
   is small, and it is in the file this task touches, so it stays in scope
   rather than going to a new issue. Split it out only if the approver
   wants test-only changes in this PR.
4. **Diagnostics: yes.** Every gate-test failure message ends with
   `[captures=N, mouth started=…, mouth holding=…, listen loop running |
   cancelled | died: <exception>]`. That says which turn the loop reached and
   whether the listen task is alive or died, and with what. Those are the
   intent's unmeasured candidates (a dead loop, a starved pool, a spinning
   loop), each told apart by one line of output.

## Design

### Tests (`tests/test_local_engine.py`)

1. **`until` moves up** from `WatchAnnounceTests` (`:1254-1258`) to
   `EngineTestCase` (`:167`), unchanged. `WatchAnnounceTests` inherits it.
   There is one polling helper, and no worker is taken while waiting.
2. **`Mouth`** (`:99`): adds a `holding` flag, set on entry to `__call__`
   (`:116`) and cleared on exit. `wait_until_speaking(case, why=…)` (`:123`)
   becomes `case.until(self.started.is_set, 30)`, with `why()` appended to
   the message.
3. **`Ears`** (`:129`): gains `over_her = threading.Event()` and
   `shut = None`. The behaviour only changes when a test sets `shut`, so
   `TurnTests`, `OnsetEars` and `SpokenConsentTests` are unaffected. When
   `shut` is set, from the second capture on:
   - if `case.mouth.holding`: set `over_her`, then block on `shut` (bounded
     at 30 s), and return `b""`;
   - otherwise: wait 10 ms and return `b""`. This is a quiet room, and the
     loop turns over slowly, as `WatchAnnounceTests.quiet` (`:1261`) already
     does.

   `again` is still set on every reopening, so the shut test's "it reopens
   after she finishes" keeps its meaning.
4. **`MicrophoneGateTests.running`** (`:288`): sets `ears.shut`, releases it
   in cleanup, keeps the task on `self.loop`, and adds `why()`.
5. **Shut test** (`:294`): its waits become `until(ears.again.is_set, 1.5)`
   and `until(…, 30)`. The negative check also fails if `over_her` was ever
   set. The 1.5 s stays a bound that load can only make *more* true, as its
   docstring already argues.
6. **Barge-in test** (`:326`): waits for `until(ears.over_her.is_set, 30)`,
   not `again`. It keeps `mouth.spoken == []`.
7. **Toggle test** (`:350`): replaces `asyncio.sleep(0.05)` (`:366`) with
   `await session._speech.join()`. When `join` returns, "One." has been
   spoken and nothing is left in the queue, so "Two. was not spoken" is
   proven rather than sampled.
8. **New tests**, both in `MicrophoneGateTests`, with
   `TURN_FAILURE_PAUSE` patched to 0.01:
   - *a turn that raises does not end listening*: the first `transcribe`
     raises `RuntimeError`. The loop stays alive. The mouth says "Something
     went wrong with that." and then the brain's reply. The brain is asked
     the second utterance. The log has `error   turn: RuntimeError: …`.
   - *a turn that always raises mutes*: after 3 failures in a row,
     `session.active` is False and the last line spoken is the mute line.
     This runs with `barge_in` on and off.

### Engine (`src/omarchy_voice/local_engine.py`)

`_listen_loop` (`:564-584`): the body of the `while` goes inside
`try/except Exception`. `CancelledError` is re-raised explicitly, as
`_wake_turn` and `_speech_loop` already do. On an exception:

- log `error   turn: <Type>: <msg>`;
- count failures in a row. A pass through the loop body that completes
  resets the count to 0;
- below `TURN_FAILURES_TO_MUTE = 3`: `_say("Something went wrong with
  that.")`. This is the line `_answer` already uses for a brain failure
  (`:480`), so there is no new wording;
- at 3: `_set_active(False)` **first**, then `_say("Listening keeps
  failing, so I have stopped. The log says why.")`. Muting drops queued
  speech (`_set_active` → `_drop_queued_speech`, `:653`). The prototype
  showed that with `barge_in` on, saying the line first and then muting
  drops it unspoken;
- always `await asyncio.sleep(TURN_FAILURE_PAUSE)` (2.0 s, the
  `_wake_turn` value). A failure that repeats can therefore never spin hot,
  even after the mute, for example if `_wait_for_toggle` itself raised.

The two constants sit next to `NOT_CAUGHT` (`:94`), as module constants so
the tests can patch them. There is no config knob, because nobody tunes this.

The prototype diff is 32 engine lines. `run()`, `_turn` and `_answer` do
not change.

## Alternatives rejected

- **A stepped clock like #86's.** It fits code that reads time. Here the
  engine reads no clock on the path under test, so a clock would be one more
  fake and would prove nothing more.
- **Longer timeouts, or skipping the test under load.** These hide the
  problem, and the intent measured that load was not the cause.
- **Giving the test its own `ThreadPoolExecutor(8)`, as `_run_until_done`
  does in production** (`realtime.py:1710-1714`). This would make the pool
  size fixed rather than CPU-dependent. It is not needed, because asyncio's
  default is `min(32, cpus + 4)` ≥ 5 on every machine, and barge-in needs 2.
  It would also hide the one real signal the new test gives at 1 worker (see
  Risks).
- **Catching the exception in `_turn` rather than in `_listen_loop`.** The
  announcement branch (`:572-578`) can raise outside `_answer`'s `try` as
  well, for example in `_settle` or in `feedback.state`. Catching in the
  loop covers every branch with one `try`.
- **Backing off exponentially and never muting.** That leaves the
  microphone open in a loop known to be broken. Muting after 3 failures
  matches what `_turn` already does when whisper is `Unavailable`
  (`:313-319`), and the pause bounds the rate either way.
- **A separate issue for the loop fix.** It is about 30 lines in the file the
  tests cover, and the recovery test belongs in this class. Splitting it
  would put two PRs on the same lines. The approver can still ask for a
  split.

## Risks

- **1 worker is not a passing condition for the barge-in test, by design.**
  With a gated mouth, barge-in needs two workers at once: one for the mouth
  and one for the recorder. The real mouth is the same, because
  `_speech_loop` runs `_speak_now` through `to_thread` (`:232`). At 1 worker
  the rewritten test **fails, 20 of 20**, after 30 s, with
  `the microphone never reopened while she was speaking … [captures=3–4,
  mouth started=True, mouth holding=False, listen loop running]`. Today's
  test at 1 worker fails with the wrong message ("never handed to the
  mouth", as the intent measured). A rewrite without `over_her` passes
  falsely once the mouth's guard expires. Failing is the honest result,
  because production's owned pool is 8 and asyncio's default
  is ≥ 5. The other three tests pass 20 of 20 at 1 worker.
- **Overlap with #114** (`fix/114-every-voice-gates-the-mic`, spec drafted
  at 21d2e36, not merged). #114 changes `_record` (a capture waits out her
  voice and the echo tail, and a shut capture returns `None`), `_say`
  (waits for "mic shut"), `_turn` (returns on `None`) and deletes
  `_answer`'s tail sleep. It lists `_listen_loop`'s announcement block as
  unchanged. All of its new behaviour is behind `not barge_in`, so the
  barge-in test's path does not move. The overlap is therefore:
  - *Textual*: this spec re-indents `_listen_loop`'s body (`:571-584`), and
    #114 edits nearby functions. Both add tests to
    `tests/test_local_engine.py` and reuse `Mouth`/`Ears`. Whichever lands
    second rebases. The conflicts are mechanical.
  - *Semantic, shut test* (`barge_in` off): under #114 the second capture
    waits for `_speech.join()` plus `ECHO_TAIL_SECONDS` inside `_record`
    rather than in `_answer`. Because `Ears` only sees the capture once
    `record_utterance` is called, `over_her` can never be set in the shut
    test, which is exactly #114's guarantee. That test should pass unchanged
    on top of #114, and the plan re-runs it after the rebase.
  - *Semantic, recovery*: a turn that raises under #114 is still caught here.
    #114's `None` return is not an exception and resets the failure count.
  - #114's spec says it "must not edit the barge-in test. #120 owns that".
    This spec keeps to that split.
- **The loop fix changes production behaviour** (the intent's constraint:
  "not to make a test pass"). It is not done to make a test pass. The
  rewritten gate tests pass without it, and it has its own tests. What
  changes for the user: a turn that raises is now one spoken line and a 2 s
  pause, not a daemon restart that leaves them muted. If `_say` or `_set_active`
  raise inside the handler (for example a full disk on `feedback.log`),
  the exception propagates to `run()` as it does today. That is no worse.
- **A pre-existing sibling of the mute-order bug.** `_turn`'s `Unavailable`
  path (`:317-318`) says its line and then mutes, so with `barge_in` on that
  line is probably dropped as well. Not fixed here. It should be filed.
- **Hosts:** the local engine only (p620, razer). CI and `nix flake check`
  run the tests. The realtime engine is untouched.

## Verification

Demonstrated in a scratch copy of the prototype (`git checkout -- src/
tests/` afterwards, so only this file is committed). The 4
`MicrophoneGateTests` (the 3 rewritten ones and the recovery test) were run
20 times per condition with `nix develop -c python -m pytest -k
MicrophoneGate`, with `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`.
The pool was capped by a scratch plugin that sets `max_workers` on
asyncio's default `ThreadPoolExecutor`. CPU contention used the intent's
method: N busy loops pinned with `taskset` to the CPUs the test was pinned
to, each killed after its run and bounded by `timeout 90`. Load average was
21–23 before the stress runs. `ps` found no spinner left afterwards.

| Condition | barge-in | stays shut | toggle drops | turn raises → recovers |
| --- | --- | --- | --- | --- |
| default pool (32 workers), unpinned | 20/20 | 20/20 | 20/20 | 20/20 |
| pool capped at 2 | 20/20 | 20/20 | 20/20 | 20/20 |
| pool capped at 1 | **0/20**, fails with the pool-starved message (see Risks) | 20/20 | 20/20 | 20/20 |
| 2 CPUs + 16 spinners | 20/20 | 20/20 | 20/20 | 20/20 |
| 1 CPU + 8 spinners | 20/20 | 20/20 | 20/20 | 20/20 |

Under contention the 4 tests took 7.5–8.3 s (2.6 s unloaded), and each
spinner batch lived 16–20 s.

Recovery, before and after:

- `origin/main` engine: *never recovered: captures=1 loop done=True
  RuntimeError('whisper fell over')*. The listen loop died on the first
  turn.
- prototype engine: the loop is alive, the mouth said `["Something went
  wrong with that.", "Closed."]`, the second utterance reached the brain,
  and the log has the `error   turn:` line.
- persistent failure, `barge_in` off and on: after 3 captures, `active` is
  False and the mouth said the error line twice and then the mute line. With
  the old order (say, then mute) and `barge_in` on, the mute line was
  dropped. That is why the order is reversed.
- Whole suite with the prototype: 1077 passed, 709 subtests.

For the implementation PR:

1. `nix develop -c pytest -q tests/` is all green.
2. The four gate tests and the mute test, 20× with the default pool, 20×
   under 16 spinners on 2 CPUs. All pass.
3. The barge-in test with the pool capped at 1 fails in about 30 s with the
   `over_her` message and the diagnostics. It does not pass, and it does
   not fail with "never handed to the mouth".
4. The recovery test fails against the unchanged `_listen_loop` (it is red
   before the engine change and green after).
5. `nix flake check` passes.
6. After #114 lands (or before, whichever is second): the shut test and
   the barge-in test still pass unchanged.
