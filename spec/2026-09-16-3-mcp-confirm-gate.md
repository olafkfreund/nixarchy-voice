---
status: draft
issue: 3
intent: intent/2026-09-16-3-mcp-confirm-gate.md
---

# Spec: a held action can be released from an MCP client

## Design

`mcp_server.py` offers two more tools, `confirm_last` and `cancel_last`, and
handles them in `call_tool` before dispatching to `Executor.call`. They are
added to the list `tools_for(config)` returns, not to `TOOL_SCHEMAS`, so the
voice path is untouched.

Neither tool decides anything. They release what the gate is already holding:

- `confirm_last(phrase)` → `_matches(phrase, config.confirm_words,
  allow_negation=False)` (`session.py:30`, the function `realtime.py:1287`
  already calls) → on a match, `executor.run_pending()`.
- `cancel_last()` → `executor.drop_pending()`.

So the policy check stays in exactly one place: `Executor._call_locked`
(`tools.py:1280`) decides what is held, and these two only carry out a decision
the user has made. Nothing re-implements `policy.check`, and the deny list is
untouched — a denied action never becomes pending, so there is nothing for
`confirm_last` to find.

**The argument is the user's words, not a boolean.** `phrase` carries what the
user actually typed, and is checked against `confirm_words` ("confirm", "yes do
it", "go ahead"). This is the trust model the approved intent chose: the same
one the voice path uses, where the model relays what it heard. A `confirm=true`
boolean would be a decision the agent makes; a phrase is a decision the agent
reports. The tool description says so plainly, and the refusal message names
the phrases the user can use, as `realtime.py:1290` does.

**The same-batch rule becomes a minimum delay.** The voice session refuses a
confirmation that arrives in the same response as the hold, or without a new
user turn (`realtime.py:1175`), because it can observe turns. An MCP server
cannot: it sees two tool calls and nothing about what happened between them.
The observable proxy is elapsed time. `Executor` records when it set
`self.pending`; a `confirm_last` arriving less than 2 seconds later is refused
with an explanation. A person reading a confirmation prompt and typing a reply
never takes under two seconds; an agent chaining a confirm straight onto its
own hold does it in milliseconds. It is a heuristic and the spec says so in the
code, with the upgrade path named: if a client ever supports elicitation, ask
the user directly and drop the delay.

**Wording of the hold message.** `mcp_server.py:61` keeps its shape and becomes
accurate: ask the user in this conversation, then call `confirm_last` with what
they said, or `cancel_last` if they decline.

Files: `src/omarchy_voice/mcp_server.py` (the two tools and their handling),
`src/omarchy_voice/tools.py` (record the time the hold was created; expose it
for the delay check), `tests/test_mcp.py` (the checks below), and `README.md`
where the MCP section describes the gate.

## Alternatives rejected

- **Move `GATE_TOOLS` out of `realtime.py` and share the schemas.** The two
  descriptions differ in the only way that matters: the voice one says "the
  user confirmed it out loud" and names `heard_phrase`; the MCP one is about
  what the user typed in a conversation. Sharing them would mean a schema with
  parameterised prose, which is more machinery than writing four lines twice.
  The logic is shared; only the wording is not.
- **A `confirm=true` boolean.** Lets an agent release a reboot on its own
  reasoning with nothing to check, and there is then no difference between "the
  user agreed" and "the model concluded the user would agree". Rejected by the
  approved intent.
- **No confirmation over MCP, cancel only.** Rejected by the user when
  answering the intent's open question: it puts `nixos-rebuild` and
  `nixarchy-apply` permanently out of reach of an agent on this desktop.
- **Out-of-band approval (notification or terminal prompt).** The strongest
  assurance and the most code, and it fails exactly when the agent is most
  useful — driving the machine while the user is not at the screen. Worth
  revisiting if a client ever supports MCP elicitation, which would put the
  prompt in the client rather than on the desktop.
- **Dropping the delay check and relying on the description alone.** The
  description is the only thing stopping a same-response confirm, and a
  description is advice. Two seconds costs one comparison.

## Risks

- **A careless client fabricates the phrase.** It can: this is the trust model
  the intent accepted. Bounded by the deny list, which no phrase releases, and
  by the transcript — every hold, confirm and cancel is already logged
  (`tools.py:1297`, `:1326`, `:1342`), so a fabricated confirmation is visible
  in `omarchy-voice log` afterwards.
- **The 2-second delay annoys a fast human.** Only reachable by a user who
  reads a reboot prompt and answers in under two seconds. The refusal explains
  itself and the next attempt succeeds, so the cost is one round trip.
- **Voice regression.** The change must not touch `GATE_TOOLS`,
  `realtime.py:1175` or `_confirm`. The existing voice tests in
  `tests/test_realtime.py` are the guard and must keep passing untouched.
- **Hosts:** none. The server is a per-session child process; no unit, no
  rebuild, nothing that outlives the client.

## Verification

1. `tests/test_mcp.py`, extended, all of it against the real `Executor` with a
   dry-run config so nothing runs:
   - `tools/list` includes `confirm_last` and `cancel_last`;
   - a gated call (`omarchy_cli` with `omarchy update`) is held, and the message
     it returns names both tools;
   - `confirm_last` with a confirmation phrase, after the delay, runs the held
     action and clears `pending`;
   - `confirm_last` with a phrase that is not a confirmation leaves it held and
     says which phrases work;
   - `confirm_last` inside the delay window is refused and leaves it held;
   - `confirm_last` with nothing pending is refused;
   - `cancel_last` drops the hold, and a later gated action is accepted rather
     than refused as a second gate;
   - a denied action (deny list) never becomes pending, so `confirm_last`
     cannot release it.
2. `python -m pytest` — the whole suite, including the voice tests, unchanged.
3. `nix flake check`.
4. Live, against the running desktop from a real client: ask Codex to run
   `omarchy update`, decline, confirm the hold is gone; ask again, approve, and
   see it run. Recorded in the PR.
