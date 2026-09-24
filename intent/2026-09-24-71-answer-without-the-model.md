---
status: draft
issue: 71
author: olafkfreund
---

# Intent: a common command must not wait for the model

Closes #71.

## Problem

On the local engine, the default, every sentence goes to Claude, including the
ones whose whole meaning is in the words. `LocalSession._answer`
(`src/omarchy_voice/local_engine.py:339-404`) hands the text straight to
`brain.ask_stream`, and `WarmBrain._turn` (`claude_backend.py:723-771`) sends
it to the model and speaks whatever comes back. There is no step in between
that could see that "switch to workspace one" means `focus {workspace: "1"}`.

Each return from the model is a warm turn of 1.5-7.6 s
(`intent/2026-09-20-23-desktop-loop-latency.md:28`). A command is at least two:
one to say what it is about to do and emit the call, one to report the result.
`LOCAL_PERSONA` (`local_engine.py:69-86`) makes the first return mandatory:
"before ANY tool call, say one short line about what you are about to do". The
comment above it gives the measured reason: a tool turn took 8.1-9.0 s to its
first word without it (`local_engine.py:60-63`). So the announcement is a patch
over the latency, not a cause of it.

### What was actually said

`~/.local/state/omarchy-voice/session.log` holds 111 `heard` lines, 2026-09-11
to 2026-09-15: 63 on the realtime engine, 48 on the local one. (The 1,333
`wake ignored` lines are background audio and are left out.) Each was read and
labelled by hand:

| Kind | Count | Examples |
| --- | ---: | --- |
| Fixed command, everything needed is in the words | 11 | "Can you switch to workspace one, please?", "Open up a new terminal, please." ×3, "What windows do I have open?" ×4, "Move Discord back to workspace 3", "Can you bring up my Teams window?", "Okay, close the weather window" |
| Fixed command, target is "it" / "the window" from the last turn | 2 | "Perfect. Close it.", "Oh, that's fine. Close the window." |
| Fixed command, transcript garbled | 3 | "Go back to works, bit one.", "…the beat up and show me the load" (btop), "when you continue playing youtube…" |
| Needs the model (search, weather, maps, terminal work, compound) | 16 | "Can you show me today's weather please?", "Create a new folder called test and inside … start a new cloud session" |
| Not a command: repair, chatter, fragments, other languages | 37 | "Yes.", "Check again.", "Plus terminal.", "I will turn the light.", "Opened it." |
| "That did not go through." (realtime engine only, one session) | 23 | |
| `nothing usable` (rejected by `clean()`) | 18 | |
| Typed test | 1 | "say exactly: you are hearing Tarquin" |

So 32 of the 111 were commands, and 11 of those 32 could be answered without
the model from the words alone. Another 5 are the same kind of command but
need either the previous turn or a guess, which is exactly what a router must
not act on alone. 79 of 111 lines are not commands at all.

What the log does **not** show: a single volume, brightness, media-key, lock,
screenshot or theme command. The issue's examples ("turn the volume down") are
reasonable but unmeasured here; four days of use by one person is the whole
sample.

### What those 11 cost today

On the realtime engine (gpt-realtime, not Claude) they were already fast, heard
→ `action` in 0-2 s: workspace one at 11:53:21 → 11:53:21, Teams at 11:52:53 →
11:52:55, Discord to workspace 3 at 11:27:59 → 11:28:01. On the local engine,
heard → last spoken sentence, from the log:

| Utterance (local engine) | Heard → done | Model returns spoken |
| --- | ---: | --- |
| "Can you open up a new terminal?" (09-12 15:52:59) | 9 s | 2: "Opening a terminal now." / "Opened a new terminal." |
| "Open up a new terminal, please." (09-13 20:09:28) | 14 s | 4: first try "went through the wrong tool" |
| "What windows are opened on this desktop?" (15:52:35) | 2 s | 1, answered from the snapshot |
| "What windows do I have open on this desktop?" (18:32:11) | 1 s, **wrong** ("No windows open"); right after "Check again." at +23 s | stale snapshot, #69 |
| "Perfect. Close it." (18:33:15) | 4 s | 2 |
| "Go back to works, bit one." (20:14:43) | 5 s | 2 |
| "…continue playing youtube…" (20:15:11) | 13 s | 3 |
| "…the beat up and show me the load" (15:53:21) | 29 s | 4 |

