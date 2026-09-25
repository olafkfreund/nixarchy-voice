---
status: draft
issue: 139
intent: intent/2026-09-24-139-tools-while-speaking.md
---

# Spec: a call that can change something waits until her line before it has played

Closes #139. Line numbers are `main` `50dcf99`. The packaged
`claude-agent-sdk` is 0.2.152.

**This is ordering only.** `_decide` and `Policy.check` do not change, and
nothing is allowed or refused that is not allowed or refused today. What
changes is *when* the PreToolUse hook starts deciding a call that can change
something: not before `ask_stream`'s consumer has read the stream up to that
call. With `barge_in` off, the consumer only gets there after her last line
has finished playing.

## Decisions on the intent's open questions

The owner approved the intent without answering these. Each is decided here,
with the reasoning. **Any of them can be rejected at this gate.** If one is,
the design below changes in that place only.

### 1. Is it a real problem? **Yes. Fix it.**

The evidence is the mechanism, not the log. The intent traced it in the SDK
(`query.py:287`, `:297-300`, `:332-337`): the hook runs on its own task the
moment the CLI asks, whatever `_answer` is doing. The persona makes an
announced call the normal case (`LOCAL_PERSONA`, `local_engine.py:141-143`),
and the flush at `content_block_stop` (`claude_backend.py:939-947`) puts
the line and the call milliseconds apart. So in any turn that follows the
persona, the call is decided while the line plays. The log agrees: 2 of 2
measurable cases acted 1 s into lines of about 1.5 s and 6.7 s. The sample is
too small to give a rate, but it contradicts nothing.

Why fix it rather than correct the docstring: "I'll close that window" said
over a window that is already gone is her describing the future after it
has happened. The persona's own rule ("never say the action happened")
exists so her words and the desktop agree. Today they do not agree for
actions. The fix is one wait in one function (see Design), and reads keep
their overlap. Correcting only the docstring would leave `_say`'s promise
("nothing else in this session runs while she is talking") false.

### 2. Where does the wait live? **In `_pre_tool_use`, before `_decide`. It covers our own tools.**

- **Not in `ask_stream`.** Our code cannot make the SDK stop reading.
  `ask_stream` is already suspended while she talks, and the hook fires
  anyway. That is the finding. Only the hook's answer holds the CLI back.
- **In the hook, not in `_decide`.** `_pre_tool_use` (`claude_backend.py:481-503`)
  is the one entry point for every call: the release path (`:371-399`), our
  own `mcp__omarchy__*` tools (`:413-419`) and Claude Code's built-ins
  (`:421-447`). A wait at its top covers all three, and `_decide` is not
  edited at all. So the gate is unchanged by construction, not by review.
- **Before the decision, not after it.** `_decide` calls
  `executor.on_action` (`:390`, `:445`), which logs `action` and turns the
  orb to "acting" (`local_engine.py:263-265`). Waiting after that would log
  the action before the line has ended. Waiting before it puts the log, the
  orb and the call in the order she says them. A denied, held or dry-run call
  also waits. Nothing runs for those, so the only effect is that the refusal
  is written after her line, which is the right order too.
- **Our own tools are covered.** The CLI asks the PreToolUse hook before it
  sends the `mcp_message` that runs `Executor.call` (`tools.py:1950-1966`).
  `_decide` allows ours at once and leaves the gate to `Executor.call`. So a
  hook that has not answered yet means `Executor.call` has not started yet.
- The wait sits inside the hook's `try`. It never raises into the SDK (see 4
  for the cap and for what happens when it ends early). The hook still
  answers every call explicitly.

**What the hook waits for.** Not "the mouth is idle". The hook can arrive
before `_answer` has even pulled the line that announces the call. The SDK
reader buffers `content_block_stop` and runs the hook task while the consumer
may not yet have run. An idle-mouth check at that moment would see an empty
queue and let the call through ahead of the line. That is the bug, just
narrower. So the hook waits until **`ask_stream`'s consumer has read the
stream up to this call**: `_turn` records the id of every `tool_use` it
reads, and the hook waits for its own `tool_use_id` to appear.

