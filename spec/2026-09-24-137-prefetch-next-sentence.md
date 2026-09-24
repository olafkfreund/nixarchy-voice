---
status: draft
issue: 137
intent: intent/2026-09-24-137-prefetch-next-sentence.md
---

# Spec: make the next sentence's clip while this one plays, inside one block of speech

Closes #137. Line numbers are `main` `be27af4` (v2.0.0). The packaged
`claude-agent-sdk` is 0.2.152 (`nix develop`,
`/nix/store/wn3ymi10bwr1pha7dhn6xy3py265si0s-python3-3.14.7-env`).

**This is a narrowed design.** Sentence N+1 is made while N plays only when
N+1 is in the same block of the model's speech as N. The engine never pulls
the model's stream past the end of a block while she talks. So it never pulls
past a tool call. The first sentence after a tool call is made as it is
today. There, the tool run is the wait, and a clip made early would not
shorten it.

## A finding that changes the intent's premise

The intent says that with `barge_in` off, "no new action starts while she is
talking", because `_say` `join()`s and the generator is suspended
(intent, "The speech path today"). **That is not true of the SDK in use.**
In `claude_agent_sdk/_internal/query.py` 0.2.152, one reader task reads the
CLI's stdout. It hands every `control_request`, which includes
`can_use_tool` (our `_decide`, `claude_backend.py:370-450`), to a task of its
own the moment it is read (`:332-337`). It puts ordinary messages into a
buffer of 100 (`:172`, `:399`). The reader only stops when that buffer is
full. So while she speaks sentence N, the reader keeps reading. A tool call
that comes within about 100 stream events after N is decided, and runs,
while she is still talking. The flush at `content_block_stop`
(`claude_backend.py:939-947`) is there so that "let me go and look" is
spoken while the look runs. That only works because of this.

So today, suspending `ask_stream` is back-pressure with about 100 messages of
slack. It does not stop actions. This spec does not rely on that
back-pressure and does not change it. It adds one bound of its own that
holds whatever the SDK does: **our code never consumes a message past the
end of a block while she is talking.** Whether actions should be allowed to
run under her voice at all is a separate question. It is flagged for the
owner below, not decided here.

## Decisions on the intent's open questions

The owner approved the intent without answering these. Each is decided here,
with the reasoning. **Any of them can be rejected at this gate.** If one is,
the design below changes in that place only.

### 1. Where does the prefetch live? **In the mouth, with a make/play seam in `Feedback`.**

The engine owns the queue, the clock (`_voice_until`), the turn and every
way of dropping speech (`_drop_queued_speech`, `local_engine.py:348-359`). A
clip made early is one more thing that must be dropped with the line it
belongs to. So it hangs off the queued line in the engine. `Feedback` gets
only the seam: it can say whether a clip can be made ahead
(`tts_command` unset and `elevenlabs.ready`), make one, and speak a line from
a clip it is given. `_speak_now(text)` stays the one fallback chain
(`feedback.py:141-158`). When no clip was made ahead, it is called exactly as
today.

Rejected: a clip cache in `Feedback`, keyed by text. It knows nothing of
turns or drops. A cancelled line's clip would stay in the cache, and it would
play if the same text were said again later. That is the one outcome the
intent forbids. The same sentence ("Done.") often comes back.

### 2. With `barge_in` off, how does N+1 reach the mouth? **Pull one sentence ahead, within the block only.**

- The backend marks the end of every block. `WarmBrain._turn` yields a
  sentinel, `BLOCK_END`, at each `content_block_stop`, after the tail it
  already flushes there (`:939-947`). It also yields one after the sentences
  of an `AssistantMessage` that arrived without deltas (`:948-958`).
  `ask_stream` passes it through only when called with `blocks=True`. The
  default is unchanged, so `tests/test_claude_backend.py` does not change.
- The engine asks for marks only from a brain that says it has them:
  `WarmBrain.marks_blocks = True`. A brain without the attribute (every
  test fake, any future brain) gets today's loop exactly. It fails closed.
