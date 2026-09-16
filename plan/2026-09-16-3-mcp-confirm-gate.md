---
status: approved
issue: 3
spec: spec/2026-09-16-3-mcp-confirm-gate.md
---

# Plan: a held action can be released from an MCP client

## The decisions this carries out

Approved in the intent and spec; repeated here so this file is enough to work
from.

1. `mcp_server.py` offers `confirm_last(phrase)` and `cancel_last()`. They are
   appended to what `tools_for(config)` returns for MCP only. `TOOL_SCHEMAS`,
   `GATE_TOOLS` (`realtime.py:243`) and the voice dispatch (`realtime.py:1175`)
   are not touched.
2. Neither tool decides anything. `confirm_last` checks the phrase and calls
   `Executor.run_pending()`; `cancel_last` calls `Executor.drop_pending()`.
   The policy check stays only in `Executor._call_locked` (`tools.py:1280`).
3. The argument is the user's words, never a boolean. It is checked with
   `_matches(phrase, config.confirm_words, allow_negation=False)` from
   `session.py:30` — the same call `realtime.py:1287` makes.
4. The voice rule "confirmation must come from a new user turn" becomes a
   minimum delay of 2 seconds between the hold being created and
   `confirm_last` being accepted, because an MCP server cannot observe turns.
   It is a heuristic; the code says so and names the upgrade path (client
   elicitation).
5. The held-action message at `mcp_server.py:61` becomes accurate: ask the
   user, then call `confirm_last` with what they said, or `cancel_last`.
6. A denied action never becomes pending, so no phrase can release one. The
   deny list is untouched.

## Steps

1. `src/omarchy_voice/tools.py`: in `_call_locked`, where `self.pending` is set
   (`tools.py:1297`), also record `self.pending_since = time.monotonic()`.
   Initialise it beside `self.pending` in `__init__`, and clear it in
   `run_pending` and `drop_pending` where `self.pending = None`. Nothing reads
   it on the voice path.
   → verify by `python -m pytest tests/test_policy.py tests/test_realtime.py`,
   unchanged and passing.

2. `src/omarchy_voice/mcp_server.py`: add the two schemas as a module-level
   list, MCP-shaped (`name`, `description`, `input_schema`) so
   `_to_mcp_tools` takes them unchanged. `confirm_last` takes a required
   string `phrase`, described as the user's own words, verbatim, with an
   explicit line that it must not be called unless the user has actually been
   asked and has answered. `cancel_last` takes no arguments.
   → verify by `tools/list` over the server returning both, in the test below.

3. `src/omarchy_voice/mcp_server.py`: in `list_tools`, return
   `_to_mcp_tools(tools_for(config) + GATE_SCHEMAS)`.
   → verify by step 2's check.

4. `src/omarchy_voice/mcp_server.py`: in `call_tool`, handle the two names
   before `executor.call`:
   - `cancel_last` → `executor.drop_pending()`; `None` gives "Nothing was
     being held.", otherwise "Cancelled: <held>. It was not run."
   - `confirm_last` → in order: nothing pending → refuse and say so; within
     2 seconds of `pending_since` → refuse, explain that the user has to be
     asked first and that it may be retried once they have answered, leave it
     held; phrase does not match → refuse, name the acceptable phrases, leave
     it held; otherwise `await asyncio.to_thread(executor.run_pending)` and
     return `result.as_tool_result()`.
   Every refusal leaves `pending` intact so the hold survives a bad attempt.
   → verify by the tests in step 6.

5. `src/omarchy_voice/mcp_server.py:61`: rewrite `confirm_instruction` to name
   both tools and say the phrase must be the user's own words.
   → verify by the held-message test in step 6.

6. `tests/test_mcp.py`: add a case class covering, against a real `Executor`
   with `Config(dry_run=True)` so nothing runs:
   a. `tools/list` includes `confirm_last` and `cancel_last`;
   b. a gated call is held and the message names both tools;
   c. confirm with a good phrase, past the delay, runs it and clears `pending`;
   d. confirm with a non-confirmation phrase leaves it held and names the
      phrases that work;
   e. confirm inside the delay window is refused and leaves it held;
   f. confirm with nothing pending is refused;
   g. cancel drops the hold, and the next gated action is held rather than
      refused as a second gate;
   h. a deny-list action never becomes pending, so confirm cannot release it.
   The delay is crossed by setting `executor.pending_since` back, not by
   sleeping: a test that sleeps two seconds is a test nobody runs.
   → verify by `python -m pytest tests/test_mcp.py`.

   **Deviation, made while implementing:** `build_server` takes an optional
   `executor` argument, so a test can hand in the `Executor` it inspects. The
   server built its own in a closure, which a test could only have reached by
   copying the code — the existing
   `test_over_mcp_it_asks_in_the_conversation_instead` does exactly that and
   would not have caught this bug. The tests drive the real protocol through
   `mcp.shared.memory.create_connected_server_and_client_session` rather than
   calling the handlers, because the bug being fixed was a tool the server
   described and never offered, which a direct call would have hidden.

7. `README.md`: in "The desktop, for a coding agent", say that a held action is
   released with `confirm_last` carrying the user's own words, or dropped with
   `cancel_last`. The existing paragraph about Claude Code being held when told
   "the user has already approved it" stays true and is worth keeping next to
   it.
   → verify by reading the rendered section.

8. Commit, push, open a PR linking intent, spec and plan, and close #3.
   → verify by `git show origin/main:src/omarchy_voice/mcp_server.py` carrying
   the change after merge, not by the PR badge.

## Tests

```bash
python -m pytest                 # whole suite, including the voice tests
nix flake check                  # expect: all checks passed
```

Live check, recorded in the PR — from Codex on p620, with the desktop running:

1. Ask it to run `omarchy update`. Expect: held, and it asks rather than
   inventing a confirmation.
2. Answer "no". Expect: cancelled, and a following gated action is held on its
   own rather than refused as a second gate.
3. Ask again, answer "confirm". Expect: it runs, and `omarchy-voice log` shows
   `HOLD` then `CONFIRM` for it.

## Rollback

`git revert` the implementation commit. The change is confined to the MCP
server plus two fields in `Executor`; nothing persists outside the process, no
unit or host state changes, and the voice path is untouched, so a revert
restores exactly the current behaviour (a hold that cannot be released over
MCP). No deployment step is involved: clients start the server per session.
