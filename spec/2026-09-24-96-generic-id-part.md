---
status: draft
issue: 96
intent: intent/2026-09-24-96-generic-id-part.md
---

# Spec: a generic last part of a desktop id must not count as the app's name

Line numbers are against `origin/main` at `72f9ab6`. Every number below was
measured on p620's real index (`capabilities.app_index()`, 303 entries, 121
with a dotted id, `PYTHONPATH=src`). A scratch script, which is not committed,
re-implemented `find_apps` with only the last-part tier swapped per candidate.
It first asserted that its "today" copy returns exactly what the real
`find_apps` returns for every query used. Nothing was launched.

## The intent's open questions, answered

The approver approved the intent without answering these. Each answer below is
a decision this spec makes, and **each can be rejected at this gate**.

### The measurement

Columns: **false** counts the intent's five false clear matches (`android`,
`setup`, `debug`, `04`, `vending`) that still launch. **lost** counts the 23
legitimate last-part matches that no longer resolve as they do today.
**desktop** gives the verdict for "desktop" on p620, and for "the desktop" on
a fixture dir that holds only Telegram (`org.telegram.desktop`) and Google
Chrome. **#70** counts changed verdicts among the 24 requests in
`tools/verify_find.py`.

| Candidate | false | lost | desktop, p620 | desktop, fixture | #70 |
| --- | --- | --- | --- | --- | --- |
| today | 5 | 0 | choice | **launch Telegram** | — |
| A. the issue's stoplist (`desktop app application client gtk qt`) | 5 | 0 | choice | nothing | 0 |
| A+. A plus this machine's offenders (`android debug setup vending 04`) | 0 | 0 | choice | nothing | 0 |
| B. structural: a generic word (A ∪ `android debug setup`), or no letter in it | 1 (`vending`) | 0 | choice | nothing | 0 |
| **C. B, and never the last part of a `waydroid.*` id** | **0** | **2** (`katana`, `livewallpaper`) | choice | nothing | 0 |
| D. last-part tier scored 90, so it never clear-matches alone | 0 | 23 (all) | choice | choice | 0 |
| E. the last part counts only if it contains a 4+ letter word of `Name` | 1 (`debug`) | 5 | choice | nothing | 0 |

**No general rule reaches 0 false and 0 lost.** Only A+ does, and A+ is not a
rule. It lists this machine's offenders, and `vending` is the last part of
the Play Store's Android package name, not a generic word. The next Waydroid
app brings its own tail. A hypothetical `com.spotify.music` would make "music"
compete with GNOME Music, and A+ would miss it until someone adds a word.

### 1. Stoplist, structural rule, or both? → C, a structural rule

The last part of an id counts as a name only when all three hold:

- it is not a generic packaging, toolkit, platform or role word:
  `desktop app application client gtk qt android debug setup`;
- it contains a letter, so a version fragment such as `04` is not a name;
- the id does not start with `waydroid.`.

