---
status: draft
issue: 139
spec: spec/2026-09-24-139-tools-while-speaking.md
---

# Plan: a call that can change something waits until her line before it has played

Closes #139. Base: `main` `50dcf99` (1085 tests collected: 108 in
`tests/test_claude_backend.py`, 104 in `tests/test_local_engine.py`). Every
`file:line` below was checked against `50dcf99`, and the SDK names against
the packaged `claude-agent-sdk` 0.2.152. Step 1 rebases onto #128, so
re-check them after that rebase. Only `src/omarchy_voice/claude_backend.py`,
`src/omarchy_voice/local_engine.py`, `tests/test_claude_backend.py` and
`tests/test_local_engine.py` change. `tools.py`, `config.py`, `flake.nix`
and the persona text do not change.

## Approved decisions, carried over from the spec

1. **It is a real problem, and it is fixed, not documented.** The
   PreToolUse hook runs on its own SDK task the moment the CLI asks
   (`_internal/query.py:540-544` calls it with `request_data["input"]` and
   `request_data["tool_use_id"]`), whatever `_answer` is doing. The persona
   makes "say one short line, then make the call" the normal turn
   (`LOCAL_PERSONA`, `local_engine.py:141-143`), and the flush at
   `content_block_stop` (`claude_backend.py:939-947`) puts the line and the
   call milliseconds apart. So today an action is decided while the line
   that announces it plays. The fix is ordering only.
2. **The gate does not change.** `ClaudeBrain._decide`
   (`claude_backend.py:363-447`) is byte-identical after this change, and
   `Policy.check` is not touched. Nothing is allowed or refused that is not
   allowed or refused today. The hook still answers every call explicitly
   and never raises (`:481-514`).
3. **The wait lives in `_pre_tool_use`, inside its existing `try`, before
   `_decide`.** Not in `ask_stream` (our code cannot make the SDK stop
   reading, and the hook fires anyway), and not inside or after `_decide`:
   `_decide` calls `executor.on_action` (`:390`, `:445`), which logs
   `action` and turns the orb to "acting" (`local_engine.py:263-265`).
   Waiting first puts the log, the orb and the call in the order she says
   them. One place covers the release path (`claude_backend.py:371-402`), our
   own `mcp__omarchy__*` tools (`:413-419`; the CLI asks the hook before it
   sends the message that runs `Executor.call`, `tools.py:1869`, whose
   `RUN`/`on_action` are at `:1952-1953`) and the built-ins (`:421-447`). A
   denied, held or dry-run call also waits; only its refusal moves after the
   line.
4. **The hook waits for its own `tool_use_id` to have been read by
   `ask_stream`'s consumer, not for an idle mouth.** An idle-mouth check can
   pass before `_answer` has even pulled the announcing line. `_turn` records
   the id of every `tool_use` it reads:
   - Streamed (`include_partial_messages`, `:769`): from the
     `content_block_start` event whose `content_block.type == "tool_use"`,
     field `content_block.id`. The API streams that start before the input
     is complete, and the hook cannot fire before the input is complete, so
     it is always in the stream ahead of the hook. The text block before it
     has already stopped and been flushed.
   - Not streamed: from each `ToolUseBlock` (`types.py:950-955`, field
     `id`) of the `AssistantMessage`, **after** that message's sentences have
     been yielded (after `:956-958`), never before.
   - With `barge_in` off, the consumer is `_answer`, which does not pull
     again until `_say` has `join()`ed (`local_engine.py:345-346`). So "read
     up to the call" means "every line before it has played". No idle signal
     from the engine is added.
   - The hook takes the id from its second argument (`tool_use_id`, SDK
     `HookCallback`, `types.py:577-583`). It is also in
     `hook_input["tool_use_id"]` (`PreToolUseHookInput`, `types.py:318`).
     The argument is the one used.
5. **Which calls wait: everything `_is_read` does not name.** `_is_read`
   (`claude_backend.py:113-117`) is checked first and is not edited. A read
   is decided at once, as today, and "Let me look." plays over the look.
   `run_shell` is not in `READ_ONLY_TOOLS` (`tools.py:58-60`) and waits. The
   release path waits too: "Rebooting now." is heard before the reboot. No
   finer "safe to overlap" list.
