---
status: approved
issue: 75
spec: spec/2026-09-24-75-unmatched-new-window.md
---

# Plan: a new window that is not the one launched must not be placed as if it were

Refs #75, the `_await_new_window` half. The `live_state` half is #78.
Line numbers are against `main` at `60a8be6`. Each one cited here was checked
there.

## Approved decisions, carried over from the spec

1. **An unmatched new window is reported, not acted on (Q1 = b).**
   `_await_new_window` returns only a window that matched. When nothing
   matched, the caller names what *did* appear (class, title, address) and
   leaves it where it is. It is not moved, focused, read, remembered or
   closed. Refusing without the report (a) and asking mid-build (c) are
   rejected.
2. **One policy, in the shared function (Q2).** The fallback is deleted from
   `_await_new_window` itself, so no caller can get a guess back. There is no
   per-caller flag. The callers differ only in wording: `compose_windows` lists
   the pane as unplaced, and `_open_web_window` says the page did not open and
   names what did.
3. **An `app` pane matches on its desktop id OR the entry's declared
   `StartupWMClass` (Q3).** It has to be either-or. Measured on this machine
   (309 visible entries, 119 declaring a class): the id alone never matches 22
   of them (Telegram, OBS, Krita, Kdenlive and others), the class alone never
   matches 41 (the Chrome PWAs among them, which declare a `crx_…` class they
   never use), and either-or leaves 0 unmatched. When the declaration is wrong,
   decision 1's report is the net.
4. **`StartupWMClass` is read by `tools.py`, not taken from #85's
   `app_index()`.** That index does not carry the key, and it drops `NoDisplay`
   entries. A sibling of `desktop_actions` that reuses `_desktop_entry_path`
   has no dependency on #85.
5. **The terminal pane's empty hint is out of scope (Q4).** An empty hint
   still means "any new classed window". A follow-up issue is opened for it
   (step 7). This change does not fix it.
6. **Compose's summary separates unmatched panes (Q5).** An unmatched pane is
   not counted in `opened` and is never an anchor. It gets its own note next to
   `slow` and `others`.
7. **`hint` accepts `str | tuple[str, ...]`.** Empty strings are dropped. An
   all-empty tuple behaves like `""`, which keeps the terminal behaviour. A
   tuple with one real hint never widens to "any window". The return type stays
   `str | None`, so every existing fake and caller keeps working.
8. **One helper, `_unmatched_new_windows(before) -> str`, for both callers.**
   It makes one `_query_json("clients")` after the wait has already failed,
   keeps the rows that are new and classed, and describes up to three as
   `class 'title' (address:0x…)`. When the query fails or finds nothing it
   returns `""`, and the callers then say nothing about other windows. They
   never claim "nothing else appeared" (#24).
9. **`web_search` and `open_page` do not change.** Because `_open_web_window`
   now fails, `web_search` returns before `_last_search_window` is set
   (`tools.py:3744`), so the next `_close_last_search` has nothing to close.
   `open_page` returns before reading.
10. **Rejected, and not to be reintroduced while implementing:** keeping the
    fallback for `app` panes only; scoring the fallback with `_rank_windows`
    (every candidate scores zero); returning `(address, strangers)` or adding an
    out-parameter (it changes the signature every fake patches); using
    `StartupWMClass` instead of the id; fixing the terminal hint here.
11. **No new module, config key or dependency.** All code is in
    `src/omarchy_voice/tools.py`. Tests use fakes only. Nothing launches, no
    service restarts, and whisper-server is not touched.

Details the spec left implicit, decided here:

- **The id passed to `_desktop_wm_class`** is the one `_pane_hint("app", …)`
  already produces (`tools.py:620-621`: `.desktop` and any `:action` stripped).
  Compute the hint once and pass it to both.
- **The new tests need a clock that moves.** The real `_await_new_window` runs
  against `PANE_TIMEOUT` (`tools.py:226`, 10 s for `app`) and
  `WEB_WINDOW_TIMEOUT` (`:429`, 15 s, bound as a default argument, so patching
  the constant does nothing). Each new test patches `omarchy_voice.tools.time.monotonic`
  with a counter that advances 1.0 per call and replaces `_wait_tick` with a
  no-op. A suite that waits real seconds is a bug.
- **The compose tests patch `omarchy_voice.tools.app_dirs`** to a temp dir.
  `tools.py:30` imports it by name, and without the patch `_desktop_wm_class`
  would read the real machine's entries, including the user's `DATA_HOME`.

## Landing order

The spec's overlap check stands: PR #85 (`feat/70-find-what-is-installed`,
still OPEN) touches `tools.py` at `55-80`, `760-800`, `1638-1790` and
`2042-2135`, plus `capabilities.py`. This change touches `1475-1489` (a new
helper after `desktop_actions`), `2904-2954`, `3105-3215` and `3566-3594`.
No shared hunk, so **either can land first**. #81 (`local_engine.py`,
`listen_local.py`, `trace.py`) and #78 (`capabilities.py`, `claude_backend.py`,
`planner.py`) do not touch these regions. The steps are written against `main`.
Step 8 covers #85 landing first.

