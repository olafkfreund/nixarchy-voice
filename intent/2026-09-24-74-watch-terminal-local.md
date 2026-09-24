---
status: approved
issue: 74
author: olafkfreund
---

# Intent: a watched command must be announced on the local engine too

Closes #74.

## Problem

Two tools promise the user an announcement later. `watch_terminal`
(`src/omarchy_voice/tools.py:3517-3520`):

```python
return Result(True,
              f"watching {pane['target']} ({pane['command']}). I will say when it "
              "finishes, even if the user has moved to another workspace. Do not "
              "wait here — say that it is being watched and carry on.")
```

and `run_in_terminal`, for any command still going after
`TERMINAL_QUICK_WAIT` (`tools.py:3489-3494`: "so I am watching it and will say
when it finishes"). The schema says the same to the model before it calls
anything: "the daemon watches in the background and interrupts with the
result" (`tools.py:914-919`).

Registering the watch is all either tool does. The announcement is someone
else's job: something has to call `Executor.poll_watches` (`tools.py:3522-3563`)
and say what it returns. The only caller in `src/` is the realtime engine's
`_watch_loop` (`realtime.py:580-601`), started at `realtime.py:1334`.
`local_engine.py` never mentions it — and `local` is the default engine
(`config.py:339`, dispatched at `cli.py:198-201`).

So on the default engine the model is told "I will say when it finishes",
tells the user so, and nothing is ever said. The watch also never leaves
`Executor._watches`: the three-hour give-up (`WATCH_MAX_SECONDS`) is checked
inside `poll_watches`, which is never called.

Demonstrated against the real `LocalSession` and `RealtimeSession`, with only
tmux faked (`tests/test_terminal.py`'s `FakeTmux`) and the mouth and the
websocket replaced by lists. The pane goes from `pytest` to `bash` straight
after the watch is registered, then each engine gets two poll intervals:

```
local    tool said: watching Work:1.2 (pytest). I will say when it finishes, even if the user has moved to another workspace. Do not wait here — say that it is being watched and carry on.
local    after 4.5s: watches=['Work:1.2'] spoken=[]
local    source mentions poll_watches: False
realtime after 4.5s: watches=[] events=['conversation.item.create', 'response.create']
realtime item role: system | first line: pytest finished in 2 seconds. It ran in tmux pane Work:1.2.
realtime muted: events=[] notify=[('Oma', 'pytest finished in 42 seconds.')]
```

### What the realtime engine does, for reference

- Polls every `WATCH_POLL_SECONDS` (2s) on a background task
  (`realtime.py:139-145`, `580-601`); a watcher exception is logged, never
  fatal.
- `_announce` (`realtime.py:603-640`) builds a one-line headline (finished in
  N seconds / pane was closed / still going after a long time).
- **Muted:** no speech, a desktop notification with the headline instead.
- **Listening:** waits for any reply in flight to end, keeps at least
  `WATCH_MIN_GAP_SECONDS` (8s) between interruptions, then appends a *system*
  item holding the headline and the pane's last 30 lines and asks for a
  response. The model says it in its own voice, told to judge from the output
  whether it worked and not to answer as though the user had spoken.

### What the local engine has to work with

- One mouth: the `_speech` queue drained by `_speech_loop`, and `_say`
  (`local_engine.py:192-219`), which waits for playback when `barge_in` is off
  — that wait is the microphone gate.
- One microphone and one loop over three states (`_listen_loop`,
  `local_engine.py:413-426`): listening (a turn is recording), muted with the
  wake word (a wake capture is recording), muted without it (nothing is).
  In the first two the recorder is open most of the time, so speaking at an
  arbitrary moment puts her voice into a capture.
- `_set_active(False)` drops any queued speech (`local_engine.py:467-478`,
  `221-228`), so "muted" means silent.
- The brain is a warm Claude Code session reached only through
  `ask_stream(text)`; it has no equivalent of a system item appended mid-
  conversation.
- It shares one `Executor` with the in-process MCP server the brain calls
  (#43), so a watch Claude Code starts via `mcp__omarchy__watch_terminal`
  lands in the same `_watches` this session would poll.

## Proposed outcome

- On the local engine, a watched command that finishes, vanishes or times out
  is announced once, as the tool promised.
- Listening: said out loud, without cutting across a reply, a recording in
  progress, or another announcement.
- Muted: not spoken; the user still finds out (as the realtime engine does
  with a notification).
- Wherever nothing will announce, the tools stop promising that something
  will.

## Affected users and systems

- `src/omarchy_voice/local_engine.py` — `LocalSession`, the default engine.
- `src/omarchy_voice/tools.py` — only if the promise's wording or the watch
  registry has to change; `poll_watches` itself is tested and correct
  (`tests/test_terminal.py`).
- `src/omarchy_voice/realtime.py` — unchanged, but it is the reference
  behaviour; anything shared should come out of it rather than be copied.
- Two other entry points make the same promise with no poller behind them:
  the standalone `omarchy-voice mcp` server (lists every tool from
  `tools_for`) and one-shot `omarchy-voice say`, whose process exits with the
  watch still registered.

## Constraints

- A background watcher must never take the session down (as
  `realtime.py:600-601`).
- Must not speak into an open microphone: the local engine's gate is the
  shape of its loop, and her own voice coming back as an instruction is a bug
  this project has already had.
- Must not speak while muted.
- Announced exactly once — `poll_watches` returns and forgets; nothing else
  may re-announce.
- Tests use fakes only (`FakeTmux`, the fake mouth and ears in
  `tests/test_local_engine.py`); nothing touches the live desktop.

## Open questions

1. **Who speaks it?** The realtime engine lets the model phrase it and judge
   success from the output. Here that means a turn through `ask_stream`: a
   Claude round trip, and an entry in the warm session's history that reads
   as if the user had said it. The alternative is `_say` of the canned
   headline — instant, no model, but no "looks like it worked". Which?
2. **When, if the microphone is open?** Queue until the current turn ends
   and the recorder is idle (and it is often idle only briefly while
   listening), or cut the capture short to announce?
3. **Muted with the wake word on:** notification only, like realtime, or
   also spoken since no instruction is being taken?
4. **Scope:** fix the local engine only, or also stop `watch_terminal` /
   `run_in_terminal` promising an announcement from the standalone MCP server
   and one-shot `say`, where there is no daemon to make it?
