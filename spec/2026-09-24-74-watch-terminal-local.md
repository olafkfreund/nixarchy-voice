---
status: approved
issue: 74
intent: intent/2026-09-24-74-watch-terminal-local.md
---

# Spec: a watched command must be announced on the local engine too

## The intent's open questions, answered

The intent was approved without answers to its four questions. Each is
decided below with the reasoning. Any of them can be rejected at this gate.

### Q1: who speaks it? The model, in an ordinary turn given the realtime engine's message.

The announcement is a normal `_answer` turn. Its text is the message the
realtime engine already sends (`realtime.py:633-641`): the headline, the pane,
the last 30 lines, and "They did not just speak to you". That text is taken
out of `realtime.py` so both engines use it. It is not copied.

Why the model and not a canned `_say`:

- **Same behaviour as the reference engine.** The user hears "pytest
  finished and three tests failed", not only "pytest finished in 42 seconds".
  The judgement comes from the output tail. A canned headline can't give it.
- **The warm session learns the job finished.** "What failed?" straight
  afterwards is answered from what it was just shown. With a canned line the
  brain never heard about the job.
- **`_answer` already does everything else this needs.** It takes the turn
  lock, so it does not cut across a reply. It speaks through the one mouth,
  with the microphone gate and the echo tail. It announces a hold if the model
  makes one, and it recovers from a failed turn. No new speaking path is
  needed.
- **The brain already knows how to phrase this.** The shared persona says
  "When you announce a finished job, the user did not just speak to you"
  (`persona.py:126`).

The cost is a Claude round trip. That is about 2.6 s to the first sentence
for a turn that answers without a tool (`local_engine.py:61-63`). This is an
unprompted announcement that is already seconds late by design (realtime waits
`WATCH_MIN_GAP_SECONDS`), so the round trip is acceptable.

The intent's worry was that the history entry "reads as if the user had said
it". The message already tells the model otherwise. #78 is a problem here: it
puts every turn under a "# What the user said" heading (`_with_desktop`). That
overlap is dealt with under Design.

### Q2: when, if the microphone is open? At the listen loop's boundary, between captures. Never by cutting one.

Demonstrated on main: nothing stops a background turn from speaking into an
open capture. `_turn_lock` is held only inside `_answer`, not while `_record`
is capturing:

```
Q2. a background announcement while listening, main as it is
  capture open:         True
  turn lock held:       False
  spoken (text, mic):   [('Your tests finished.', True)]
```

(Real `LocalSession._turn` and `_answer`, `record_utterance` replaced by a
quiet room that blocks for 1 s, and the mouth records whether the microphone
was open when it spoke.)

So the watcher does not speak. It puts the finished job in a list, and
`_listen_loop` (`local_engine.py:413-426`) announces from that list before it
opens the next capture. At that point the microphone is shut by construction,
the same way it is shut during a reply.

The worst-case delay while listening is one capture. A silent room returns
after `MAX_UTTERANCE_SECONDS` (15 s, `listen_local.py:36`). If the user is
talking, the delay is the rest of their turn.

Cutting a capture short was rejected. If the user is mid-sentence, it loses
their instruction to deliver ours. Ending only a capture that has *heard
nothing yet* would cap the delay near zero. It would also mean tracking speech
inside the level callback. That is the upgrade path if 15 s turns out to
matter, and it is marked in the code with a `ponytail:` note.

### Q3: muted with the wake word on? A notification only, the same as realtime.

This is already decided by the approved constraint "Must not speak while
muted". The engine agrees: `_set_active(False)` drops queued speech
(`local_engine.py:474`, `:221-228`). There is also a practical reason. While
muted with the wake word on, a wake capture is open nearly all the time
(`wake_max_seconds` = 4 s cycles). Anything spoken would go into it, and a
model-phrased sentence could contain the wake word. The realtime engine
notifies with the headline (`realtime.py:615-617`), and this does the same.

A job queued while listening that is still waiting when the user mutes is
also turned into a notification. It is not dropped, and it is not spoken.

### Q4: scope? The local engine, and the tools stop promising where nothing polls.

