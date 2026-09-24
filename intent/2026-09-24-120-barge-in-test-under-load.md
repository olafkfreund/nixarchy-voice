---
status: approved
issue: 120
author: olafkfreund
---

# Intent: the barge-in gate test must not depend on how loaded the machine is

Closes #120.

## Problem

`tests/test_local_engine.py::MicrophoneGateTests::test_barge_in_lets_the_microphone_stay_open_while_she_talks`
(test at `tests/test_local_engine.py:326`) failed twice in `nix flake check`
on p620 at load average 23–33, with *"the sentence was never handed to the
mouth at all"*: the 30 s `to_thread(self.started.wait, 30)` in
`Mouth.wait_until_speaking` (`tests/test_local_engine.py:125`) returned False.
CI on `main` (fc33ae2) passed, and the test passes alone in the dev shell.

The question the issue leaves open is whether this is a slow-machine test
problem or a real starvation bug in the engine (the mouth thread never
running because the thread pool is full). Measured on `origin/main` fc33ae2,
Python 3.14.7 from the dev shell, p620 (128 CPUs) at load average 31–36:

### The path from the brain's sentence to the mouth

- `_answer` → `_say` (`src/omarchy_voice/local_engine.py:244`) puts the
  sentence on `_speech`; with `barge_in` on it does not `join()`
  (`:255-256`), so the turn returns at once.
- `_speech_loop` (`:222`) takes it and runs the mouth with
  `asyncio.to_thread(self.feedback._speak_now, text)` (`:232`). In the test,
  `_speak_now` is the gated `Mouth` (`tests/test_local_engine.py:198`), which
  sets `started` and then blocks until cleanup.
- `to_thread` is the running loop's **default executor**. In production that
  is the owned `ThreadPoolExecutor(max_workers=8)` set by `_run_until_done`
  (`src/omarchy_voice/realtime.py:1710-1714`), which the local engine also
  uses (`src/omarchy_voice/local_engine.py:917`). In the test there is no
  owned executor: it is asyncio's default, `min(32, os.process_cpu_count()+4)`
  = **32** on p620. The Nix sandbox does not narrow that: `nix-daemon`'s
  `Cpus_allowed_list` is `0-127`, `cores = 0`, no `CPUAffinity`.
