---
status: approved
issue: 75
intent: intent/2026-09-24-75-unmatched-new-window.md
---

# Spec: a new window that is not the one launched must not be placed as if it were

Refs #75 (the `_await_new_window` half; the `live_state` half is in #78).
Line numbers are against `main` at `60a8be6`.

## The intent's open questions, answered

The intent was approved without answers to its five questions, so each is
decided here with the reasoning shown. Q3 is partly a claim, so it is answered
by demonstration. **Any of these can be rejected at this gate.**

### Q1: what does an unmatched new window become? (b) Report it, don't act on it.

`_await_new_window` returns only a window that matched. When nothing matched,
the caller says what *did* appear (class, title, address) and leaves it where
it is. It is not moved, focused, read, remembered or closed.

- **(a) Refuse** alone is rejected. It is (b) without the report: the model
  hears "did not appear" when something did, which is the "not nothing opened
  when something did" constraint broken the other way.
- **(c) Ask** is rejected. `compose_windows` is in the middle of a build and
  has no turn to ask in. A per-pane question would mean a new tool shape for a
  case that should be rare once Q3 lands. (b) already gives the model the
  choice: it has the address, and `window.move` is one dispatch. It can ask the
  user in its next sentence, which is where (c) would have ended up.

### Q2: one policy or one per caller? One policy, in the shared function.

The fallback comes out of `_await_new_window`. So neither caller can get a
guess back, whatever it passes. There is no per-caller flag to get wrong later.
The callers differ only in their wording. `compose_windows` lists the pane as
unplaced. `_open_web_window` says the page did not open and names what did.

Per caller would keep the guess for `app` panes. But Q3 takes away the reason
to guess for every `app` entry that declares a class. A guess the model can't
see is exactly what the intent is removing.

### Q3: fix the app hint instead? Yes, in scope, and read by `tools.py`, not #85's `app_index()`.

An `app` pane matches on the desktop id **or** the entry's declared
`StartupWMClass`. It has to be *or*, not "use the declared class": Chrome PWAs
declare a class they never use. Demonstrated with the real `_window_matches`
against a synthetic Telegram window and a live PWA row. Then a census of this
machine's entries, and a read-only `hyprctl clients -j`. Nothing launched.

```
B. the real _window_matches
  Telegram window vs desktop id     : False
  Telegram window vs StartupWMClass : True
  Sonarr PWA (live class chrome-dkfoldflcfkbhibhiajfgobmfkifgbdl-Default) vs StartupWMClass crx_dkfoldflcfkbhibhiajfgobmfkifgbdl: False
  Sonarr PWA vs desktop id          : True

C. census: 309 visible, 119 declare StartupWMClass
  never match on desktop id alone          : 22
  never match on StartupWMClass alone      : 41
  never match on either (id or WMClass)    : 0
```

(The intent counted 344/151 by a slightly different visibility filter. It
found the same 22 misses: Telegram, OBS, Krita, Kdenlive and the rest.)

- The declared class alone would **break 41 entries that work today**, the
  PWAs among them. Either-or fixes all 22 and breaks none.
- The census assumes the live class is the declared one. That is a
  declaration, not proof. Of the windows open now, none of the 22 is running,
  and `.scrcpy-wrapped` shows that Nix wrappers can rename a class. **When the
  declaration is wrong, Q1's report is the net**: the pane is named as unplaced
  along with the class that did appear. It never goes silently missing.
- The 190 entries that declare no class get no help from this. They get Q1's
  report.

**Not `capabilities.app_index()` from #85.** It does not read
`StartupWMClass`. It is a search index that drops `NoDisplay` entries, and
compose needs one known id resolved. `main` already has that lookup:
`_desktop_entry_path` (`tools.py:1462`), used by `desktop_actions`
(`tools.py:1475`). A sibling reader next to it has no dependency on #85, so
this can land before or after it.

### Q4: the `terminal` pane's empty hint? Out of scope, left as it is.