6. **Cap: `SPEECH_FIRST_CAP_SECONDS = 10.0`**, a module constant in
   `claude_backend.py`, not config. Far below the CLI's 60 s hook timeout
   (`HookMatcher.timeout`, default 60, `types.py:601-602`; our matcher at
   `claude_backend.py:589-590` sets none). When the cap is hit the hook logs
   `warn    <tool> waited 10s for her line; deciding now` and goes on to
   `_decide`, which decides as today.
7. **The end of the turn releases every waiting hook.** `ask_stream`'s
   `finally` (`:903-911`, the one that already clears `_releasing`) wakes
   them. A call released this way is decided as today. Refusing it would be
   a gate change and is out of scope.
8. **A call that waited is visible.** When its id was not already read on
   entry and the wait ended before the cap, the hook logs
   `wait    <description> <n.n>s for her line` before `_decide`
   (`<description>` is `describe_tool`'s). `say` is logged when a line starts
   (`local_engine.py:334`), so only this line shows the difference.
9. **The cost is accepted.** An announced action starts when the line ends,
   typically 1-2 s later, at most the cap. Reads, routed turns (#71) and a
   call with no line before it pay nothing: the id is already read.
10. **Opt-in, and only with `barge_in` off.** `ClaudeBrain.speech_first =
    False` is a class attribute beside `builtin_tools` (`:279`), so
    `WarmBrain`, `think()` (the `say` CLI and the MCP path) and every
    existing test are unchanged. `brain_for` (`local_engine.py:184-203`)
    sets `speech_first = not config.barge_in` on the `LocalBrain` it builds.
    With `barge_in` on, `_say` does not `join()`, "read" would not mean
    "heard", and that mode keeps today's overlap. It fails open to today's
    timing, which is safe: this is ordering, not policy.
11. **#137 and #114 need no design change.** #137's I4 ("nothing after a
    `BLOCK_END` is pulled until every queued line has played") keeps the
    barrier meaning "heard", because a call always follows the block that
    announces it. #139 records ids *after* #137's `BLOCK_END` yields. #114's
    gate reads `_voice_until` and `_mic_shut`; this plan touches neither.
12. **A missing or empty `tool_use_id` does not wait.** Every existing test
    calls the hook with `None` (`tests/test_claude_backend.py:50`, `:267`,
    `:479`, `:519`; `ScriptedBrain` and `EchoBrain`, `tests/test_local_engine.py:538`,
    `:577`).
13. **`_say`'s docstring** (`local_engine.py:327-332`) gains one clause: the
    promise that nothing runs while she talks holds for actions through the
    brain's wait, and reads still overlap. `_say`'s code does not change.
    The persona text does not change.
14. **Rejected, not to be reintroduced:** waiting in `ask_stream`; waiting
    for an idle mouth (`_speech.join()`, `_voice_until`) or an engine
    callback into the brain; waiting inside `_decide`; refusing a call whose
    turn ended while it waited; a "safe to overlap" list; a config key for
    the cap; waiting with `barge_in` on.

Two points the spec leaves open are resolved here (flagged):

- **A. The per-turn state is one attribute, compared by identity.** The spec
  says "`_read_tools` and one `asyncio.Condition`, reset at the start of
  `ask_stream`", plus "a turn-over flag". A flag reset by the *next*
  `ask_stream` can clear before a hook woken by the previous turn's end
  re-checks it (the waiter runs a loop tick after `notify_all`); that hook
  would then wait out the cap. Resolution: `self._reading: tuple[set[str],
  asyncio.Condition] | None`, `None` in `ClaudeBrain.__init__`, a fresh
  tuple at the top of `ask_stream`, set back to `None` in its `finally`.
  The hook captures the tuple on entry and waits until its id is in that
  set **or `self._reading is not` that tuple**. "Turn over" is the identity
  change, so it cannot be un-set. A hook that arrives while `_reading` is
  `None` (no stream running: `think()`, or a CLI still working after the
  consumer left) does not wait. This is the spec's design with the flag
  folded into the reference; no behaviour the spec states changes.
- **B. The `wait`/`warn` lines go to the log only.** `_note`/`Executor.record`
  also write the transcript, which is the record of calls that did not run
  (`tools.py:1858-1866`). These lines are timing, not verdicts, so they go
  through `self.executor.on_record(line)` (the log sink alone). A cap hit
  logs `warn` and not `wait`.

## Landing order

**After #128.** `fix/128-wake-capped-echo-tail` (spec approved, `a9545ef`;
no plan or code yet at the time of writing) edits `local_engine.py` and
`tests/test_local_engine.py`, the two engine files this plan edits.
Overlap, by hunk: #128 changes `_record` (`_mic_shut.clear()` at `:405`,
the `finally` at `:416`) and `_wake_turn` (`:478-482`), and adds tests near
`:1687`; #139 changes `brain_for` (`:197-203`) and `_say`'s docstring
(`:327-332`), and adds one class before `FailureTests` (`:1592`). No shared
hunk is expected, but #139 goes second and re-checks every engine line
after rebasing. #128 unmerged is a stop (step 1).

