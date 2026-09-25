---
status: draft
issue: 139
author: olafkfreund
---

# Intent: a tool call must not act while she is still saying the line before it

Closes #139.

## Problem

With `barge_in` off, which is this machine's setting (`config.py:345`), the
code reads as if nothing acts while she talks. `_say` waits for each
sentence to finish playing (`local_engine.py:326-346`, `await
self._speech.join()`). While it waits, `_answer` does not pull the next
sentence from `ask_stream` (`local_engine.py:584-596`). The `_say` docstring
says so: "nothing else in this session runs while she is talking".

The SDK does not work that way. Checked in the installed
`claude_agent_sdk` 0.2.152, in `_internal/query.py`:

| Line | What it does |
| --- | --- |
| `:287` | `start()` runs `_read_messages()` as a detached task (`spawn_detached`). It reads the CLI's stdout whoever is consuming the stream. |
| `:332-337` | Every `control_request` is sent to `_spawn_control_request_handler`, and the reader goes on (`continue`). |
| `:297-300` | That handler starts one task per request (`self.spawn_task(self._handle_control_request(request))`). |
| `:532-536` | `hook_callback`: our `PreToolUse` hook, `_pre_tool_use` → `_decide` (`claude_backend.py:481-503`, registered at `:589`). |
| `:548-560` | `mcp_message`: calls to our own `mcp__omarchy__*` tools. So `Executor.call` runs on a handler task too, not only the decision. |
| `:170-172`, `:399` | Ordinary messages go into a buffer of 100. The reader blocks only when that buffer is full. |

A note on #137's spec: it names `can_use_tool` as the path. The path is the
`PreToolUse` hook. `can_use_tool` is only `_alarm`, the tripwire
(`claude_backend.py:593`). The effect is the same, because both are control
requests that the reader starts on a task of their own.

So here is what happens while `_answer` is blocked in `_say`. The CLI sends
the hook request for the next tool call. `_decide` answers it at once, and
the tool runs, all while she is still talking. The persona makes this the
common case rather than a race. "Before ANY tool call, say one short line
about what you are about to do, then make the call" (`LOCAL_PERSONA`,
`local_engine.py:143`). And `ask_stream` flushes that line at
`content_block_stop` (`claude_backend.py:939-947`), which is right before
the `tool_use`. So the line starts playing and the call is decided within
milliseconds. The line is said *before* the call only in the sense that its
playback started first.

What the user hears:

- "Opening a terminal now." The terminal is already open halfway through
  the sentence. That is harmless.
- "I'll close that window." The window is gone before she finishes. Or a
  second action starts before she has announced the first.
- The orb goes to "acting" (`_on_action`, `local_engine.py:263-265`) in the
  middle of a sentence.

**The policy gate is not bypassed.** Every call still goes through
`_pre_tool_use` → `_decide` → `Policy.check` (`claude_backend.py:423`), or
through `Executor.call` for our own tools. Deny rules, holds and dry-run
apply exactly as today. A held call (`HOLD`) does not run at all, so it
cannot land mid-sentence. This is about *order*, meaning what she says against
what the machine does. It is not about *whether* a call is allowed.

### What the session log shows

