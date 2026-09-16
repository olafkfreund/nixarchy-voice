---
status: draft
issue: 3
author: olafkfreund
---

# Intent: a held action can be released from an MCP client

## Problem

The safety gate holds an action and tells the caller how to release it. Over
MCP that instruction cannot be followed.

`mcp_server.py:61` sets the held-action message to "ask them in this
conversation, and call `confirm_last` once they agree". `confirm_last` is not
in the MCP tool list: it lives in `GATE_TOOLS` in `realtime.py:243`, which the
voice session adds to the schemas and dispatches at `realtime.py:1175`.
`tools_for()` does not carry it, so an MCP client is told to call a tool it was
never offered. `cancel_last` is missing in the same way, so a held action
cannot be dropped either — and `tools.py:1292` refuses every later gated action
while one is pending, so the hold blocks the next one too until the process
restarts.

The gate itself is behaving correctly. Nothing unsafe runs, the deny list is
untouched, and the action stays held. What is wrong is that the message
describes a route out that does not exist, and there is no other route: the
user has to leave the agent and run the command by hand.

Everything in `DEFAULT_CONFIRM` (`config.py:107`) is affected — reboot,
shutdown, suspend, `nixos-rebuild`, `nixarchy-apply`, `nix flake update`,
`omarchy update`, `close-all`, `hl.dsp.exit`. Observed from Codex 0.154.0 and
opencode 1.18.30, both running the server as a local stdio command; reproduced
directly against `omarchy-voice mcp` over raw JSON-RPC (issue #3).

## Proposed outcome

From an MCP client, a held action can be carried through to a decision without
leaving the conversation:

- the user is asked, and on agreement the action runs;
- on refusal the hold is dropped and later gated actions work again;
- whatever the outcome, the message the agent receives when the hold is created
  describes something it can actually do.

The confirmation still has to be the user's, not the agent's. An agent that
decides on its own that the user agreed must not be able to release a reboot.
How strong that assurance can be over MCP is the open question below.

## Affected users and systems

- `src/omarchy_voice/mcp_server.py`, and whichever of `tools.py` /
  `realtime.py` ends up owning the shared gate tools.
- Every MCP client of this server. Codex and opencode on p620 are configured
  today (`~/.codex/config.toml`, `~/.config/opencode/opencode.json`); Claude
  Code uses the same server.
- The voice session must keep its current behaviour exactly: the `heard_phrase`
  check, the refusal of a same-batch confirm, and the requirement of a new user
  turn (`realtime.py:1175`).
- No host or deployment change. The server is a per-session child process.

## Constraints

- Must not weaken the voice path. The rules at `realtime.py:1175` exist because
  a voice channel is an open microphone.
- Must not let an agent release a held action on its own say-so, with no
  evidence the user was asked.
- Must not add a second policy implementation. The gate is the one place that
  must not drift (`mcp_server.py:9`), so any new tool releases the existing
  `Executor.run_pending` / `drop_pending` rather than re-checking anything.
- The deny list stays absolute: it is not confirmable by any route.
- `run_pending` executes whatever is pending, so nothing may reach it without
  passing the same checks the voice path applies.
- stdio is the transport: nothing may be written to stdout that is not a
  protocol message (`mcp_server.py:126`).

## Open questions

1. **Answered (2026-09-16): (a), the relayed phrase.** `confirm_last` is
   offered over MCP requiring the user's own words, checked against
   `confirm_words` exactly as the voice path checks them. The assurance is the
   client agent's honesty in relaying what the user typed — the same trust
   placed in the voice model today — and no second policy implementation.
   This answers 2 as well: the system-administration commands stay reachable
   from an MCP client. 3 stands: `cancel_last` is offered too.

   **What counts as the user's confirmation over MCP?** The voice session has a
   signal the MCP server does not: it knows the user spoke, and when. An MCP
   server sees only tool calls from a model. Candidates, for the approver to
   choose between:
   a. Offer `confirm_last` requiring the user's own words, checked against
      `confirm_words` as the voice path does. Honest agents relay; a
      dishonest or careless one can fabricate the phrase.
   b. Require confirmation to arrive as a separate call after the client has
      returned to the user, approximating the "new user turn" rule with
      something observable, if anything here is observable.
   c. Do not offer confirmation over MCP at all. Change the message to say the
      action is held and the user must run it themselves, and offer only
      `cancel_last` so the hold can be cleared.
2. **Does (c) leave the server usefully able to administer the machine?** The
   held set is exactly the system-administration commands an agent on this
   desktop would most want — `nixos-rebuild`, `nixarchy-apply`. Deciding they
   are permanently out of reach over MCP is a real decision, not a fallback.
3. **Should `cancel_last` be offered regardless of the answer to 1?** It only
   ever drops a pending action, so it cannot cause an action to run.