**#137 (draft; waits for #135, then lands last in its batch): whichever
merges second rebases.** The overlap and its resolution:

- `claude_backend.py` `_turn`: #137 adds `yield BLOCK_END` after the
  flushed tail at `content_block_stop` (`:939-947`) and after the
  `AssistantMessage` sentences (`:956-958`). #139 adds its
  `content_block_start` branch right after `:947` and its `ToolUseBlock` ids
  right after `:958`: the same two insertion points. Resolution: #137's
  `yield BLOCK_END` first, then #139's id recording (decision 11). Never
  the other way round, or the id is read before the block's lines have
  played.
- `claude_backend.py` `ask_stream` (`:870-911`): #137 adds `blocks=False` to
  the signature (`:870-871`) and filters `BLOCK_END` in the loop
  (`:889-891`); #139 adds the `_reading` line at `:881` and the release in
  the `finally` (`:903-911`). Keep both; they are adjacent, not
  overlapping.
- `local_engine.py` `_say` (`:326-346`): #137 adds `wait=` and rewrites the
  body; #139 adds one clause to its docstring (`:327-332`). Keep #137's
  body and signature, add #139's clause.
- After #137, `_say(..., wait=False)` no longer joins in ahead mode; the
  barrier is the `join()` at `BLOCK_END`. Mutation M8 then becomes "remove
  the `BLOCK_END` join", and test E1 must still pass in ahead mode.

## Steps

0. **Baseline.** `git branch --show-current` is
   `fix/139-tools-while-speaking`; `gh issue view 139` is OPEN. With
   `export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`, run
   `nix develop -c python3 -m pytest -q` and
   `nix develop -c python3 -m unittest discover -s tests`
   → verify by both passing: 1085 at `50dcf99` (108 backend, 104 engine).
   Record any failure that already exists before continuing.
