---
status: approved
issue: 73
intent: intent/2026-09-24-73-read-before-ocr.md
---

# Spec: ask the media player before photographing it, and keep every guard fresh

## The intent's open questions, answered

The intent was approved without answers to its six questions, so each one is
decided here. The factual ones were measured again today, read-only, on this
desktop. Any decision can be rejected at this gate.

The measurements change the spec's shape. **AT-SPI is scoped out, and so is
guard caching.** What ships is MPRIS (a read and a control), plus one
regression test that pins the guard freshness this spec argues for. The
reasons follow.

### Q1: yes, AT-SPI is still blocked. It is scoped out of this issue.

The intent's premise was that #31's blockers are now closed. They are, but
they were never what blocked Chrome:

```
$ gh issue view 821 -R olafkfreund/nixarchy   CLOSED 2026-09-23  A graphical microvm template, so desktop tooling can be tested without a real session
$ gh issue view 823 -R olafkfreund/nixarchy   CLOSED 2026-09-20  The VMs have no accessibility bus, so nothing AT-SPI can be tested in them
$ gh issue view 31                            CLOSED             Try the accessibility tree before OCR in click_text — but it needs the desktop to expose one first
```

Both are about **VM test infrastructure**. Neither makes Chromium build a web
tree. The Chrome that is running now was checked again:

```
$ pgrep -ax chrome | grep -v -- --type=      -> pid 301556, google-chrome-153.0.8010.52
$ tr '\0' '\n' < /proc/301556/cmdline | grep -c accessib
0
$ busctl --user get-property org.a11y.Bus /org/a11y/bus org.a11y.Status IsEnabled ScreenReaderEnabled
b true
b true
```

The bus is on and Chrome still has no `--force-renderer-accessibility`, so
the intent's census holds. Chrome exposes frames only, and 19 of the 28
logged screen actions were in Chrome. The intent counted **zero** of the 28
as answerable by AT-SPI on this desktop as configured. A reader, a role
normaliser, window-origin arithmetic and a new privacy walk would all be
built for Nautilus, which does not appear in the log.

**Decision:** no AT-SPI code, no source router and no fallback chain in #73.
The revisit condition is concrete: **nixarchy launches Chromium (and the
Chrome web apps) with `--force-renderer-accessibility`.** When this spec is
approved, a follow-up issue should be opened that names that condition and
carries the constraints the intent already wrote down (window-relative
bounds, non-ATK role names, empty is unknown, no bus writes). This spec does
not open it.

The intent's proposed order (tmux → clipboard → AT-SPI → MPRIS → OCR) goes
with it. tmux already answers terminal questions: 14 logged actions, none of
them through OCR. The clipboard holds what was copied, not what is on
screen, so it is not a screen source.

### Q2: nobody enables AT-SPI. A future reader reads only when the bus is already on.

This package does not set `toolkit-accessibility`, `QT_ACCESSIBILITY` or the
bus flags, and neither does its HM module. A voice query must never write
bus state (that is an approved constraint), and turning on a desktop-wide
accessibility bus is nixarchy's decision, not a voice assistant's. Why the
bus is on here today is still unexplained. That does not block #73, because
#73 no longer reads it.

### Q3: in-repo, for when AT-SPI returns. Not the ai-mirror CLI.

This is recorded for the follow-up and is not built here. The `ai-mirror`
CLI would bring three things this repo has ruled out: it writes `IsEnabled`
and `ScreenReaderEnabled` on first read (`ensure_enabled`, ai-mirror
`a11y.py:77-107`), it filters with its own privacy list rather than
`sensitive_patterns`, and it has an ownership gate that means nothing to a
voice query. Importing `a11y.py` skips the gate but keeps the other two. A
small in-repo reader over PyGObject, with the Atspi typelib in the wrapper,
is what the follow-up should spec.

### Q4: neither per-call nor a TTL. Guard answers are not shared. Measured, and here is why.

A scratch harness (not repo code) counted every `Popen` during
`click_text("screen")` on this desktop. `_dispatch` and `_press_button` were
stubbed, so the pointer never moved. It then did the same with a prototype
that memoised `_query_rows` for the length of one look (the memo cleared at
each `grim`):

