---
status: approved
issue: 73
spec: spec/2026-09-24-73-read-before-ocr.md
---

# Plan: ask the media player before photographing it, and keep every guard fresh

## Approved decisions, carried over from the spec

1. **AT-SPI is scoped out of #73.** There is no AT-SPI code, no source router
   and no fallback chain. Chrome, where 19 of the 28 logged screen actions
   happened, still runs without `--force-renderer-accessibility`, so AT-SPI
   would answer 0 of 28 here. The intent's order (tmux → clipboard → AT-SPI →
   MPRIS → OCR) is dropped with it. The revisit condition is concrete:
   nixarchy launches Chromium and the Chrome web apps with
   `--force-renderer-accessibility`.
2. **Nobody enables AT-SPI.** Neither this package nor its HM module sets
   `toolkit-accessibility`, `QT_ACCESSIBILITY` or the bus flags. A voice query
   never writes bus state.
3. **A future AT-SPI reader is in-repo, not the ai-mirror CLI.** It would be a
   small PyGObject reader with the Atspi typelib in the wrapper. That belongs
   to the follow-up and is not built here.
4. **Guard caching is dropped. This deliberately departs from the approved
   intent outcome "one tool call pays for each guard at most once".** The
   guard chain is unchanged: no per-call memo, no TTL, and not the "strictly
   safe" 19 → 14 sharing either. The re-check in `_target_moved` is the only
   guard answer dated after the OCR gap, so sharing any answer across the two
   reads hands the click a "safe" answer that is seconds old. A TTL would
   carry "no vault" across a `wait_for` poll. The strictly safe version saves
   about 73 ms of 1.5–5 s and adds state to the guard path. The cost is the
   OCR, and the fix for that is not doing it, which is what MPRIS does.
5. **One regression test pins guard freshness.** If a vault opens between the
   read and the re-check, the click is stopped.
6. **The recording guard does not apply to an MPRIS read**, because there is
   no frame. The lock and sensitive-window checks do apply.
7. **MPRIS ships now, kept small.** 9 of the 28 logged screen actions were
   Spotify, and the 2026-09-12 sequence ended with a false "Playing".
8. **Packaging:** `playerctl` joins the wrapper's `runtimeInputs`. There is no
   HM module change and no Python dependency (no `dbus-next`).
9. **Read: `system_query(topic="media")`.** It is not a new tool, and
   `system_query` is already in `READ_ONLY_TOOLS`, so it works under
   `--dry-run`. `_media_status()` does these in order: refuse if locked, give
   an install hint if `playerctl` is missing, then run one
   `playerctl -a --format '{{playerName}}\t{{status}}\t{{artist}}\t{{title}}' status`
   through `_shell`. "No players found" means nothing is playing. It prints one
   line per player. A title is **withheld** (the player and status are kept,
   and the category is named, never the title) when `_sensitive_kind(player,
   artist\ntitle)` matches, **or** when the player is a browser
   (`_BROWSER_PLAYER`, a `ponytail:` tuning knob) and `clients` either fails
   (fail closed) or has a sensitive window **on any workspace**.
10. **Control: a new `media_control(action)` tool**, where action is one of
    `play`, `pause`, `play-pause`, `next` or `previous`. It has a validator
    (which runs under `--dry-run`), `describe()` returns `media {action}`, and
    it sends `playerctl <action>` through `_shell`. Then it polls
    `playerctl status` every 0.1 s for up to 1 s until the status is the
    expected one: `Playing` for play, `Paused` for pause, and different from
    the pre-action read for play-pause. Next and previous do one read. The reply
    reports what the player says, not what was hoped for. "No players found" is
    an error that says to open the music first. There is no lock check (like
    `launch_app`), and the reply carries a status word, never a title. It is
    in neither `READ_ONLY_TOOLS` nor `INPUT_TOOLS`.
11. **Routing is one sentence** appended to `read_screen`'s description. It
    points "what is playing" and play/pause at `system_query media` and
    `media_control`. There is no router.
