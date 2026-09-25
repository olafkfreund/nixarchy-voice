---
status: approved
issue: 137
spec: spec/2026-09-24-137-prefetch-next-sentence.md
---

# Plan: make the next sentence's clip while this one plays, inside one block of speech

Closes #137. Written at `main` `be27af4` (v2.0.0). Every `file:line` below was
read at `be27af4`. **This plan is implemented on #135's merged code, not on
`be27af4`.** Step 1 rebases onto it and re-locates every reference. Where a
line moved, the plan is corrected in the same commit as the code, per the
workflow. Spec baseline: `tests/test_local_engine.py`, `tests/test_feedback.py`
and `tests/test_claude_backend.py` give 220 passed, 84 subtests (`b0e3a54`).
The step 1 baseline replaces it.

Files that change: `src/omarchy_voice/claude_backend.py`,
`src/omarchy_voice/feedback.py`, `src/omarchy_voice/local_engine.py`, and
the tests `tests/test_claude_backend.py`, `tests/test_feedback.py`,
`tests/test_local_engine.py`. `elevenlabs.py` changes **only** if step 1 finds
that #135 left no whole-clip collect mode (decision 11). `trace.py`,
`tools/timing_report.py`, `config.py`, `listen_local.py` and `flake.nix` do
not change.

**Safety.** This change touches #114's microphone gate and #86's consent
clock. Every step that edits `local_engine.py` has a test that names the
invariant it protects, and step 11 breaks each one on purpose.

## Approved decisions, carried over from the spec

The spec's decisions 1 to 6, its Design and its "Approved with" note, one
decision per number. Nothing here needs the intent or the spec to implement.

1. **The prefetch lives in the engine, hung off the queued line.** The
   engine owns the queue, `_voice_until`, the turn and every way of
   dropping speech, so a clip made early is dropped with its line.
   `Feedback` gets only a seam (decision 10).
   *Rejected:* a clip cache in `Feedback` keyed by text. A cancelled line's
   clip would stay in it and play when the same text ("Done.") came back.
2. **A queued line is a `_Line(text, clip)`,** where `clip` is the future of
   its make-ahead or `None`. **The mouth plays only the clip attached to the
   line it has just taken off the queue.** Never a clip found by text, by
   position or later.
3. **The backend marks the end of every block.** `claude_backend.BLOCK_END`
   is a module-level sentinel (`object()`, never a `str`). `WarmBrain._turn`
   yields it at each `content_block_stop`, after the tail it already flushes
   there (`claude_backend.py:939-947`), and once after the sentences of an
   `AssistantMessage` that arrived without deltas (`:948-958`).
   `ask_stream(..., blocks=False)` passes it on only when `blocks=True`, and
   drops it otherwise. The default output of `ask_stream` is unchanged.
4. **Only a brain that says it marks blocks is asked for marks.**
   `WarmBrain.marks_blocks = True`. The engine reads
   `getattr(self.brain, "marks_blocks", False)`. A brain without it (every
   test fake, any future brain) gets today's loop exactly. It fails closed.
5. **With `barge_in` off, the engine pulls one sentence ahead, within the
   block only.** This mode ("ahead mode") runs only when `barge_in` is off,
   the brain marks blocks, and `feedback.can_make_ahead()` is true, all read
   at the start of `_answer`'s model branch.
   - Each sentence is queued with `_say(text, wait=False)`. The engine then
     waits until the mouth has taken it off the queue before it pulls again.
     So **at most one unplayed line is ever queued**, and the brain is at most
     one sentence ahead of the mouth.
   - `BLOCK_END`: the engine `join()`s the queue. Everything queued plays out
     before the stream is pulled again. Pulling `BLOCK_END` reads nothing past
     the block, because the generator stops at that `yield`.
   - The stream ending, or anything leaving the loop: the engine `join()`s,
     as today, so a held prompt, the trace and the next capture all come
     after her last sentence.
   *Rejected:* pulling freely one ahead (the pull would run on through a tool
   run while she talks, for no gain: the gap after a tool is the tool run).
   *Rejected:* making clips only for lines already queued (with `barge_in`
   off nothing is queued ahead, so it would do nothing on this machine).
6. **Every other case is today's loop, line for line:** `barge_in` on,
   Piper, `tts_command`, espeak-ng, an unmarked brain. With `barge_in` on the
   engine already pulls without waiting, and the only addition is decision 7:
   the mouth makes the clip for the head of the queue while it plays.
7. **When a make-ahead starts, and how many.** The mouth makes ahead for the
   line at the head of the queue (`peek()`), at two moments: when it takes a
   line, and when `_say` queues a line while the mouth is speaking. **At most
   one make-ahead is in flight.** If one is still running (for any line,
   including a dropped one), no new one starts, and that line is made the
   ordinary way when its turn comes. Only when `feedback.can_make_ahead()`.
8. **A make-ahead in flight is left to finish, never cancelled.** `urllib`
   and `ffmpeg` block a thread and cannot be cancelled cleanly. It is bounded
   by the 30 s timeout of the whole-clip call. Its result is thrown away if
   its line was dropped. A done-callback retrieves the result or the
   exception, so nothing is left unretrieved.
   *Rejected:* closing the socket under `urllib` (a new failure mode on the
   path that must degrade, never mute).