## Steps

0. **Baseline.** `nix develop -c pytest tests -q` → record the pass count
   (792 passed, 414 subtests, on this branch, whose `src/` and `tests/` are
   identical to `main`).

1. **`tests/test_compose.py` and `tests/test_web.py`: write the regression
   tests first, and watch them fail on `main`.** Write tests 1-5 in "Tests"
   below, the ones marked *fails on main*, before any `src/` edit.
   → verify: `nix develop -c pytest tests/test_compose.py tests/test_web.py -q`
   shows exactly those five failing, for the reason in each test's docstring
   (Discord adopted, read, remembered or closed). Every other test passes.

2. **`src/omarchy_voice/tools.py`: `_await_new_window` (`2904-2954`).**
   - The signature becomes `hint: str | tuple[str, ...] = ""`.
   - Before the loop:
     `hints = tuple(h for h in ((hint,) if isinstance(hint, str) else hint) if h)`.
   - Delete `fallback: list[dict] = []` (`2924`), `fallback = fresh` (`2942`)
     and the block after the loop (`2949-2953`). After the deadline it returns
     `None`.
   - `matched = [c for c in fresh if any(_window_matches(c, h) for h in hints)] if hints else fresh`.
   - Docstring: it returns only a window that matched. Callers report what else
     appeared, through `_unmatched_new_windows`.
   → verify: test 1 (case 1) and the hint-tuple edges pass, and
   `test_compose.py:99-116`, `test_reach.py:629-645` and
   `test_hypr_events.py:141-171` still pass.