The log does not record which tools the local engine called: across those
sessions it has only three `action` lines, none for the commands above, and
`trace_timings` is off by default (`config.py:395`), so continuations are
counted here from the spoken sentences, not measured. `usage … 'turns'` counts
user turns, not model returns (`claude_backend.py:780`).

What each fixed command would call, read off the realtime `action` lines and
the tool schemas in `tools.py`:

- workspace N → `hypr_dispatch focus {workspace: "N"}` (log 11:53:21)
- open terminal → `omarchy_cli "launch terminal"` (log 11:28:32, 20:09:31)
- focus an app → `hypr_dispatch focus {window: "address:…"}`, after finding the
  address among open windows (log 11:52:55)
- move an app to workspace N → `hypr_dispatch window.move {workspace, follow,
  window: "address:…"}` (log 11:28:01)
- close a named window → `hypr_dispatch window.close {window: …}`. The realtime
  model sent `hl.dsp.window.close()` with **no target** for "close the weather
  window" (log 12:01:22), i.e. it closed whatever had focus.
- list windows → no tool; the answer is the client list.
- volume, brightness, lock, screenshot, theme, nightlight → `omarchy_cli`
  (the tool's own description says so, `tools.py:727-733`).
- media play/pause/next → no tool does this. `playerctl` is installed, nothing
  in `src/` calls it; the YouTube case above cost three model returns.

### What there is to route to

- `omarchy commands --json`: 365 routes, 73 marked `requires_sudo`, each with
  `args` and a `summary`. `audio output volume <raise|lower|mute-toggle|+N|-N>`,
  `system lock`, `capture screenshot`, `theme set <name>`, `toggle nightlight`,
  `launch terminal`, `launch or focus <pattern> <command>` all exist.
- `hyprctl binds -j`: 4,946 binds, 271 with a description (262 distinct):
  "Volume up", "Mute", "Play", "Next track", "Lock system", "Screenshot",
  "Workspace 1 (all displays)", "Move window to workspace 1", "Close window". The
  `arg` of every described bind is an opaque Lua reference (`"7"`, `"10"`), not
  a command. The binds are a vocabulary of what this desktop does, not
  something that can be executed by name.

### What #70 already gives, and what it does not

PR #85 (`feat/70-find-what-is-installed`) adds `capabilities.find_apps()` and
`clear_match()`: a lexical score of an installed app against a name, and a
clear match only when one row scores ≥95 with nothing within 15 points.
`launch_app` resolves names through it. Run read-only against this machine:

| Name | `clear_match` | Top rows |
| --- | --- | --- |
| terminal | `preferred-terminal` | 100 preferred-terminal, 70 Alacritty |
| btop | `btop` | 100 btop |
| weather | `org.gnome.Weather` | 100 org.gnome.Weather |
| Discord | none | 100 omarchy-Discord, 100 discord |
| Teams | none | 88 QuickWebApps teams, 88 a Chrome app, 87 teams-for-linux |
| youtube | none | 100 omarchy-YouTube, 100 waydroid youtube |
| light | none | 89 Dying Light |
| the beat up | none | nothing |

Two things follow. It answers "what is installed", not "which open window",
and three of the 11 fixed commands (focus Teams, move Discord, close weather)
are about open windows. And a clear app match is not a clear intent: "today's
weather" clear-matches GNOME Weather, while the user got, and presumably
wanted, a spoken forecast. Whatever this issue builds must use #70's matcher
for apps rather than write a second one, and must not treat its answer as the
answer to the whole sentence.

## Proposed outcome

- The fixed commands the log shows ("switch to workspace N", "open a
  terminal", "what windows are open", "focus / move / close <app>") run
  without a model turn when the words leave exactly one reading, and are heard
  as done in well under the 9-14 s they take today.
- The same holds for the desktop's own fixed features (volume, media, lock,
  screenshot, nightlight, theme) once they are measured to occur.
- Anything with more than one reading, a reference to the last turn, a garbled
  word, or no match goes to the model exactly as today, optionally with the
  candidates found so the model does not look them up again.
- A misfire is rarer than today, not commoner: 79 of 111 heard lines were not
  commands, and some contain command words ("Plus terminal.", "I will turn the
  light.", "Opened it.").
- There is a replayable eval over the real `heard` lines that says, per line,
  what the router would do, so a change to it is judged by numbers.

## Affected users and systems

- Everyone on `[realtime] engine = "local"`, the default:
  `local_engine.py` (`_answer`, `LOCAL_PERSONA`) and `claude_backend.py`
  (`WarmBrain`).
- `tools.py` `Executor`, which every route must still go through, and
  `capabilities.py` once #70 lands.
- Possibly the realtime engine and typed `say`, which share the `Executor`;
  whether they get the router is an open question. #77 notes the three engines
  already carry three copies of the same machinery.
- Not the MCP server: there the model is the caller's, and the caller already
  chose to spend a turn.

## Constraints

- **Never act on a guess.** Only an exact or alias match whose every argument
  is in the words runs directly. Lexical similarity alone never runs
  anything; this is the rule nixarchy-menu's Smart Match already states
  ("Results never run automatically").
- **Negation, direction and toggles are part of the match.** "Don't close
  it", "turn it down" and "turn it up" must never meet the same route.
  `toggle nightlight` and `audio output volume mute-toggle` are toggles: a
  router must not claim "off" when it can only flip.
- **The policy gate is not bypassed.** A routed command goes through
  `Executor.call` → `_call_locked` (`tools.py:1622`, `1637`), so
  `policy.check(description)` (`tools.py:1643`) and the confirm hold still
  apply, including the defaults in `config.py:108` (confirm) and `:132` (deny).
  A route that would be held must be held, and said so, not run.
- **Build on #70.** App names go through `find_apps` / `clear_match`; no second
  app matcher. Windows go through the existing window matching
  (`tools.py:600` `_window_matches`, `:2792` `_resolve_window`).
- **The model still hears about it.** The warm session must know what the
  router did, or the next "close it" refers to something the model never saw.
- **Measured, not asserted.** The trace in PR #81 starts when the user stops
  talking; "faster" for this issue is read off that trace and off the eval, not
  estimated.
- No new runtime dependency without the eval showing lexical matching falls
  short. `rapidfuzz` is in nixpkgs; `difflib` is already used (`tools.py:309`,
  `keys.py:23`, and #70).

## Open questions

1. **Where does the router sit?** (a) In front of the model: a hit runs and is
   spoken, a miss goes to Claude unchanged; fastest, but the warm session must
   be told what happened. (b) Inside the turn only: candidates are prepended to
   the user's text and Claude still decides; safer, but still one model return
   per command. (c) Both: (a) for exact hits, (b) for near ones, as the issue
   comment proposes.
2. **Which commands first?** The log supports four: workspace N, open
   terminal, list windows, focus/move/close a named open window. Volume, media,
   lock, screenshot and theme are absent from the log. Ship only what was
   measured, or include the desktop's fixed features on the strength of the
   bind descriptions?
3. **A learned cache of transcript → tool calls from verified successes, now or
   later?** It would catch repeats the phrase table misses, but it learns from
   one person's four days and needs a definition of "verified".
4. **Which engines?** The realtime engine already did these in 0-2 s. Local
   only, or local and typed `say`?
5. **What is spoken for a routed command?** Nothing but the result ("Workspace
   one."), or the same announce-then-report pair the persona asks of the model?
6. **Does the eval gate the change?** If so, what bar: e.g. zero actions on the
   79 non-command lines, and at least the 11 fixed commands routed?
