---
status: approved
issue: 74
spec: spec/2026-09-24-74-watch-terminal-local.md
---

# Plan: a watched command must be announced on the local engine too

## Approved decisions, carried over from the spec

1. **The model speaks the announcement, in an ordinary `_answer` turn (Q1).**
   Its text is the realtime engine's system message, unchanged: headline,
   pane, the last 30 lines, and "They did not just speak to you". It is not a
   canned `_say`. The round trip (about 2.6 s to the first sentence) is
   accepted.
2. **The text is moved out of `realtime.py`, not copied.** Two module
   functions, `watch_headline(job)` (the three cases: closed, still going,
   finished in N seconds) and `watch_message(job)` (the system text).
   `RealtimeSession._announce` calls both and behaves exactly as before.
3. **Announced at the listen loop's boundary, never into an open capture
   (Q2).** The watcher never speaks. It queues the job, and `_listen_loop`
   announces queued jobs before it opens the next capture. The worst-case
   delay while listening is one capture (15 s in a silent room). Cutting a
   capture short is rejected. A `ponytail:` note names the upgrade (end a
   capture that has heard nothing yet) in case 15 s matters.
4. **Muted, with or without the wake word: a notification only (Q3).**
   `feedback.notify("Oma", headline)`, the same as realtime. A job queued
   while listening that is still waiting when the user mutes becomes a
   notification. It is neither dropped nor spoken.
