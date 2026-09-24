---
status: draft
issue: 76
intent: intent/2026-09-24-76-confirm-replays-once.md
---

# Spec: confirming a held action runs that action, once, and nothing else

## The intent's open questions, answered

The intent was approved without answers to its four questions. Each is
decided below with the reasoning. Any of them can be rejected at this gate.

### Q1: how does the held action run? Through the model, in a turn where the gate allows only that call.

Confirming starts a **release turn**. The brain sends a message that names the
held call exactly (tool plus JSON input) and asks for that call only. During
that turn the gate allows one call: the held one, matched on tool and input.
Everything else is refused. The approval ends with the turn, whether or not it
was used.

The model still has to make the call, but it can no longer do anything else.
If it makes no call, or a different one, nothing runs and the user is told.
The guarantee comes from the gate, not from what the model chooses to do.

Rejected:

- **Execute the stored `(tool, input)` ourselves.** Claude Code runs its own
  Bash, Write and Edit with its own cwd, sandbox, timeouts and edit rules. We
  would have to rebuild each tool, and an `mcp__ai-mirror__*` call is not ours
  to make. The warm session would also never learn that the action ran. This
  would be a third release mechanism, beside `Executor.run_pending` and the
  gate.
- **Hold the whole turn open, with the hook waiting for the user.** The turn
  holds `_turn_lock` and the microphone loop (`local_engine.py:258-285`,
  `:347`) for as long as the user takes to answer. The "needs confirming"
  line is only said once the turn ends (`local_engine.py:392-397`), so it
  would never be heard. Claude Code applies a hook timeout, and this spec does
  not rely on how that timeout behaves. It would be a new mechanism, and the
  riskiest one.

This is the realtime engine's rule applied to this gate: one held thing,
released once, and exactly that thing (`Executor.run_pending`,
`realtime.py:945-953`). The only difference is that Claude Code makes the call.

### Q2: several holds in one utterance? Still one at a time. The rest is not resumed.

The gate keeps one `pending`, the same as `Executor.pending` everywhere else.
The release turn runs the held call and nothing more. A second held action in
the same sentence ("…then update the system") is refused in the release turn,
not held. The model is told to tell the user what is still undone, and the
user asks again. Resuming the rest of an utterance is what the replay did, and
the replay is the bug.

### Q3: a spoken "confirm" on the local engine? Shown to fail. Its own issue, not this change.

Demonstrated below (run C) against main, with the real `LocalSession`,
`ClaudeBrain` gate and `mcp_server` confirm path. Only the model is scripted.
A spoken "confirm" reaches the model as text. The model's two plausible moves
are both dead ends:

- `confirm_last` is allowed at the hook (ours). It then answers "nothing is
  waiting", because the MCP gate only reads `executor.pending`
  (`mcp_server.py:137-138`) and a Claude Code hold is on the brain.
- Retrying `reboot` is held again.

The word "confirm" then becomes `_last_text`, and a later keybind confirm
replays it.

To fix spoken confirmation, something has to decide whether the transcript
counts as consent. `_local_confirm`'s docstring deliberately trusts neither
the model nor the transcript (`local_engine.py:495`). The engine's own
spoken prompt contains the word "confirm" (`local_engine.py:397`), and the
microphone reopens just after it (`ECHO_TAIL_SECONDS`). That is a separate
design with its own risk (echo), so it belongs in a new issue. The approver is
asked to file it. It is not filed from here.

After this change a spoken "confirm" is harmless: `_last_text` is deleted, so
it can never be replayed, and the approval only exists inside a release turn.

### Q4: fix `cmd_say` too? Yes, in the same change.

The code path is the same (`cli.py:175-176`: `planner.confirm()` then
`planner.think(text)`). The fix is one line once the brain has a release turn.
The one-shot path is where the replay is worst: `think` opens a fresh session
(`claude_backend.py:497`), so the replayed turn remembers nothing. The release
message carries the exact call, so it works in a fresh session without the
history.

## Demonstration (main at `60a8be6`)

Script: real `LocalSession`, real `ClaudeBrain._pre_tool_use`, real
`mcp_server.build_server` on the session's `Executor`. Each utterance's Bash
calls are scripted and go through the hook. Nothing is executed. An "allow" is
counted.