9. **Dropping.** Barge-in, mute, toggle and `_drop_queued_speech` take the
   line off the queue as today, and its clip goes with it. Each dropped line
   whose clip was started is logged as
   `tts     made ahead, not played (<n> chars)`. On stop the mouth task is
   cancelled, as today. A turn that raises in ahead mode drops the line
   queued ahead, lets the playing line finish, logs, then apologises.
10. **The `Feedback` seam.** `can_make_ahead()` is `tts_command` unset and
    `elevenlabs.ready(config)`. `make_ahead(text)` returns a whole clip from
    #135's collect mode with `trace=None`. `_speak_now(text, made=None)`:
    - `made` a clip: play it through #135's `pw-cat` stage and return only
      when `pw-cat` has exited.
    - `made` an exception: log `tts     elevenlabs failed (<exc>) — using
      piper` and go to `_speak_piper(text)`, as today.
    - `made is None`: today's body, untouched.
    The mouth calls `_speak_now(text)` with **one** argument when nothing was
    made ahead, so every existing one-argument fake of `_speak_now` holds.
11. **#135 lands first, and #137 uses its whole-clip collect mode** ("Approved
    with", both specs). One ffmpeg argv and one mastering. The old
    download-then-decode `synth` is **not** brought back. A clip made in
    collect mode has not played a sample, so any failure in it is
    `Unavailable` (never `Cut`), and the line falls to Piper whole.
12. **Piper, `tts_command` and espeak-ng are not made ahead.** A later issue
    can add Piper once #136 exists and its trace shows a gap worth hiding.
13. **The make-ahead passes `trace=None`.** Its spans would otherwise land
    inside N's SPEAK and distort N's `first-audio` (`trace.py:120-134`).
14. **The trace after.** For a line made ahead, the mouth opens one SYNTH
    span named `ahead`, around the wait for its clip, inside that line's own
    SPEAK span (`trace.mark(trace_mod.SYNTH, "ahead")`, `trace.py:85`). When
    the clip was ready it is about 0 s. A line made the ordinary way keeps
    #135's `request` and `buffer` spans. So `synth=` still means "time she
    waited for synthesis", and `first-audio` is not changed by clips made
    ahead. `tools/timing_report.py` sums SYNTH by name already
    (`BROKEN_DOWN`, `trace.py:55`).
15. **`_speech` is a `_SpeechQueue(asyncio.Queue)` with `peek()`,** which
    reads `self._queue[0]` (the subclass hook `PriorityQueue` uses) or
    returns `None`. Everything that reads the queue today keeps its meaning,
    including `_record`'s `self._speaking or not self._speech.empty()`
    (`local_engine.py:379`).
16. **`_say` keeps its order.** The log line, `_voice_until = inf`, `_said`
    and the `_mic_shut` wait (`:326-344`) all stay before the line is queued.
    After queueing, `_say` starts a make-ahead if the mouth is speaking.
    `wait=False` skips only the final `join()`.
17. **The mouth's `finally` is unchanged** (`:316-324`). `_voice_until`
    becomes finite only when the queue is empty after `_speak_now` (so
    `pw-cat`) returns. A make-ahead that finishes or fails never touches
    `_voice_until`, `_speaking` or `task_done`.
18. **Cost accepted; no config flag.** At most one sentence of characters
    per interruption, counted by the log line from decision 9. If the
    evidence gate (decision 19) fails, the change is reverted, not left in
    place switched off.
19. **The evidence gate is the owner's, and it can revert this.** After
    merge, with real audio, never run by an agent (it costs quota):
    `trace_timings = true`, `barge_in` off, ElevenLabs on, a fixed list of
    10 typed prompts, each answered in three sentences with no tools, on
    `main` then on the branch build (about 60 sentences). **Keep** only if the
    branch's p50 `speak=` is lower than `main`'s by at least one third of
    `main`'s p50 `synth=`, and p50 `first-audio=` moves by no more than 10 %.
    **Otherwise revert.**
20. **The SDK finding is not changed here.** The SDK already runs tool
    permission decisions while she speaks (`claude_agent_sdk/_internal/query.py`
    0.2.152, `:332-337`, buffer 100 at `:172`). It is tracked as its own
    issue. This change does not rely on that back-pressure and does not alter
    it. Its own bound is decision 5: our code never consumes a message past
    the end of a block while she is talking.

### Invariants (each is a test in step 2, each is broken in step 11)

- **I1, #114, her voice shuts the capture.** With `barge_in` off,
  `_voice_until` is `inf` from the `_say` of the first sentence until the
  `pw-cat` of the last queued line returns. `_say` still waits for
  `_mic_shut` before it queues.
- **I2, #114, the echo tail.** The next capture opens no earlier than the end
  of her last play plus `ECHO_TAIL_SECONDS` (0.35 s, `local_engine.py:48`).
  `_answer` does not return, reach a held prompt or finish the trace before
  her last line has played.