12. **No trace change.** The `tool` span name plus `_shell`'s `subprocess`
    span (`playerctl` against `grim` and `tesseract`) already show which
    source answered.
13. **Rejected:** AT-SPI now, a router, a per-call memo or 19 → 8, a TTL, the
    strictly safe 19 → 14, one `media(action)` tool with `status` among the
    actions (it would break the `--dry-run` read), media keys through
    `send_shortcut`, and a Python D-Bus library.
14. **Landing order:** after #85 (`feat/70-find-what-is-installed`), then
    rebase. #85 edits the same schema list and `describe()` in `tools.py`.
    #81 and #78 do not overlap. #84 needs no change for this: its spec loads
    the whole `omarchy` server per server with `alwaysLoad`, so
    `media_control` is simply one more always-loaded tool.

Details the spec left open, decided here. Each one only narrows behaviour:

- **`_shell` strips its output** (`tools.py:1873` on `main`), so the last
  row's empty artist and title tabs are gone. Measured live, it returns
  `chromium\tStopped`. Rows are padded:
  `(line.split("\t") + ["", "", ""])[:4]`.
- **The withheld wording.** When the category is known:
  `chromium: Playing — title withheld (a private browsing window is open)`.
  When `clients` failed: `… title withheld (the window list could not be read)`.
- **`media_control` also returns `install_hint("playerctl", "playerctl")`**
  when `playerctl` is missing. The spec states this only for the read.
- **play-pause pre-read.** If it says "No players found", the call gets the
  nothing-to-do error and no action is sent. Any other pre-read failure sends
  the action, does one status read, and reports it without claiming a change.
- **The lock check inherits `_session_is_locked`'s fail-open**
  (`omarchy-shell` absent → not locked). That is unchanged from the screen
  path.
- **The regression test's fakes.** `tools.shutil.which` is patched so
  `grim`, `tesseract` and `omarchy-shell` resolve. `_recorded_by_process` is
  stubbed to `None` so a real recorder on the host cannot fail the test. The
  recording guard is not what the test is about, and `pw-dump` is still faked
  through `subprocess.run` as the spec says.

## Line references

`tools.py` lines are cited **as they will be after #85**, read with
`git show feat/70-find-what-is-installed:src/omarchy_voice/tools.py` and
labelled *(feat/70)*. After the rebase, re-check them with `grep -n`, because
#85 may change again before it merges. The other files are the same on `main`
and `feat/70`.

## Steps