```
today:          processes=19 in 1554 ms
   2 x grim   4 x hyprctl -j clients   7 x hyprctl -j monitors
   2 x omarchy-shell lock isLocked     2 x pw-dump   2 x tesseract
today (_target_moved alone):          7 processes, 402 ms
memo per look:  processes=12 in 1599 ms
   2 x grim   2 x clients   2 x monitors   2 x lock   2 x pw-dump   2 x tesseract
memo per look (_target_moved alone):  6 processes, 327 ms
```

That reproduces the intent's 19. It also shows why the intent's "19 → 8"
cannot be had safely:

1. **The re-check's guards are the only fresh ones before the click.**
   `_input_refused` runs at `tools.py:2703`, *before* the full OCR at `2706`.
   By the time the pointer moves, its answer is 0.7–5 s old. The one
   window-list read dated after that gap is the one `_ocr_words` makes inside
   `_target_moved` (`2665`, called at `2725`). If a vault opens over the
   target during the OCR, that fresh `_capture_refused` is what stops the
   click. Sharing the lock, `pw-dump`, `/proc` or `clients` answer across the
   two reads, which is the only way to reach 8, hands the click a "safe"
   answer that is seconds old. That is the exact case the constraint
   forbids.
2. **Even inside one look, sharing makes the seam older.** In the prototype,
   `_capture_refused`'s window check reused the `clients` answer that
   `_input_refused` fetched before the 93 ms lock check. So the check that
   decides whether pixels are taken was reading an answer about 100 ms older
   than it does today. The measured 19 → 12 includes that.
3. **What is strictly safe is small.** Answers could be shared only where
   no other work sits between the two reads. That means the pre-seam
   `monitors`/`clients` pair, and the `monitors` read that `_screen_unavailable`
   and `_visible_workspaces` both make, back to back, inside the seam. That
   gives 19 → 14 on `click_text` and 8 → 7 on `read_screen`. It saves about 73 ms
   on a call measured at 1.5 s here and 5+ s on a dense window, and 15 ms on a
   read. That is too little to justify adding state to the guard path.
4. **A TTL is worse.** `wait_for(text)` polls every 0.6 s (`WAIT_POLL_TEXT`,
   `tools.py:501`) plus an OCR, so a 2 s TTL would carry "no vault visible"
   straight across a poll in which one appeared.