- **I3, bounded.** At most one unplayed line queued, at most one make-ahead
  in flight, brain at most one sentence ahead of the mouth.
- **I4, no pull past a block while she talks.** Nothing after a `BLOCK_END`
  is pulled until every queued line has played. So no tool call is pulled.
- **I5, #86.** `_voice_until` keeps its meaning, and every line is in `_said`
  from the moment it is queued, before it plays. `SpokenConsentTests` pass
  unchanged.
- **I6, #120 recovery.** A failed make-ahead is never a turn failure: the
  line falls through to Piper and `failures` is unchanged. A turn that
  raises drops the line queued ahead, lets the playing line finish,
  apologises, and counts toward `TURN_FAILURES_TO_MUTE` (`:163`) exactly as
  today. No make-ahead starts after a mute. Executor threads: at most the
  mouth's one plus one make-ahead.
- **I7, barge-in on.** Lines are still queued without a `join()`, the
  capture may stay open, #86 still refuses a spoken confirm, and a barge-in
  empties the queue, clips included.
- **I8, dropped is never played.** After cancel, stop, mute, toggle,
  barge-in or a turn failure, a clip made ahead is never played. That
  includes one that finishes after the drop, and the same text said again in
  a later turn.
- **I9, the default path is unchanged.** With Piper, `tts_command`,
  espeak-ng or an unmarked brain, the calls to `ask_stream` and `_speak_now`,
  their arguments, order and stepped times, equal `main`'s.

## Spec ambiguities, resolved here (flagged for the reviewer)

- **A. Who adds the collect mode.** #135's spec ("Alternatives rejected")
  says that if #135 merges first, "#137 adds that mode". Both "Approved
  with" notes say #137 *uses* #135's collect mode. Resolution: step 1 reads
  #135's merged `elevenlabs.py`. If it has a collect mode (a `synth`, or a
  flag on `speak`, returning `(pcm, rate)`), step 4 calls it. If it does not,
  step 4 adds it exactly as #135's spec describes: one flag in the calling
  thread's read loop that appends PCM to a buffer instead of writing it to
  `pw-cat`, same argv, same mastering, `trace=None` allowed. It is not the
  old `synth` and not a second copy.
- **B. Playing a clip once `_play` is gone.** Decision 2 of the spec says a
  made clip is played "with `_play`", and #135 deletes `_play`. Resolution:
  the clip is played through the same `pw-cat` argv #135 uses, factored so
  both paths call one helper if #135 did not already. The mouth returns only
  when `pw-cat` has exited (#135 decision 5, I1, I2). No second `pw-cat`
  argv.
- **C. "Waits until the mouth has taken it".** The spec does not say how,
  and it says "after a sentence, `_say` does not join; the engine pulls the
  next item" without a wait for the first one. If the brain yields N+1
  before the mouth has taken N, two unplayed lines would be queued (I3).
  Resolution: the wait follows **every** `_say(..., wait=False)` in ahead
  mode, the first included. It is an `asyncio.Event` `_taken`, set by
  `_speech_loop` right after `get()` and by `_drop_queued_speech`, and the
  wait is `while not self._speech.empty(): _taken.clear(); await
  _taken.wait()`. It adds no clock and no flag the gate reads.
- **D. "A turn that raises drops the line queued ahead".** Resolution: only
  in ahead mode. With `barge_in` on, a raising turn keeps today's behaviour
  (I7, I9).
- **E. Landing order.** The spec makes only #135 a precondition. The order
  approved for the batch is #138, #135, #136, #137. Resolution: #135 unmerged
  is a hard stop. #138 or #136 unmerged is also a stop-and-report, so the
  owner decides; this plan does not land out of order.

## Landing order

**LAST: #138, #135, #136, #137.**

- **#135** (stream ElevenLabs, `perf/135-stream-elevenlabs`, spec approved
  at `e03fcf6`) is a hard precondition: this plan builds on its collect mode
  and its `pw-cat` stage, and on its SYNTH names `request`/`buffer`.
- **#136** (resident Piper, `perf/136-resident-piper`) overlaps in
  `feedback.py`: it rewrites `_speak_piper` (`feedback.py:170-206`) and adds
  `PiperWorker` with a lock around `speak`, which `_speak_now` reaches. This
  plan edits `_speak_now` right above it, and a failed make-ahead falls
  through to #136's `_speak_piper`. It also touches `local_engine.py`'s start
  and stop wiring (`:953-1013` at `be27af4`) and `tests/test_feedback.py`.
  Rebase conflicts are expected in `_speak_now` and the feedback tests. The
  resolution keeps #136's `_speak_piper` as is and adds only decision 10's
  branch. #136's worker is a process, not an executor thread, so I6's thread
  bound (mouth plus one) is unchanged by it.
- **#138** (end of turn) changes `end_of_speech_seconds` and its tests
  (`EndOfSpeechTests`, `tests/test_local_engine.py:1707`, and
  `SteppedRoomCase` users). Overlap: `tests/test_local_engine.py` only. The
  I2 test asserts the capture's *opening* time, which the hold does not
  change.