- Streamed, which is how this brain runs (`include_partial_messages`,
  `claude_backend.py:769`): the id is taken from the `content_block_start`
  of the `tool_use` block. The API streams that block before its input is
  complete, and the hook cannot fire before the input is complete. So the
  event is always in the stream ahead of the hook's request. The text block
  before it has already stopped, and its tail has already been yielded.
- Not streamed: the id is taken from the `AssistantMessage`'s `ToolUseBlock`,
  **after** that message's sentences have been yielded, never before.
- With `barge_in` off, the consumer is `_answer`. It does not pull again
  until `_say` has `join()`ed (`local_engine.py:344-346`). So "read up to
  the call" implies "every line before it has played". No separate idle
  signal from the engine is needed, and none is added.

### 3. Which calls wait? **Everything `_is_read` does not name. `_is_read` is kept as it is.**

- Reads never wait. `_is_read(tool)` (`claude_backend.py:113-117`) is checked
  first, and a read goes straight to `_decide` as today. "Let me look." keeps
  playing over the look.
- `_is_read` is coarse, but only in the safe direction. It is an allowlist,
  so a tool that is not on it waits. The cost of a harmless call waiting is
  the rest of one line. The cost of a harmful call not waiting is the bug. A
  second list, "safe to overlap", would be a new classification to keep
  correct and to review, and it would buy back about a second on a few calls.
  Rejected. Bash is not offered at all (#94); `run_shell` is ours, is not in
  `READ_ONLY_TOOLS` (`tools.py:58-60`), and waits, `ls` included.
- The release path waits too. A confirmed call is an action, and "Rebooting
  now." should be heard before the reboot starts.

### 4. The cap, and the latency cost. **10 s, then the call is decided as today. The cost is accepted.**

- **Cap: `SPEECH_FIRST_CAP_SECONDS = 10.0`**, a module constant in
  `claude_backend.py`, not config. The longest line in the log is about
  6.7 s of playback, and ElevenLabs synthesis comes before it. 10 s covers
  that with room to spare. It stays far below the CLI's hook timeout of 60 s
  (`HookMatcher.timeout` default, `types.py:601`), so the CLI never times the
  hook out. A hook that times out may let the call run unchecked, the same
  as a hook that raises.
- **When the cap is hit**, the hook logs
  `warn    <tool> waited 10s for her line; deciding now` and goes on to
  `_decide`. The call is decided exactly as today. A stuck `pw-cat`, a
  consumer that stopped pulling, or an id that never shows up cost 10 s and
  one log line, never a hung turn and never a skipped decision.
- **The wait also ends when the turn ends.** `ask_stream`'s `finally` (the
  one that already clears `_releasing`, `claude_backend.py:899-908`) wakes
  every waiting hook. Toggle-off, barge-in, cancel and `reset_turn` all end
  the consumer, so none of them leaves a call waiting out the cap. A call
  released this way is decided as today, and today's interrupt then deals
  with the turn. Choosing to refuse it would be a gate change, and is out of
  scope.
- **A call that waited is visible.** When the id was not already read on
  entry, the hook logs `wait    <description> <n.n>s for her line` before
  `_decide`. Because `say` is logged when a line starts (`local_engine.py:333`),
  the `say`/`action` order alone cannot show the difference. This line
  does.