- In `_answer` (`local_engine.py:585-594`), with `barge_in` off, a brain that
  marks blocks, and `feedback` able to make ahead:
  - After a sentence, `_say` queues it and does not `join()`. The engine
    pulls the next item while it plays.
  - If the next item is a sentence, it is queued (see 3 for the clip). The
    engine then waits until the mouth has taken it, which means N has
    finished, before it pulls again. **At most one unplayed line is ever
    queued.**
  - If the next item is `BLOCK_END`, the engine `join()`s: everything queued
    plays out before the stream is pulled again. Pulling `BLOCK_END` does
    not read past the block, because the generator stops at that `yield`.
  - When the stream ends, or anything leaves the loop, the engine `join()`s
    as today, so a held prompt, the trace and the next capture all still
    come after her last sentence.
- In any other case (`barge_in` on, Piper, `tts_command`, espeak-ng, an
  unmarked brain) the loop is today's loop, line for line.

What this gives up: a sentence that follows a tool call is not made ahead.
What it keeps: whatever our code pulls while she talks is text from the block
she is already speaking. No tool call is in it.

Rejected: pull freely one sentence ahead. The pull would go on through a
tool run and the next model call while she talks. The finding above shows
that the SDK already reads ahead, but that is not a reason for our code to
widen it. It would also buy nothing: the gap after a tool call is the tool
run, not the synthesis. Rejected: make clips only for lines already queued.
With `barge_in` off, which is this machine's setting, nothing is ever
queued ahead, so the feature would do nothing here.

With `barge_in` on, the engine already pulls without waiting. The mouth makes
the clip for the line at the head of the queue while it plays the current
one. Nothing about pulling changes in that mode.

### 3. Cancelling a clip being made or already made. **Left to finish, never played, dropped with its line.**

- A queued line is a small object: the text and, if one was started, the
  future of its clip. The only clip the mouth ever plays is the one attached
  to the line it has just taken off the queue. No clip is played by text, by
  position or later.
- The mouth makes ahead for the line at the head of the queue, at two
  moments: when it takes a line, and when `_say` queues a line while the
  mouth is speaking. **At most one make-ahead is in flight at a time.** If
  one is still running for a dropped line, no new one starts. That line is
  then made the ordinary way when its turn comes.
- `urllib` and `ffmpeg` in `elevenlabs.synth` (`elevenlabs.py:116`) block a
  thread and cannot be cancelled cleanly. So a request in flight is **left to
  finish**, bounded by `synth`'s 30 s timeout, and its result is thrown away.
  A done-callback takes the result or the exception, so nothing is left
  unretrieved.
- Barge-in, mute, toggle and `_drop_queued_speech` take the line off the
  queue, as today. Its clip goes with it and is logged as
  `tts     made ahead, not played (<n> chars)`. On stop, the mouth task is
  cancelled, as today. A turn that raises drops the line queued ahead, lets
  the playing line finish, then apologises (see Invariants).
- The make-ahead calls `synth` with `trace=None`. Its SYNTH spans would
  otherwise land inside N's SPEAK span and distort N's `first-audio`
  (`trace.py:120-134`).

### 4. Order against #135 and #136. **Independent. #137 does not wait for either one. Piper is not made ahead here.**

- **#135** (stream ElevenLabs into `pw-cat`) makes the first sample of a
  sentence come sooner. It helps the sentences that cannot be made ahead: the
  first one, and the first after a tool call. #137 hides the whole synthesis
  of the others. They work on different sentences.
- The seam is shared (`feedback.py:148-149`). Whichever merges second
  rebases. The one constraint on #135: a line made ahead has no `pw-cat`
  running yet, so it needs a whole clip, as `synth` returns today. If #135
  replaces `synth` with a streaming call, it must keep a way to make a whole
  clip, with the same mastering, or #137's make-ahead falls back to today's
  path. That is safe, because only the gain is lost. This constraint is sent
  to #135's spec author.