## Step 1 record: preconditions, baseline, references (2026-09-25)

- #137 OPEN; branch `perf/137-prefetch-next-sentence`, rebased cleanly onto
  `origin/main` `6879b0b`, which carries #138, #135 (with `elevenlabs.synth`,
  `_play` deleted), #136 and #139 (`WarmBrain._mark_read`), plus #77, #83,
  #91, #128, #129, #143, #144, #145.
- Baseline on the rebased branch, before any change: `pytest tests -q` 1214
  passed (1333 subtests); `unittest discover -s tests` 1214, OK. The three
  files of this plan: 251 passed, 89 subtests (was 220 and 84 at `b0e3a54`).
- **Ambiguity A is resolved by #135:** the collect mode exists,
  `elevenlabs.synth(text, config, timeout=30.0, trace=None) -> (pcm, RATE)`,
  the same `_run` as `speak` with `play=False`. `elevenlabs.py` does not
  change here.

References, re-located by symbol at `6879b0b` (the `be27af4` numbers above
are superseded by these):

| Plan said | At `6879b0b` |
| --- | --- |
| `elevenlabs.py` `speak`, collect mode, `Unavailable`/`Cut`, pw-cat argv | `speak` :283, `synth` :297, `_run` :167, `_ffmpeg_argv` :133, `Unavailable` :51, `Cut` :55, `PLAYER` :46 |
| `feedback.py:170-206` `_speak_piper`; `_speak_now`; pw-cat stage | `_speak_now` :267-289; `_speak_resident` :311 and `_speak_piper` :331-369 (#136); the pw-cat stage is inside `elevenlabs._run`, not in `feedback.py` |
| `local_engine.py:252-257` `_voice_until`/`_mic_shut` | :259, :263-264 |
| `self._speech =` | :236 |
| `_speech_loop` :299-324 (`finally` :316-324) | :306-331 (`finally` :324-331) |
| `_say` :326-346 | :333-355 |
| `_drop_queued_speech` :348-359 | :357-367 |
| `_record`'s wait :376-387 (`:379`) | :380-398 (the `_speaking or not empty` at :388) |
| `_answer` :544-620, loop :585-594, `except` :596-605 | :556-640, loop :597-608, `except` :612-621 |
| listen loop failures :710-725; `TURN_FAILURES_TO_MUTE` :163; `ECHO_TAIL_SECONDS` :48 | :704-736; :163; :48 |
| `claude_backend.py` `ask_stream` :870-911, `_turn` :913-960, `WarmBrain` :727 | `ask_stream` :897-944, `_mark_read` :946 (#139), `_turn` :953-1020, `WarmBrain` :754 |
| `_turn`'s `content_block_stop` :939-947; `AssistantMessage` :948-958 | :979-986; :995-1010. #139 added `content_block_start` (:988-994) and, in the `AssistantMessage` branch, `_mark_read` after the sentences (:1006-1009). `BLOCK_END` goes after the sentences and **before** that `_mark_read`, so read is still heard. |
| `trace.py:85` `mark`; `:120-134` `first_audio`; `:55` `BROKEN_DOWN` | :87; :121-136; :57 |
| tests: `FakeBrain` :34, `MicrophoneGateTests` :313, `ScriptedBrain` :505, `SpokenConsentTests` :758, `Room` :1086, `SteppedRoomCase` :1168, `FailureTests` :1588, `EndOfSpeechTests` :1707 | :34, :313, :505, :758, :1086, :1168, `FailureTests` :1847, `EndOfSpeechTests` :1966; also `EndpointTests` :1556 and #139's `SpeechFirstTests`/`ActingClient` :1728/:1668 |

### Deviations found while implementing (2026-09-25): the tests (step 2)

- **The fake mouth lets the loop run before it plays.** `IsolatedAsyncioTestCase`
  runs its loop in debug mode, where an executor thread's completion can be
  handled before a task woken earlier: an instant fake mouth then finishes a
  line before the engine has queued the next, and nothing is made ahead. The
  mouth settles the loop (ten `sleep(0)`, as #139's `SpeechFirstTests` mouth
  does) before it plays, as a real line lasts long enough for it to.
- **Test 5** asserts the queue is empty at every pull (stronger than "plays
  started ≥ pulls − 2", and not racy against the mouth's thread).
- **Tests 9 and 12, "turn failure".** In ahead mode the brain is never pulled
  while a line is queued (the `_taken` wait), so a brain that raises can never
  leave one queued: a raise while N plays has nothing to drop. Test 12 keeps
  that case (the brain raises after N; N finishes, then the apology) and adds
  a turn that breaks with N+1 queued through a fault in the engine's own
  `_taken` wait (`_BreakingEvent`), which is what the `except`'s drop is for.
  M16 is caught there.
- **"Barge-in"** in test 9 and 13 is the toggle with `barge_in` on: the
  engine has no other way to drop queued speech.
- **Test 14** asserts `first_audio` is the first line's own synthesis
  (1.0 s), not equal to the run with ahead mode off: on a clock that stands
  still between lines, that run's second `request` span opens at the exact
  end of the first SPEAK and `Trace.first_audio`'s inclusive bound counts it.
  Pre-existing, and not changed here.
- **Test 3 fails on `main`** (plan: G): it also asserts the make-ahead
  records, which are empty there.
- **Two tests added:** `test_the_real_brain_without_a_cloud_voice_says_no_marks`
  (I9 through the real `WarmBrain`, so M19 is caught by an engine test) and
  `test_a_call_still_waits_for_her_line_in_ahead_mode` (#139's wait, read =
  heard, holds in ahead mode; M1 also breaks it).
- **Decision 8's retrieval** is checked in test 9 (a failed clip, dropped):
  the M23 handler is `loop.set_exception_handler`, with `gc.collect()`.

Step 2's run on the unchanged `src/`: every F test fails for its reason
(`ask_stream() got an unexpected keyword argument 'blocks'`, `'Feedback'
object has no attribute 'make_ahead'`/`'can_make_ahead'`, `_speak_now()
takes 2 positional arguments`, `no attribute '_SpeechQueue'`, gaps
`[1.0, 1.0] != [0.0, 0.0]`, "Two. was never made ahead while One.
played"): 27 failed (subtests included), 9 passed. The 9 are the G tests
(2, 4, 6, 7, the two added), `BlockEndTests.test_unasked_the_output_is_todays`
and `MakeAheadTests.test_only_the_cloud_voice_is_made_ahead` (its subtests
fail).

## Steps

0. **Baseline, on the branch as it is.** `gh issue view 137` shows OPEN, and
   `git branch --show-current` is `perf/137-prefetch-next-sentence`. With
   `export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`, run
   `nix develop -c python3 -m pytest -q` and
   `nix develop -c python3 -m unittest discover -s tests`
   → verify by both passing. Record the counts and any failure that already
   exists.
1. **Precondition: #135 has merged. If not, stop and report. Do not
   implement.** Check `gh pr list --state merged --search "135 in:title"`
   and `git fetch origin && git log origin/main --oneline | grep '#135'`.
   Do the same for #138 and #136 (ambiguity E): if either is unmerged, stop
   and report. If all have merged: `git rebase origin/main`, repeat step 0 on
   the new base, and record the new baseline (total, and the three test
   files). Then re-locate on the new code, and correct in this file:
   - `elevenlabs.py`: #135's `speak` and its collect mode, if any
     (ambiguity A), the `Unavailable`/`Cut` classes, the `pw-cat` argv.
   - `feedback.py`: `_speak_now`, the `pw-cat` stage, `_speak_piper` after
     #136.
   - `local_engine.py`: `_voice_until`/`_mic_shut` init (`:252-257`),
     `self._speech = ` in `__init__`, `_speech_loop` (`:299-324`), `_say`
     (`:326-346`), `_drop_queued_speech` (`:348-359`), `_record`'s wait
     (`:376-387`), `_answer` (`:544-620`, the loop at `:585-594`, the
     `except` at `:596-605`), the listen loop's failures (`:710-725`).
   - `claude_backend.py`: `ask_stream` (`:870-911`), `_turn` (`:913-960`),
     `WarmBrain` (`:727`).
   - tests: `SteppedRoomCase` (`:1168`), `Room` (`:1086`), `FakeBrain`
     (`:34`), `ScriptedBrain` (`:505`), `SpokenConsentTests` (`:758`),
     `FailureTests` (`:1588`), `MicrophoneGateTests` (`:313`).
   → verify by a clean rebase, a green baseline, and every reference
   confirmed or corrected here in the first implementation commit.
2. **Tests first, and see them fail.** Add before any `src/` change.
   - `tests/test_claude_backend.py`: `BlockEndTests`. A fake SDK stream (the
     shape the file already uses) with two text blocks and a
     `content_block_stop` after each, and a second case with an
     `AssistantMessage` and no deltas. `blocks=True` yields `BLOCK_END` after
     each block's sentences and tail. The default call's output is equal to
     today's. `WarmBrain.marks_blocks is True`.
   - `tests/test_feedback.py`: `MakeAheadTests`, with #135's fakes for
     `urlopen` and `Popen` (no network, no audio). `can_make_ahead()` is
     False with `tts_command` set or ElevenLabs not ready. `make_ahead`
     calls the collect mode with `trace=None` and #135's ffmpeg argv.
     `_speak_now(text, made=clip)` starts the `pw-cat` fake with the clip and
     returns only after its `wait` returns (a thread and an Event). `made=`
     an exception logs `elevenlabs failed` and calls the Piper fake with the
     whole text. `made=None` makes the same calls as today's body.
   - `tests/test_local_engine.py`: `PrefetchTests(SteppedRoomCase)`, placed
     after `EndpointTests`, away from #120's and #138's classes.
     - *Stepped clock* from `SteppedRoomCase`; `no_tail()` except in the I2
       test, which uses `stepped_sleep()`.
     - *Prefetch mouth*, replacing `feedback._speak_now` after `room()`:
       `mouth(text, made=None)`. With `made is None` it steps the clock 1 s
       (synthesis). Then it records `(text, made, start, _voice_until)`,
       steps 1 s (play), and records the end. It never plays a clip that was
       not handed to it.
     - *Fake make-ahead*: `feedback.can_make_ahead` returns a switch;
       `feedback.make_ahead(text)` records `(text, now, _voice_until)`,
       optionally blocks on a per-text `threading.Event` (to be "in flight"
       or "finish after the drop"), then returns `("clip", text, serial)` or
       raises. It never steps the clock: its work is hidden behind the play.
       It counts concurrent calls.
     - *Block brain* (`FakeBrain` subclass, `marks_blocks = True`,
       `ask_stream(text, *, release=False, from_user=True, blocks=False)`):
       a script of strings, `BLOCK_END` and `TOOL`. It records `now` at each
       resume (a pull) and, at `TOOL`, records `now` as the tool run and
       steps 1 s. With `blocks=False` it drops `BLOCK_END`. Optionally it
       raises at a given point.
     - Tests (each drives the real `_answer`, `_say`, `_speech_loop`,
       `_drop_queued_speech`, and the real `_listen_loop` where a capture is
       asserted). F = fails on the unchanged `src/`; G = guard, may pass on
       `main`, proven in step 11.
       1. `test_three_sentences_in_one_block_play_back_to_back` (gain, F):
          gaps between plays 0 s; the same script with `can_make_ahead`
          False gives 1 s.
       2. `test_plays_are_in_order_and_never_overlap` (outcome, G).
       3. `test_her_voice_holds_the_mic_shut_through_the_reply` (I1, G):
          every mouth and make-ahead record has `_voice_until == inf`, and
          `_mic_shut` was awaited before each put (a `_say` spy records it).
       4. `test_the_capture_opens_after_the_last_play_plus_the_tail` (I2, G):
          real `_listen_loop` with `Room`; the next capture opens between
          last play end + 0.35 s and + 0.40 s; `_answer` returned after the
          last play ended; the TIMING line is logged after it.
       5. `test_one_line_ahead_and_one_make_in_flight` (I3, F): at every
          pull, plays started ≥ pulls − 2; the queue never holds two lines;
          the fake's concurrency counter never exceeds 1; a make-ahead
          blocked in flight stops a second from starting, and that line is
          spoken with `made=None`.
       6. `test_nothing_after_a_block_end_is_pulled_while_she_talks`
          (I4, G): `[S1, BLOCK_END, TOOL, S2]`: the tool's time ≥ S1's play
          end; S2 was not made ahead (`made is None`).
       7. `test_an_unmarked_brain_or_no_cloud_voice_is_todays_loop`
          (I9, G): `FakeBrain` (no `marks_blocks`), and the block brain with
          `can_make_ahead` False: the pull times and the `_speak_now` call
          list (one argument each) equal a recorded run with the ahead mode
          forced off.
       8. `test_the_next_line_is_said_before_it_plays` (I5, G): N+1 is in
          `_said` when `make_ahead` is called for it; `SpokenConsentTests`
          unchanged.
       9. `test_a_dropped_clip_is_never_played` (I8, F), subTests: mute,
          toggle, stop, barge-in (with `barge_in` on), turn failure. N plays,
          N+1 is queued and its make-ahead started (asserted: fails on
          `main`), the drop happens during N. Then: no mouth record has
          N+1's clip, including when the fake's Event is set after the drop;
          and in the next turn the same text is spoken with a fresh clip or
          `made=None`, never the dropped serial.
       10. `test_a_dropped_clip_is_logged` (decision 9, F): the log has
           `tts     made ahead, not played (<len> chars)` once per dropped
           line whose clip was started, and not for a line without one.
       11. `test_a_failed_make_ahead_falls_to_piper` (I6, F): the fake
           raises; `_speak_now` gets `made=` the exception, the Piper fake
           (at `_speak_piper`) speaks the whole line, the log has
           `elevenlabs failed`, and the listen loop's `failures` is 0.
       12. `test_a_raising_turn_lets_the_playing_line_finish` (I6, G): the
           brain raises after yielding N+1: N's play completes, N+1 is never
           played, then "Something went wrong with that." plays;
           `FailureTests` unchanged; no make-ahead is started after a mute.
       13. `test_barge_in_on_makes_the_head_of_the_queue` (I7, F): no
           `join()` in `_say`, the head of the queue is made ahead while N
           plays, and a barge-in empties the queue and its clip.
       14. `test_a_line_made_ahead_has_one_ahead_span` (decision 14, F): a
           real `Trace`; the line made ahead has exactly one SYNTH `ahead`
           inside its own SPEAK, the make-ahead was called with no trace, and
           `first_audio` equals the run with the ahead mode off.
       15. `test_peek_reads_the_head_and_takes_nothing` (decision 15, F).
   → verify by `nix develop -c python3 -m pytest -q
   tests/test_claude_backend.py tests/test_feedback.py
   tests/test_local_engine.py -k "BlockEnd or MakeAhead or Prefetch"` on the
   unchanged `src/`: every test marked F fails for the reason in its name,
   and the backend and feedback units fail. G tests may pass. No assertion
   sleeps real time and then checks; every wait is an Event or the stepped
   clock.
3. **`claude_backend.py`: `BLOCK_END`, `blocks=`, `marks_blocks`**
   (decisions 3, 4). The sentinel at module level; `_turn` yields it after
   the flushed tail at `content_block_stop` (`:939-947`) and after the
   `AssistantMessage` sentences (`:948-958`); `ask_stream(..., blocks=False)`
   filters it unless `blocks`; `WarmBrain.marks_blocks = True`. The `spoke`
   flag is not set by `BLOCK_END` ("Done." still comes when nothing was
   said).
   → verify by `BlockEndTests` passing and `tests/test_claude_backend.py`
   otherwise unchanged and green.
4. **`feedback.py` (and `elevenlabs.py` only under ambiguity A): the seam**
   (decisions 10, 11, 13; ambiguities A and B). `can_make_ahead()`,
   `make_ahead(text)` with `trace=None`, `_speak_now(text, made=None)`.
   → verify by `MakeAheadTests` passing, #135's tests passing unchanged, and
   `grep -n "trace=self.trace" src/omarchy_voice/feedback.py` showing it only
   on the ordinary path.
5. **`local_engine.py`: `_SpeechQueue`, `_Line`, `_taken`, the in-flight
   slot** (decisions 2, 7, 15; ambiguity C). `_SpeechQueue(asyncio.Queue)`
   with `peek()`; `_Line` a small dataclass (`text`, `clip=None`); in
   `__init__`, `self._speech = _SpeechQueue()`, `self._taken =
   asyncio.Event()`, `self._ahead = None` (the one make-ahead future).
   `_make_ahead(line)` starts one only if `feedback.can_make_ahead()`,
   `line.clip is None`, and `_ahead` is `None` or done: `loop.run_in_executor
   (None, feedback.make_ahead, line.text)`, stored on the line and in
   `_ahead`, with a done-callback that calls `.exception()` (decision 8).
   → verify by test 15 passing, and `grep -n "_speech.put\|_speech.get"`
   showing every put wraps a `_Line`.
6. **`local_engine.py` `_speech_loop`** (decisions 2, 7, 14, 17). After
   `get()`: `self._taken.set()`; `_make_ahead(self._speech.peek())` if there
   is one. If `line.clip` is set: inside SPEAK, open SYNTH `ahead`, `await
   asyncio.wrap_future`-or-await the clip, catching `Exception` as `made`,
   close the span, then `to_thread(_speak_now, line.text, made)`. Else
   `to_thread(_speak_now, line.text)`, exactly today's call. The `finally`
   is not touched.
   → verify by tests 1, 11, 14 passing, and `git diff` of the `finally`
   block empty.
7. **`local_engine.py` `_say(text, wait=True)`** (decision 16). Queue
   `_Line(text)`; after the put, if `self._speaking`, `_make_ahead` on the
   head; `wait=False` skips only the final `join()`. Nothing above the put
   moves.
   → verify by tests 3, 8 passing and `HerVoiceGatesTheMicTests`,
   `SpokenConsentTests`, `MicrophoneGateTests` passing unchanged.
8. **`local_engine.py` `_drop_queued_speech` and the raising turn**
   (decision 9; ambiguity D). For each line taken off, log
   `tts     made ahead, not played (<n> chars)` if `line.clip` is set; set
   `_taken` at the end. The clock rule at the top is unchanged. In
   `_answer`'s `except` (`:596`), in ahead mode only, call
   `_drop_queued_speech()` then `await self._speech.join()` before the
   apology.
   → verify by tests 9, 10, 12 passing.
9. **`local_engine.py` `_answer`: the ahead loop** (decisions 4, 5, 6;
   ambiguity C). Compute `ahead = not barge_in and getattr(brain,
   "marks_blocks", False) and feedback.can_make_ahead()`. When false, the
   call and loop are byte-for-byte today's. When true: `ask_stream(...,
   blocks=True)`; `BLOCK_END` (checked by identity, before `.strip()`) →
   `await self._speech.join()`; a sentence → the TURN span handling as
   today, `await self._say(sentence, wait=False)`, then the `_taken` wait;
   after the loop, and in a `finally` around it, `await
   self._speech.join()`.
   → verify by tests 1 to 7 and 13 passing, then the whole of
   `tests/test_local_engine.py` under `timeout 300` with no hang.
10. **Before/after with fakes, by the implementer.** A scratch script (not
    committed) under
    `/tmp/claude-1000/-mnt-data-Source-home-GitHub-nixarchy-voice/<session>/scratchpad/`
    drives the real `LocalSession` with step 2's fakes on `origin/main` (via
    `git worktree`) and on the branch, for: one block of three sentences;
    block, tool, block; mute during sentence 1; `barge_in` on with a
    barge-in during sentence 1. It prints each sentence's make start, play
    start, play end and the capture's open time.
    → verify by identical capture times and played lines on both, except
    that the gaps inside a block fall from 1 s to 0 s. The gap after the
    tool is unchanged.
11. **Mutation checks.** Apply each alone, run the named tests, revert with
    `git checkout -- src/`:
    | # | Mutation | Guards | Must fail |
    | --- | --- | --- | --- |
    | M1 | `_answer` ignores `BLOCK_END` and keeps pulling | I4, dec. 5 | 6 |
    | M2 | the make-ahead's done-callback sets `_voice_until = time.monotonic()` | I1, dec. 17 | 3, 4 |
    | M3 | the mouth's `finally` sets `_voice_until` even when the queue is not empty | I1, I2 | 3, 4 |
    | M4 | `_said.append` moved to `_speech_loop` (at play) | I5, dec. 16 | 8 |
    | M5 | `_drop_queued_speech` leaves the line's clip in a `dict` by text that the mouth reads | I8, dec. 1 | 9 (same-text subtest) |
    | M6 | the mouth plays the latest finished clip instead of `line.clip` | I8, dec. 2 | 9 |
    | M7 | the `_taken` wait removed (two lines ahead) | I3, dec. 5, amb. C | 5 |
    | M8 | the in-flight check removed (two make-aheads) | I3, dec. 7 | 5 |
    | M9 | `make_ahead` passes `trace=self.trace` | dec. 13 | 14, `MakeAheadTests` |
    | M10 | no SYNTH `ahead` span around the clip wait | dec. 14 | 14 |
    | M11 | a failed make-ahead: `made` swallowed and nothing spoken | I6, dec. 10 | 11 |
    | M12 | a failed make-ahead re-raised into the turn | I6 | 11 (`failures`) |
    | M13 | `getattr(brain, "marks_blocks", True)` | I9, dec. 4 | 7 |
    | M14 | `can_make_ahead()` ignores `tts_command` | I9, dec. 12 | `MakeAheadTests` |
    | M15 | the drop log line removed | dec. 9 | 10 |
    | M16 | the raising turn skips the drop | I6, amb. D | 12 |
    | M17 | `_say` puts before the `_mic_shut` wait | I1 (#114) | 3, `HerVoiceGatesTheMicTests` |
    | M18 | `_drop_queued_speech` skips the clip when `barge_in` is on | I7 | 13 |
    | M19 | `ask_stream` passes `BLOCK_END` with `blocks=False` | dec. 3 | `BlockEndTests`, 7 |
    | M20 | `_answer` returns without the final `join()` | I2 | 4 |
    | M21 | `peek()` pops the head | dec. 15 | 15, 1 |
    | M22 | `make_ahead` uses a second ffmpeg argv or loudnorm | dec. 11 | `MakeAheadTests` |
    | M23 | the done-callback removed | dec. 8 | 9 (a "never retrieved" handler set with `loop.set_exception_handler` records a call) |
    → verify by every mutation turning its tests red, and `git diff --stat`
    empty under `src/` after each revert.
12. **Full suites and the flake.** With
    `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`:
    `nix develop -c python3 -m pytest -q`,
    `nix develop -c python3 -m unittest discover -s tests`,
    `nix flake check --no-write-lock-file`
    → verify by all three passing, with the count equal to step 1's baseline
    plus the new tests, and no existing test edited except to add fakes.
13. **The owner's evidence gate, after merge** (decision 19). Not run by an
    agent. The owner runs `python3 tools/timing_report.py --since <main day>
    --until <branch day>` and `--since <branch day>`, and posts p50 and p95
    of `speak`, `synth`, `first-audio` and `synth.ahead` for both windows on
    #137.
    → **keep** if branch p50 `speak` ≤ main p50 `speak` − (main p50 `synth`
    ÷ 3) and p50 `first-audio` is within 10 % of main's. **Otherwise revert**
    (Rollback), and record the numbers on #137. The owner also totals the
    `made ahead, not played` lines from `session.log` as the cost.

## Tests

```
export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent
nix develop -c python3 -m pytest -q tests/test_claude_backend.py tests/test_feedback.py tests/test_local_engine.py -k "BlockEnd or MakeAhead or Prefetch"   # all pass (F tests fail before step 3)
nix develop -c python3 -m pytest -q tests/test_local_engine.py -k "SpokenConsent or HerVoiceGatesTheMic or MicrophoneGate or Failure"  # unchanged, all pass
nix develop -c python3 -m pytest -q                        # step 1 baseline + new tests, all pass
nix develop -c python3 -m unittest discover -s tests       # same count, OK
nix flake check --no-write-lock-file                       # passes
```

No test calls ElevenLabs, plays audio, opens a microphone or reaches D-Bus.
No assertion depends on how much real time has passed.

## Rollback

One implementation commit on `perf/137-prefetch-next-sentence`, on top of
#135 (and #136, #138). Before merge, drop the branch. After merge, `git
revert <sha>` restores today's `_answer` loop, `_speech` queue and
`_speak_now`. #135's collect mode stays if #135 owns it; if step 4 added it
(ambiguity A), the revert removes it, and #135's streaming path is
unaffected. There is no config key, state file or migration. The hosts
(p620 and razer) pick up either direction on the next `omarchy-voice`
rebuild or restart. The evidence gate (step 13) failing is a revert, not a
switch-off (decision 18).