The first bullet is a short word list, but each word stands for a category
and none of them is a name. Measured cost: `katana` (Facebook) and
`livewallpaper` (TikTok's video wallpaper) no longer launch by their package
tail. The intent already called these two arguable. Both apps stay reachable
by what a person says. "facebook" and "tiktok" were a choice before this
change and are a choice after it. "facebook" scores 100 for both the
QuickWebApps Facebook and the Waydroid one. "tiktok" puts a Chrome web app
(88) above the wallpaper (86). **The approver must accept these two losses by
name** (intent constraint). If the approver refuses them, the fallback is B:
0 lost, and `vending` stays a clear match for the Play Store.

D was rejected because it loses all 23. E was rejected because it loses 5 and
still launches "debug".

### 2. Numeric or version tails? → in scope

A last part with no letter never counts. `04` from `ubuntu-24.04` stops
launching, and "ubuntu" is still a choice, exactly as today.

### 3. Waydroid package tails? → yes, handled by id prefix

A Waydroid entry is `waydroid.<android package>`. An Android package name is a
developer's identifier, not the label a launcher shows. That is why four of
the five false matches come from it (`android`, `vending`, `debug`, and
`katana`/`livewallpaper` as tails nobody says). The entry keeps its full id
and its `Name`, so "instagram" still launches Instagram (measured).

### 4. Does "desktop" need its own guard? → no, it is in the generic set; the test pins the fixture

`desktop` is in the generic set. The unit test pins **the fixture case** (only
Telegram and Chrome, "the desktop" → nothing launched, "telegram" → Telegram).
That is the only case that fails today. The p620 case is already a choice
because Claude-Desktop, Gemini-desktop and others contain the word, so a test
of it would pass without the fix and prove nothing. `tools/verify_find.py`
prints the p620 verdict for anyone who runs it.

### 5. Measure razer before the spec? → no, as a pre-merge check instead

The rule does not depend on one host's list. Razer is measured read-only
before merge: `tools/verify_find.py` over ssh with the session environment
exported (see `driving-razer`). It then prints the new generic-tail section
(below). A generic tail that still launches there, or a name-shaped tail that
stops launching, goes back to the approver. It does not block writing the
plan.

## Design

`src/omarchy_voice/capabilities.py` only.

1. Add, just above `find_apps` (after `_initials`, which ends at `:498`):

   ```python
   # A last id part that names a package, toolkit, platform or role, not an app (#96).
   GENERIC_ID_PARTS = frozenset({"desktop", "app", "application", "client", "gtk",
                                 "qt", "android", "debug", "setup"})


   def _id_name(app_id: str) -> str:
       """The last part of a reverse-DNS id when it can be the app's name, else "".

       `dev.zed.Zed` → "zed". Not `org.telegram.desktop` (a generic word), not
       `ubuntu-24.04` (a version), and not `waydroid.<android package>`, whose
       tail is a developer's identifier ("android" is Instagram).
       """
       lowered = app_id.lower()
       tail = lowered.rsplit(".", 1)[-1]
       if (lowered.startswith("waydroid.") or tail in GENERIC_ID_PARTS
               or not any(ch.isalpha() for ch in tail)):
           return ""
       return tail
   ```

2. `capabilities.py:518-519`: build `ident` from `_id_name(row["id"])` instead
   of `row["id"].lower().rsplit(".", 1)[-1]`. The existing `- {""}` drops the
   empty string. The full id and the command stay in `ident`, so
   `org.telegram.desktop`, `waydroid.com.instagram.android` and `ubuntu-24.04`
   still match when said in full. The fuzzy fallback (`:531-533`) iterates
   `ident`, so a dropped tail is not fuzzy-matched either. The prototype did
   the same.

3. `find_apps`'s docstring (`:502-508`): "the whole name, id, command or the
   id's last part when it names the app".

Nothing else changes. `clear_match` (`:543-553`), `Executor._resolve_app`
(`tools.py:2140-2161`) and `_tool_find_app` (`tools.py:2163`) all read
`find_apps`, so every engine (local, realtime, Claude, MCP) gets the fix
through `Executor.call`. Why here: this is the one place the tail becomes a
score, and every caller routes through it.

### Interactions

- **#88** (`fix/88-desktop-suffix-in-id`, plan approved). Its plan's
  "Precondition" section and step 1 (`plan/2026-09-24-88-desktop-suffix-in-id.md:75-96`)
  stop unless #96 is merged, or unless both land in one PR with #96's steps
  first. After this change, the fixture's "the desktop" launches nothing, so
  #88's `_desktop_id` fix can no longer turn it into a Telegram launch. #88's
  own rollback note (`:223-224`) already treats #96 as safe alone.
- **#71's router.** It does not use `find_apps`. `router.py` on `origin/main`
  and on `origin/feat/71-answer-without-the-model` contains no `find_apps`,
  `clear_match` or `app_index`. Its only launch route is the fixed
  `omarchy_cli "launch terminal"` (`router.py:158`). Its window matching
  (`router.py:112`) is against open windows. An "open <app>" request falls
  through to an engine and reaches `launch_app` → `_resolve_app`, which this
  change covers. Nothing to coordinate.

## Alternatives rejected

- **A, the issue's stoplist alone.** It fixes the issue's `desktop`/`app`/`gtk`
  but leaves all five real false clear matches (measured 5/0).
- **A+, a list of the observed offenders.** It is 0/0 on p620 only because it
  was fitted to p620. `vending` is not a generic word, and the next Waydroid
  or versioned id is a new bug.
- **B without the Waydroid prefix.** 0 lost, but "vending" still launches the
  Play Store. This is the fallback if the approver refuses the two losses.
- **D, demote the tier to 90.** It is safe, but "texteditor", "diskutility"
  and 21 others stop launching (0/23). The intent requires keeping them.
- **E, require overlap with `Name`.** It loses `diskutility`, `shaper`,
  `simplescan`, `tubeconverter` and `katana`, and still launches `debug`
  ("Debug COSMIC Connect").
- **Drop the tier entirely.** This is D's result at 0: all 23 lost.

## Risks

- **An app whose real name is a generic word** and whose `Name` differs from
  that word, e.g. a hypothetical `org.example.Client` named "Foo". Saying
  "client" no longer launches it. Its `Name` still does, and the full id still
  does. The set is kept to nine words for this reason.
- **A Waydroid app said by its package tail** (`katana`) stops launching. This
  is the accepted cost above. Its `Name` still works.
- **Razer** has not been measured. It may have its own generic tails the set
  misses, or a name-shaped tail it loses. The pre-merge check covers it. The
  worst case is today's behaviour for a missed word, never a new launch.
- No new launches are possible. The change only removes candidates from
  `ident`, so a score can only fall, and `clear_match` can only return
  None where it returned an id before, or the same id.

## Verification

1. **Unit tests, fixture dir** (`tests/test_find_apps.py`, a new case class
   with its own entries, so the existing `ENTRIES` and their verdicts do not
   move). Entries copy p620's ids and `Name`s. Assert `clear_match` is None
   for every false match: `android` (`waydroid.com.instagram.android`,
   Instagram), `vending` (`waydroid.com.android.vending`, Google Play Store),
   `debug` (`waydroid.org.cosmic.cosmicconnect.debug`), `setup`
   (`org.freedesktop.IBus.Setup`), `04` (`ubuntu-24.04`), and "the desktop"
   (`org.telegram.desktop` plus a Chrome entry only). Assert it is unchanged
   for a sample of the 23: `texteditor` → `org.gnome.TextEditor`,
   `diskutility` → `org.gnome.DiskUtility`, `xournalpp` →
   `com.github.xournalpp.xournalpp`, `shaper` → `org.gtk.Shaper`,
   `tubeconverter` → `org.nickvision.tubeconverter`, `demo4` →
   `org.gtk.Demo4`, plus `zed` → `dev.zed.Zed`, "instagram" → Instagram,
   "telegram" → Telegram and the full id `org.telegram.desktop` → itself.
   One `launch_app` test with the recording `_shell` (the existing `launched`
   helper) checks that "open android" runs nothing.
2. **`tools/verify_find.py`**: add a second list printed the same way, the
   eight generic tails (`android setup debug 04 vending desktop app gtk`) and
   the 23 legitimate tails. Expected on p620: no generic tail shows `LAUNCH`.
   21 of the 23 show the same `LAUNCH` as today, and `katana`/`livewallpaper`
   show `nothing`. The 24 #70 requests are unchanged (measured 0 changes).
3. `python3 -m unittest discover -s tests`: all pass.
4. `nix flake check --no-write-lock-file`: passes.
5. Razer, read-only, before merge: step 2's script over ssh (Q5).
