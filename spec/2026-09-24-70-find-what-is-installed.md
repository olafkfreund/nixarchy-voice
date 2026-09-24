---
status: draft
issue: 70
intent: intent/2026-09-24-70-find-what-is-installed.md
---

# Spec: the assistant must be able to find what is installed, by the name a person uses

## The intent's three open questions, answered

The intent was approved without answers to its three questions, so each is
decided here, and the third by measurement. Any of them can be rejected at
this gate.

### Q3 first, because it was measured: lexical matching over local metadata, standard library only

A scratch prototype (not repo code) matched 24 real requests against this
machine's desktop entries. It used `Name`, `GenericName`, `Keywords`,
`Comment`, the desktop id and the `Exec` basename, with `difflib` for close
spellings. There was no new dependency and no model.

| Request | Top hit |
|---|---|
| zed / zedd (typo) | Zed (`dev.zed.Zed`) |
| the file manager | File Manager (`preferred-file-manager`) |
| obsidian / obsidien (typo) | Obsidian (`obsidian`) |
| obs · slack · spotify · firefox · music · image viewer · disk usage · terminal · calculator | the right app, score 100 |
| visual studio code | Visual Studio Code (`code`) |
| my password manager | 1Password, Proton Pass: a correct choice |
| screen recorder | Wayfarer Screen Recorder |
| email | Aerion, Birdtray, Tuta Mail: a reasonable choice |
| **vs code** | **nothing** |
| **code** | only VS Code; it should be a choice with Claude Code |
| **browser** | Icon Browser tied with Web Browser |
| **notes** | Xournal++, weakly; Obsidian does not declare "notes" |

That is **20 of 24** with the right top hit, at 15-30 ms per query. Three of
the four misses have lexical fixes (design §2). "Notes → Obsidian" is the one
genuinely semantic case. The model itself is the semantic layer there: it
knows Obsidian is for notes once a lookup by "obsidian" is open to it. So
Model2Vec is **not** added. `rapidfuzz` is not added either, since `difflib`
handled the typos. A semantic tier stays a follow-up, justified only if the
request set grows misses.

### Q1: desktop entries and their actions now; PATH, units and MCP as follow-ups

This issue indexes launchable apps: every visible desktop entry and its
`Actions=`. That answers "open X", which is the measured failure. PATH
commands (3,817 names, most of them helpers, needing a tldr or `--help`
summary to be findable) and user units and MCP servers become two follow-up
issues. Each is a different question ("use X to do Y", "is X running") with
its own risks, since a PATH command is not launched the way an app is.

### Q2: a lookup tool, and `launch_app` accepts the name a person uses

Two parts, and neither changes the prompt from turn to turn:

- **`find_app(query)`**, a read-only tool shaped like `omarchy_help`. It
  returns ranked candidates with their ids and actions, for exploring ("what
  have I got for email?").
- **`launch_app` resolves a name itself** when exactly one entry is a clear
  match. "open zed" becomes one `launch_app("zed")` call that launches
  `dev.zed.Zed`, with **no extra round trip**. That meets the intent's "the
  common case costs none beyond the launch itself". An ambiguous name returns
  the choices, and runs nothing.

The per-turn shortlist (the daemon matching the utterance before the model is
asked) is #71's router, and it will use the same `find_apps` function.

## Design

### 1. `capabilities.app_index()`: scanned when asked, never cached

It returns one row per visible desktop entry: `id`, `name`, `generic`,
`keywords`, `comment`, `command` (the `Exec` basename) and `actions`. It
reads only the `[Desktop Entry]` group. It skips `NoDisplay=true`,
`Hidden=true`, entries whose `OnlyShowIn` excludes `$XDG_CURRENT_DESKTOP`,
and entries whose `NotShowIn` includes it. An id seen twice keeps the first
one, in `app_dirs()` order.

Measured at **36 ms for 303 entries**. That is cheap enough to scan on every
lookup, so an app installed or removed shows up on the next call, with no
cache and no invalidation. This meets the intent's "a stale index is worse
than a slow one" directly. `ponytail:` comment: the upgrade path, if the scan
ever shows up in a trace, is caching on the application directories' mtimes.

### 2. `capabilities.find_apps(query, limit=8)`: tiers, best first

The query's words, minus filler (`open`, `launch`, `start`, `the`, `my`,
`app`, …), are scored against each row:

| Score | Match |
|---|---|
| 100 | the whole query equals the name, the id, the id's last dotted part (`Zed` in `dev.zed.Zed`), or the command |
| 95 | the query's letters are the initials of the name's first words (`vs` + `code` → **V**isual **S**tudio **Code**) |
| 90 − extra words | every query word is a word of the name |
| 70 | every query word is in the name, GenericName or Keywords |
| 40 | …or in Comment |
| ≤ 60 | `difflib` ratio ≥ 0.8 against the name, id or command (typos) |

Ties are broken by preferring Omarchy's `preferred-*` entries, since on this
desktop "browser" means the configured default, and then by name.

**A clear match is one rule: exactly one row at 100, and no other row within
15 points of it.** "zed" is clear (nothing else comes close). "code" is not:
VS Code scores 100 and Claude Code 89 ("code" is one of its two words), so
it comes back as a choice, as the intent asked. "discord" is not either, with
two entries at 100.

### 3. The manifest stops listing apps

`{apps}` in the manifest template (`capabilities.py:726-728`) becomes one
fixed line: *"Every installed application can be opened by the name a person
uses: pass it to launch_app. To see what is installed for a purpose, call
find_app."* `installed_apps()` and its `limit=28` are deleted.