1. **Precondition: #128 has merged.** `gh pr list --state merged --search
   "128 in:title"` and `git fetch origin && git log origin/main --oneline |
   grep '#128'`. If not merged, **stop and report; do not implement.** If
   merged, `git rebase origin/main`, repeat step 0, record the new baseline,
   and re-check every `file:line` here (in the first implementation commit).
   If #137 has merged too, apply its notes under Landing order now.
   → verify by a clean rebase, a green baseline, and the lines confirmed or
   corrected in this file.
2. **`tests/test_claude_backend.py`: the fake SDK stream and seven tests,
   failing on `main`.** One class, `SpeechFirstTests(
   unittest.IsolatedAsyncioTestCase)`, directly before `class
   SystemPromptTests` (`:1328`). Same `setUp` patches as `WarmBrainTests`
   (`:1003-1013`: `ClaudeBrain._options`, `_with_desktop`).
   - *Stepped clock:* patch `claude_backend.time` with
     `SimpleNamespace(monotonic=lambda: self.now)`, `self.now = 1000.0`.
   - *Events:* `tool_start(id, name)` →
     `_named("StreamEvent", event={"type": "content_block_start", "index": 1,
     "content_block": {"type": "tool_use", "id": id, "name": name,
     "input": {}}})`. `said_then_tool(text, id, name)` → an
     `AssistantMessage` whose `content` is `[SimpleNamespace(text=text),
     _named("ToolUseBlock", id=id, name=name, input={})]`.
   - *`HookingClient(FakeClient)`* (`FakeClient` is `:960`): `query()` also
     starts, for each scripted `(id, tool, tool_input)`, a task
     `brain._pre_tool_use({"hook_event_name": "PreToolUse", "tool_name":
     tool, "tool_input": tool_input, "tool_use_id": id}, id, {"signal":
     None})`, at `t=1000`, before the consumer reads anything, as the SDK
     does. It records `(now, decision)` per id when a hook returns, and sets
     `self.pause` once every scripted hook has answered: the `"pause"` item
     in the pipe models the CLI not streaming past a call until it is
     answered.
   - *Consumer:* for each sentence, `settle()` (ten `asyncio.sleep(0)`), note
     `(sentence, now)`, `self.now += 1`, `settle()`. One stepped second per
     line, and any hook that can run does run at the time it could.
   - *Spy:* the instance's `_decide` is wrapped to append `("decide", tool,
     now)` to `self.log`; the brain's Executor is `Executor(config,
     on_record=self.log.append)`.
   - Every test body runs under `asyncio.wait_for(..., 5)` real seconds, a
     deadlock guard only; no assertion depends on real time.
   - Tests:
     - B1 `test_an_action_waits_for_the_line_before_it` (I2; decisions 3, 4,
       8, B): `speech_first=True`, `dry_run=False`; pipe `[delta("Pausing the
       music now. "), block_stop(), tool_start("t1",
       "mcp__omarchy__media_control"), "pause", result()]`. t1 is decided at
       `now >= 1001`; a `wait    ` line ending `1.0s for her line` comes
       before the `decide` entry; the transcript has no `wait` line.
     - B2 `test_reads_and_calls_without_an_id_never_wait` (I3; decisions 5,
       12): pipe with "Let me look, then pause it. ", `block_stop()`,
       `tool_start("r1", "mcp__omarchy__hypr_query")`, `tool_start("t1",
       "mcp__omarchy__media_control")`, `"pause"`. r1 is decided at 1000, t1
       at `>= 1001`. A direct hook call for `media_control` with id `None` is
       decided at once, with no `wait` line.
     - B3 `test_the_cap_decides_as_today` (I4; decision 6): first assert
       `claude_backend.SPEECH_FIRST_CAP_SECONDS == 10.0`, then patch it to
       0.05. `dry_run=True`; hook for `("t9", "Write", {"file_path":
       "/tmp/x", "content": "x"})`, id never streamed. The verdict equals the
       verdict of the same call with `speech_first=False` (the dry-run
       refusal: a denied/held/dry-run call waits and is refused as today);
       the log has `warn    Write waited 0.05s for her line; deciding now`
       and no `wait    ` line.
     - B4 `test_the_end_of_the_turn_releases_a_waiting_call` (I4; decision 7,
       A): cap left at 10; pipe `[delta("Closing it now. "), block_stop(),
       "pause"]`, hook for t9 never streamed. After the first sentence the
       hook is **not** done; `await stream.aclose()`, `settle()`; now it is
       done, allowed as today, with no `warn` line.
     - B5 `test_brains_do_not_wait_by_default` (I5; decision 10):
       `ClaudeBrain.speech_first is False` and `WarmBrain.speech_first is
       False`; B1's pipe on a default brain decides t1 at 1000, with no
       `wait` line.
     - B6 `test_a_non_streamed_call_is_read_after_its_text` (decision 4):
       pipe `[said_then_tool("Pausing the music now. ", "t1",
       "mcp__omarchy__media_control"), "pause", result()]`. t1 is decided at
       `>= 1001`.
     - B7 `test_two_calls_in_a_turn_do_not_deadlock` (decision 4, no
       deadlock): cap at 10; pipe `[delta("Pausing it, then muting. "),
       block_stop(), tool_start("t1", …), tool_start("t2", …), "pause",
       delta("Done. "), block_stop(), result()]`. The turn ends inside the
       5 s guard, both calls are decided at `>= 1001`, no `warn` line, and
       the consumer got both sentences.
   → verify by `nix develop -c python3 -m pytest -q
   tests/test_claude_backend.py -k SpeechFirst` on the unchanged `src/`:
   **all seven fail** (decided at 1000, no `wait`/`warn` line, hook already
   done before `aclose`, or `AttributeError` on `speech_first` /
   `SPEECH_FIRST_CAP_SECONDS`).
3. **`tests/test_local_engine.py`: two engine tests, failing on `main`.**
   One class, `SpeechFirstTests(SteppedRoomCase)` (`SteppedRoomCase`
   `:1168`), directly before `class FailureTests` (`:1592`).
   - The brain is the real one: `session = self.build(barge_in=…)`, then
     `session.brain = local_engine.brain_for(session.config,
     session.executor)`, with `claude_backend._new_client` patched to a small
     hook-running fake of the same shape as B's (defined in this file, not
     imported across test modules), `ClaudeBrain._options` patched to
     `SimpleNamespace(system_prompt="")` (`LocalBrain._options` appends to
     it), `_with_desktop` patched, and `await brain.start(warm_up=False)`.
   - When the hook allows, the fake SDK runs the call as the CLI would:
     notes `now`, then `await asyncio.to_thread(session.executor.call,
     "media_control", {"action": "pause"})` (dry run: `RUN` and `on_action`
     log `action`, nothing executes).
   - The mouth records `(text, start, end)` and steps `self.now` by 1 s.
   - Drive the real `_answer("pause the music")`.
   - E1 `test_her_line_plays_before_the_action_runs` (I2; decisions 3, 4,
     10): `barge_in=False`. The call starts at or after the play end of
     "Pausing the music now."; in the log, `say`, then `wait`, then
     `action`.
   - E2 `test_barge_in_keeps_the_overlap` (I5; decision 10):
     `brain_for(Config(barge_in=False), …).speech_first is True`; with
     `barge_in=True`, `session.brain.speech_first is False`, the call starts
     before that line's play end, and there is no `wait` line.
   → verify by `nix develop -c python3 -m pytest -q
   tests/test_local_engine.py -k SpeechFirst`: **both fail** on the
   unchanged `src/` (call during the line; `AttributeError`).
4. **`claude_backend.py`: the constant, the flag, the state** (decisions 6,
   10, A). `SPEECH_FIRST_CAP_SECONDS = 10.0` beside `DRY_RUN_READS`
   (`:110`); `speech_first = False` beside `builtin_tools` (`:279`);
   `self._reading = None` in `ClaudeBrain.__init__` (`:281-302`).
   → verify by B5's attribute asserts passing and `tests/test_claude_backend.py`
   otherwise unchanged.
5. **`claude_backend.py` `ask_stream` and `_turn`: record what was read**
   (decisions 4, 7, A).
   - `ask_stream` (`:881`): `turn = self._reading = (set(),
     asyncio.Condition())` as the first line of the `try`, before the
     `NO_SESSION` return. In the `finally` (`:903-911`): if `self._reading
     is turn`, set it to `None`; then `async with turn[1]:
     turn[1].notify_all()`.
   - A helper `_mark_read(tool_use_id)`: if `self._reading`, add the id and
     `notify_all` under the condition.
   - `_turn`: a new `elif event.get("type") == "content_block_start"` after
     the `content_block_stop` branch (`:939-947`) that calls `_mark_read`
     for a `tool_use` block with an id. In the `AssistantMessage` branch,
     after the sentence loop (`:956-958`), `_mark_read(block.id)` for each
     block with `type(block).__name__ == "ToolUseBlock"` (the file's
     dispatch-by-name convention, `:927`).
   → verify by `WarmBrainTests` and `SnapshotPerTurnTests` passing unchanged.
6. **`claude_backend.py` `_pre_tool_use` (`:501-503`): the wait** (decisions
   3, 5, 6, 8, 12, A, B). Inside the `try`, before `result = await
   self._decide(...)`: when `self.speech_first`, `tool_use_id`, `not
   _is_read(tool)` and `turn := self._reading`, and the id is not already in
   `turn[0]`: note `time.monotonic()`, then `async with turn[1]: await
   asyncio.wait_for(turn[1].wait_for(lambda: tool_use_id in turn[0] or
   self._reading is not turn), SPEECH_FIRST_CAP_SECONDS)`, and log the
   `wait` line through `self.executor.on_record`. `except TimeoutError`: log
   the `warn` line the same way and fall through. Then `_decide`, unchanged.
   The docstring gains two sentences: the wait, and that it is ordering
   only.
   → verify by B1-B7 passing, and by
   `diff <(git show 50dcf99:src/omarchy_voice/claude_backend.py | sed -n
   '/async def _decide/,/def _dry_run_refusal/p') <(sed -n '/async def
   _decide/,/def _dry_run_refusal/p' src/omarchy_voice/claude_backend.py)`
   printing nothing (decision 2).
7. **`local_engine.py` `brain_for` (`:197-203`) and `_say` (`:327-332`)**
   (decisions 10, 13). Build `brain = LocalBrain(config, executor)`, set
   `brain.speech_first = not config.barge_in`, return it. Add the docstring
   clause to `_say`.
   → verify by E1 and E2 passing, `PersonaTests` (`:2135`) and
   `HerVoiceGatesTheMicTests` passing unchanged.
8. **Mutation checks.** Apply each alone, run both `SpeechFirstTests`
   classes (and the named suite), revert with `git checkout -- src/`:

   | # | Mutation | Decision | Must fail |
   | --- | --- | --- | --- |
   | M1 | the predicate waits for an idle mouth instead of the id (at the hook's `t=0` nothing is queued yet, so: `lambda: True`) | 4 | B1, B2, B6, B7, E1 |
   | M2 | the non-streamed ids are recorded before the sentence loop | 4 | B6 |
   | M3 | drop the `_is_read` check, so reads wait | 5 | B2 |
   | M4 | remove the `wait_for` cap | 6 | B3 (by its 5 s guard) |
   | M5 | `ask_stream`'s `finally` does not notify, or the predicate drops `self._reading is not turn` | 7, A | B4 |
   | M6 | `ClaudeBrain.speech_first = True` | 10 | B5 |
   | M7 | the wait moves after `_decide` | 2, 3 | B1 (decided at 1000), E1 (`action` before `wait`) |
   | M8 | `_say` stops `join()`ing with `barge_in` off (after #137: drop the `BLOCK_END` join) | 11 | E1 |
   | M9 | `brain_for` sets `speech_first = True` whatever `barge_in` is | 10 | E2 |
   | M10 | the `wait` line is dropped, or written with `_note` | 8, B | B1 |
   | M11 | a `None` id waits | 12 | B2 |
   | M12 | the `warn` path returns a deny instead of falling through to `_decide` | 1, 6 | B3 |

   Decision 9 (cost) is measured by B1/E1's one stepped second. Decisions 13
   and 14 are checked by reading the diff.
   → verify by every mutation turning its tests red, and `git diff --stat`
   showing only the intended files after each revert.
9. **Full suites and the flake.** With
   `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`:
   `nix develop -c python3 -m pytest -q`,
   `nix develop -c python3 -m unittest discover -s tests`,
   `nix flake check --no-write-lock-file`
   → verify by all three passing, with the count equal to the step 0/1
   baseline plus 9 (1094 on `50dcf99`). `HookTests`, `GateTests`,
   `ReleaseTurnGateTests`, `DryRunTests`, `WarmBrainTests` and every engine
   test pass with no edits (I1).

## Tests

```
export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent
nix develop -c python3 -m pytest -q tests/test_claude_backend.py -k SpeechFirst   # 7 passed (all fail before step 4)
nix develop -c python3 -m pytest -q tests/test_local_engine.py -k SpeechFirst     # 2 passed (both fail before step 7)
nix develop -c python3 -m pytest -q tests/test_claude_backend.py -k "Hook or Gate or DryRun or WarmBrain"   # unchanged, all pass
nix develop -c python3 -m pytest -q                                               # baseline + 9, all pass
nix develop -c python3 -m unittest discover -s tests                              # same count, OK
nix flake check --no-write-lock-file                                              # passes
```

No test runs the real CLI, plays audio or reaches D-Bus. Time is the stepped
clock; real time is used only by the 5 s deadlock guards.

After merge, run by the owner: in real sessions, announced actions show a
`wait` line, and no `warn … waited 10s` line appears. A `warn` on every
action means the stream id and the hook id differ (spec risk "id
mismatch").

## Rollback

One commit on `fix/139-tools-while-speaking`. Before merge, drop the branch.
After merge, `git revert <sha>`: the hook decides at once again, and
`brain_for` stops setting the flag. No config, state file or migration. For
an emergency without a revert, `barge_in = true` turns the wait off (and
brings back the open mic that goes with it). The daemon picks the change up
on the next `omarchy-voice` rebuild or restart on p620 and razer.
