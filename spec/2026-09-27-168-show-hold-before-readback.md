---
status: draft
issue: 168
intent: intent/2026-09-27-168-show-hold-before-readback.md
---

# Spec: what you are confirming is on screen while she reads it out

## Decisions on the intent's open questions

The intent was approved on 2026-09-27 ("continue then") with its questions
unanswered, so the recommendations are taken. The first is adjusted by one
fact found while writing this spec:

1. **A short notification, and the full text on the bar.** The bar's text is
   shown only as the indicator's hover tooltip (`VoiceIndicator.qml:105`), not
   inline. So the notification cannot cut a recipe silently: when it has to
   shorten one, its last line says how many steps are not shown and where to
   read them. A dedicated window or menu page is still rejected (below).
2. **Replaced, not stacked, and closed on yes or cancel.** Checked against
   nixarchy's notification server (`shell/plugins/notifications/Service.qml`
   in the omarchy tree):
   - it honours `replaces_id` (`:164`, `:202`, `:240`);
   - it honours a close from the sender (`:56`).

   mako, plain Omarchy's server, supports both too.

## What the server does, which the design has to fit

Taken from nixarchy's shell, the store path razer and p620 run:

- A card shows **2 lines of summary and 3 of body**
  (`components/NotificationCard.qml:176`, `:190`), elided, with no expand.
- **low** lasts 5 s and **normal** 8 s, 30 s at most whatever
  `expire_timeout` says. **critical** stays until it is closed or dismissed
  (`Service.qml:97-110`).
- With Do Not Disturb on, only app `omarchy-action`, or `notify-send` at
  critical, get through (`NotificationLogic.js:118`). Oma's app name is `OMA`.

## Design

### 1. The hold is shown when it starts, not after her sentence

`Executor.__init__` (`src/omarchy_voice/tools.py:2079`) gains
`on_hold: Callable[[str], None] | None`, stored like `on_action` (`:2092`).
It is called with the description at the two places a hold is created:

- `Executor._hold` (`tools.py:2245`), after `self.pending` is set and
  recorded;
- the Claude backend's permission callback (`claude_backend.py:437-440`),
  after `self.pending = description`, as `self.executor.on_hold(description)`.
  The backend already holds the executor (`:285`).

`LocalEngine` passes `on_hold=self._show_hold` (`local_engine.py:241`).
`_show_hold(held)` is what `_settle()` does for a hold today (`:316-327`):
`feedback.state("confirm", held)` plus the notification. It records `held` as
`self._shown_hold`.

`_settle()` (`:316`) then:

- with a hold equal to `self._shown_hold`: sets the bar state only (it is
  idempotent) and posts nothing new;
- with a different hold (a second gate, or a release that re-held the next
  step of an action, `tools.py:5354`): calls `_show_hold`, which *replaces*
  the card;
- with no hold: closes the card if one is open, clears `_shown_hold`, and
  sets the bar to listening or idle as now.

`HELD_PROMPT` ("Say the word on the screen…") is unchanged. Now it is true
while she says it.

### 2. One card per hold: replaced, closed, never stacked

`Feedback.notify` (`src/omarchy_voice/feedback.py:248`) gains
`replace: int | None = None` and returns the notification ID:

- it adds `-p` (print ID) and, when given, `-r <id>` to the existing
  `notify-send` call;
- it parses stdout as an int, or returns `None` on no output (an old
  libnotify, or notify off). The other callers ignore the return value, so
  they are unchanged.

`Feedback.close(note_id)` runs
`busctl --user call org.freedesktop.Notifications /org/freedesktop/Notifications
org.freedesktop.Notifications CloseNotification u <id>`. `busctl` ships with
systemd, which every supported host has. Failures are ignored: the worst case
is a card the user dismisses by hand.

The hold card is posted at **critical**, so it stays for as long as the hold
waits, where normal would vanish after 8-30 s. Because it is closed on yes or
cancel, critical does not mean a card left behind.

### 3. The card's layout: nothing important is clipped

`LocalEngine._hold_card(held) -> (summary, body)`:

- **summary** (up to 2 lines, never clipped in practice): how to answer.
  `Say "confirm" or press the confirm key`, or `Press the confirm key` with
  `barge_in`. That is the text `_settle` puts at the end of the body today,
  where it was the first thing clipped.
- **body** (up to 3 lines): `held`, split into one line per numbered step
  where `describe()` joined them with `; N. `. Only `describe("action")` for a
  save builds text that way (`tools.py:2476-2491`). Anything else is one line.

  With more than 3 lines, the body is the first 2 lines plus
  `+N more — hover the voice indicator to read all of it`. The server wraps
  long lines, so a card can still clip a wrapped step. That is the ceiling,
  marked `ponytail:` in the code. The upgrade path is a dedicated view.

The bar state keeps the full `held` text, unchanged.

## Alternatives rejected

- **Call `_settle()` before her spoken prompt instead of after**
  (`local_engine.py:739-746`). This fixes the 6 s at the end of the turn but
  not the time between the hold and the end of the turn (08:19:52 → :58 in
  the demo), and not the Claude backend's holds.
- **A dedicated window or a menu page for the pending hold.** It takes focus
  from what the user is doing mid-sentence. It is a new UI to build, and on a
  voice-first path the card and the bar are enough once they are timely and
  honest about clipping.
- **Normal urgency with the longest timeout.** It is capped at 30 s, and a
  hold can wait longer. Critical plus an explicit close covers any wait.
- **Getting past DND by posting as `notify-send` or `omarchy-action`.** That
  borrows a trust rule written for other programs. Under DND the card is
  hidden as every Oma notification is today. The bar indicator still breathes
  in the confirm state, and she still says it.
- **Only fixing the text layout** (the clipped "Say confirm" line). That
  leaves the demo's failure, a yes before anything was shown.

## Risks

- **A critical card left open** if the daemon dies mid-hold. The hold dies
  with it. The card stays until dismissed, and it is the same card the next
  hold would replace. Acceptable, and noted.
- **`on_hold` runs on the tool's thread**, since executor tools run off the
  loop. `_show_hold` writes a file and runs `notify-send`, as `_on_action`
  already does from there. `_shown_hold` is a single attribute assignment.
- **Old libnotify without `-p`:** `notify` returns `None`, so replace and
  close are skipped and the behaviour is today's (stacked cards that expire).
- **mako's own `max-visible`/line limits** on plain Omarchy may clip
  differently. The summary-first layout helps there too. The live check is on
  razer's quickshell. mako is covered by the unit tests only.
- **The MCP server's holds** (`mcp_server.py`) are a separate process with no
  bar or voice, and are not affected.

## Verification

- `tests/test_feedback.py`, with `subprocess.run` faked:
  - `notify` passes `-p` and returns the parsed ID;
  - `replace=7` adds `-r 7`;
  - empty stdout gives `None`;
  - `close(7)` runs the `busctl … CloseNotification u 7` call;
  - notify off → nothing is run.
- `tests/test_local_engine.py` (where `_settle` is tested), with a fake
  feedback:
  - a hold raised mid-turn posts the card **before** the turn's speech ends;
  - the end-of-turn `_settle` does not post a second card;
  - confirm and cancel close it;
  - an action that re-holds at its next step replaces the card (same ID);
  - `_hold_card` on a 3-step save gives one line per step and the answer text
    in the summary. On a 5-step one it gives 2 lines plus `+3 more …`.
- `tests/test_actions.py` and `tests/test_claude_backend.py`: `on_hold` is
  called once per hold, from `_hold` and from the permission callback.
- The full suite, `nix flake check`, and `nix build .#omarchy-voice`.
- **Live on razer** (announced on the bus first), replaying the demo's "ask"
  beat with `grim` stills every 2 s:
  1. the card appears within about 1 s of the `HOLD` line in `session.log`,
     before her readback starts;
  2. it reads `Say "confirm"…` as the summary, with the 3 steps on 3 lines;
  3. `listen confirm` mid-readback → the card is gone at the next still;
  4. a 5-step recipe shows `+3 more — hover the voice indicator…`, and the
     tooltip holds all 5 steps.