5. **The tools stop promising where nothing polls (Q4).**
   `Executor.announces_watches = False` by default, beside
   `confirm_instruction`. `RealtimeSession` and `LocalSession` set it True on
   the `Executor` they build. The local brain's in-process MCP server shares
   that `Executor` (#43), so Claude Code's `mcp__omarchy__watch_terminal`
   sees it.
6. **`watch_terminal` with `announces_watches` false** refuses without
   registering: "`<target>` is running `<command>`. Nothing in this process
   will say when it finishes, so do not promise that. Read it later with
   read_terminal." Nothing is left in `_watches`.
7. **`run_in_terminal`'s long path with it false** does not call `watch()`,
   says "still running in `<target>` after Ns. Nothing here will say when it
   finishes; tell the user, and read it later with read_terminal.", and stays
   `ok=True` (the command did start).
8. **The `watch_terminal` schema describes both cases** ("the voice daemon
   watches in the background … Anywhere else it says it cannot"). It is not
   filtered, because `tools_for(config)` never sees the `Executor`.
   `persona.py` and `capabilities.py` are unchanged.
9. **`LocalSession`:** `_announcements: list[dict]`; a `_watch_loop` the same
   shape as realtime's (sleep `WATCH_POLL_SECONDS`, `poll_watches` in a
   thread, log `watch   <target>: <headline>`, queue if active, else notify;
   log any exception as `warn    watcher: …` and carry on). `_listen_loop`
   announces queued jobs before `_turn`, popping each first and re-checking
   `active` between them. `_set_active(False)` turns the queue into
   notifications and clears it. `run()` starts the watcher beside
   `_speech_loop` and cancels it in the same `finally`. Each job is announced
   once, because `poll_watches` returns and forgets.
10. **Not changed:** `Executor.poll_watches`, `watch()`,
    `WATCH_MAX_SECONDS`, the realtime engine's behaviour, `mcp_server.py`
    (its `Executor` keeps the False default) and `cli.py`.
11. **#78's "# What the user said" heading must not head an announcement.**
    `_with_desktop` gets a keyword that replaces the heading, reached through
    `ask_stream` and `_answer` as a keyword. The plan picks the name (below).
12. **Sequencing.** This lands after #81 (`feat/72-listen-faster`), #78
    (`fix/69-snapshot-per-turn`) **and #76's implementation**
    (`fix/76-confirm-replays-once`). Before #76, `_answer(watch_message(job))`
    would set `_last_text` to the announcement, and a keybind confirm would
    replay it. `_answer(message)` takes no trace.
13. **Verification** is unit tests with fakes (`FakeTmux`, the fake brain,
    mouth and ears). Nothing touches tmux, the microphone or the desktop.

Three details the spec left open, decided here. Each is flagged in the PR:

- **The keyword is `from_user: bool = True`.** `_with_desktop(text,
  from_user=True)`: when False, the `# What the user said` heading is left
  out and the text follows the snapshot directly. `watch_message` already
  opens with its own `# A watched command finished` heading, so that heading
  is the replacement, and `watch_message` stays byte-identical to what
  realtime sends. It is threaded as `ask_stream(text, *, release=False,
  from_user=True)` → `_turn(text, *, from_user=True)`, and
  `_answer(text, trace=None, *, release=None, from_user=True)`. A string
  `heading=` was rejected: there is exactly one other heading, and it is
  already inside the message.
- **`_answer` does not write a `heard` line for an announcement.** The
  watcher has just logged `watch   <target>: <headline>`, and after #76 the
  log does not show utterances nobody said.
- **After announcing, `_listen_loop` goes round again (`continue`)** instead
  of falling into `_turn`, so a mute or stop during an announcement is seen
  before a capture opens.

## Code as it will be after #81, #78 and #76

#81 changes `local_engine.py`, #78 changes `claude_backend.py` and
`capabilities.py`. Neither touches `realtime.py`, `tools.py`,
`mcp_server.py` or `persona.py`. #76 is not implemented yet; its approved
plan (`fix/76-confirm-replays-once:plan/2026-09-24-76-confirm-replays-once.md`)
changes `_answer` to `_answer(self, text, trace=None, *, release: str | None =
None)`, `ask_stream` to `ask_stream(self, text, *, release=False)`, deletes
`_last_text`, and adds a `ScriptedBrain(WarmBrain)` with a `_turn(self,
text)` override to `tests/test_local_engine.py`. Line numbers below were
checked on the branch named; step 1 re-checks them after #76 lands.

- `main:src/omarchy_voice/realtime.py`: imports :38-43,
  `WATCH_POLL_SECONDS` :142, `WATCH_MIN_GAP_SECONDS` :145,
  `RealtimeSession.__init__` builds its `Executor` :397-398, `_watch_loop`
  :580-601, `_announce` :603-643 (headline :605-610, notify when muted
  :615-617, message text :633-641), watcher started :1334.
- `main:src/omarchy_voice/tools.py`: `watch_terminal` schema :913-919,
  `confirm_instruction` :1575-1577, `_tool_run_in_terminal` :3447 (long path
  :3489-3494), `watch()` :3497, `_tool_watch_terminal` :3505-3520 (idle
  refusal :3509-3513, registration and promise :3516-3520), `poll_watches`
  :3522 (early return :3536-3537).
- `main:src/omarchy_voice/mcp_server.py`: `build_server` makes or takes the
  `Executor` :116 and overrides `confirm_instruction` :120-124.
- `feat/72-listen-faster:src/omarchy_voice/local_engine.py`: realtime
  import :42, `LocalSession.__init__` builds its `Executor` :134-135,
  `_last_text` :153-154 (gone after #76), `_turn` :260-286, `_answer` :376
  (`heard` log :386, `ask_stream` :396), `_listen_loop` :450-463,
  `_set_active` :504-515 (`_drop_queued_speech` :511), `run()` :560-627
  (`speech` task :579, `speech.cancel()` :616).
- `fix/69-snapshot-per-turn:src/omarchy_voice/claude_backend.py`:
  `_with_desktop` :558-570 (heading :570), `WarmBrain.ask_stream` :715-737
  (`self._turn(text)` :728), `WarmBrain._turn` :739-742.
- `fix/69-snapshot-per-turn:tests/test_claude_backend.py`: `WarmBrainTests`
  patches `_with_desktop` with `side_effect=lambda text: text` :642-643;
  snapshot tests :867-883.
- `main:tests/test_terminal.py`: `FakeTmux` :34-56, `WatchingTests`
  :213-298 (`watch_terminal` called at :222, :228, :256).
- `main:tests/test_realtime.py`: `AnnounceTests` :610-702.
- `main:tests/test_mcp.py`: `ConfirmWordingTests` :56.
- Unchanged, for reference: `main:src/omarchy_voice/persona.py:122-127`;
  `capabilities.py` "call watch_terminal rather than waiting" at
  `main:…:813`, `fix/69-snapshot-per-turn:…:825`.

## Steps

0. **Baseline.** On this branch as it stands, `nix develop -c pytest tests
   -q` → record the pass count. It is re-recorded in step 1, after the
   rebase, and that second number is the one step 9 compares against.

1. **Precondition: #81, #78 and #76 are merged.** `gh pr view 81 --json
   state` and `gh pr view 78 --json state` say `MERGED`, and #76's PR
   (`gh pr list --state merged --head fix/76-confirm-replays-once`) is
   merged. **If any of the three is not merged, stop and report. Do not
   implement against unmerged code, and do not land this before #76.** Then
   `git fetch origin && git rebase origin/main` (this branch carries only the
   intent, spec and plan), and re-run `nix develop -c pytest tests -q` →
   record the new baseline. Check the post-#76 shapes:
   `grep -n '_last_text' src/omarchy_voice/local_engine.py` prints nothing;
   `grep -n 'def _answer\|def _listen_loop\|def _set_active\|_speech_loop()' src/omarchy_voice/local_engine.py`
   and `grep -n 'def ask_stream\|def _turn\|def _with_desktop' src/omarchy_voice/claude_backend.py`
   give the lines this plan edits. If they moved, use the new numbers; if the
   code they describe changed, stop and report.

2. **Regression tests first, and they must fail on main.**
   - `tests/test_local_engine.py`,
     **`test_a_finished_watch_is_announced`** (the intent's demonstration:
     the watch is never announced). Drive the real `LocalSession.run()` with
     `local_engine.brain_for` returning a `FakeBrain(["pytest failed."])`,
     `ControlServer` replaced by a stub with `start`/`stop`, and
     `listen_local.Server.start` returning None. The session is active; the
     ears return `b""` after a short sleep (a quiet room, so the loop turns
     over). Register `session.executor.watch("Work:1.2", "pytest",
     seen_busy=True)`, with the executor's `_tmux_panes` returning that pane
     idle and `_capture_pane` returning `Result(True, "3 failed")`. Patch
     `local_engine.WATCH_POLL_SECONDS` to 0.01 with `create=True`, so the
     patch itself works on main. Within 2 s expect `brain.asked` to be one
     message containing "pytest finished" and "3 failed", "pytest failed."
     spoken, and `executor._watches == {}`. Then set `_stop`. On main the
     brain is never asked: the assertion fails after the 2 s wait.
   - `tests/test_terminal.py`,
     **`test_watch_terminal_promises_nothing_when_nothing_announces`**:
     `FakeTmux(announces=False)` (a kwarg added in this step, default True,
     setting `announces_watches`; on main it sets an attribute nothing
     reads). `ex.call("watch_terminal", {"target": "Work:1.2"})` is not ok,
     says "Nothing in this process will say", does not say "I will say", and
     `ex._watches == {}`. On main it is ok, promises, and registers.
   - `tests/test_mcp.py`,
     **`test_watch_terminal_over_mcp_does_not_promise`** (Q4's
     demonstration): `build_server(Config(), executor)` with a default
     `Executor` whose `_resolve_pane` returns a busy `Work:1.2` running
     `pytest`. Calling `watch_terminal` over the in-memory client returns
     text without "I will say when it finishes", and nothing is registered.
     On main it promises and registers.
   → verify: `nix develop -c pytest tests/test_local_engine.py
   tests/test_terminal.py tests/test_mcp.py -q -k
   "finished_watch_is_announced or promises_nothing or over_mcp_does_not_promise"`
   → **3 failed, 0 passed** on the rebased main, each on its assertion.
   Paste the failure lines into the PR.

3. **`src/omarchy_voice/realtime.py`: extract the text.**
   - Module functions `watch_headline(job) -> str` (from :605-610) and
     `watch_message(job) -> str` (from :628 and :633-641, with the headline
     and the stripped tail inside), placed after `WATCH_MIN_GAP_SECONDS`
     (:145).
   - `_announce` (:603) calls `headline = watch_headline(job)` and sends
     `watch_message(job)` as the `input_text`. Nothing else in it changes.
   → verify: `nix develop -c pytest tests/test_realtime.py -q` passes with
   `AnnounceTests` (:610-702) unchanged.

4. **`src/omarchy_voice/tools.py`: `announces_watches`.**
   - `Executor.__init__`: `self.announces_watches = False` after
     `confirm_instruction` (:1575-1577), with a comment: only a daemon that
     polls `poll_watches` sets it, and a tool must not promise what its
     process cannot keep.
   - `_tool_watch_terminal` (:3505): after the idle refusal (:3509-3513),
     if not `self.announces_watches`, return decision 6's refusal
     (`Result(False, …)`), before `self.watch(...)` at :3516.
   - `_tool_run_in_terminal` long path (:3489-3494): when false, skip
     `self.watch(...)` and return decision 7's text, `ok=True`.
   - Schema (:915-919): decision 8's wording.
   → verify by steps 2 and 8.

5. **Set it where a daemon owns the `Executor`.**
   - `realtime.py:397-398`: `self.executor.announces_watches = True` right
     after the `Executor` is built.
   - `local_engine.py:134-135`: the same.
   → verify: a test in step 8 checks both constructors.

6. **`src/omarchy_voice/claude_backend.py`: a turn that is not the user's.**
   - `_with_desktop(text, from_user=True)` (:558): when False, drop
     `# What the user said\n\n` and put `text` straight after the snapshot.
     One sentence in the docstring on why.
   - `WarmBrain.ask_stream(self, text, *, release=False, from_user=True)`
     (:715, with #76's `release`) passes it to `self._turn(text,
     from_user=from_user)` (:728).
   - `WarmBrain._turn(self, text, *, from_user=True)` (:739):
     `asyncio.to_thread(_with_desktop, text, from_user)` (:742).
   - `ClaudeBrain.think` and `_ask` are not changed: the cold path never
     announces.
   → verify by step 8.

7. **`src/omarchy_voice/local_engine.py`: poll, queue, announce between
   captures.**
   - Import `WATCH_POLL_SECONDS, watch_headline, watch_message` from
     `.realtime` beside `ECHO_TAIL_SECONDS` (:42).
   - `__init__`: `self._announcements: list[dict] = []`.
   - `_watch_loop()`: decision 9. It checks `self.active` when a job
     arrives, not when it is spoken.
   - `_listen_loop` (:450-463), in `if self.active:` before `_turn()`: if
     `self._announcements`, pop the first job, `await
     self._answer(watch_message(job), from_user=False)`, and `continue`.
     A `ponytail:` comment: the worst case is one capture (15 s); the upgrade
     is ending a capture that has heard nothing yet.
   - `_answer(self, text, trace=None, *, release=None, from_user=True)`: no
     `heard` line when `from_user` is False; pass `from_user` to
     `ask_stream`.
   - `_set_active` (:504-515), in the `else` beside `_drop_queued_speech()`:
     notify each queued job's headline, then clear the list.
   - `run()`: `watcher = asyncio.create_task(self._watch_loop())` beside
     `speech` (:579); `watcher.cancel()` beside `speech.cancel()` (:616).
   - `FakeBrain.ask_stream` in `tests/test_local_engine.py` takes
     `from_user=True` and records it in `self.from_user`. #76's
     `ScriptedBrain._turn` takes `*, from_user=True` (it overrides the
     method whose signature changed in step 6).
   → verify by step 8.

8. **Tests** (beside step 2's regressions).
   - `tests/test_local_engine.py`, a new `WatchAnnounceTests(EngineTestCase)`
     driving `_watch_loop` and `_listen_loop` as tasks, with
     `WATCH_POLL_SECONDS` at 0.01 and the executor's `_tmux_panes` and
     `_capture_pane` faked as in step 2:
     - `test_listening_announces_once`: one ask, with the headline and tail;
       `brain.from_user == [False]`; a further poll announces nothing.
     - `test_muted_notifies_and_says_nothing`, and the same with
       `wake_word="oma"`: `notify` gets `("Oma", "pytest finished in N
       seconds.")`, nothing is spoken, `brain.asked == []`.
     - `test_queued_then_muted_becomes_a_notification`: queue a job, call
       `_set_active(False)` before the loop reaches it: one notification, no
       speech, `_announcements == []`.
     - **`test_a_job_finishing_mid_capture_waits_for_the_capture`** (the
       spec's Q2 demonstration, reversed): the ears block on an event while
       the job finishes; `brain.asked == []` and nothing spoken while the
       capture is open; after release, the announcement is spoken and the
       mouth records `session.feedback.mic_open == False` for it.
     - `test_a_watcher_that_raises_keeps_polling`: `poll_watches` raises
       once, then returns a job; the log has `warn    watcher:` and the job
       is still announced.
     - `test_the_daemons_announce_watches`: `LocalSession(Config())` and
       `RealtimeSession(Config())` both have
       `executor.announces_watches is True`; a bare `Executor` has False.
   - `tests/test_claude_backend.py`: `_with_desktop("x", from_user=False)`
     (with `capabilities.live_state` patched) has the snapshot heading and no
     `What the user said`; the default still has it. Through `ask_stream(…,
     from_user=False)`, the `FakeClient` receives the heading-less text.
     `WarmBrainTests`' patch at :642-643 becomes
     `side_effect=lambda text, *a, **k: text`.
   - `tests/test_terminal.py`: `FakeTmux(announces=True)` by default keeps
     `WatchingTests` (:213-298) passing unchanged. New:
     `run_in_terminal` with `tools.TERMINAL_QUICK_WAIT` patched to 0 on a
     `FakeTmux(announces=False)` is ok, says "Nothing here will say",
     registers nothing; with `announces=True` it registers and promises, as
     today. An idle pane still gets the "already idle" refusal with
     `announces=False` (the idle check comes first).
   - `tests/test_realtime.py` `AnnounceTests` unchanged; plus
     `watch_message(job)` contains "They did not just speak to you" and the
     tail, and `watch_headline` gives the three cases.
   - **Mutation checks**, each then restored:
     (a) in `_watch_loop`, `await self._answer(...)` directly instead of
     queueing → `test_a_job_finishing_mid_capture_waits_for_the_capture`
     fails;
     (b) drop the `if self.active` check in `_watch_loop` →
     `test_muted_notifies_and_says_nothing` fails;
     (c) drop the `announces_watches` refusal in `_tool_watch_terminal` →
     `test_watch_terminal_promises_nothing_when_nothing_announces` and the
     MCP test fail;
     (d) pass `from_user=True` from `_listen_loop` →
     `test_listening_announces_once` fails on `from_user`.
   → verify: `nix develop -c pytest tests -q` equals the step-1 baseline
   plus the new tests, no new failures, and step 2's three tests pass.

9. **Whole check.** `nix flake check --no-write-lock-file` → it passes.

10. **Live check, read-only and optional.** Only if a tmux pane is busy with
    something long already: `nix run .#omarchy-voice -- -n say --no-confirm
    "watch the busy terminal and tell me when it is done"`. Expected: the
    reply does not promise to announce, and says to check back (the one-shot
    `say` executor has `announces_watches` False). It reads tmux and
    registers nothing. The daemon path is not exercised live: that needs the
    service restarted on this branch, which this plan does not do.

11. **PR.** Push and open a PR that closes #74 and links the intent, spec and
    plan. The description lists the three decisions above that the spec left
    open, pastes step 2's failing lines from main, and states that it was
    rebased on #76.

## Tests

```
nix develop -c pytest tests -q                                   # baseline + new, 0 new failures
nix develop -c pytest tests/test_local_engine.py tests/test_terminal.py tests/test_mcp.py -q \
  -k "finished_watch_is_announced or promises_nothing or over_mcp_does_not_promise"   # 3 fail on main, pass after
nix develop -c pytest tests/test_local_engine.py tests/test_terminal.py tests/test_mcp.py \
  tests/test_realtime.py tests/test_claude_backend.py -q
nix flake check --no-write-lock-file                             # CI parity
```

Plus the mutation checks in step 8 and the optional check in step 10.

## Rollback

It is a single squash-merged PR, with no Nix, config or schema change and no
persisted state (`_watches` and `_announcements` live in memory).
`git revert <merge>` brings back the silent local engine and the
`watch_terminal` promise everywhere; realtime is unaffected either way,
because the extracted text is byte-identical. There is no switch to turn
announcements off without reverting. None is needed: a watch only exists when
the model calls `watch_terminal`, or when a `run_in_terminal` outlives its
quick wait.