0. **Baseline.** `nix develop -c pytest tests -q` → record the pass count
   (792 passed on this branch today, which is `main` plus docs. After #85 it
   is 792 plus #85's tests).

1. **Precondition: #85 is merged, and this branch sits on it.**
   `gh pr view 85 --json state,mergedAt` → it must be `MERGED`. **If it is not
   merged, stop and report. Do not implement against `main` or `feat/70`.**
   If it is merged: `git fetch origin && git rebase origin/main` (docs-only
   commits, so no conflict is expected), then re-run step 0 and record the new
   count. Also check `gh issue view 73` (it must be OPEN) and
   `git branch --show-current` (it must be `feat/73-read-before-ocr`). → verify
   by `git merge-base --is-ancestor origin/feat/70-find-what-is-installed HEAD`,
   or by `grep -n '"find_app"' src/omarchy_voice/tools.py` returning the
   READ_ONLY_TOOLS line.

2. **`nix/package.nix`: `playerctl` in the wrapper.** Add `, playerctl` to
   the argument list after `, tmux` (:14). Add `playerctl` to `runtimeInputs`
   (:63-87) after `tmux` (:76), with the comment
   `# MPRIS: system_query(media) and media_control shell out to it (#73).`
   `flake.nix:28,53` use `callPackage`, so nothing else changes. → verify by
   step 10's `nix build` plus the grep of the wrapper.

3. **`src/omarchy_voice/tools.py`: `media` topic for `system_query`.**
   - Schema *(feat/70)* :1212-1231. Append `"media"` to the `enum`
     (:1224-1225). Change the description (:1214-1218) to name "what is
     playing" among the topics.
   - Registry: `SYSTEM_QUERIES["media"] = lambda executor: executor._media_status()`
     next to `SYSTEM_QUERIES["battery"]` *(feat/70)* :1345. `_tool_system_query`
     (:4050-4077) already dispatches callables at :4062-4063, so it needs no
     change.
   → verify by step 7 (enum and registry tests).

4. **`tools.py`: `Executor._media_status()`**, placed after `_battery`
   *(feat/70)* :4079-4101, with the module-level
   `_BROWSER_PLAYER = re.compile(r"chrom|firefox|brave|vivaldi|msedge|librewolf", re.I)`
   carrying a `# ponytail: tuning knob …` comment.
   - `if self._session_is_locked():` (:2556-2571) → `Result(False, "the
     session is locked, so what is playing was not read. …")`. playerctl is
     never run.
   - `if not shutil.which("playerctl"): return Result(False, install_hint("playerctl", "playerctl"))`
     (`install_hint` is already imported at :30).
   - `self._shell(["playerctl", "-a", "--format", FMT, "status"], timeout=4)`.
     Output containing "No players found" → `Result(True, "no media player is
     running, so nothing is playing")`. Any other failure →
     `Result(False, "playerctl failed: <first line>")`.
   - For each row, pad it (see the details above). The browser check is done
     lazily, at most once per call: `_query_rows("clients")` (:3041) over
     **all** rows, with no workspace or `mapped` filter, each row checked with
     `_sensitive_kind` (:2164). A failure gives the "could not be read" reason.
   - Line: `f"{player}: {status}"`, plus ` — {artist} – {title}` when there is
     a title and it is not withheld, or the withheld suffix.
   - It does not call `_screen_is_recorded` (decision 6).
   → verify by step 7.

5. **`tools.py`: the `media_control` tool.**
   - Schema: insert a new entry after `system_query`'s closing brace
     *(feat/70)* :1231, before `remember` (:1232). Use the spec's description
     verbatim: *"Play, pause, skip or go back in whatever media player is
     active: Spotify, a video in the browser. It needs no window and no screen
     read. The answer says what the player reports afterwards, so do not claim
     it is playing unless it says Playing."* `action` is required, enum
     `["play","pause","play-pause","next","previous"]`, and
     `additionalProperties: False`.
   - `MEDIA_ACTIONS` is a module-level tuple of the five, with
     `MEDIA_POLL = 0.1` and `MEDIA_SETTLE = 1.0`.
   - `describe()` *(feat/70)* :1747: add
     `if name == "media_control": return f'media {args.get("action", "")}'`
     beside the `system_query` branch (:1832-1833).
   - `_validate_media_control(self, action: str) -> str | None`: an error
     naming the five actions for anything else. It is picked up by the dry-run
     path *(feat/70)* :1689-1698 with no change there.
   - `_tool_media_control(self, action)`: validate, check for playerctl
     (install hint), and for play-pause pre-read `playerctl status`. Then
     `self._shell(["playerctl", action], timeout=4)`. "No players found" →
     `Result(False, f"no media player is running, so there is nothing to
     {action}. Open the music in its app first.")`. Then poll
     `playerctl status` with `time.monotonic()` and `time.sleep(MEDIA_POLL)`
     until the expected status arrives or `MEDIA_SETTLE` passes (next and
     previous: one read). Replies: `sent {action}; the player now reports
     {status}`, or `sent {action}, but the player still reports {status}, so
     it may not have taken it`.
   - It is **not** added to `READ_ONLY_TOOLS` (:57) or `INPUT_TOOLS` (:54).
   → verify by step 7 and the existing `tests/test_input_guard.py` marker scan
   (`INPUT_MARKERS`, :28-29), which must still pass without listing it.

6. **`tools.py`: `read_screen`'s description** *(feat/70)* :981-987. Append:
   *"For what is playing, or to play and pause, use system_query media and
   media_control, not the screen."* `persona.py:99` (system_query for "time")
   is left as it is, because the tool description is the whole routing
   change. → verify by step 7.

7. **`tests/test_media.py` (new)**, plain unittest with `Executor._shell`
   faked by argv (a dict from the tuple of `cmd` to a `Result`, which also
   records the calls). `_session_is_locked` and `_query_rows` are stubbed
   directly, and `tools.shutil.which` is patched.
   - `test_locked_session_refuses_and_never_runs_playerctl`: locked=True →
     `ok False`, "locked" is in the output, and no `playerctl` argv was
     recorded.
   - `test_no_player_says_nothing_is_playing`: `Result(False, "No players found")`
     → `ok True`, "nothing is playing".
   - `test_spotify_row_prints_player_status_artist_title`:
     `spotify\tPlaying\tSoundgarden\tBlack Hole Sun` →
     `spotify: Playing — Soundgarden – Black Hole Sun`.
   - `test_stripped_trailing_fields_are_padded`: `chromium\tStopped` →
     `chromium: Stopped`, with no exception.
   - `test_sensitive_title_is_withheld_by_category`: title
     `Your one-time code` → the output lacks the title and names "a
     credential or one-time code".
   - `test_browser_title_withheld_while_vault_open_on_hidden_workspace`:
     `chromium\tPlaying\tA\tSong`, with clients = a `1Password` window on
     workspace `9`, `mapped: False` → "Song" is absent and "a password manager"
     is present.
   - `test_browser_title_withheld_when_clients_fails`: `_query_rows` →
     `([], "hyprctl clients failed: x")` → "Song" is absent and "could not be
     read" is present.
   - `test_browser_title_shown_with_only_ordinary_windows` → "A – Song" is
     present.
   - `test_non_browser_player_does_not_read_clients`: spotify row with
     `_query_rows` set to raise → no exception.
   - `test_missing_playerctl_gives_install_hint`: `which` → None → the
     output equals `install_hint("playerctl", "playerctl")`.
   - `test_media_topic_in_enum_and_registry`.
   - `test_play_then_playing_reports_playing`: status → `Playing` → "now
     reports Playing".
   - `test_play_never_taking_gives_up_within_one_second`: status is always
     `Paused`, with `tools.time.monotonic` and `tools.time.sleep` faked by a
     counter clock → "may not have taken it", and at most 11 status reads.
   - `test_play_pause_expects_a_change_from_the_pre_read`: pre `Paused`,
     then `Playing` → "now reports Playing".
   - `test_next_does_one_status_read`.
   - `test_no_player_for_control_is_an_error`: "nothing to pause", `ok False`.
   - `test_unknown_action_refused_by_validator_also_under_dry_run`:
     `Executor(Config(dry_run=True)).call("media_control", {"action": "shuffle"})`
     → `ok False`, and `_shell` is never called.
   - `test_dry_run_pause_is_described_not_sent`: dry_run `pause` →
     `"[dry-run] would run: media pause"`, and `_shell` is never called.
   - `test_describe_media_control`: `describe("media_control", {"action": "pause"}) == "media pause"`.
   - `test_media_control_is_neither_read_only_nor_input`.
   - `test_media_control_reaches_mcp`: `"media_control"` in the names from
     `mcp_server._to_mcp_tools(tools_for(Config()))`.
   - `test_read_screen_description_points_at_media`: "system_query media" is
     in the `read_screen` schema description.
   → verify by `nix develop -c pytest tests/test_media.py -q`, all passing.

8. **`tests/test_matching.py`: the spec's regression test**, a new method
   in `ClickStalenessTests` (:524):
   `test_a_vault_that_opens_between_the_read_and_the_recheck_stops_the_click`.
   - A fresh `Executor(Config(dry_run=False))` **without** `with_desktop`
     (:54) and without stubbing `_ocr_words`, because the real guard chain must
     run twice. `_target_geometry` → `("0,0 800x600", None)`, and `_dispatch` and
     `_press_button` record calls, as in `setUp` (:527-533).
   - Fake `_shell`: `omarchy-shell lock isLocked` → `"false"`, and
     `hyprctl -j monitors` → one monitor `activeWorkspace {"name": "1"}`,
     `dpmsStatus: True`. `hyprctl -j clients` → `[ORDINARY_WINDOW]` until a
     flag is set, then `[VAULT]`. The VAULT fixture is copied from
     `tests/test_input_guard.py:31-33`: `1Password`, workspace `1`, 0,0
     800x600, which covers the 300x60 verify region.
   - Patch `omarchy_voice.tools.subprocess.run`: `grim` → sets the flag and
     returns non-empty stdout. `tesseract` → a TSV header plus one 12-column
     row whose text is `Continue` at left 380, top 290, conf 90. `pw-dump` →
     `b"[]"`. Patch `tools.shutil.which` → a path. Stub `_recorded_by_process`
     → `None`.
   - Assert: `result.ok is False`, `dispatched == []`, and
     `"a password manager" in result.output`.
   - **Mutation check:** memoise `_query_rows` for the length of one
     `_tool_click_text` call (a dict on the instance that is filled on the
     first call and cleared on return: the spec's rejected prototype) → this
     test **fails** (the click is dispatched). Revert → it passes. Record both
     runs in the PR.
   → verify by `nix develop -c pytest tests/test_matching.py -q`. The existing
   `ClickStalenessTests` pass unchanged.

9. **Whole suite.** `nix develop -c pytest tests -q` → the step 1 count plus
   the new tests (the 22 above plus 1), with 0 failures and no new warnings.

10. **Nix.** `nix flake check --no-write-lock-file` → it passes. `nix build`
    then `grep -o '[^:]*playerctl[^:]*' result/bin/omarchy-voice` → prints
    one `/nix/store/…-playerctl-…/bin` path.

11. **Live checks, read-only.** Do not play or pause the user's media, do not
    restart services, and do not touch whisper-server.
    - `playerctl status` and `playerctl -l` → record them (today: `Stopped`,
      `chromium.instance301556`).
    - `nix run .#omarchy-voice -- -n say --no-confirm "what's playing?"` → one
      `system_query` call with `topic: media` and no `read_screen`. The answer
      names the registered player and its status, and the trace shows a
      `playerctl` subprocess and no `tesseract`.
    - `nix run .#omarchy-voice -- -n say --no-confirm "pause the music"` →
      `[dry-run] would run: media pause`. Afterwards, `playerctl status` is the
      same as before.
    - A real `media_control` is **not** run. The approver does that by hand if
      wanted.

12. **Follow-up and PR.** Open the AT-SPI follow-up issue from decision 1.
    It names the revisit condition and carries the intent's constraints:
    window-relative bounds, non-ATK roles, empty means unknown, no bus writes,
    and an in-repo reader. Then push, and open a PR that closes #73 and links
    the intent, spec and plan. The PR body states the decision 4 departure in
    its own line.

## Tests

```
nix develop -c pytest tests -q                                   # step 1 count + 23, 0 failures
nix develop -c pytest tests/test_media.py tests/test_matching.py tests/test_input_guard.py tests/test_mcp.py -q
nix flake check --no-write-lock-file                             # CI parity
nix build && grep -o '[^:]*playerctl[^:]*' result/bin/omarchy-voice   # playerctl on the wrapper PATH
playerctl status                                                 # read-only, before and after the dry runs
nix run .#omarchy-voice -- -n say --no-confirm "what's playing?"
nix run .#omarchy-voice -- -n say --no-confirm "pause the music"
```

Plus the mutation check in step 8. It must fail with the memo in place and
pass once the memo is reverted.

## Rollback

This is a single squash-merged PR with no config, schema file or persisted
state. `git revert <merge>` removes the `media` topic, `media_control`, the
`read_screen` sentence and the new tests, and it drops `playerctl` from the
wrapper. The next `nixos-rebuild` / `home-manager switch` picks that up, and
the user service needs a restart to see the old tool list. The guard chain is
untouched by this plan, so a revert cannot weaken or strengthen any guard. A
session that called `media_control` before the revert simply stops seeing the
tool.