3. **`tools.py`: `_unmatched_new_windows(self, before) -> str`**, next to
   `_await_new_window`. One `_query_json("clients")`, rows not in `before` and
   with a class, sorted by `focusHistoryID`, at most three, each
   `f"{class} {title!r} (address:{address})"`, joined with `"; "`. It returns
   `""` on an empty or failed query. The docstring says why it never claims
   absence (#24).
   → verify by steps 5 and 6.

4. **`tools.py`: `_desktop_wm_class(app_id) -> str`**, a module function
   after `desktop_actions` (`1475-1488`), in the same shape: find the file with
   `_desktop_entry_path`, return the first `StartupWMClass=` value stripped,
   or `""`. One difference from `desktop_actions`: stop at the second line
   that starts with `[`, so a key in an action group is never taken.
   → verify: a unit test with a temp entry returns `TelegramDesktop`. An entry
   without the key returns `""`, and so does a missing entry.

5. **`tools.py`: `_tool_compose_windows` (`3105-3215`).**
   - Add `unmatched: list[str] = []` next to `slow` (`3122`).
   - At `3165-3167`, compute `hint = _pane_hint(kind, target, name)` once. For
     `kind == "app"`, pass `(hint, _desktop_wm_class(hint))`. Every other kind
     passes `hint` unchanged.
   - At `3175-3178`, when `address is None`: `desc = self._unmatched_new_windows(before)`.
     If it is non-empty, append `f"{label} (instead: {desc})"` to `unmatched`.
     Otherwise append `label` to `slow`, as today. `placed` still gets `None`
     (`3174`), so the pane is never moved, focused or used as an anchor.
   - `3204`: `if not opened and not slow and not unmatched:`.
   - After the `slow` note (`3208-3210`), add:
     `" Did not appear as asked: {'; '.join(unmatched)}. Those windows were left where they opened; tell the user, and move one with window.move only if they say it is the one they wanted."`
   → verify: tests 2, 5, 6 pass, and `test_compose.py:201-231` still passes
   (its fake `_query_json` returns `[]`, so the helper is silent and the pane
   stays in `slow`).

6. **`tools.py`: `_open_web_window` (`3566-3594`).** At `3586-3588`, when
   `address is None`, call `desc = self._unmatched_new_windows(before)`. If it
   is non-empty, the reason becomes
   `f"the browser did not open a window within {timeout:.0f}s. A different window did appear ({desc}); it is not the page, so it was not read. Say so rather than assuming it worked."`.
   Otherwise the reason is today's text. `_tool_web_search` and
   `_tool_open_page` are not edited (decision 9).
   → verify: tests 3, 4 and 8 pass, and the `test_web.py` #24 cases
   (`BaselineFailureTests`, `:307` on) still pass.

7. **Mutation checks, then the whole suite.**
   - Re-add the fallback (the three deleted pieces of step 2) → tests 1-4
     fail, and so does test 5. Restore it.
   - Pass `hint` alone for `app` panes (drop `_desktop_wm_class`) → test 5
     fails. Restore it.
   - `nix develop -c pytest tests -q` → the baseline plus the new tests, with no
     new failures.
   - `nix flake check --no-write-lock-file` → passes.

8. **If #85 merged first:** `git fetch && git rebase origin/main`. Resolve by
   keeping both sides (no hunk is shared). Then re-run
   `nix develop -c pytest tests -q` and `nix flake check --no-write-lock-file`.
   Otherwise skip this step.

9. **Follow-up issue, then PR.**
   - Open the follow-up from decision 5: "compose_windows: a terminal pane's
     empty hint still takes any new classed window". It cites
     `tools.py` `_pane_hint`'s `return ""` for terminals (`622` on `main`), and
     says that the class depends on what `xdg-terminal-exec` picks. It links
     this PR.
   - Push, and open a PR that says "Refs #75" (not "Closes": the `live_state`
     half is #78), links the intent, spec and plan, and states the landing
     order above.

## Tests

The new tests, in `tests/test_compose.py` (1, 2, 5, 6, 7, and the
`_desktop_wm_class` unit) and `tests/test_web.py` (3, 4, 8). The windows used:
`DISCORD = {"address": "0xdiscord", "class": "discord", "title": "Discord", "focusHistoryID": 0}`
and an `EDITOR` already open in the baseline. The web tests use a subclass of
`SearchingExecutor` that does **not** override `_await_new_window` (the
existing one at `test_web.py:66` fakes it away, which is why it could never
catch this), with `_query_rows` returning `[EDITOR]` on the first call and
`[EDITOR, DISCORD]` after it, and with `_ocr_region` recording calls.

1. `test_an_unrelated_window_is_not_returned_as_the_one_launched`: baseline
   `{editor}`, Discord appears, hint `apnews.com`, timeout 0.5 → `None`, not
   `0xdiscord`. **Fails on main.**
2. `test_compose_does_not_adopt_an_unrelated_discord_window`: one `app` pane,
   `spotify`, on workspace `"4"`. Spotify never maps and Discord appears. No
   `_dispatch_lua` call names `0xdiscord`. The summary has no "Composed", and
   it contains "Did not appear as asked", `discord` and `address:0xdiscord`.
   **Fails on main.**
3. `test_open_page_does_not_read_an_unrelated_window`:
   `open_page("https://apnews.com")` with only Discord appearing → `ok=False`,
   `_ocr_region` never called, and the reason names `discord`.
   **Fails on main.**
4. `test_the_next_search_does_not_close_the_users_discord`: search 1 with only
   Discord appearing → `ok=False` and names it. `_last_search_window` is
   `None`. Search 2 → no `window.close` names `0xdiscord`.
   **Fails on main.**
5. `test_telegram_composes_on_its_declared_class`: temp entry
   `org.telegram.desktop.desktop` with `StartupWMClass=TelegramDesktop`. Both a
   `TelegramDesktop` window (`focusHistoryID` 1) and Discord (`focusHistoryID`
   0) appear. → the Telegram window is moved to workspace 4, and "Composed"
   names the pane. Discord is not moved. **Fails on main by fallback**:
   the id hint never matches, so `main` takes Discord, the focused one.
6. `test_a_pwa_still_composes_on_its_desktop_id`: an entry declaring
   `StartupWMClass=crx_dkfoldflcfkbhibhiajfgobmfkifgbdl`, whose window class
   is `chrome-dkfoldflcfkbhibhiajfgobmfkifgbdl-Default` (the live Sonarr row) →
   matched on the id.
7. `test_hint_tuple_edges`: `("", "")` takes any classed new window, like
   `""`. `("apnews.com", "")` does not take Discord.
8. `test_a_failed_requery_never_claims_nothing_else_appeared`: the baseline
   succeeds, and every later `_query_rows` fails → the reason has no
   "instead" or "different window" clause and no "nothing else" wording.

Unchanged and passing: `test_compose.py:85-89` (`_pane_hint` still returns
`str`), `:99-116`, `:201-231`; `test_reach.py:629-645`;
`test_hypr_events.py:141-171`; the `test_web.py` #24 cases.

```
nix develop -c pytest tests -q                                        # baseline + new, 0 new failures
nix develop -c pytest tests/test_compose.py tests/test_web.py -q      # the new tests
nix develop -c pytest tests/test_reach.py tests/test_hypr_events.py -q
nix flake check --no-write-lock-file                                  # CI parity
```

No live run. The behaviour needs a window to fail to appear while another one
does, and producing that on the desktop means launching apps. The fakes cover
it.

## Deviations while implementing

No approved spec decision changed. These are test and mutation details:

- **Tests 2, 5 and 6 compose two panes, not one.** `_validate_compose_windows`
  refuses a single pane (`SinglePaneTests`). The second pane, `vlc`, never
  maps anything, so it only ever lands in `slow`. The compose tests also patch
  `omarchy_voice.tools.shutil.which`, because `_pane_command` builds an `app`
  pane only when `uwsm-app` or `gtk-launch` is on `PATH`.
- **Test 5 passes the target `org.telegram.desktop.desktop`.** `_pane_command`
  and `_pane_hint` strip one trailing `.desktop`, so the target
  `org.telegram.desktop` becomes the id `org.telegram` and never finds the
  entry. That was already true before this change and it is not fixed here.
- **Test 1 uses a 5 s timeout, not 0.5 s.** The moving clock steps 1.0 per
  look, so a 0.5 s wait never looks and returns `None` for the wrong reason
  (the test passed on `main` when it was written that way).
- **Mutation A (fallback re-added) fails tests 1-4 and test 7, not test 5.**
  With `_desktop_wm_class` in place, the Telegram window matches before the
  fallback is reached. Test 5 fails under mutation B (the id alone), as
  planned.

## Rollback

It is a single squash-merged PR in one source file and two test files, with no
Nix, config or schema change and no persisted state. `git revert <merge>`
restores the fallback and id-only app hints. The follow-up issue from step 9
stays open either way.