This also fixes a second staleness bug found while reading. The manifest's
cache key (`capabilities.py:733-751`) covers versions and three files'
mtimes, **not the application directories**, so today's 28-entry list does
not even refresh when an app is installed. The manifest no longer depends on
installed apps at all, so it stays byte-stable, as #69 needs.

### 4. `launch_app` resolves names before the policy gate sees them

In `Executor._call_locked`, before `describe()` (`tools.py:1641`), a call to
`launch_app` without a `url` whose `app` (minus any `:action`) is **not** an
existing desktop id goes through `find_apps`:

- **A clear match:** `args["app"]` is replaced with the id (keeping any
  `:action`), and one line is recorded: `resolved 'zed' → dev.zed.Zed`. Then
  it continues as before. `describe` and the policy check therefore see
  `launch dev.zed.Zed`, so a deny or confirm rule written against an id
  (`1password`) still catches "my password manager".
- **Candidates but no clear match:** it returns `Result(False, "…more than one
  app fits 'code': Visual Studio Code (code), Claude Code (claude-code). Ask
  which, or call launch_app with the id.")` **before** the policy check.
  Nothing has been run, and nothing is described as run.
- **Nothing matched:** it continues unchanged, so the existing refusal (or the
  `allow_shell` command-line path, `tools.py:2027-2042`) applies exactly as
  today.

`launch_app`'s description and the manifest's wording stop saying "by
desktop entry id … from the application list", and say "an installed app by
name or desktop id".

### 5. `find_app`, the tool

The schema sits beside `omarchy_help` (`tools.py:744`), with `query`
required, described as *"What is installed for a name or a purpose — "zed",
"password manager", "screen recorder". Returns ids and actions. launch_app
already takes a plain name, so call this only to explore or to choose."*
`_tool_find_app` formats up to 8 rows as
`Name (id) — GenericName [actions: new-window, …]`. If nothing matches, it
says so plainly and says the app is not installed under that name. It is added
to `READ_ONLY_TOOLS` (`tools.py:57`). MCP and the realtime engine take the
schema from the same list, so it reaches every entry shape without separate
work.

### 6. A request set that stays

The 24 requests above become a test over a fixture directory of hand-written
`.desktop` files (tests cannot depend on this machine's apps), plus
`tools/verify_find.py`. That script runs the same requests against the real
machine and prints the top hits, the way `tools/verify_matching.py` does for
`click_text`. It is the yardstick for whether a semantic tier is ever needed.

## Alternatives rejected

- **List every app in the prompt.** 303 rows is thousands of tokens, changes
  with every install, and breaks the stable prefix #69 depends on.
- **Keystroke's `descriptions.json` as the corpus.** Only 32 of its 80 app
  entries exist here (see the intent's correction).
- **Model2Vec / embeddings now.** Measured: lexical gets 20 of 24, and the one
  semantic miss is covered by the model. It would add a Nix-built model and a
  tokenizer for one case.
- **`rapidfuzz`.** `difflib` handled the typos at 15-30 ms per query. It would
  be a new dependency with nothing measured to show it is needed.
- **A persistent index, refreshed on inotify or on a timer.** That means
  staleness and invalidation code, to save 36 ms.
- **Resolving names inside `_tool_launch_app`.** The policy check runs before
  that (`tools.py:1641-1643`), so the gate would judge `launch zed`, not
  `launch dev.zed.Zed`.
- **Also indexing PATH, units and MCP now.** Different questions and different
  risks. They are follow-ups (Q1).

## Risks

- **A clear match that is wrong.** "Discord" has two entries (`omarchy-Discord`,
  `discord`), both at 100, so it is a choice, not a guess. The 15-point margin
  is a tuning knob, and the request set is how it is checked.
- **Launching something the model did not name exactly.** Resolution only
  maps a name to an entry that is already launchable from the app launcher,
  and the resolved id still goes through the policy gate.
- **The manifest's wording changes**, so the cached manifest is regenerated
  once (its key includes `capabilities.py`'s mtime). Nothing to migrate.
- **Hosts:** none beyond the user service. No Nix, config or packaging change.

## Verification

- Unit (`pytest tests -q` in `nix develop`), over a fixture application
  directory:
  - `app_index` skips `NoDisplay`, `Hidden`, `OnlyShowIn=GNOME;` and
    `NotShowIn=Hyprland;`, reads only `[Desktop Entry]` (not an action
    group's `Name=`), and keeps the first of a duplicate id.
  - `find_apps`: "zed" and "zedd" give `dev.zed.Zed`; "the file manager"
    gives the entry with `GenericName=File Manager`; "vs code" gives `code`;
    "code" is not a clear match; "discord" is not a clear match; "browser"
    prefers `preferred-web-browser`; "flurble" gives nothing.
  - `_call_locked`: `launch_app("zed")` records the resolution, and the
    policy sees `launch dev.zed.Zed` (a deny rule on `dev.zed.Zed` refuses
    it). `launch_app("code")` returns the choice and never reaches policy or
    launch. `launch_app("dev.zed.Zed")` is untouched.
  - `find_app` is in `READ_ONLY_TOOLS` and appears in the MCP tool list.
  - The manifest contains no app rows and names `find_app`.
- `nix flake check --no-write-lock-file` passes.
- Live: `tools/verify_find.py` prints the 24 requests' top hits on this
  machine and the table above. Then the three dry runs from the intent again,
  `omarchy-voice -n say --no-confirm "open zed"` (and "open obsidian", "open the
  file manager"): each is **one** `launch_app` call naming the right id, with
  no `ToolSearch`, no guessed route and no second attempt, and the time is
  compared with the intent's 7.6-9.7 s.
