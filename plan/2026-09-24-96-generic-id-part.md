---
status: draft
issue: 96
spec: spec/2026-09-24-96-generic-id-part.md
---

# Plan: a generic last part of a desktop id must not count as the app's name

Every line number below was checked against `origin/main` at `35ef252`
(940 passed). The spec cited `72f9ab6`. The lines it names in
`capabilities.py` and `tools.py` have not moved since then.

## Approved decisions, carried over from the spec

The approver approved the spec as written: rule C, accepting the two losses
named in decision 3.

1. **The problem, measured on p620** (303 entries, 121 with a dotted id).
   `find_apps` scores 100 when the whole query equals the last dotted part of
   an id (`capabilities.py:518`). On p620, 28 last parts are the only thing
   that gives their app a clear match. Five of them are false: `android`
   (Instagram), `setup` (IBus Preferences), `debug` (Debug COSMIC Connect),
   `vending` (Google Play Store) and `04` (`ubuntu-24.04`). On a fixture that
   holds only Telegram and Google Chrome, "the desktop" launches Telegram. The
   other 23 are legitimate and must keep working:
   `gemini4352 katana livewallpaper lmstudio lanmouse turtle boxbuddyrs
   gdmsettings diskutility fileroller soundrecorder systemmonitor texteditor
   tubeconverter xournalpp waydroidhelper colorprofileviewer simplescan demo4
   printeditor4 shaper widgetfactory4 nodeeditor`. The spec gives the count,
   not the list. The list above comes from a read-only rescan of p620 for this
   plan, and it has the same 303/121 entries.

2. **The candidates the spec measured.** "False" is how many of the 5 false
   matches still launch. "Lost" is how many of the 23 no longer resolve.

   | Candidate | false | lost | desktop, p620 | desktop, fixture | #70 |
   | --- | --- | --- | --- | --- | --- |
   | today | 5 | 0 | choice | launch Telegram | — |
   | A. the issue's stoplist (`desktop app application client gtk qt`) | 5 | 0 | choice | nothing | 0 |
   | A+. A plus `android debug setup vending 04` | 0 | 0 | choice | nothing | 0 |
   | B. generic word (A ∪ `android debug setup`), or no letter | 1 (`vending`) | 0 | choice | nothing | 0 |
   | **C. B, and never the last part of a `waydroid.*` id** | **0** | **2** | choice | nothing | 0 |
   | D. last-part tier scored 90 | 0 | 23 | choice | choice | 0 |
   | E. last part counts only if it shares a 4+ letter word with `Name` | 1 (`debug`) | 5 | choice | nothing | 0 |

   The spec rejected A because it still launches all five. It rejected A+
   because the list fits one machine: `vending` is not a generic word, and
   the next Waydroid app brings its own tail. It rejected B because "vending"
   still launches the Play Store. B is the fallback only if the approver ever
   takes back the two losses. It rejected D because it loses all 23, and E
   because it loses 5 and still launches "debug". Dropping the tier
   altogether is D's result.

3. **Rule C, exactly.** The last part of an id counts as a name only when all
   three hold:
   - it is not in `GENERIC_ID_PARTS = {desktop, app, application, client,
     gtk, qt, android, debug, setup}` (case-insensitive);
   - it contains at least one letter, so a version fragment such as `04` is
     not a name;
   - the id does not start with `waydroid.`. A Waydroid id's last part is an
     Android developer's identifier, so it never counts.

   **Accepted losses, by name:** `katana` (`waydroid.com.facebook.katana`,
   Facebook) and `livewallpaper`
   (`waydroid.com.zhiliao.musically.livewallpaper`, TickTock Video Wallpaper by
   TikTok) no longer launch by their package tail. Their `Name`s still work,
   and "facebook" and "tiktok" were already a choice before this change and
   stay one.

4. **Numeric and version tails are in scope.** A tail with no letter never
   counts. "ubuntu" stays a choice, and "ubuntu-24.04" said whole still
   matches the `Name`.

5. **"desktop" needs no guard of its own.** It is in the generic set. The unit
   test pins the fixture case, the only case that fails today. On p620,
   "desktop" is already a choice, so a test there would pass without the fix.

6. **Where: `capabilities.py` only.** Add a `_id_name(app_id)` helper and use it
   where `find_apps` builds `ident`. The full id and the command stay in
   `ident`. The fuzzy fallback iterates `ident`, so a dropped tail is not
   fuzzy-matched either. `clear_match`, `Executor._resolve_app` and
   `_tool_find_app` are unchanged, and every engine gets the fix because they
   all call `find_apps`.

7. **No new launch is possible.** The change only removes candidates from
   `ident`. A score can only fall, so `clear_match` either returns the same id
   as before or returns None where it used to return one.

8. **Razer is measured read-only before merge.** It does not block the plan.
   If a generic tail still launches there, or a name-shaped tail stops
   launching, the result goes back to the approver.

9. **#71's router does not use `find_apps`.** There is nothing to coordinate.

## Relation to #88