- **The cost.** An announced action now starts when the line ends, instead of
  about 50 ms after it starts. That adds the rest of the line to the turn:
  typically 1-2 s ("Right, switching now." is about 1.5 s), and at most the
  cap. Reads, routed turns (#71, no brain) and a call with no line in front
  of it pay nothing. The hook finds its id already read. This is the point
  of the change: the time is spent so that she is heard first.

### 5. `barge_in` on, #137 and #114. **`barge_in` on keeps today's overlap. #137 and #114 need no change.**

- **`barge_in` on: no wait.** In that mode `_say` does not `join()`, so the
  consumer runs ahead of the mouth, and "read up to the call" would not mean
  "heard". Making it mean that would need a separate idle signal and would
  change a mode the intent left alone. The wait is switched on by the brain
  the local engine builds, and only when `barge_in` is off:
  `LocalBrain.speech_first = not config.barge_in` in `brain_for`
  (`local_engine.py:197-201`). `WarmBrain`/`ClaudeBrain` default to `False`,
  so every other caller and every existing backend test is unchanged. It
  fails open to today's behaviour, which is safe: this is ordering, not
  policy.
- **#137 (make-ahead, lands after #135): no conflict.** #137's I4 is "nothing
  after a `BLOCK_END` is pulled until every queued line has played". A tool
  call always comes after the block that announces it, so under #137 the
  consumer still reaches the call's `tool_use` only after the mouth is empty.
  The barrier still means "heard". Make-ahead does not count as speaking on
  its own. A clip being made belongs to a line that is already queued, and
  the queue is what the consumer waits out. #137 adds a yield of `BLOCK_END`
  in `_turn` at the same `content_block_stop` and after the non-streamed
  sentences. This spec records the id *after* those yields, so the order is
  the same whichever merges first. The one textual overlap is `_turn`, and
  whichever merges second rebases. #137's risk note says a fix "would need
  … `_decide` to wait on the mouth". This spec waits in `_pre_tool_use`
  instead, on the consumer, and does not touch `_decide` (decision 2).
- **#114: unchanged.** The gate reads `_voice_until` and `_mic_shut`, and
  this spec touches neither. No capture is open during a turn. A call now
  runs during the echo tail rather than during the line. Nothing listens
  then, so nothing changes for the microphone.

## Design

Two files change, plus tests.

1. **`claude_backend.py`**
   - `SPEECH_FIRST_CAP_SECONDS = 10.0`.
   - `ClaudeBrain.speech_first = False` (class attribute).
   - Per turn: `self._read_tools: set[str]` and one `asyncio.Condition`,
     reset at the start of `ask_stream`. `_turn` adds the `id` of each
     `tool_use` it reads (decision 2 says where) and notifies. `ask_stream`'s
     `finally` sets a turn-over flag and notifies.
   - `_pre_tool_use`: inside the existing `try`, before `_decide`, when
     `self.speech_first` and not `_is_read(tool)`, wait on the condition until
     `tool_use_id` is in `_read_tools` or the turn is over, bounded by
     `asyncio.wait_for(..., SPEECH_FIRST_CAP_SECONDS)`. `TimeoutError` gives the
     `warn` line and goes on. A real wait gives the `wait` line. Then `_decide`,
     unchanged. A missing or empty `tool_use_id` does not wait.
2. **`local_engine.py`**: `LocalBrain` sets `speech_first` from
   `config.barge_in`. `_say`'s docstring gets one clause saying the promise
   holds for actions through the brain's wait, and that reads still overlap.

The persona text does not change: "say one short line … then make the call"
is now true in the order she means.

## Invariants

- **I1, gate unchanged.** `_decide` is byte-identical. Every call is still
  answered explicitly, and the hook never raises.
- **I2, heard first.** With `barge_in` off, a non-read call's `_decide`, and so
  its `on_action`, `Executor.call` and the call itself, starts no earlier than
  the end of playback of every line yielded before its `tool_use`.
- **I3, reads overlap.** An `_is_read` call is decided without waiting, while
  the line plays.
- **I4, bounded.** No hook waits longer than `SPEECH_FIRST_CAP_SECONDS`. The end
  of `ask_stream`, for any reason, releases every waiting hook at once.
- **I5, opt-in.** With `speech_first` false (`barge_in` on, any other brain),
  hook timing is today's.

## Alternatives rejected

- **Wait in `ask_stream` / stop consuming.** It does not hold the CLI back.
  That is the finding.
- **Wait for an idle mouth (`_speech.join()`, or `_voice_until`).** It can
  pass before the announcing line is queued (decision 2). It would also need
  an engine callback into the brain.
- **Wait inside `_decide`, after the policy.** `on_action` would log the call
  as run before her line ends. It would also need three insertion points in
  the function that must not change.
- **Refuse a call whose turn ended while it waited.** That is a gate change,
  not ordering.
- **A "safe to overlap" list finer than `_is_read`** (decision 3).
- **A config key for the cap.** One value, no second user. A constant.
- **Waiting with `barge_in` on** (decision 5).

## Risks

- **Id mismatch.** If the hook's `tool_use_id` ever differs from the stream's
  block id (a CLI change), every action waits the full cap. It is still safe,
  but it is slow, and `warn … waited 10s` lines show it in `session.log` at
  once. The plan pins the field names against the SDK 0.2.152 types.
- **Subagent calls.** A tool used inside a subagent would not appear in the
  main stream and would wait out the cap. Nothing offered spawns subagents
  (#94: no `Task`). The cap bounds it if that changes.
- **Non-streamed text with no sentence end.** In the `AssistantMessage`-only
  path, a tail without a terminator stays in `buffer` past the tool call, as
  today. The call does not wait for text that has not been yielded. It is rare
  with partial messages on, and unchanged by this spec.
- **Hosts.** p620 and razer run `barge_in` off, so both get the wait.

## Verification

No audio, no network, no real CLI. Every run uses
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`. Time comes from a stepped
clock: `SteppedRoomCase` (`tests/test_local_engine.py:1168`) for the engine,
and the same stepping for the backend, which drives `FakeClient`
(`tests/test_claude_backend.py:960`) with scripted stream events and calls
`_pre_tool_use` on its own task, the way the SDK does.

| Test | Proves |
| --- | --- |
| stream `["Closing it now." , block stop, tool_use t1]`, consumer takes 1 stepped s per sentence, hook for t1 started at t=0: `_decide` runs at t≥1, and the `action` log follows the `wait` line | I2 |
| same with a read (`mcp__omarchy__hypr_query`): decided at t=0 | I3 |
| hook's id never appears: decided at the cap, `warn` logged, verdict equal to a no-wait run | I4 |
| turn cancelled mid-line (`aclose` the stream): the waiting hook returns before the cap | I4 |
| `speech_first` false: decision times equal to `main` | I5 |
| non-streamed `AssistantMessage` with text then `ToolUseBlock`: the id is recorded after the text is yielded | decision 2 |
| engine: `LocalSession` with a `LocalBrain` on `FakeClient`, `barge_in` off: the fake `Executor.call` starts after the fake mouth's play end; `barge_in` on: it starts during the play | I2, decision 5 |
| every existing `HookTests`, `GateTests`, `ReleaseTurnGateTests`, `DryRunTests` passes unchanged | I1 |

**Mutation checks.** Each must make at least one named test fail. The plan
lists which.

1. The hook waits for an idle mouth instead of for the id.
2. The id is recorded before the non-streamed sentences are yielded.
3. `_is_read` calls wait.
4. The `wait_for` cap is removed.
5. `ask_stream`'s `finally` does not wake the waiters.
6. `speech_first` defaults to true.
7. The wait moves after `_decide`, so the `action` log comes first.
8. `_say` stops `join()`ing with `barge_in` off (the barrier stops meaning
   "heard").

**Suites.** `nix develop -c python3 -m pytest -q` is green, and no existing
test is edited except to add fakes.

**After merge, run by the owner.** In the next real sessions, check that
announced actions show a `wait` line and that no `warn … waited 10s` lines
appear.
