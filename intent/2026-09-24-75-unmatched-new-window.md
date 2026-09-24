---
status: draft
issue: 75
author: olafkfreund
---

# Intent: a new window that is not the one launched must not be placed as if it were

Refs #75 (the `_await_new_window` half; the `live_state` half is in #78).

## Problem

`Executor._await_new_window` (`src/omarchy_voice/tools.py:2904-2954`) waits for
the window a launch just opened. A candidate must be new (not in `before`),
have a class, and match `hint` (the site's host, the app id or the desktop
id). If nothing matches before the timeout, it returns a window anyway
(`tools.py:2949-2954`):

```python
# The hint never matched but something did appear. Better to place that
# than to report nothing opened, so long as it is not an unclassed dialog.
if fallback:
    fallback.sort(key=lambda c: c.get("focusHistoryID", 999))
    return fallback[0].get("address")
return None
```

`fallback` holds every new classed window seen on the last poll
(`tools.py:2942`). That can be anything that happened to map during the wait:
a chat client starting, a window the user opened, a second app's splash. The
callers can't tell a matched window from a guessed one, so they act on the
guess as if it were the launched window.

This is the same bug as #24 and #51: an unknown answer read as a real one.
Here "I did not see the window I launched" becomes "here is the window you
launched". `intent/2026-09-20-24-empty-versus-unknown.md` fixed the baseline
half of this function (`before` from a failed query made every open window look
new). The fallback is what is left. It also breaks a constraint that intent
#23 (`intent/2026-09-20-23-desktop-loop-latency.md:89-91`) set for window
lookup: *"An ambiguous name must return the candidates, never silently act on
the best-scoring window."* The fallback does not even score its guess. It takes
whichever unmatched window has focus.

### Who calls it, and what they do with the address

Two direct callers and three tools, all on `main` at `60a8be6`:

| Tool | Path | Hint | What is done to the returned window |
| --- | --- | --- | --- |
| `compose_windows` | `_tool_compose_windows` → `tools.py:3165-3167` | `_pane_hint` (`tools.py:609-622`): host for `web`, desktop id for `app`, app id for `tui`, `""` for `terminal` | counted as `opened` (`3179`), **moved** to the target workspace (`3180-3182`), used as the preselect anchor for the next pane (`3138-3146`), handed to `_equalize_columns` (`3185`), **focused** at the end (`3187-3189`), and named in "Composed … : <labels>" (`3206`) |
| `web_search` | `_tool_web_search` (`3734`) → `_open_web_window` (`3566`, await at `3585`) | `urlparse(url).hostname`, e.g. `www.google.com` (`3740`) | **OCR'd and reported as the search results** (`3750-3759`), and **saved as `_last_search_window`** (`3744`), so the next search **closes it** (`_close_last_search`, `3709-3725`) |
| `open_page` | `_tool_open_page` (`3766`) → `_open_web_window` | host without `www.` (`3770`) | **OCR'd and reported as the page** (`3774-3780`), its address given to the model |

No other code calls it (`grep -n _await_new_window src/`). `hypr_events.py:3`
only mentions it in a docstring.

### Demonstrated with fakes

`Executor._query_rows` is patched: the baseline returns an editor window, and
every later query also returns a Discord window that was not launched. The real
`_await_new_window`, `_tool_compose_windows`, `_open_web_window`,
`_tool_open_page` and `_tool_web_search` run unchanged. Timeouts are cut to
0.5 s. `_shell`, `_dispatch` and OCR are stubbed, so nothing touches the
desktop.