#88's approved plan (branch `fix/88-desktop-suffix-in-id`,
`plan/2026-09-24-88-desktop-suffix-in-id.md:75-96`) has a precondition, and
its step 1 stops unless #96 has landed. The reason: once #88 lets
`launch_app` accept `org.telegram.desktop`, "the desktop" on the fixture
would launch Telegram. **This plan unblocks #88.** `DesktopWordTests` (step 2) is
the exact fixture check #88's step 1 runs. #88's rollback note (`:223-224`)
already treats #96 as safe on its own.

## Spec points this plan settles

- **The full id `org.telegram.desktop`.** Spec verification 1 lists "the full
  id `org.telegram.desktop` → itself" among the unchanged cases. It is not a
  `find_apps` match on `main` and will not be one after this change.
  `_words` splits the query at the dots, so the top score is 54. As a
  `launch_app` argument it goes through `_resolve_app`, which strips
  `.desktop` (`tools.py:2149`). It then finds no `org.telegram` entry and
  fails. That failure is bug #88, not this issue. **Resolution:** this plan
  does not assert the full id. #88's tests pin it, and #88 lands after this
  plan. The spec's design point 2 says the full id "still matches when said
  in full". That holds for `ubuntu-24.04`, whose `Name` has the same words. It
  does not hold for dotted ids, and it was not true before either. No code
  depends on it.
- **The 23 legitimate tails.** The spec gives the count and a sample. The full
  list is in decision 1, taken from the read-only rescan.

## Steps

0. **Baseline.** `nix develop -c pytest tests -q` → verify by
   `940 passed` (measured on this branch, docs-only on top of `35ef252`).

1. **`tests/test_find_apps.py`: let a case class bring its own entries.**
   `AppCase.setUp` writes the module-level `ENTRIES` (`:54-56`). Add a class
   attribute `entries = ENTRIES` to `AppCase` (`:46`), and change the loop to
   `self.entries.items()`. The existing classes keep `ENTRIES`, and their
   verdicts do not move → verify by the suite still at 940 passed.

2. **`tests/test_find_apps.py`: add the failing tests first.** Add the two
   classes below, after `MatchTests` (`:93-116`). The ids, `Name`s and commands
   are copied from p620:

   - `GenericTailTests(AppCase)` with `entries = TAIL_ENTRIES`:
     `waydroid.com.instagram.android` (Instagram),
     `waydroid.com.android.vending` (Google Play Store),
     `waydroid.com.android.chrome` (Chrome),
     `waydroid.org.cosmic.cosmicconnect.debug` (Debug COSMIC Connect),
     `waydroid.com.facebook.katana` (Facebook), `org.freedesktop.IBus.Setup`
     (IBus Preferences, `Exec=ibus-setup`), `ubuntu-24.04` (Ubuntu-24.04,
     `Exec=distrobox …`), `org.gnome.TextEditor` (Text Editor),
     `org.gnome.DiskUtility` (Disks), `com.github.xournalpp.xournalpp`
     (Xournal++, `Exec=xournalpp-wrapper`), `org.gtk.Shaper` (Icon Editor),
     `org.nickvision.tubeconverter` (Parabolic,
     `Exec=org.nickvision.tubeconverter`), `org.gtk.Demo4` (GTK Demo,
     `Exec=gtk4-demo`), `dev.zed.Zed` (Zed). Waydroid entries use
     `Exec=waydroid app launch <package>`.
     - `test_a_generic_tail_is_not_a_name`: for each of `android`, `setup`,
       `debug`, `vending`, `04`, as a subTest, `self.clear(q)` is None.
     - `test_a_name_shaped_tail_still_launches`: `texteditor`, `diskutility`,
       `xournalpp`, `shaper`, `tubeconverter`, `demo4` and `zed` each clearly
       match their id. "instagram" clearly matches
       `waydroid.com.instagram.android`.
     - `test_a_waydroid_package_tail_is_not_a_name`: `katana` is None, and
       "facebook" clearly matches `waydroid.com.facebook.katana`. This pins the
       accepted loss and shows that the `Name` still works.
     - `test_open_android_runs_nothing`: use the same `launched` helper as
       `LaunchByNameTests` (`:119-124`). Move it up to `AppCase`, or call it as
       `LaunchByNameTests.launched(self, …)`, whichever is the shorter diff.
       `launch_app("android")`, then `shell.assert_not_called()`.
   - `DesktopWordTests(AppCase)` with `entries = {"org.telegram.desktop":
     "Name=Telegram\nExec=Telegram -- %u\n", "google-chrome": "Name=Google
     Chrome\nExec=google-chrome-stable\n"}`:
     - "the desktop" and "desktop": `self.clear` is None.
     - "telegram": `self.clear` is `org.telegram.desktop`.

   → verify by `nix develop -c pytest tests/test_find_apps.py -q`. On `main`,
   exactly these fail: the 5 generic tails (one test, 5 subTests), `katana`,
   `open android` (the shell was called with Instagram), and both desktop
   words. Every name-shaped, "instagram", "facebook" and "telegram"
   assertion passes. A scratch prototype confirmed these verdicts on
   `main` and under rule C.