- **#136** (resident Piper) removes the 1.6 s start-up. After it, a Piper
  sentence's wait is a small fraction of a second, so making it ahead would
  save little. It would also need PCM kept in memory from a process #136 has
  not designed yet. Piper, `tts_command` and espeak-ng are **not** made
  ahead, and with them the loop is today's loop. A later issue can add Piper
  once #136 exists and its trace shows a gap worth hiding.

### 5. Cost of clips that are never played. **Accepted: at most one sentence per interruption, on by default, no flag.**

The make-ahead is bounded to one line, and to one in flight. So an
interruption (barge-in, mute, toggle, stop, a failed turn) throws away at
most one sentence of characters. A sentence here is typically 40 to 120
characters. The log line from 3 counts every clip that was not played, so
the owner can add up the waste from `session.log` without guessing.

There is no config flag. The switch that matters is decision 6: if the trace
does not show the gain, the change is reverted, not left in place turned off.
A flag would be a second code path to keep safe for a feature that should
not exist unless it pays.

### 6. The evidence, now that gate C is overridden. **A fixed before/after trace, run by the owner. Revert if it does not pay.**

- **What the trace shows after.** For a line made ahead, the mouth opens one
  SYNTH span, named `ahead`, around the wait for its clip, inside that line's
  own SPEAK span. When the clip was ready, that span is about 0 s. A line
  made the ordinary way keeps its `request`, `download` and `decode` spans. So
  `synth=` in the TIMING line still means "time she waited for synthesis",
  and `first-audio` is not changed by clips made ahead. `tools/timing_report.py`
  already sums SYNTH by name (`BROKEN_DOWN`, `trace.py:57`), so `ahead` shows
  up with no change to the report.
- **The protocol.** The owner runs it. It is never run by an agent, because it
  costs quota. `trace_timings = true`, `barge_in` off, ElevenLabs on. A fixed
  list of 10 typed prompts, each answered with a three-sentence reply that
  uses no tools. Run it on `main`, then on the branch. About 60 sentences in
  all.
- **What keeps the change.** On the branch, the p50 of `speak=` is lower than
  on `main` by at least one third of `main`'s p50 `synth=`. Two of the three
  sentences are made ahead, so two thirds is the ideal, and one third leaves
  room for noise. The p50 of `first-audio=` does not move by more than 10 %.
  If it moves more, the trace fix is wrong. **If `speak=` does not fall by
  that much, the change is reverted.**

## Design

Three files change, plus tests. `trace.py` and `elevenlabs.py` do not change.

1. **`claude_backend.py`**: the `BLOCK_END` sentinel, `ask_stream(...,
   blocks=False)`, `WarmBrain.marks_blocks = True`. `_turn` yields
   `BLOCK_END` at each block end (decision 2).
2. **`feedback.py`**: `can_make_ahead()` (`tts_command` unset and
   `elevenlabs.ready`), and `make_ahead(text)`, which is `elevenlabs.synth(text,
   config, trace=None)`. `_speak_now(text, made=None)` gets an optional
   argument, where `made` is a clip or the exception the make-ahead raised.
   With a clip, it plays it with `_play`. With an exception, it logs
   `tts     elevenlabs failed (...) — using piper` and goes on to
   `_speak_piper`, as today. With `None` it is today's body. The mouth calls
   `_speak_now(text)` with one argument when nothing was made ahead, so every
   existing test that replaces `_speak_now` with a one-argument fake still
   holds.
3. **`local_engine.py`**:
   - `_speech` holds `_Line` objects (`text`, `clip` future or `None`).
     `_speech` subclasses `asyncio.Queue` with a `peek()`, the way
     `PriorityQueue` subclasses it. Everything that reads the queue today,
     including `_record`'s `self._speaking or not self._speech.empty()`
     (`:376`), keeps its meaning.
   - `_speech_loop` (`:299-324`): take the line, start the make-ahead for the
     head of the queue, get the line's clip if it has one (the SYNTH `ahead`
     span), then `_speak_now`. The `finally` block is unchanged. In
     particular, `_voice_until` becomes finite only when the queue is empty
     after `pw-cat` returns.
   - `_say(text, wait=True)`: the log line, `_voice_until = inf`, `_said`, and
     the `_mic_shut` wait, all unchanged and all before the line is queued.
     After queueing, it starts a make-ahead if the mouth is speaking.
     `wait=False` skips only the final `join()`.
   - `_answer`: the loop from decision 2. It runs only when `barge_in` is
     off, `brain.marks_blocks` is true and `feedback.can_make_ahead()` is
     true.
   - `_drop_queued_speech`: also logs each dropped line whose clip had been
     started.