An empty hint still means "any new classed window" (`tools.py:2943`). A
terminal's class depends on what `xdg-terminal-exec` picks
(`omarchy launch terminal` → `uwsm-app -- xdg-terminal-exec`). Resolving that
is a separate question. A terminal also maps well inside its 6 s budget, so the
race window is small. A follow-up issue should be opened for it. It is not
opened by this spec.

### Q5: separate matched and unmatched panes in compose's summary? Yes.

An unmatched pane is not counted in `opened`, and it is not an anchor. It gets
its own note, next to `slow` and `others`. This falls out of Q1.

## Design

All in `src/omarchy_voice/tools.py`. No new module, config key or dependency.

### 1. `_await_new_window` (`2904-2954`): return a match or nothing

- Delete the `fallback` list, its assignment (`2942`) and the branch that
  returns it (`2949-2954`). After the deadline the method returns `None`.
- `hint` accepts `str | tuple[str, ...]`. Normalise it once:
  `hints = tuple(h for h in ((hint,) if isinstance(hint, str) else hint) if h)`.
  Then `matched = [c for c in fresh if any(_window_matches(c, h) for h in hints)] if hints else fresh`.
  An all-empty tuple behaves like `""`, which keeps the terminal behaviour of
  Q4. A tuple with one real hint never widens to "any window".
- Docstring: say that it returns only a match, and that callers report what
  else appeared.

Existing callers and fakes (`tests/test_web.py:66`,
`tests/test_compose.py:206,224`) keep working: the return type is still
`str | None`.

### 2. What appeared instead: one helper for both callers

```python
def _unmatched_new_windows(self, before: set[str]) -> str:
    """Classed windows that appeared since `before`, described for the model, or ""."""
```

It makes one `_query_json("clients")` after the wait has already failed, which
is ~15 ms here, against a 6-15 s timeout. It filters the rows the way the
loop does (not in `before`, has a class) and describes up to three as
`class 'title' (address:0x…)`. When the query fails or finds nothing it
returns `""`, and the callers then say nothing about other windows. They never
claim "nothing else appeared" (#24).

### 3. `_desktop_wm_class(app_id) -> str` next to `desktop_actions` (`1475`)

`StartupWMClass=` from the entry found by `_desktop_entry_path`, or `""`. Same
shape and error handling as `desktop_actions`.

### 4. `_tool_compose_windows` (`3105-3215`)

- For `kind == "app"`, the hint passed at `3165-3167` becomes
  `(_pane_hint(...), _desktop_wm_class(app))`. Other kinds are unchanged.
  `_pane_hint` keeps returning `str`, so `tests/test_compose.py:85-89` stands.
- When `address is None`, call `_unmatched_new_windows(before)`. If it returns
  something, the pane goes into a new `unmatched` list instead of `slow`, as
  `f"{label} (instead: {desc})"`. `placed` still gets `None`, so the pane is
  never moved, focused or used as an anchor.
- Summary, after the `slow` note (`3208-3211`):
  `" Did not appear as asked: {…}. Those windows were left where they opened;
  tell the user, and move one with window.move only if they say it is the one
  they wanted."`
- `"nothing opened"` (`3204-3205`) also looks at `unmatched`. A build where
  only strangers appeared reads "Nothing came up on …" plus the note. It is not
  a bare failure, because something did happen.

### 5. `_open_web_window` (`3566-3594`)

When `address is None` (`3586`), append `_unmatched_new_windows(before)` to the
reason if it is non-empty:
`"…did not open a window within 15s. A different window did appear ({desc});
it is not the page, so it was not read. Say so rather than assuming it worked."`
`web_search` then returns before `_last_search_window` is set (`3744`), so the
next `_close_last_search` (`3709`) has nothing to close. `open_page` returns
before reading. Neither tool's code changes.

## Alternatives rejected

- **Keep the fallback for `app` panes only (per-caller flag).** It still
  silently adopts a guess on the one path that moves and focuses windows.
  Q3 covers the known cases with no guess at all.
- **Score the fallback (best `_rank_windows` hit).** A score against a hint that
  matched nothing is zero for every candidate. There is nothing to rank.
- **Return `(address, strangers)` or add an out-parameter.** Either one changes
  the signature every fake and test patches. The helper re-queries once, only
  on the failure path, and the signature stays as it is.
- **Use `StartupWMClass` instead of the desktop id.** Breaks 41 entries,
  including the PWAs (demonstration C).
- **Take `StartupWMClass` from #85's `app_index()`.** It doesn't carry the key,
  it is filtered for search, and it would tie this change's landing order to
  #85 for no gain.
- **Fix the terminal hint here.** Separate question (Q4).

## Risks

- **An `app` whose live class matches neither its id nor its declared class**
  (190 undeclared entries, wrapped binaries like `.scrcpy-wrapped`) used to be
  composed by luck. Now it is reported as unplaced, with its class and address.
  This is the intended trade, stated by the intent. It is visible, and the model
  can move the window itself.
- **Two windows appear, and the launched one matches but maps late.** Unchanged:
  the loop still waits for a match until the deadline.
- **A declared `StartupWMClass` that is a common substring** (e.g. `foot`) could
  match a foreign window by substring. That is the same exposure the desktop id
  hint already has through `_window_matches`. It still only considers *new*
  windows, not all of them.
- **Hosts:** code is host-independent. The census is from p620. Other hosts'
  entries differ, and Q1's report covers any gap.
- **Merge overlap:** #85 touches `tools.py` at `55-80`, `760-800`, `1638-1790`
  and `2042-2135`, and `capabilities.py`. This change touches `1475` (new helper
  after `desktop_actions`), `2904-2954`, `3105-3215` and `3566-3594`. No shared
  hunk, so the order does not matter. #81 (`local_engine.py`, `listen_local.py`,
  `trace.py`) and #78 (`capabilities.py`, `claude_backend.py`, `planner.py`) do
  not touch these files' regions.