**Decision:** the guard chain is unchanged. The cost is the OCR, not the
guards (the intent's own table puts the guards at 0.18 s of a 0.7–5 s read),
and the only way to make OCR cheaper is not to do it. That is what the MPRIS
part does. **This departs from an approved outcome bullet** ("one tool call
pays for each guard at most once"). If the approver still wants the strictly
safe 19 → 14 version, it is point 3 above. It fits in `_query_rows` as a memo
scoped by a context manager. The capture seam always opens a fresh scope.

What the spec adds instead is **one regression test** that fails if a later
change ever shares a guard answer across the two reads (Verification).

### Q5: no. The recording guard applies to frames, and an MPRIS read has none.

The guard's stated reason is that "the frame would land in that recording"
(`tools.py:2282-2291`). It already fails open by design. A spoken answer can
be recorded, but that is true of `system_query`, `read_terminal` and every
other tool, and none of them check. **Lock and sensitive-window checks do
apply** to the new read (Design §2). Their reasons, a voice at a locked
machine and a private title, survive the loss of the frame.

### Q6: yes, ship it now, kept small.

The log says media is more than "once". **9 of the 28 screen actions were
Spotify** (3 reads, 4 clicks, 2 waits). The one media sequence
(`session.log` 2026-09-12 15:29:48–15:31:29) ended with the model saying
"Playing the Psychedelic Space and Stoner playlist now" when nothing was
playing. The user had to say so, and it took a `read screen` and a
`press space` to fix. With a 7 ms `playerctl status`, the claim is checked
instead of photographed. The part of that sequence MPRIS cannot do (search,
pick a playlist) stays with the screen, and that is fine.

Measured today, read-only:

```
$ playerctl -l                     -> chromium.instance301556
$ playerctl -a --format '{{playerName}}\t{{status}}\t{{artist}}\t{{title}}' status
chromium	Stopped		                      (rc 0; `-a metadata` fails on this metadata-less player, `status --format` does not)
$ playerctl -p nonexistent status  -> "No players found", rc 1
```

## Design

Three changes to code, one to packaging, and one test. There are no new
modules and no change to `trace.py`.

### 1. `playerctl` joins the wrapper

`nix/package.nix`: add `, playerctl` to the argument list (next to `, tmux`,
line 14) and `playerctl` to `runtimeInputs` (`63-87`), with a one-line comment
saying the media tools shell out to it. `flake.nix:28,53` use `callPackage`,
so nothing else changes. It is on the system PATH here today but not in the
wrapper, so without this line the tool would fail on a host that does not
happen to have it installed.

### 2. `system_query(topic="media")`: the read

`SYSTEM_QUERIES["media"] = lambda executor: executor._media_status()`, beside
`battery` (`tools.py:1324`). `media` is added to the schema's `enum`
(`tools.py:1203-1204`). The description (`1193-1197`) gains "what is playing".
`system_query` is already in `READ_ONLY_TOOLS` (`57`), so it runs under
`--dry-run` and needs no new tool.

`Executor._media_status()`, in order:

1. `if self._session_is_locked():` refuse. The reply says the session is
   locked, so what is playing was not read. This is the approved constraint.
   It is the same 93 ms call `_screen_unavailable` makes (`2487-2502`).
2. If `playerctl` is not on PATH, return `install_hint("playerctl", "playerctl")`.
3. `self._shell(["playerctl", "-a", "--format", "{{playerName}}\t{{status}}\t{{artist}}\t{{title}}", "status"], timeout=4)`.
   It goes through `_shell`, so tests fake it and the trace records a
   `subprocess` span named `playerctl`. That span is how the trace shows which
   source answered (see §5). "No players found" gives `Result(True, "no media
   player is running, so nothing is playing")`. Any other failure is an
   error that quotes playerctl's first line.
4. For each row, the title is **withheld** (the reply keeps player and status,
   and names the category, never the title) when either of these holds:
   - `self._sensitive_kind(player, f"{artist}\n{title}")` matches. This is
     the #46 matcher with this repo's `sensitive_patterns`.
   - The player is a browser (`_BROWSER_PLAYER = re.compile(r"chrom|firefox|brave|vivaldi|msedge|librewolf", re.I)`,
     marked `ponytail:` as a tuning knob), **and** `_query_rows("clients")`
     either fails or contains any window, on any workspace, for which
     `_sensitive_kind` matches. A browser's MPRIS player can be fed by any of
     its tabs, including a private one that is not on screen, and a hidden
     window can still play audio. So this check looks at every open window,
     not only the visible ones. It fails closed, like #51.
5. The reply is one line per player: `spotify: Playing — Soundgarden – Black Hole Sun`.

Recording is not checked (Q5). It costs about 100 ms (lock and playerctl),
plus 15 ms when a browser is playing, against 0.7–5 s for OCR.

### 3. `media_control(action)`: play, pause, play-pause, next, previous

A new schema is added after `system_query` (`tools.py:1191-1210`): *"Play,
pause, skip or go back in whatever media player is active: Spotify, a video
in the browser. It needs no window and no screen read. The answer says what
the player reports afterwards, so do not claim it is playing unless it
says Playing."* `action` is an enum of the five.

- `_validate_media_control(action)` returns an error for anything else. It
  runs under `--dry-run` like every other validator (`1661-1670`).
- `describe()` (`1802`, beside `system_query`): `media {action}`.
- It is **not** in `READ_ONLY_TOOLS`, because it changes state. It is **not**
  in `INPUT_TOOLS` either, because it sends no key or pointer event, and
  `test_input_guard`'s marker scan (`tests/test_input_guard.py:28-29`)
  agrees, since it uses `_shell` only.
- `_tool_media_control`: `playerctl <action>` through `_shell` (with
  playerctld running, playerctl's default player is the one most recently
  active). "No players found" gives `Result(False, "no media player is
  running, so there is nothing to <action>. Open the music in its app
  first.")`. Then it reads `playerctl status` every 0.1 s for up to 1 s,
  until the status is the expected one: `Playing` for play, `Paused` for
  pause, and different from the status read before the action for
  play-pause. Next and previous do one read. The reply is `sent play; the
  player now reports Playing`, or, when the expected status never came, `sent play, but the
  player still reports Paused, so it may not have taken it`. This closes the
  2026-09-12 false "Playing" (#24).
- It has no lock check. That matches `launch_app`, and the reply carries a
  status word, never a title.

### 4. `read_screen` points media questions away from the screen

One sentence is appended to `read_screen`'s description (`tools.py:960-966`):
*"For what is playing, or to play and pause, use system_query media and
media_control, not the screen."* This is the whole of "media questions do not
photograph Spotify". It needs no router, because the model picks the tool.

### 5. The trace: no change

The intent wants the trace to show which source answered. It already does.
Each tool call is a `tool` span with the tool's name (`tools.py:1630`), and
every `_shell` child is a `subprocess` span named by `cmd[0]` (`1833-1835`).
So a media answer shows as `playerctl` and a screen answer as `grim` and
`tesseract`. A "fallback rate" needs a fallback chain, and Q1 removed it.
The AT-SPI follow-up adds its own phase if it adds one.

### 6. The guard regression test

`tests/test_matching.py`, in `ClickStalenessTests` (`524`): **a vault that
opens between the read and the re-check stops the click.** Fake `_shell`
(lock false, `monitors` fixed), fake `pw-dump`, and patch `subprocess.run`
for `grim`/`tesseract` to return one TSV with the target word. The
`clients` answer starts with an ordinary window and switches to the `VAULT`
fixture at the first `grim`. The test asserts that `_dispatch` was never
called and that the reply names a password manager. It fails on any change
that reuses the first read's guard answers for the re-check, which is
exactly what Q4 rejects.

## Alternatives rejected

- **An AT-SPI reader now, in-repo or through ai-mirror.** It would answer 0
  of 28 logged actions on this desktop (Q1). The approved constraints (no bus
  writes, window-relative bounds, non-ATK roles, a new privacy walk) are all
  cost with no measured return until Chromium carries the flag.
- **A source router (tmux → clipboard → AT-SPI → MPRIS → OCR).** With AT-SPI
  out, only MPRIS is new, and one sentence in a tool description routes it.
  A router would also have to decide what counts as a "media question",
  which the model already does.
- **Per-call guard memo (19 → 12, measured) or the intent's 19 → 8.** Either
  one lets the re-check, the only guard dated after the OCR gap, see a
  seconds-old "safe" answer (Q4.1–2).
- **A ~2 s TTL across calls.** It carries "safe" across a `wait_for` poll
  (Q4.4).
- **Strictly safe sharing (19 → 14).** It is safe, but it saves about 73 ms of
  1.5–5 s and adds state to the guard path (Q4.3). The approver can take it
  instead.
- **One `media(action)` tool with `status` among the actions.** It could not be
  in `READ_ONLY_TOOLS`, so "what's playing" would stop working under
  `--dry-run`, which is how `say -n` and the planner look. `system_query`
  already is the read-only fact tool.
- **Media keys through `send_shortcut`.** Omarchy binds `XF86AudioPlay` etc.
  (checked with `hyprctl binds`), but `send_shortcut` sends the key to a
  window and skips the compositor's binds. It also sits behind the input
  guard and reports nothing about what happened.
- **`python-dbus`/`dbus-next` for MPRIS.** That is a Python dependency to replace a
  7 ms process that is already packaged.

## Risks

- **A private title leaks through MPRIS.** Browsers are covered by the
  any-sensitive-window rule. A non-browser player is covered only by
  matching its own title. The failure mode is the same as #46's: it matches
  on class and title, so it misses things and it misfires. The browser regex
  is a tuning knob.
- **Over-withholding.** With any sensitive window open, "what's on YouTube"
  gets "Playing", with the title withheld. That errs on the side the
  intent's constraints ask for.
- **playerctl picks the wrong player** when several are active. playerctld
  follows the most recently active one, and the status reply says which
  player it read, so the model can see a mismatch.
- **The status poll adds up to 1 s** to a control call when the player is
  slow to report. That is bounded, and it is still far below a
  `read_screen`.
- **Declining the "each guard at most once" bullet** is a departure from
  the approved intent. It is argued in Q4 and can be reversed at this gate.
- **Merge overlap in `tools.py`** with open PRs, described below so the plan
  can sequence them. There is no semantic conflict.
- **Hosts:** the user service on this machine. The only packaging change is
  `playerctl` in the wrapper. No HM module change, and no desktop setting is
  touched.

### Overlap with open PRs

- **#85 `feat/70-find-what-is-installed`** edits `tools.py`: `READ_ONLY_TOOLS`
  (`57-58`, adds `find_app`), `TOOL_SCHEMAS` (a `find_app` schema before
  `launch_app`, about `762`), `_call_locked` (`1659`, name resolution before
  `describe`), `describe()` (about `1782`, adds `find_app`) and `_resolve_app` after
  `_validate_launch_app`. This spec touches `describe()` near `system_query`
  (`1802`) and adds a schema after `system_query` (`1191`). Those are
  different hunks in the same function and list, so a rebase conflict is
  possible but mechanical. This spec does not touch `READ_ONLY_TOOLS`. Land
  after #85 and rebase.
- **#81 `feat/72-listen-faster`** adds two phases to `trace.py`. This spec
  does not touch `trace.py` (§5). No overlap.
- **#78 `fix/69-snapshot-per-turn`** touches `capabilities.py`,
  `claude_backend.py` and `planner.py`. None of those are touched here.
- **#84 (branch `perf/84-tools-not-deferred`, no PR yet)** is about how the
  tool list reaches Claude Code. A new `media_control` schema is one more
  entry for it to carry. The plan should check that branch's state at
  sequencing time.

## Verification

- **Unit** (`pytest tests -q` in `nix develop`). Plain unittest, with
  `Executor._shell` as the seam, in a new `tests/test_media.py`:
  - status: locked → refused, and playerctl is never called. No player gives
    "nothing is playing". A Spotify row prints player, status, artist and
    title. A player whose title matches a sensitive pattern gets its title
    withheld, and the reply does not contain it. A chromium row while a
    `1Password` window is open on a hidden workspace is withheld. A chromium
    row while `clients` fails is withheld (fails closed). A chromium row with
    only ordinary windows is shown. A missing playerctl gives the install
    hint.
  - `media` is in the `system_query` enum and in `SYSTEM_QUERIES`.
  - control: `play` then `Playing` gives "now reports Playing". `play` then
    `Paused` throughout gives "may not have taken it", and the poll gives up
    by 1 s (clock faked). An unknown action is refused by the validator, also
    under `dry_run`. No player gives the "nothing to play" error.
    `describe("media_control", {"action": "pause"}) == "media pause"`.
  - `media_control` is not in `READ_ONLY_TOOLS` and not in `INPUT_TOOLS`.
    The existing marker scan still passes. It appears in the MCP tool list.
  - `read_screen`'s description names `system_query media`.
  - §6: the vault-between-reads test in `test_matching.py`.
- `nix flake check --no-write-lock-file` passes, and `nix build` puts
  `playerctl` on the wrapper's PATH:
  `grep -o '[^:]*playerctl[^:]*' result/bin/omarchy-voice`.
- **Live, read-only:**
  - `omarchy-voice -n say --no-confirm "what's playing?"` gives one
    `system_query(media)` call and no `read_screen`. The answer names the
    chromium player as Stopped, or whatever is registered at the time. The
    trace shows a `playerctl` subprocess and no `tesseract`.
  - `omarchy-voice -n say --no-confirm "pause the music"` gives a
    `[dry-run] would run: media pause`, and nothing is sent to the player.
  - A real `media_control` is **not** run in verification, because it would
    change the desktop. The approver runs it by hand if wanted.