This is the approved outcome ("Wherever nothing will announce, the tools stop
promising that something will"), so it is in scope. Demonstrated on main for
the standalone `omarchy-voice mcp` server (real `build_server`, tmux faked
with `tests/test_terminal.py`'s `FakeTmux`):

```
Q4. `omarchy-voice mcp`
  tool said:            watching Work:1.2 (pytest). I will say when it finishes, even if the user has moved to another workspace. Do not wait here — say that it is being watched and carry on.
  watches afterwards:   ['Work:1.2']
```

The only caller of `poll_watches` in `src/` is `realtime.py:595`. One-shot
`say` builds its own `Executor` (`cli.py:118`) and exits, so the watch is
never read either.

The fix follows the pattern `confirm_instruction` already uses
(`tools.py:1575`, overridden by `mcp_server.py:120`). Whoever owns the
`Executor` says whether anything will announce, and the tools read that.

## Design

### 1. The announcement text comes out of `realtime.py`, unchanged

Two module-level functions are added, built from `_announce`
(`realtime.py:603-643`):

- `watch_headline(job) -> str`: the three cases at `:605-610`.
- `watch_message(job) -> str`: the system text at `:633-641`.

`RealtimeSession._announce` calls both. Its behaviour does not change, and
`tests/test_realtime.py:610-702` proves that. `local_engine.py` already
imports from `realtime` (`ECHO_TAIL_SECONDS`, `_run_until_done`, `:42`) and
imports these two plus `WATCH_POLL_SECONDS` the same way.

### 2. `Executor.announces_watches` (`tools.py`)

- `Executor.__init__`: `self.announces_watches = False`, next to
  `confirm_instruction` (`:1575`).
- `RealtimeSession.__init__` (`realtime.py:397`) and `LocalSession.__init__`
  (`local_engine.py:134`) set it to `True` on the `Executor` they build. The
  local brain's in-process MCP server shares that `Executor` (#43), so a
  `mcp__omarchy__watch_terminal` from Claude Code sees it.
- `_tool_watch_terminal` (`:3505-3520`): when `announces_watches` is false,
  the tool refuses without registering. The result reads: "`<target>` is
  running `<command>`. Nothing in this process will say when it finishes, so
  do not promise that. Read it later with read_terminal." There is no
  registration, so nothing is left in `_watches` to leak.
- `_tool_run_in_terminal`'s long path (`:3489-3494`): when false, it does not
  call `watch()` and says "still running in `<target>` after Ns. Nothing here
  will say when it finishes; tell the user, and read it later with
  read_terminal." It is still `ok=True`, because the command did start.
- The `watch_terminal` schema (`:916-919`) says "the voice daemon watches
  in the background … Anywhere else it says it cannot." The schema is static
  (`tools_for(config)` never sees the `Executor`), so it describes both cases
  rather than being filtered.
- `persona.py:122-127` and `capabilities.py:811-815` are unchanged. They are
  true on both daemons, and elsewhere the tool result corrects the model at
  the moment it matters.

### 3. `LocalSession` polls, queues, and announces between captures (`local_engine.py`)

- `__init__`: `self._announcements: list[dict] = []`.
- `_watch_loop()`: the same shape as `realtime.py:580-601`. Sleep
  `WATCH_POLL_SECONDS`, then `await asyncio.to_thread(self.executor.poll_watches)`.
  For each job, log `watch   <target>: <headline>`. If `self.active`, append
  the job to `_announcements`. If not, call
  `self.feedback.notify("Oma", headline)`. Any exception except cancellation
  is logged as `warn    watcher: …` and the loop carries on.
  `poll_watches` returns a job and forgets it, and the job is then either
  queued or notified. Nothing re-reads it, so each job is announced once.
- `_listen_loop` (`:420-426`): in the `if self.active:` branch, **before**
  `await self._turn()`, announce every queued job with
  `await self._answer(watch_message(job))`, popping each one first. Between
  announcements, re-check `self.active`: a mute that lands during an
  announcement stops the rest.
- `_set_active(False)` (`:467-478`): any jobs still queued become
  notifications (headline only) and the list is cleared. Being muted stays
  silent.
- `run()` (`:523-580`): start `_watch_loop` next to `_speech_loop` (`:542`)
  and cancel it in the `finally` next to `speech.cancel()` (`:573`).

### Not changed

`Executor.poll_watches` and `watch()` (tested in `tests/test_terminal.py`),
`WATCH_MAX_SECONDS`, the realtime engine's behaviour, `mcp_server.py` (its
`Executor` keeps the `False` default), and `cli.py`.

### Overlap with open PRs and #76: the plan must sequence after all three

- **#81 `feat/72-listen-faster`** rewrites `_turn`, `_wake_turn` and `run()`
  (a whisper-server started beside the brain and stopped in `finally`). It
  also changes `_answer` to `_answer(text, trace=None)`. This spec adds a task
  to `run()`'s start and `finally`, and a pre-turn step to `_listen_loop`,
  which #81 leaves alone. The plan rebases onto #81. `_answer(message)` needs
  no trace: the announcement's timing is not a user's turn.
- **#78 `fix/69-snapshot-per-turn`** wraps every warm turn in `_with_desktop`
  (`WarmBrain._turn`), which heads the text "# What the user said". An
  announcement under that heading contradicts its own "They did not just
  speak to you". The plan must rebase onto #78 and give `_with_desktop` a way
  to head a turn that did not come from the user. The smallest version is a
  keyword that replaces the heading, reached through `ask_stream`, and
  therefore `_answer`, as a keyword. The plan decides the exact name. It is
  not left as is.
- **#76 `fix/76-confirm-replays-once`** (spec only, not yet a PR) deletes
  `_last_text`. **Until it lands, `_answer(watch_message(job))` would set
  `_last_text` to the announcement, and a keybind confirm of a Claude Code
  hold would replay it to the brain.** The #74 plan must land after #76's
  implementation. If #74 has to go first for some reason, it must not go
  through `_last_text`.

## Alternatives rejected

- **Canned `_say(headline)`**: see Q1. It gives no judgement from the output,
  and the brain never learns about the job.
- **Speak from the watcher task as soon as a job finishes**: shown in Q2 to
  land in an open capture.
- **Cut the capture to announce**: loses a user's sentence in progress (Q2).
- **Speak while muted with the wake word on**: this breaks an approved
  constraint (Q3).
- **Run a poller in `omarchy-voice mcp` / `say` instead of refusing**: `say`
  exits immediately. An MCP server has nowhere to speak and cannot interrupt
  its client (there is no server-initiated message the client is obliged to
  act on). Only the daemons can keep the promise.
- **Filter `watch_terminal` out of the tool list where nothing polls**:
  `tools_for(config)` does not know the `Executor`, and the planner, MCP and
  Claude paths each list tools differently. A refusal in the one tool body
  covers all of them.

## Risks

- **Pane output reaches the model unprompted.** The tail can contain text
  written to steer it. The realtime engine already accepts this. Here the
  same PreToolUse gate applies to anything the model then tries, and the
  message tells it to ask before carrying on.
- **Delay up to one capture (15 s) while listening.** This is intended. The
  upgrade path is noted in Q2.
- **A dead Claude session.** The user hears `NO_SESSION` ("Claude Code isn't
  running.") instead of the headline. The headline is still in the log. This
  is the same failure every other turn has.
- **An announcement turn can make a hold** if the model reaches for a gated
  tool. It is spoken and settled like any hold (`local_engine.py:392-398`).
  After #76, releasing it is exact.
- **Existing tests that call `watch_terminal` on a bare `FakeTmux`**
  (`tests/test_terminal.py:213-298`) now get the refusal. They set
  `announces_watches = True`, which is what a daemon does.
- **Hosts:** every host on the default local engine gains a 2 s tmux poll.
  It is one `tmux list-panes`, and only while a watch exists: `poll_watches`
  returns early at `tools.py:3536`.

## Verification

Unit tests with fakes only: `FakeTmux`, and the fake brain, mouth and ears in
`tests/test_local_engine.py`. Nothing touches tmux, the microphone or the
desktop.

- `tests/test_local_engine.py`: **regression, the intent's demonstration.**
  Register a watch on a `FakeTmux`-backed executor, flip the pane from
  `pytest` to `bash`, and run the watcher with `WATCH_POLL_SECONDS` patched
  small. Then:
  - listening: the brain is asked once with a message containing
    "pytest finished" and the tail, the reply is spoken, and `_watches` is
    empty;
  - a second poll announces nothing (exactly once);
  - muted, with or without the wake word: `notify` gets the headline, nothing
    is spoken, and the brain is not asked;
  - queued, then muted before the loop reaches it: one notification, no
    speech;
  - **the Q2 demonstration, reversed**: a job that finishes while a capture is
    open is spoken only after the capture returns, and the mouth records
    `mic_open == False`;
  - a watcher that raises is logged, and the next poll still runs.
- `tests/test_terminal.py`: `watch_terminal` and `run_in_terminal`'s long path
  with `announces_watches` false refuse or say so and register nothing. With
  it true they behave as today.
- `tests/test_mcp.py`: `watch_terminal` through `build_server` with a default
  `Executor` gives the refusal (Q4's demonstration, as a test).
- `tests/test_realtime.py:610-702` passes unchanged against the extracted
  `watch_headline` / `watch_message`.
- `python -m pytest tests` passes.
- `nix flake check --no-write-lock-file` passes.