## Verification

Unit tests with fakes only. The seam is `_query_rows` / `_query_json` /
`_shell` / `_dispatch`. Nothing launches, no service is restarted, and
whisper-server is not touched. In `tests/test_compose.py` and
`tests/test_web.py`:

1. **The intent's case 1 as a regression**: baseline `{editor}`, every later
   query adds an unrelated `Discord`, hint `apnews.com`, timeout 0.5 →
   `_await_new_window` returns `None`, not `0xdiscord`.
2. **The intent's case 2**: `compose_windows` with a Spotify `app` pane that
   never maps while Discord appears → no `window.move` or `focus` dispatch names
   `0xdiscord`. The summary does not say "Composed … music", and it contains
   "Did not appear as asked" with `Discord` and `address:0xdiscord`.
3. **The intent's case 3**: `open_page("https://apnews.com")` with only Discord
   appearing → `ok=False`, no OCR call, and the reason names Discord's class.
4. **"The next web_search closes your Discord"**: search 1 while only Discord
   appears → fails and names it. Search 2 → no `window.close` names
   `0xdiscord` (`_last_search_window` stays `None`).
5. **Telegram still composes**: entry `org.telegram.desktop` declaring
   `StartupWMClass=TelegramDesktop` (a temp dir patched in as `app_dirs`), with
   a window of class `TelegramDesktop` → matched, moved to the workspace and
   counted in "Composed".
6. **A PWA still composes on its id**: an entry declaring `crx_…` and a window
   whose class is the desktop id → matched.
7. **Hint tuple edges**: `("", "")` takes any classed new window (terminal
   behaviour). `("apnews.com", "")` does not take Discord.
8. **The helper never claims absence**: a failed `_query_json` after the
   timeout → the reason has no "instead" clause and no "nothing else" wording.
9. Existing tests unchanged and passing, in particular
   `test_compose.py:99-116`, `test_reach.py:643`,
   `test_hypr_events.py:150,169` and the `test_web.py` #24 cases.

Then run `nix flake check --no-write-lock-file`, which runs the whole suite.