```
1. the real method, hint never matches, an unrelated window appears
   _await_new_window(before={0xeditor}, 0.5, hint='apnews.com') -> '0xdiscord'  after 0.51s (the whole timeout)
2. compose_windows moves it
   dispatch focus {'workspace': '4'}
   dispatch window.move {'workspace': '4', 'window': 'address:0xdiscord'}
   dispatch focus {'window': 'address:0xdiscord'}
   dispatch layout {'message': 'preselect r'}
   dispatch window.move {'workspace': '4', 'window': 'address:0xfiles'}
   dispatch focus {'window': 'address:0xdiscord'}
   -> ok=True 'Composed workspace 4 in a columns layout: music, files.'
3. open_page reads it as the page
   -> ok=True 'opened https://apnews.com (window address:0xdiscord):\n\n<OCR of #general - Discord>'
5. web_search remembers it as its own window, and the next search closes it
   search 1 -> "results for 'rubber duck' (window address:0xdiscord, and on screen for the user to see):\n\n<OCR of #g"
   search 2 sent: dispatch window.close {'window': 'address:0xdiscord'}
```

In (2) Spotify never mapped. The user's Discord was pulled off workspace 1,
tiled, and focused, and the model was told "music" was composed. In (3) and (5)
the model gets a chat window's text as a news page or as search results. In (5)
the next search **closes a window the user had open**. `_close_last_search`'s
docstring says this must never happen: *"closing a window somebody wanted is a
worse failure than leaving a stale one behind"*. It tracks the window by
address to avoid exactly that, and the fallback gives it the wrong address.

### When the fallback is right