3. **`src/omarchy_voice/capabilities.py`: add `GENERIC_ID_PARTS` and
   `_id_name`.** Put them between `_initials` (ends `:498`) and `find_apps`
   (`:501`), exactly as in the spec:

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
   → verify by step 6.

4. **`capabilities.py:518-519`: build `ident` from `_id_name`.**
   `ident = {row["id"].lower(), _id_name(row["id"]), row["command"].lower()} - {""}`.
   The existing `- {""}` drops the empty string. → verify by step 6.

5. **`capabilities.py:502-504`: the `find_apps` docstring.** Change "100 the
   whole name, id or command" to "100 the whole name, id, command, or the id's
   last part when it names the app (#96)". → verify by reading the diff.

6. **Run the file, then the suite.** `nix develop -c pytest
   tests/test_find_apps.py -q` → verify by all passing. Then
   `nix develop -c pytest tests -q` → verify by 940 plus the new tests, with no
   new failures.

7. **Mutation check.** Apply each mutant alone, run
   `tests/test_find_apps.py`, then restore:
   - drop the `startswith("waydroid.")` clause → the `vending` subTest and
     the `katana` test fail;
   - drop the `isalpha` clause → the `04` subTest fails;
   - remove `"desktop"` from `GENERIC_ID_PARTS` → `DesktopWordTests` fails;
   - replace `_id_name(row["id"])` with the old `rsplit` → every test from
     step 2 that failed on `main` fails again.

   → verify by each mutant failing at least one new test, and `git diff`
   showing no mutant left behind.

8. **`tools/verify_find.py`: add the tails.** Add two lists after `REQUESTS`:
   `GENERIC_TAILS = ["android", "setup", "debug", "04", "vending", "desktop",
   "app", "gtk"]` and `NAME_TAILS`, the 23 from decision 1. Loop over the three
   lists with a heading line each, and keep the existing print line unchanged.
   Add one docstring line: the tail lists come from #96, and none of
   `GENERIC_TAILS` should show `LAUNCH`. → verify by `python3
   tools/verify_find.py` on p620: no generic tail shows `LAUNCH` ("desktop" is
   a `choice`). 21 of the 23 show the same `LAUNCH` as today. `katana` and
   `livewallpaper` do not launch (the spec measured `nothing`). The 24 #70
   requests print the same verdicts as on `main`. Compare them with the same
   script run in a worktree of `origin/main`.

9. **Whole check.** `nix flake check --no-write-lock-file` → verify by it
    passing.

10. **Live, read-only.** Nothing is launched and nothing is written outside a
    temporary directory.
    - p620: `python3 tools/verify_find.py` (step 8).
    - razer, before merge, only if `ssh -o ConnectTimeout=5 razer true`
      succeeds. Razer has no checkout of this repo (checked 2026-09-24), so
      stream the branch's `src` and `tools`:
      `git archive HEAD src tools | ssh razer 'd=$(mktemp -d) && tar -x -C "$d"
      && XDG_CURRENT_DESKTOP=Hyprland python3 "$d/tools/verify_find.py"; rm -rf
      "$d"'`. `verify_find.py` only reads desktop entries, so the Hyprland
      socket variables from the razer notes are not needed. Only
      `XDG_CURRENT_DESKTOP` matters, for `OnlyShowIn`.
      → verify by no generic tail showing `LAUNCH`, and every name-shaped tail
      that launched on `main` still launching. Otherwise, stop and take it to
      the approver (decision 8). If razer is unreachable, record "razer
      skipped: unreachable" in the PR.
    - **Measured while writing this plan** (razer reachable, 243 entries, 92
      dotted, rule C prototyped in memory, desktop dirs from the ssh shell's
      `XDG_DATA_DIRS`): `setup` is the only generic tail
      that launches there today, and rule C stops it. All 22 name-shaped
      tails there (including `geforcenow` and `podmandesktop`, which p620 does
      not have) keep their clear match. Razer has no Waydroid entries, so it
      loses nothing. The pre-merge run confirms this against the real code.

11. **Commit and PR.** Commit code and tests together, citing this plan, as
    `fix(find): a generic last id part is not an app's name (#96)`. Any
    deviation updates this file in the same commit. Push, and open a PR that
    closes #96. The PR links the intent, spec and plan, and records the
    razer result from step 10. Once merged, #88's step 1 passes.

## Tests

```
nix develop -c pytest tests -q                      # 940 + new, 0 new failures
nix develop -c pytest tests/test_find_apps.py -q    # new tests: fail on main, pass here
python3 tools/verify_find.py                        # p620, launches nothing
nix flake check --no-write-lock-file                # CI parity
```

Plus the mutation check (step 7) and the razer run (step 10).

## Rollback

This is one squash-merged PR in `capabilities.py`, `tests/test_find_apps.py`
and `tools/verify_find.py`. It has no Nix, config or schema change, and no
persisted state. `git revert <merge>` restores the tail as a name. The five
false clear matches come back, and so do `katana`/`livewallpaper`. If #88 has
merged by then, revert #88 first, or "the desktop" launches Telegram (see
#88's precondition). For a partial rollback to fallback B, delete the
`waydroid.` clause only. `vending` then launches again, and nothing is lost.