- Per turn, the listen loop takes one worker at a time: `_record`
  (`:292`), `_hear` (`:410`), `_route` (`:520`, added by #71). The test itself
  takes one more for its own waiter (`tests/test_local_engine.py:125`, then
  `:340`), the mouth one. Peak: 3 workers.

### What today's merges put on this test's loop

The test builds a `LocalSession` and runs `_speech_loop` and `_listen_loop`
only (`tests/test_local_engine.py:199`, `:288-292`). It never calls `run()`,
so the watch loop, the resident whisper server and the control socket are
never started (`src/omarchy_voice/local_engine.py:802-815`). `git log -G
'to_thread|create_task|_spawn' 5959d84..origin/main -- local_engine.py`:

| Commit | Added | On this test's path? |
| --- | --- | --- |
| 91073f1 #74 | `to_thread(self.executor.poll_watches)` in `_watch_loop` (`:595`), `create_task(self._watch_loop())` in `run()` (`:803`) | No — `run()` only |
| fefdf80 #72 | `to_thread(listen_local.Server.start)` (`:815`), `to_thread(server.stop)` (`:848`) in `run()`; `_hear` reworded, still one `to_thread` | Only `_hear`, same count as before |
| cb57abd #86 | `_spawn(self._typed(text))` for typed turns; `_consent` returns before any await when nothing is held | No new worker use |
| 50e2da8 #71 | `to_thread(router.route, …)` in `_route` (`:520`) | **Yes** — one short call per turn (the window query is a lambda in the test) |
| #82, #101 | nothing in `local_engine.py` | No |

### Reproduction

`probe120` (a scratch pytest plugin, not committed) timed, per run, when the
mouth thread actually ran and when the test resumed, and counted captures;
`POOL=n` capped asyncio's default executor. CPU contention was N spinning
processes pinned with `sched_setaffinity` to the CPUs the test was pinned to
with `taskset`, each ending itself within 2 minutes; `pgrep` afterwards
found none left.

| Condition | Runs | Result | Mouth ran after | Test resumed after | Captures by then |
| --- | --- | --- | --- | --- | --- |
| alone, unpinned (32 workers) | 8 | 8 pass (4 probed) | 13–112 ms | 108–218 ms | 69–140 |
| whole `test_local_engine.py`, unpinned | 2 | 2 pass (83 + 26 subtests, 17 s) | 46–66 ms | 61–70 ms | 41–46 |
| pinned to 1 CPU (5 workers), no spinners | 3 | 3 pass | 5–9 ms | 6–9 ms | 3 |
| 1 CPU + 8 spinners on it | 3 | 3 pass (5.2 s each, ~5× slower) | 32–43 ms | 47–59 ms | 2 |
| 2 CPUs + 16 spinners, whole module | 1 | pass (25 s) | 31 ms | 40 ms | 2 |
| 2 CPUs + 16 spinners, alone | 3 | 3 pass | 26–44 ms | 36–54 ms | 2 |
| default executor capped at **2** | 3 | 3 pass | 66–93 ms | 137–181 ms | 81–113 |
| default executor capped at **1** | 1 | **fail, 30.0 s, same message** | never | 30.0 s | 1 |

Heavy CPU contention (5× slowdown) left the mouth starting within 60 ms: the
30 s timeout is about 500× what load alone produced here, and the issue's
failure did not reproduce under it. The only reliable reproduction is a pool
of one worker: the test's own waiter (`:125`) is submitted before the listen
loop's first turn, holds the only worker for 30 s, and the mouth's job queues
behind it — the exact failure text, with one capture.

### What the evidence says

- **Not a starvation bug in the engine that this test can see.** None of
  #74/#72/#86's background work runs on this test's loop; the test needs 3
  workers of 32; it passes down to 2. In production the owned pool is 8 and
  the long holders (a capture up to 15 s, the mouth, a watch poll, a routed
  call) are fewer than that.
- **A test that is load-sensitive in two ways it did not intend.**
  1. Its waits are themselves thread-pool jobs (`:125`, `:340`), so the test
     competes with the code under test for the resource whose exhaustion
     produces its own failure message. A 30 s wait that starts late, or never
     gets a worker, reads as "never handed to the mouth".
  2. With `barge_in` on and `Ears` returning instantly, `_listen_loop`
     (`:564-583`) spins: 69–140 full turns before the test observes the
     mouth, each writing the session log and the state file synchronously on
     the event loop and queueing another "One." that is never spoken. The
     mouth is not what the test is waiting on for most of that time; it is
     waiting for its own resume through a loop kept busy by fakes.
- **What caused the two 30 s failures is not established.** CPU contention
  did not reproduce it and the sandbox has 32 workers. Unmeasured candidates:
  sandbox disk I/O under 20+ parallel builds (every spun turn does ~5
  synchronous file writes on the loop), GIL hand-off to the mouth thread
  against a spinning main thread, or an exception in `_listen_loop` (it has
  no `try`), which would stop turns silently and look identical from the
  test.

## Proposed outcome

- `nix flake check` on a machine at load average 30+ passes
  `MicrophoneGateTests` every time, or fails with a message that names the
  real cause rather than a 30 s wall-clock expiry.
- The barge-in test proves the same thing it proves today — the microphone
  reopens while the mouth still holds the sentence, and nothing was spoken —
  without its verdict depending on thread-pool size, CPU load, or how fast
  the fakes let the loop spin.
- If a real engine defect is found on the way (the listen loop dying
  silently, or pool exhaustion in `run()`), it is fixed or filed, not hidden
  by the test rewrite.

## Affected users and systems

- `tests/test_local_engine.py`: `Mouth`, `Ears`, `EngineTestCase.build`,
  `MicrophoneGateTests` (3 tests share the pattern).
- Possibly `src/omarchy_voice/local_engine.py` `_listen_loop`, only if the
  approver wants the silent-death case surfaced (open question 3).
- `nix flake check` on p620 and anyone running it while other agents build;
  CI.

## Constraints

- Must keep asserting against the recorder and the gated mouth (5959d84's
  point): no assertion may become a bet on a clock.
- Must not weaken what the test proves: "the microphone reopened while she
  was still speaking" stays provable, not probable.
- `tests/_isolated.py` is imported first (#99); no test touches real state.
- Must not change product behaviour to make a test pass.

## Open questions

1. **Test rewrite shape.** Drive it from events the loop itself awaits
   (e.g. `Ears` sets an `asyncio.Event` and blocks the second capture on a
   `threading.Event` so the loop cannot spin; the test awaits
   `asyncio.Event`s, not `to_thread` waits) — or a stepped clock like #86's
   `SpokenConsentTests`? Events keep the test off the thread pool entirely;
   the clock pattern fits less well because nothing here is time-based.
2. **Scope.** Only the barge-in test, or all three `MicrophoneGateTests`
   (and `wait_until_speaking` everywhere it is used), since they share the
   `to_thread(…wait, 30)` pattern?
3. **Real bug or not.** `_listen_loop` has no `try`, so an exception in
   `_turn` ends listening silently in production too. Surface that (log and
   continue, or let the test see the task's exception) as part of this, as a
   separate issue, or not at all?
4. **Diagnostics.** Should the rewritten test report the listen task's
   exception and capture count on failure, so a future sandbox failure says
   which of the candidates above it was?