## Invariants

Each one is a test. See Verification.

- **I1, #114, her voice shuts the capture.** With `barge_in` off,
  `_voice_until` is `inf` from the `_say` of the first sentence until the
  `pw-cat` of the last queued line returns. A make-ahead that finishes, or
  fails, never touches `_voice_until`. `_say` still waits for `_mic_shut`
  before a line is queued.
- **I2, #114, the capture waits out the echo.** The next capture opens no
  earlier than the end of her last `pw-cat` plus `ECHO_TAIL_SECONDS`
  (0.35 s). `_answer` does not return, reach a held prompt or finish the
  trace before her last line has played.
- **I3, bounded.** At most one unplayed line is queued, and at most one
  make-ahead is in flight. With `barge_in` off, the brain is at most one
  sentence ahead of the mouth.
- **I4, no pull past a block while she talks.** With `barge_in` off, nothing
  after a `BLOCK_END` is pulled until every queued line has played.
- **I5, #86.** `_voice_until` keeps its meaning. Every line is in `_said`
  from the moment it is queued, which is before it plays (fails closed).
  `SpokenConsentTests` pass unchanged.
- **I6, #120.** A failed make-ahead is never a turn failure. The line falls
  through to Piper. A turn that raises drops the line queued ahead, lets the
  line that is playing finish, logs, apologises, and counts toward
  `TURN_FAILURES_TO_MUTE` exactly as today. No make-ahead starts after a
  mute. The thread count is at most the mouth's one plus one make-ahead.
- **I7, barge-in on.** Lines are still queued without a `join()`, the capture
  may stay open, #86 still refuses a spoken confirm, and a barge-in empties
  the queue, clips included.
- **I8, dropped is never played.** After a cancel, stop, mute, toggle,
  barge-in or a turn failure, a clip that was made ahead is never played. That
  includes a clip that finishes after the drop, and the same text said again
  in a later turn.
- **I9, the default path is unchanged.** With Piper, `tts_command`,
  espeak-ng or an unmarked brain, the calls to `ask_stream` and `_speak_now`,
  and their order and timing, are the same as on `main`.

## Alternatives rejected

- **A clip cache in `Feedback`**: see decision 1. It could replay a
  cancelled clip.
- **Pull ahead with no block bound**: see decision 2. Our code would widen
  the window in which actions run under her voice, for no gain.
- **Make ahead only for lines already queued**: see decision 2. It does
  nothing with `barge_in` off, which is the default and this machine's
  setting.
- **Wait for #135**: see decision 4. The two help different sentences.
  Waiting would tie #137 to #135's open questions about mastering and voice.
- **A config flag**: see decision 5.
- **Cancel the HTTP request**: `urllib` has no clean cancel from another
  thread. Closing the socket under it would be a new failure mode on the one
  path that must degrade, never mute. Letting it finish costs at most one
  sentence of quota.

## Risks

- **Actions already run under her voice (the finding).** This is not caused
  by this change, and not fixed by it. If the owner wants "no action while
  she talks" to be a real guarantee, that is a new issue. It would need the
  SDK's reader to wait for the consumer before it handles a control request,
  which the SDK does not offer, or `_decide` to wait on the mouth. It is out
  of scope here.
- **Overlapping spans.** The TURN span of a sentence pulled ahead now runs
  during the previous SPEAK span. `phase_seconds` sums can then exceed the
  task's total. `speak=` and `synth=` stay comparable. `model-turn=` is not
  used as evidence for this change.
- **Shutdown.** A make-ahead in flight at stop holds one executor thread for
  up to `synth`'s 30 s timeout. The mouth's own `to_thread` has the same
  exposure today. This adds at most one more thread.