```
A. confirm replays the whole utterance
  held:           reboot
  confirm says:   Confirmed: reboot
  brain asked:    ['commit my notes and reboot', 'commit my notes and reboot']
  allowed:        ['git -C ~/notes commit -am wip', 'git -C ~/notes commit -am wip', 'reboot']
B. something said between hold and confirm
  held:           reboot
  confirm says:   Confirmed: reboot
  brain asked:    ['commit my notes and reboot', 'what time is it', 'what time is it']
  allowed:        ['git -C ~/notes commit -am wip', 'date', 'date']
  still approved: {'reboot'}
C. a spoken 'confirm' with a Claude Code hold
  held:           reboot
  hook on confirm_last: allow
  confirm_last says:    ERROR: nothing is waiting for confirmation. Do not call this again.
  re-trying reboot:     deny
  still held:     reboot
  _last_text:     'confirm'
  keybind says:   Confirmed: reboot
  hook on confirm_last: allow
  confirm_last says:    ERROR: nothing is waiting for confirmation. Do not call this again.
  re-trying reboot:     allow
  brain asked:    ['commit my notes and reboot', 'confirm', 'confirm']
```

A and B reproduce the intent. In C (`CONFIRM_DELAY` patched to 0), the spoken
"confirm" releases nothing. The keybind then replays the word "confirm", and
the reboot only runs because the scripted model happens to retry it.

## Design

### 1. The hold keeps the call, not only its description (`claude_backend.py`)

- `ClaudeBrain.__init__` (`:230-231`): `pending: str | None` stays, because
  `_held()`, `_settle()`, `cmd_say` and the replies all read it as a string.
  `_confirmed: set[str]` is **removed**. Two slots are added:
  `_held_call: tuple[str, dict] | None` (set with `pending` in the
  `NeedsConfirmation` branch, `:312-319`) and `_approved` (the call the user
  said yes to, or None).
- `confirm()` (`:238-243`) moves `_held_call` into `_approved`, clears both
  `pending` and `_held_call`, and returns the **release message**: text built
  from the approved call, or None if nothing was held. Callers already read
  `pending` before confirming (`local_engine.py:502`, `cli.py:171`), so the
  description they print does not change. The message says the user has just
  confirmed exactly this one call, gives the tool name and its JSON input,
  says to make that call now, unchanged, and nothing else, and then to say in
  one sentence whether it ran. Worded as a fact about the user, so it still
  reads correctly under #78's "What the user said" heading.
- `cancel()` (`:245-247`) also clears `_held_call`.

### 2. The release turn: one call allowed, then the approval dies

- `think(text, *, release=False)` (`:249`) and
  `WarmBrain.ask_stream(text, *, release=False)` (`:699`) set
  `self._releasing = release` for the length of the turn, and in a `finally`
  clear `_releasing` and `_approved`. The approval is armed only inside the
  turn that was started to spend it. It cannot be spent by a turn that was
  already running when the user confirmed. It cannot outlive the release turn.
- `_decide` (`:264`): a new first branch, **before** the `mcp__omarchy__`
  allow at `:272`, so our own tools cannot repeat either. It replaces the
  `_confirmed` branch at `:280-302`:
  - While `_releasing`, a call that matches `_approved` is allowed. Matching
    means the same tool and the same input, ignoring Bash's `description` key
    (a free-text label Claude Code shows; it does not run). The approval is
    spent (`_approved = None`) and goes through the existing
    `_dry_run_refusal`, the `CONFIRM` transcript line, `on_action` and
    `_actions`, exactly as `:288-302` do today.
  - While `_releasing`, a tool in `DRY_RUN_READS` (Read, WebSearch,
    ToolSearch) takes the ordinary path. ToolSearch may be needed to load a
    deferred tool's schema before the held call can be made. None of these
    can change anything.
  - While `_releasing`, anything else is refused with `PermissionResultDeny`:
    "Only the call the user confirmed may run now. Do not retry anything
    else; tell the user what is still undone." A second attempt at the
    already-spent call is refused the same way.
  - Matching the full input is stricter than today's match on the description
    string. A Write whose content changed between hold and release no longer
    passes because its path is the same.
- If the release turn ends with `_approved` still set, the brain says so
  itself. `ask_stream` yields `"<description> was not run."` as its last
  sentence, and `think` appends the same text to `turn.reply`. The engine
  does not need to know anything about how the gate works.
- Every call still gets an explicit allow or deny from `_pre_tool_use`
  (`:364-397`). Nothing reaches `can_use_tool`.

### 3. The local engine sends the release, not the last utterance (`local_engine.py`)

- `_local_confirm` (`:502-511`) becomes:
  `text = self.brain.confirm()`, then
  `self._spawn(self._answer(text, release=True))`. The reply text
  `Confirmed: {held}` is unchanged.
- `_answer` (`:339`) gets a `release=False` keyword that it passes through to
  `ask_stream`. The log line reads `release {held}` instead of
  `heard   {text!r}`, so the log does not show an utterance nobody said.
