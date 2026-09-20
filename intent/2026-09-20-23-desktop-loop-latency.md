---
status: approved
issue: 23
author: olafkfreund
---

# Intent: the desktop loop is slow in the wrong places

## Problem

Driving the desktop by voice still feels slow, and the working assumption has
been that the cost is in finding windows and moving around workspaces — that
every action re-execs `hyprctl`, that new windows are found by polling, that
there is no persistent compositor connection. All of that is true. None of it is
where the time goes.

Measured on this machine, Hyprland 0.56.0, quickshell 0.3.1, 2560x1440:

| Operation | Measured |
|---|---|
| `hyprctl -j clients` (one fork) | 13 ms |
| same query over raw `.socket.sock` | 3.5 ms |
| `grim -g 2560x1440 -` (PNG) | **962 ms** |
| same capture, `grim -t ppm` | **42 ms** |
| `grim \| tesseract`, full screen | **3.97 s** |
| `grim \| tesseract`, 800x600 region | 0.84 s |
| `Atspi.get_desktop(0)` | 5 ms |
| Claude warm brain, to first spoken sentence (`tools/bench_local.py`) | 1.5–7.6 s |

Finding a window costs 13 milliseconds. Reading the screen costs four seconds,
and roughly one of those is PNG compression on an image that is piped straight
into tesseract and thrown away — `read_screen` and `click_text` both call plain
`grim -g <geom> -` (`tools.py:1702`, `tools.py:1796`), and neither needs a PNG.

Above both of those sits the model. Every additional tool round trip is a whole
turn, 1.5–7.6 s. A window-lookup miss is not a 13 ms miss; it is a `hypr_query`,
a model turn and a retry. `_window_matches` (`tools.py:337-347`) is a lowercase
substring over four concatenated fields, so "chat" does not find
"Discord - #general".

There is also a fixed cost nobody measured into: an unconditional
`time.sleep(2.0)` before every web page read, and a second 2.0 s on retry
(`WEB_RENDER_SETTLE`, `tools.py:2583` and `2591`).

So the honest statement of the problem is: **the expensive things are OCR and
model turns, and the cheap thing is the Hyprland IPC we were about to rebuild.**
An in-memory client table fed from `.socket2.sock` would save about 9.5 ms per
query against a turn that costs seconds, while taking on cache invalidation
against an event stream that (in v0.56.0's `EventManager.cpp`) truncates event
data at 1024 bytes, disconnects clients at 64 queued events, and carries no
sequence numbers to detect the gap.

Two independent reviews, Codex (OpenAI, `gpt-6-astra`) and Gemini (Antigravity,
model not reported), were asked to attack this ranking. Both agreed with it.
Codex additionally showed that the numbers above are component benchmarks, not
whole-task traces, and that the repo has never measured a task end to end.

## Proposed outcome

- A per-phase trace exists for whole tasks, so every later change is argued
  against p50/p95 task completion, dependent model continuations and
  wrong-target rate — not against a microbenchmark.
- Reading the screen no longer spends a second compressing an image that is
  immediately discarded.
- OCR looks at the window being asked about rather than the whole monitor.
- Naming a window the way a person would name it finds it, and an ambiguous name
  comes back as a choice rather than a guess.
- Nothing sleeps for a fixed two seconds.
- Waiting for a window wakes on the compositor saying so, rather than asking
  every 150 ms — while `hyprctl` stays the authority for anything consequential.

## Affected users and systems

- Everyone using `read_screen`, `click_text`, `wait_for`, `web_search`,
  `open_page` and `compose_windows` — the whole OCR and window-waiting surface.
- `src/omarchy_voice/tools.py` (the capture helpers, `_window_matches`,
  `_await_new_window`, `wait_for`, `_read_web_window`) and
  `tools/bench_local.py`.
- Both engines and all four entry shapes, since they share one `Executor`.
- Hyprland 0.56 `.socket2.sock`, if the wake-up listener is adopted.

## Constraints

- Must not trade accuracy for speed silently. PPM is larger through the pipe,
  and `--dpi 300` overrides input resolution metadata rather than rescaling the
  bitmap — dropping it is an OCR-quality experiment, not a free win. Any change
  to the capture path is judged on OCR output over the same frames, not only on
  the clock.
- Must not let ranked matching become authority. An ambiguous name must return
  the candidates, never silently act on the best-scoring window. The gate is
  regex-over-a-description, so a wrong target is a wrong description.
- Must keep "unknown" distinct from "empty". Related: `_query_json`
  (`tools.py:2057-2062`) already collapses a failed `hyprctl` into `[]`, which
  `wait_for(what="window_gone")` (`tools.py:2824`) reads as success.
- A compositor event means "recheck", never "it happened". `hyprctl` stays
  authoritative for every consequential read, and the listener must never need
  `Executor._lock` while a tool holds it waiting on an event.
- No new runtime dependency without justifying it against the wrapped tool list
  in `nix/package.nix:63-83`.
- `tests/` is plain unittest with `Executor._shell` as the seam. New code routes
  external commands through it.

## Out of scope, deliberately

Each of these was considered and set aside, with the reason recorded so it is
not re-proposed:

- **A full `.socket2.sock` state cache.** ~9.5 ms saved per query against a
  1.5–7.6 s turn, in exchange for cache invalidation against a lossy,
  unsequenced event stream. Only the wake-up use survives.
- **Compositor-side Lua batching.** It cannot make an external application map
  synchronously, and blocking inside Hyprland stalls the event processing the
  operation is waiting for. It would also widen the issue #22 bypass.
- **Two-way quickshell IPC.** Worth doing for disambiguation UI, but that is
  capability work, and `HyprlandToplevel.lastIpcObject` is documented stale
  until refreshed, so it is not a freshness shortcut.

## Follow-on, not planned here

To be filed separately once the tracing exists, because the traces decide the
shape:

- **Coarser composite tools** (`ensure_window_focused`, `interact_element`) to
  cut dependent model turns. Both reviewers called this the highest-leverage
  change for perceived latency. It depends on issue #22 landing first, so a
  composite tool's `describe()` can be trusted.
- **AT-SPI-first element interaction**, reusing `ai-mirror`'s
  `src/ai_mirror/a11y.py`. The desktop root answers in 5 ms, but coverage here is
  currently thin: Chrome exposed 11 nodes without `--force-renderer-accessibility`,
  quickshell and Qt apps exposed 0 children with `QT_ACCESSIBILITY` unset, and
  terminals expose nothing.

## Open questions

1. **Sequencing.** Does the tracing (step 0) land and get looked at before the
   capture changes, or do the two one-line `grim` fixes go first because they
   are unambiguous and the trace then measures the improved baseline?
2. **One issue or several?** Six items in one chain is a large spec. The capture
   changes, the matching change and the wait/event change are independent.
3. **Where the trace output lives.** `tools/bench_local.py` is run by hand.
   Should the per-phase timings also be emitted into `session.log` behind a flag,
   so a slow turn in real use can be explained after the fact?
