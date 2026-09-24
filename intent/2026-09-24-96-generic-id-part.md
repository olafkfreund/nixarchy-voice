---
status: draft
issue: 96
author: olafkfreund
---

# Intent: a generic last part of a desktop id must not count as the app's name

Closes #96.

Line numbers are against `main` at `72f9ab6`. The measurements are from p620's
real app index, read with `capabilities.app_index()` and `find_apps()`
(`PYTHONPATH=src`). The `launch_app` rows used a recording `_shell`. The two
"before and after #88" rows used a fixture dir. Nothing was launched.

## Problem

`find_apps` gives a query score 100 when it equals the last dotted part of a
desktop id (`capabilities.py:518`, `row["id"].lower().rsplit(".", 1)[-1]`).
The rule is there for reverse-DNS ids whose last part is the app's name.
`clear_match` launches a 100 unasked when nothing else scores within 15
(`capabilities.py:543-553`). But many ids end in a word that is not a name.
It can be a platform (`android`), a role (`setup`, `debug`), a toolkit
(`gtk`), a version (`04`), or the filename suffix itself (`desktop`). That
word then acts as a name the person never said.

### What p620 has

There are 303 visible entries, and 121 of them have dotted ids. These last
parts are generic words or not names at all:

| Last part | Id | Name | `find_apps(part)` today |
| --- | --- | --- | --- |
| `android` | `waydroid.com.instagram.android` | Instagram | **clear match**, 100 |
| `setup` | `org.freedesktop.IBus.Setup` | IBus Preferences | **clear match**, 100 |
| `debug` | `waydroid.org.cosmic.cosmicconnect.debug` | Debug COSMIC Connect | **clear match**, 100 |
| `04` | `ubuntu-24.04` | Ubuntu-24.04 | **clear match**, 100 (a version fragment, not a dotted name) |
| `vending` | `waydroid.com.android.vending` | Google Play Store | **clear match**, 100 |
| `desktop` | `org.telegram.desktop` | Telegram | 100, but a choice: Claude-Desktop and Gemini-desktop score 89 |
| `app` | `com.flycrys.app`, `com.seance.app` | Flycrys, Seance | two at 100, a choice |
| `gtk` | `proton.vpn.app.gtk` | Proton VPN | 100, but a choice: GTK Demo scores 89 |

The other words named in the issue match no last part here: `client`, `qt`,
`application`, `browser`. The same goes for `editor`, `viewer`, `player`,
`manager`, `settings`, `launcher`, `web`, `gnome` and `kde`. None of them gives
a clear match today through the last part.

Said the way a person says it, the clear matches launch. Through
`Executor.call("launch_app", …)` with `_shell` recorded:

```
'android' -> ran [uwsm-app, waydroid.com.instagram.android.desktop]
'setup'   -> ran [uwsm-app, org.freedesktop.IBus.Setup.desktop]
"open android" / "open the debug" / "run setup" / "open 04" -> clear matches as above
```

So **"open android" launches Instagram today**, with #88 not involved.

### "Open the desktop", before and after #88

On a fixture dir with only Telegram (`org.telegram.desktop.desktop`) and Chrome
installed, which is a host with no other "desktop" app:

```
find_apps('open the desktop') -> [(100, 'org.telegram.desktop')], clear: org.telegram.desktop
today     : RESOLVE 'the desktop' → org.telegram.desktop
            refused: no desktop entry named 'org.telegram'   (#88's bug), ran []
after #88 : RESOLVE 'the desktop' → org.telegram.desktop
            ran [uwsm-app, org.telegram.desktop.desktop]     (Telegram opens)
```