- `_last_text` (`:153-154`, `:348`) is **deleted**. The replay was its only
  use.
- Holds of our own tools (`:496-501`) are untouched.

### 4. `cmd_say` (`cli.py:175-176`)

`planner.confirm(); turn = planner.think(text)` becomes
`turn = planner.think(planner.confirm(), release=True)`. Nothing else changes.

### Not changed

`realtime.py`, `mcp_server.py`, `Executor`, `planner.Planner`, the spoken
"Say confirm, or cancel" line (Q3), and the README's confirmation section
(README:852-861 describes the local release, which still holds).

### Overlap with open PRs: the plan must sequence after both

- **#81 `feat/72-listen-faster`** rewrites `_turn`, `_wake_turn` and `run()`,
  adds `_hear`, and changes `_answer` to `_answer(text, trace=None)`. This
  spec adds `release=False` to the same signature and edits `_answer`'s body
  (the `_last_text` line and the log line). It also edits `_local_confirm`,
  which #81 leaves alone. The plan rebases onto #81 and uses
  `_answer(text, trace=None, *, release=False)`.
- **#78 `fix/69-snapshot-per-turn`** wraps every turn's text in
  `_with_desktop` inside `_ask` (`:498`) and `WarmBrain._turn` (`:725`). The
  release message will arrive under "# What the user said" with a fresh
  snapshot in front of it. That is harmless, and it is why the message is
  worded as a statement about the user. #78 also changes the tests in
  `test_claude_backend.py` that this spec edits. The plan rebases onto #78.

## Alternatives rejected

- **Execute the stored call directly**, or **block the hook until the user
  answers**: see Q1.
- **Keep the replay and add "only do X" to the text.** Nothing enforces it.
  The gate would still let every other call in the utterance through, and our
  own tools are allowed at the hook unconditionally (`:272-278`).
- **Keep `_last_text` but store it with the hold.** This fixes run B (wrong
  text) but not run A (the whole utterance again).
- **Expire `_confirmed` on a timer.** An approval would still outlive its
  hold, just for less time, and could still be spent by an unrelated turn.
- **Clear the hold when a new utterance arrives.** The realtime engine and
  the MCP server keep a hold until it is confirmed, cancelled or replaced.
  Diverging here is a separate decision.

## Risks

- **The model does not make the call, or changes it** (a different `timeout`
  on Bash, reworded Write content). Nothing runs, and the user hears
  "<held> was not run." This fails closed. It can be worse in the cold `say`
  session, which has only the message to go on. If it happens in practice,
  the next step is to relax matching for specific input keys, one at a time,
  with evidence. The gate is not loosened.
- **A held Write with large content** makes the release message as large as
  the content. That is acceptable for the size of files this assistant
  writes. It is noted here in case it turns out not to be.
- **Our own tools are refused during a release turn.** The model cannot, for
  example, `notify` about the outcome in that turn. It says it instead.
- **Every host running the local engine or `say` with the Claude brain.** The
  realtime engine and MCP server code paths are untouched.

## Verification

Unit tests with fakes. Nothing runs on the live desktop.

- `tests/test_local_engine.py`: **regression, the intent's demonstration.**
  Drive the real `ClaudeBrain` gate under a `LocalSession` with a scripted
  `ask_stream`, as in the demonstration above. Run A: after confirm,
  `allowed == ['git … commit -am wip', 'reboot']` (the commit once). Run B:
  after an intervening "what time is it", confirm allows exactly `reboot`,
  `date` appears once, and `_approved` is None afterwards. The existing
  `test_a_held_action_is_spoken_and_confirm_releases_it` (`:335-360`) no
  longer expects the utterance twice. It expects one ordinary turn plus one
  release turn. `FakeBrain.ask_stream` takes `release`.
- `tests/test_claude_backend.py`:
  - In a release turn, the held call is allowed once. A second identical call
    is refused. A different Bash call and an `mcp__omarchy__*` call are
    refused. `ToolSearch` is not refused.
  - An approval from `confirm()` is not honoured outside a release turn, which
    covers a turn that was already running.
  - An unused approval is gone after the release turn, and the turn says
    "<held> was not run."
  - A Write with the same path but different content does not match. A Bash
    call that differs only in `description` does match.
  - The existing tests that read `_confirmed` (`:103-124`, `:223-237`,
    `:362-373`) are rewritten against `_approved` and release turns. The
    dry-run case keeps its assertion: confirmed under dry-run is still
    refused, and the approval is still spent.
- `tests/test_backend_choice.py` (`:177-247`): `cmd_say` sends the release
  message with `release=True`, not the original text.
- `python -m pytest tests` passes.
- `nix flake check --no-write-lock-file` passes.