- **Private queue state.** `peek()` reads `asyncio.Queue._queue`, the
  subclass hook the standard library's own queues use. A test pins its
  behaviour.
- **Hosts.** p620 and razer use ElevenLabs with `barge_in` off, so both get
  the change. Installs without a key get none of it (I9).

## Verification

No ElevenLabs call and no audio, ever. Every run uses
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`. Time comes from the
stepped clock that `SteppedRoomCase` provides (`tests/test_local_engine.py:1168`).
Nothing depends on how loaded the machine is. Baseline at `b0e3a54`:
`tests/test_local_engine.py`, `tests/test_feedback.py` and
`tests/test_claude_backend.py` give 220 passed, 84 subtests.

**New tests** (`PrefetchTests(SteppedRoomCase)`, plus backend and feedback
units). The fake mouth takes 1 stepped second to make a clip and 1 to play
it. The fake brain records the stepped time of each pull and of each "tool"
it runs between blocks.

| Test | Proves |
| --- | --- |
| three sentences in one block: gap between plays is 0 s after, 1 s before | the gain |
| plays are in order and never overlap | outcome |
| `_voice_until` is `inf` throughout the reply, and the capture opens at the last play's end + 0.35 s (+ one frame) | I1, I2 |
| the brain is never more than one sentence ahead, and the queue never holds two unplayed lines | I3 |
| `[S1, BLOCK_END, tool, S2]`: the tool runs after S1 has finished playing, and S2 is not made ahead | I4 |
| an unmarked brain, and `can_make_ahead()` false: pull times and `_speak_now` calls equal `main`'s | I9 |
| N+1 is in `_said` before it plays, and `SpokenConsentTests` pass unchanged | I5 |
| mute, toggle, stop, and a barge-in during N with N+1 made ahead: N+1 is never played, including when its make finishes after the drop, and including the same text in the next turn | I8 |
| the make-ahead raises: the line is spoken by the Piper fake, the log line is written, and `failures` is unchanged | I6 |
| the turn raises with N+1 queued: N finishes, N+1 is dropped, the apology plays, and `FailureTests` pass unchanged | I6 |
| `barge_in` on: no `join()`, the head of the queue is made ahead, a barge-in empties everything | I7 |
| trace: a line made ahead has one `ahead` SYNTH inside its own SPEAK, and `first_audio` equals the unprefetched run | decision 6 |
| backend: `BLOCK_END` after every `content_block_stop` and after an `AssistantMessage`-only block, only with `blocks=True` | decision 2 |

**Mutation checks.** Each change below must make at least one named test
fail. The plan lists which test.

1. `_answer` ignores `BLOCK_END` and keeps pulling (I4).
2. The make-ahead's completion sets `_voice_until`, or the mouth's
   `finally` ignores a queued line (I1, I2).
3. `_said` is appended when a line plays instead of when it is queued (I5).
4. `_drop_queued_speech` leaves the clip reachable, or the mouth plays the
   latest finished clip instead of its line's clip (I8).
5. The engine allows two lines ahead (I3).
6. The make-ahead passes `trace=self.trace` (decision 6).
7. A failed make-ahead is swallowed without falling through to Piper, or it
   raises into the turn (I6).
8. `marks_blocks` defaults to true (I9).

**Before/after protocol with fakes, run by the plan's author.** A scratch
script, not committed, drives the real `LocalSession` against the fakes above
on `main` and on the branch. It prints, for four scenarios, each sentence's
make start, play start, play end and the time the capture opened: one block
of three sentences; block, tool, block; mute during sentence 1; `barge_in`
on with a barge-in during sentence 1. The expected result: the same capture
times and the same played lines on both, except that in the first two
scenarios the gaps inside a block fall from 1 s to 0 s. The gap after the
tool is unchanged.

**After merge, with real audio, run by the owner.** Decision 6's protocol.

**Suites.** The full `nix develop -c python3 -m pytest -q` is green. The
three files above are at least 220 passed, and no existing test is edited
except to add fakes.