`config.LOG_FILE` (`~/.local/state/omarchy-voice/session.log`, 3058 lines)
was scanned with a throwaway script. It was read only, and nothing was
committed. The scan took local-engine sessions only (`start engine=local`)
and left out the `2026-09-24 12:05:01` burst, which is test output (#99). It
matched each `action`/`HOLD` line to the last `say` in the same turn.
It estimated playback as words ÷ 2.7 per second.

- 49 local turns. Only **3** have an `action` or `HOLD` line. 2 of those
  follow a `say` in the same turn.
- **Both** land before the sentence could have finished:
  - `2026-09-13 20:09:30 say Opening a terminal now.` → `20:09:31 action
    omarchy launch terminal`. That is 1 s after the say, and the line is
    about 1.5 s long.
  - `20:11:24 say Cloud session launched in the new \`test\` folder —
    standing by, I'll let you know when it's ready.` → `20:11:25 action ls
    -la …`. That is 1 s after the say, and the line is about 6.7 s long.
- Limits of this evidence. The `say` line is written when `_say` *starts*
  (`local_engine.py:333`), not when playback ends. ElevenLabs synthesis
  comes before any sound. The timestamps have 1 s resolution. So "mid-sentence"
  means consistent with it, not proven. The log also misses tool calls. Many
  turns whose words describe an action have no `action` line (for example
  `20:10:01` "Finding that terminal…" and `20:11:14` "Making the folder…").
  So 2 of 2 is a small sample, and all it says is that nothing contradicts
  the mechanism. It does not give a rate.

The mechanism above is the stronger evidence. In any turn that follows the
persona rule, the call is decided while the line before it plays.

## Proposed outcome

- With `barge_in` off, a tool call that can change something does not run
  until she has finished the line before it. "I'll close that window" is
  heard in full before the window closes.
- Read-only lookups (`_is_read`, `claude_backend.py:113-117`) are not made
  to wait. They still run under the line that announces them ("Let me
  look."), which is what the `content_block_stop` flush is for.
- Nothing about what is allowed changes. Every call meets the same gate,
  and it meets the gate first.
- The session log shows the order. An action that waited is logged after the
  sentence it waited for.

## Affected users and systems

- `src/omarchy_voice/claude_backend.py`: `_decide` / `_pre_tool_use`, and
  `ask_stream`'s flush at `content_block_stop`.
- `src/omarchy_voice/local_engine.py`: `_say`, `_answer`, `_on_action`, and
  whatever tells the brain that the mouth is idle.
- Our own tools through `Executor.call` (`tools.py:1950-1966`), because the
  SDK runs them on handler tasks too.
- The persona text, if its promise changes.
- #137 (make-ahead, spec approved, not merged). Its I4 invariant ("no pull
  past a block while she talks") holds either way. Its finding is the
  source of this issue.
- The user on p620 and razer, and anything reading `session.log` (#71's
  `eval_router.py`).

## Constraints

- The gate must stay what it is. Any wait comes *after* the policy
  decision or *before* it, but it must never replace the decision or skip
  it. `_pre_tool_use` must still answer every call explicitly and must
  never raise (`claude_backend.py:481-515`).
- A wait must be bounded. A mouth that never goes idle (a stuck `pw-cat`, a
  failed ElevenLabs call falling back to Piper) must not hang the turn or
  the SDK's handler task.
- Toggle-off, barge-in and cancel must still stop a turn that is waiting.
  `_drop_queued_speech` must not leave a call waiting forever.
- Read-only lookups must not get slower.
- No change to the SDK. The dev shell pins 0.2.152, and the fix lives in
  our code.
- With `barge_in` on, she does not wait for playback today
  (`local_engine.py:344-346`). This intent does not ask for that mode to
  change unless the approver says so.
- Tests must not touch the user's real files (#99).

## Open questions

1. **Is it a problem in practice?** The log has 2 cases out of 2 where it
   could be measured, but the sample is tiny and the log misses calls. The
   mechanism says it happens on almost every announced call. Is "the window
   closes while she says she will close it" worth fixing, or is it
   acceptable and only the docstring and the #137 premise need correcting?
2. **Should `_decide` wait for the mouth to go idle before allowing a call
   that is not read-only?** Or should the wait be somewhere else, for example
   in `ask_stream`, which stops reading so the hook request is not yet
   answered? And does the same wait apply to our own `mcp__omarchy__*`
   tools, whose side effect happens in `Executor.call`, not in `_decide`?
3. **Must read-only lookups never wait?** `_is_read` is an allowlist
   (`DRY_RUN_READS` plus `READ_ONLY_TOOLS`). Everything else, Bash included,
   would count as "can change something", even a harmless `ls`. Is that
   classification the right one for ordering, or is it too coarse?
4. **The cost.** A call that waits loses the overlap that the
   `content_block_stop` flush was built for. For an action, that adds the
   rest of the announcing line, often 1-2 s ("Right, switching now."). Is
   that accepted, and is there a cap after which the call runs anyway?
5. **Interaction with #137 and `barge_in` on.** Should make-ahead count as
   "speaking"? A clip being synthesised has not been heard yet. With
   `barge_in` on, should actions wait for the mouth too, or does that mode
   keep today's overlap on purpose?