The fallback came in with the initial import (`6034269`, "omarchy-voice
0.3.0"). `git log -S "Better to place that"` finds only that commit. Its
comment is the only rationale. No test covers it: of the six tests that call
the real method with a hint (`tests/test_compose.py:103,110,116`,
`tests/test_reach.py:643`, `tests/test_hypr_events.py:150,169`), none reaches
the unmatched branch.

It is right when the launched app's window never looks like the hint. That can
only happen for `app` panes in `compose_windows`, whose hint is the desktop id.
Same harness, case 4:

```
4. the fallback is right: Telegram's desktop id never matches its class
   _window_matches(TelegramDesktop, 'org.telegram.desktop') -> False
   _await_new_window(..., hint='org.telegram.desktop') -> '0xtg' (placed only by the fallback)
```

To see how common this is, every visible `.desktop` entry on this machine that
declares a `StartupWMClass` was checked. For each, `_window_matches` compared
`{class: StartupWMClass, initialTitle: Name}` with the hint `_pane_hint` would
use. Chrome PWA entries were counted with class = desktop id, which is what
`hyprctl clients` shows for them live, e.g.
`chrome-cifhbcnohmdccbgoicgdjpfamggdegmo-Profile_3`.

- 344 visible entries. 151 declare a `StartupWMClass`, and **22 of those
  (15%) can never match**. Among them: `org.telegram.desktop`
  (`TelegramDesktop`), `com.obsproject.Studio` (`obs`), `org.kde.krita`,
  `org.kde.kdenlive`, `org.gnome.SystemMonitor`, `org.gnome.tweaks`,
  `waveterm` (`Wave`), `proton.vpn.app.gtk`, and the three `gsuite-new-*`
  launchers (`chrome-docs.new__-Profile_4`).
- The other 193 declare no class, so this check can't tell whether they match.

| Caller / pane kind | Needs the fallback? | Why |
| --- | --- | --- |
| `compose_windows`, `app` | **Yes, sometimes**: 22 of 151 checkable entries here, the rest unknown | hint is the desktop id, class often differs |
| `compose_windows`, `tui` | No | `omarchy launch tui --app-id=X` maps as `org.omarchy.X` (live: `org.omarchy.herdr`), which contains `X` |
| `compose_windows`, `web`; `web_search`; `open_page` | Not by construction | `omarchy-launch-webapp` runs `<chromium-family> --app=<url>`. The class carries the host (`chrome-docs.new__-Profile_4`), and `initialTitle` matches too (`tests/test_compose.py:94-97`). Not measured live: launching is off limits here |
| `compose_windows`, `terminal` | Never reaches it | hint is `""`, so `matched = fresh` (`tools.py:2943`) already takes *any* new classed window. Same guess, different branch |

So the three tools that read, report or close the window never need the
fallback, and only `app` panes in `compose_windows` do.

### Evidence from the session log

`~/.local/state/omarchy-voice/session.log` (2026-09-11 to 2026-09-24, 2906
lines) has 9 `web_search` actions, no `compose_windows` and no `open_page`. The
log records the query, not the address `_await_new_window` returned or whether
the hint matched, so a wrong placement can't be seen or ruled out from it. The
one oddity, two different searches followed by `read screen` of the same
address `0x582c1fbf9690` (15:25:16 and 15:25:48 on 2026-09-12), can't be pinned
on this bug. There is **no direct evidence from use**. The case rests on the
code and the fakes.

## Proposed outcome

- A tool never moves, focuses, reads out, remembers or closes a window as "the
  one just launched" unless it matched what was launched.
- When the launched window did not appear but something else did, the model is
  told that honestly. It learns what did appear (class and title), so it can
  choose. It is never handed that window as if it were the launched one.
- `web_search` never closes a window it did not confirm it opened.
- An `app` pane whose class differs from its desktop id, like Telegram or OBS,
  still ends up composed, or the result says plainly that it did not. It does
  not silently go missing.

## Affected users and systems

- `src/omarchy_voice/tools.py`: `_await_new_window` and its two callers,
  `_tool_compose_windows` and `_open_web_window` (and through it `web_search`
  and `open_page`).
- Anyone who runs `compose_windows` with `app` panes, since that is the only
  path where the fallback currently gives the right answer.
- Everything reaches this through the one shared `Executor`, whichever engine
  is running.

## Constraints

- **The #24 guard stays.** The baseline failure refusal at `tools.py:3148-3155`
  and `3576-3579` is unchanged, and so is skipping unclassed windows
  (`PROFILE_ERROR_TITLE`, `tools.py:442-456`).
- **No longer waits.** `PANE_TIMEOUT` and `WEB_WINDOW_TIMEOUT` stay as they
  are. Today the fallback already costs the full timeout (case 1: 0.51 s of a
  0.5 s budget), and a fix must not add to it.
- **Unknown reaches the model as a fact it can act on**, as in #24 and #51. Not
  an exception, not silence, and not "nothing opened" when something did.
- **Proven with fakes.** The mocking seam stays `_query_rows` / `_shell` /
  `_dispatch`, and nothing launches on the live desktop to prove it.
- **Must not make `app` panes worse** for the 22 known mismatched ids without
  saying so. Removing the fallback outright turns "composed, correctly" into
  "did not appear" for Telegram, OBS, Krita and the rest.

## Open questions

1. **What does an unmatched new window become?** (a) *Refuse*: return nothing,
   so the pane or page is "did not appear". This is the simplest option, but it
   breaks the 22 known `app` ids. (b) *Report, don't act*: return it marked as
   unconfirmed, with class and title, so the tool says "X did not appear; a
   window of class Y did" and leaves it where it is. (c) *Ask*: return it as a
   choice, in the spirit of #23.
2. **One policy or one per caller?** Only `app` panes ever need the fallback.
   `web_search` and `open_page` never do, and they are where it does the most
   harm: OCR presented as results, and a later close. Should the web path drop
   the fallback outright while `compose_windows` gets (b) or (c)?
3. **Fix the app hint instead?** Reading `StartupWMClass` from the `.desktop`
   entry into the hint would cover the 22 known misses, if the live class
   really is the declared one. It does nothing for the 193 entries that
   declare no class. Is that in scope here, or a follow-up that lets (1a) be
   safe later?
4. **Is the `terminal` pane's empty hint in scope?** It takes any new classed
   window by a different branch (`tools.py:2943`). The failure is the same,
   though less likely because a terminal maps fast.
5. **Should compose's summary separate matched and unmatched panes?** Today an
   unmatched pane counts as `opened` and is named in "Composed …". Should it be
   listed apart, like the existing `slow` and `others` notes?