After #88 the fixture used #88's approved `_desktop_id` rule, applied as a
local patch in the script. On p620 itself "the desktop" is a choice today
("more than one app fits 'the desktop': Telegram, Claude-Desktop,
Gemini-desktop, GitHub Desktop, Pear Desktop…"). That only happens because
other entries have "desktop" in their names. Remove those, and the fixture
result is what p620 would do. The #88 spec's second run described p620 as
Telegram and Chrome only. The real index is fuller than that, and it protects
p620 by accident.

### What must keep working

28 clear matches on p620 exist **only** because of the last-part tier. This
was measured by re-running `find_apps` with the last part removed from
`ident` and comparing `clear_match` for each id's last part. Subtract the
generic ones above (`04`, `android`, `debug`, `setup`, `vending`), and 23
legitimate ones remain. In each, the last part is the app's name joined into
one word:

`boxbuddyrs`, `colorprofileviewer`, `demo4`, `diskutility`, `fileroller`,
`gdmsettings`, `gemini4352`, `katana`, `lanmouse`, `livewallpaper`,
`lmstudio`, `nodeeditor`, `printeditor4`, `shaper`, `simplescan`,
`soundrecorder`, `systemmonitor`, `texteditor`, `tubeconverter`, `turtle`,
`waydroidhelper`, `widgetfactory4`, `xournalpp`.

Two of those are arguable. `katana` (Facebook) and `livewallpaper` (TikTok)
are Android package tails, not names a person would say.

**`zed` → `dev.zed.Zed`, the issue's example, does not depend on this tier.**
`Name=Zed` scores 100 on its own. The same holds for `nautilus`, `krita`,
`ghostty`, `wezterm`, `remmina` and most others: their name already equals the
last part.

## Proposed outcome

- A query that is only a generic word never becomes a clear match through the
  last part of an id. "Open the desktop", "open android" and "run setup"
  launch nothing unasked. They get the usual lookup or choice.
- Every one of the 23 legitimate last-part matches above still resolves to the
  same id. `zed`, `nautilus` and the other name matches are unaffected.
- A case in `tools/verify_find.py` and a unit test on a fixture dir cover
  both directions.
- #88 can land without making "open the desktop" launch Telegram.

## Affected users and systems

- `src/omarchy_voice/capabilities.py`: `find_apps` (`501-540`), and
  indirectly `clear_match` and `Executor._resolve_app` (`tools.py:2140`), which
  launches a clear match.
- `find_app` tool output. It lists the same scores, so a generic tail would
  rank lower there too.
- Every engine (local, realtime, Claude, MCP). They all reach `launch_app`
  through `Executor.call`.
- Hosts: p620 (Telegram, Waydroid, IBus installed). Razer's index was not
  measured.
- Tests: `tests/test_find_apps.py`, `tools/verify_find.py`.

## Constraints

- It must land before #88 or in the same PR (#88's plan, precondition step 1).
- It must not move any of the 23 legitimate last-part matches, or any #70
  corpus result in `tools/verify_find.py`, unless the approver accepts the
  change by name.
- Matching stays lexical and local. There is no model call and no new
  dependency.
- The #70 rule stays: a close spelling never launches unasked.
- Tests use fixture dirs only. Nothing is launched while verifying.

## Open questions

1. **Stoplist, a structural rule, or both?** The issue proposes a stoplist
   ("desktop", "app", "application", "client", "gtk", "qt"…) plus "the last
   part equals the file suffix". On p620 the real offenders are `android`,
   `setup`, `debug`, `vending` and `04`, and none of them is on that list. A
   structural alternative is to use the last part only when it is not also a
   word in the app's own `Name`. That catches `desktop` for Telegram, but
   `debug` appears in "Debug COSMIC Connect", so it would not. Another
   alternative is to drop the last part to below the clear-match threshold
   (for example 90) instead of removing it. It then stays findable but never
   launches unasked. That keeps all 23, but they would no longer launch by
   their joined form ("texteditor"). Which way?
2. **Are numeric or version tails in scope?** `04` from `ubuntu-24.04` comes
   from a dot in a version, not a reverse-DNS id. Should a tail that is all
   digits never count?
3. **Waydroid package tails** (`android`, `vending`, `katana`,
   `livewallpaper`, `debug`): are they worth special handling, for example
   ignoring the last part for `waydroid.*` ids? Or is the general rule enough?
4. **Does "desktop" need its own guard, given that on p620 it is a choice
   today?** The fixture shows a clear launch on a host where no other name
   contains "desktop". Should the test pin the fixture case, the p620 case, or
   both?
5. **Should razer's index be measured before the spec**, so that the list of
   legitimate matches that must survive covers both hosts?
