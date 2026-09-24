---
status: approved
issue: 82
spec: spec/2026-09-24-82-installed-commands.md
---

# Plan: find an installed command-line tool by what it does, without finding becoming running

Every `file:line` is checked against `origin/main` at `93c6dac`. On that
commit, `nix develop -c pytest tests -q` gives 988 passed, and
`nix develop -c python3 -m unittest discover -s tests` runs 988 tests OK
(re-run for this plan on 2026-09-24).

## Approved decisions, carried over from the spec

1. **A new read-only tool, `find_command`.** Its only argument is `query`
   (string, required, `additionalProperties: false`). It goes into
   `READ_ONLY_TOOLS` (`tools.py:58-60`), so the confirm gate never holds it and
   dry run still runs it (`tools.py:1784`, `1811`). Deny rules still apply to
   it (#100, `tools.py:204-208`): `find_command "ssh"` is refused on a default
   install, which is what `find_app` does today.
2. **A separate index from `find_apps`.** Only `_words`
   (`capabilities.py:433`) is shared. `find_apps` (`capabilities.py:521-560`),
   `clear_match` (`563-573`), the #96 generic-part rule, `find_app` and
   `launch_app` are not touched. "open zed" still resolves through
   `find_apps`.
3. **What is indexed.** The entries of `os.environ["PATH"]`, leaving out any
   entry under `/nix/store/`. The first directory to list a name wins. Only
   regular files with `os.access(X_OK)` are indexed. Names that start with
   `omarchy-`, `nixarchy-` or `.` are skipped, because `omarchy_help` covers
   them.
4. **Descriptions come from files only.** There is no network call, and no
   binary is ever run. `--help`/`--version` and the `tldr` CLI are rejected.
   - **Man `NAME` lines** are the source that is always present. They are
     read from `man1` and `man8` under each `MANPATH` directory. A `.gz` page
     is read through Python's `gzip` module, not the `gzip` binary. The first
     8 KB of a page is searched for `.SH NAME` (man macros) or `.Nd` (mdoc),
     and the text after `\-` is kept.
   - **tldr page files** add to the man lines when a local cache exists. They
     are read from `~/.cache/tldr/pages/{linux,common}/<name>.md` (the Python
     client's layout), and `linux` wins. The `> ` lines are the description,
     leaving out "More information" and "See also". The `- ` and `` ` `` line
     pairs are the examples. An alias page ("This command is an alias of
     `magick mogrify`") is resolved one hop, to `magick-mogrify.md`. The cache
     is never refreshed. `# ponytail:` one known cache path; add tealdeer's or
     tlrc's when a machine has one.
   - When a command has both, the two descriptions are **joined for
     matching**. Example lines are **shown and never scored**. Scoring them
     ranked `mcat`, `obs` and `steam` alongside `ffmpeg`.
   - **Nothing new goes into the flake.** Shipping tldr pages as a flake input
     (27 MB) is the follow-up if a default install's answers turn out to need
     examples.
   - A command with no description is kept in the index. An **exact-name**
     lookup finds it ("installed, no description on this machine"). A purpose
     search cannot match it.
5. **A staleness check, with no restart needed.** The index is built on first
   use and held in-process. It is keyed on `(realpath, st_mtime_ns)` of every
   PATH, man and tldr directory. The key costs about **1.0 ms** for 57
   directories, and a rebuild costs about **0.30 s**. A new install changes a
   profile symlink's target or a directory's mtime, so the new command is
   findable on the next lookup. A full rescan on every call, the way
   `app_index` works, was rejected because it is 10 times as costly.
6. **Ranking.** If the query is exactly a name on PATH, that command comes
   first, described or not. Otherwise the score is word overlap weighted by
   IDF, over the name's parts and the joined description. Stop words are
   dropped, and each word is cut to a 5-character prefix, so "convert" meets
   "conversion". "colour" does not meet "color", and that is accepted. A
   match on every query word multiplies the score by 1.5. Ties go to tldr,
   then to the shorter name. When there is no exact match, up to 3 close
   spellings come from `difflib.get_close_matches` over all names, so `yt-dl`
   finds `yt-dlp`.
7. **8 candidates are returned**, each with its description. The model does
   the picking: it can rephrase or look a name up exactly, and an exact name
   always resolves.
8. **What the handler prints.** Rows of `  name — description`, with
   `(no description)` when there is none. When the top hit is an exact name,
   up to 4 examples (tldr) or the man `SYNOPSIS` lines go under it. When
   nothing matches, it says "nothing installed matches '<q>'", gives the close
   spellings if there are any, and says "it is not installed under that
   name". The output is capped, and it never includes a path.
9. **`describe`** returns `find commands: '<query>'`.
10. **The tool reaches every engine through `tools_for`**
    (`mcp_server.py:129`, `realtime.py:547`). `_is_read`
    (`claude_backend.py:106-110`) takes it from `READ_ONLY_TOOLS`. The #94
    allowlist `BUILTIN_TOOLS` (`claude_backend.py:123`) needs no change. The
    schema adds about 80 tokens (#69), and none of the index goes into the
    prompt.
11. **Manifest.** One static sentence goes under "Applications installed here"
    (`capabilities.py:861-863`): *"For command-line tools, call find_command
    before saying whether something is installed or how to use it."*
12. **No persona steering toward `run_in_terminal`.** `persona.py:118-121`
    stays as it is. The spec's Q1 finding, that `run_in_terminal` runs any
    command with `allow_shell` off, is tracked in **#112 (OPEN)**, and #112
    gates any such steering. #82 adds no way to run anything.
13. **The prototype numbers** (p620, the daemon's PATH, joined descriptions):
    3,262 commands, 1,860 of them described (1,579 from man alone, 281 more
    from tldr). The index builds in 0.30 s and a query takes 2–5 ms. For the
    12 scored requests, **top-8 finds 10/12**. Top-1 is 3/12 in the spec's
    summary table (see "Spec ambiguities" below).
14. **The known misses are accepted**: `jq` for "json query" and `fd` for
    "find files by name". `hyprpicker` ranks 3rd for "colour". If real
    requests keep missing, the next step is a small synonym table, not a
    model.
15. **Out of scope:** changing or gating `run_in_terminal` (#112), p620's
    `deny_patterns_replace` (#109), and sharing an index with #83.

## Spec ambiguities, resolved here (the approver should confirm)

- **A. `command_index` is already taken (a plan deviation).** The spec names the new function
  `command_index()`, but `capabilities.py:364` already defines
  `command_index()` (the omarchy route index, `COMMAND_INDEX` at `:361`).
  `search_commands` (`:403`), `omarchy_help` (`tools.py:2247`),
  `tests/test_compose.py:587-621` and #103's spec all use it. **Resolution:**
  the new functions are named `path_commands()` (the index) and
  `find_commands()` (the search). The in-process cache is a module global,
  `_PATH_COMMANDS`, and it is not `COMMAND_INDEX`. **The name changed to avoid
  clashing with the existing Omarchy `command_index`. Behaviour is the same
  as in the spec.** The existing `command_index` and `search_commands` are
  left unchanged.
- **B. Where the live check lives.** Spec §5 says to add `COMMAND_REQUESTS` to
  `tools/verify_find.py`. The plan request names `tools/verify_commands.py`.
  **Resolution:** a new `tools/verify_commands.py`. `verify_find.py` is about
  desktop apps (its docstring describes the #70 set), so a separate file keeps
  it unchanged. The content is what spec §5 says.
- **C. The request count.** The spec lists 14 requests and scores 12.
  **Resolution:** the 12 scored requests are the 14 without "pdf to text"
  (`pdftotext` is not installed) and "pick a colour" (the known stem miss).
  Both of those are still run and printed, just not scored.
- **D. Top-1.** The spec's summary says top-1 is 3/12, but its results table
  shows `mogrify`, `yt-dlp`, `wl-copy` and `hyprpicker` at 1st, which is 4.
  **Resolution:** the gate is top-8 ≥ 10/12, as the spec's Verification
  says. Top-1 is printed and does not gate.
- **E. No `MANPATH`.** The spec reads `MANPATH` only. **Resolution:** when
  `MANPATH` is unset or empty, use `<dir>/../share/man` for each PATH entry,
  which is man-db's own rule. This is one line, and without it an install
  with no `MANPATH` would get no man descriptions.
- **F. No network, tested two ways.** The spec checks this with
  `unshare -rn`. **Resolution:** the unit tests patch `socket` and `urllib`
  to fail, which works in the nix sandbox. `unshare -rn` stays as an
  optional manual check.

## Overlaps with other open branches, and the landing order

All three branches hold only intent and spec on 2026-09-24. None has code yet.

- **fix/103-cache-eviction** rewrites `capabilities.py` `command_index()`
  (`:364-400`), `manifest()` (`:889-915`) and `_cache_key()` (`:868-886`),
  and adds `CACHE_KEEP` at `:360`. #82 adds no code in those line ranges (see
  A). **There is a real interaction.** On main, `_cache_key` stamps
  `capabilities.py` by its mtime, and every store file has mtime 1. So after
  a rebuild, the daemon keeps serving the cached manifest, and step 3's
  sentence would not reach it. #103 keys on the file's content, which fixes
  that.
- **fix/97-compose-checks-apps** edits `tools.py` `_call_locked`
  (`:1757-1763`), `_resolve_app` (`:2170-2191`), `_tool_launch_app`
  (`:2231-2234`), the compose schema (`:1137`, `1142`) and
  `_validate_compose_windows` (`:3308-3318`). #82's handler goes between
  `_tool_find_app` and `_tool_launch_app` (`:2206-2208`). That is next to
  #97's hunks, so a rebase may conflict on context lines, but no line has two
  meanings.
- **fix/101-terminal-secrets** edits `tools.py:917-933` and `3524-3800`,
  `config.py` and `realtime.py`. It does not touch the lines #82 edits.
  Nothing overlaps.

**Landing order.** In `tools.py`: #101, then #97, then #82. In
`capabilities.py`: #103 before #82. Taken together, that is #103, #101, #97,
then #82. #103 comes before #82 so that the manifest sentence reaches a
running daemon without anyone deleting the cache by hand. #82 lands last,
because it only adds code and rebases onto the others' context lines. If #82 has to land
before #103, the PR says so and gives #103's manual step
(`rm -f ~/.cache/omarchy-voice/manifest-*.md` and a daemon restart).

## Steps

Each step names a file, the change, and how it is verified. Every Python
command runs with `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`.

0. **Baseline.** Run `git switch feat/82-installed-commands && git rebase
   origin/main`, then both test runners. → verify by: 988 passed and 988 OK,
   and `git diff --stat origin/main -- src tests tools` is empty.
1. **`tests/test_find_commands.py` (new): the tests, written first.** The file
   imports `_isolated` before any `omarchy_voice` import (#99). The fixtures
   are built under `tempfile.TemporaryDirectory()`:
   - **A fake PATH.** `bin1/` holds `vconv` (executable, man page "Video
     conversion tool"), `vplay` (executable; its tldr description is "Play
     media files" and its *examples* mention "convert a video"), `jqx`
     (executable, no description), `notexec` (mode 0644), `omarchy-foo` and
     `.hidden`. `bin2/` holds a second `vconv`, which must lose to `bin1`'s.
     There is also a PATH entry that starts with `/nix/store/` (a fake
     prefix; the filter is on the string) and holds `storeonly`, and a
     `profile` symlink to `bin1/`.
   - **A fake MANPATH.** `man1/vconv.1.gz` (gzipped, `.SH NAME` /
     `vconv \- Video conversion tool`, and a `.SH SYNOPSIS` section) and
     `man8/mdoctool.8` (mdoc, `.Nd`).
   - **A fake tldr dir.** `linux/vplay.md` (normal),
     `common/mog.md` (an alias of `vconv mog`), `common/vconv-mog.md` (the
     target page).
   - The environment is set with `mock.patch.dict(os.environ, {"PATH": …,
     "MANPATH": …})`, and `capabilities.TLDR_PAGES` is patched to the fixture
     tldr dir. The module cache is cleared in `setUp`/`addCleanup`. **Nothing
     from the real machine is read.**

   Tests:
   - **Parsing.** Descriptions come from man, from mdoc and from tldr. The
     alias resolves one hop. tldr and man are joined for a command that has
     both. Examples are parsed, and an exact lookup of a man-only command
     shows its SYNOPSIS.
   - **Filtering.** `notexec`, `omarchy-foo`, `.hidden` and `storeonly` are
     absent. The first PATH directory wins for `vconv`.
   - **Exact names.** `find_commands("jqx")` puts `jqx` first, though it has
     no description. It is also first when other commands' descriptions
     contain "jqx". It is absent from a purpose search.
   - **Ranking.** "convert a video" puts `vconv` first. The prefix meets
     "conversion", and `vplay`'s examples are not scored. A tie goes to tldr,
     then to the shorter name. Twelve matching fixtures return 8.
   - **Close spellings.** `find_commands("vcon")` returns no rows. The
     handler's text says "not installed under that name" and offers `vconv`.
   - **Staleness.** A second call with nothing changed does not rebuild
     (count calls to the builder, `_build_path_commands`). A new executable,
     with the directory's mtime bumped explicitly by `os.utime` (so the test
     does not depend on timestamp granularity), is found on the next call
     with no restart. Retargeting the `profile` symlink also rebuilds.
   - **No network.** For the whole class, `socket.socket`,
     `socket.create_connection` and `urllib.request.urlopen` are patched to
     raise `AssertionError`. Every test above still passes.
   - **Nothing is executed.** For the whole class, `subprocess.Popen`,
     `subprocess.run`, `os.system`, `os.posix_spawn`, `os.posix_spawnp`,
     `os.fork`, every `os.exec*` and every `os.spawn*` that exists, and
     `capabilities._run`, are patched to raise `AssertionError`. Every test
     above still passes.
   - **Wiring.** Modelled on `tests/test_find_apps.py:316-320`:
     `find_command` is in `READ_ONLY_TOOLS` and in
     `tools_for(Config())`, and `_is_read("mcp__omarchy__find_command")` is
     true. `Executor(Config()).call("find_command", {"query": "ssh"})` is
     refused. In dry run, a query reaches the handler. The output never
     contains a fixture directory path.
   - **Manifest.** `capabilities.manifest(refresh=True)`, with `CACHE_DIR`
     and `_run` patched as `tests/test_find_apps.py:325-327` does, contains
     "find_command". This goes in this file, not in `test_find_apps.py:324`,
     which #103 edits.

   → verify by: the new file fails with `AttributeError`/`ImportError` for
   the missing names, and every other test still passes.
2. **`src/omarchy_voice/capabilities.py`: the index and the search**, placed
   after `clear_match` (`:563-573`) and before `live_state` (`:576`). It adds:
   - `TLDR_PAGES = Path.home() / ".cache/tldr/pages"`;
   - `_path_dirs()`, `_man_dirs()` (ambiguity E) and `_stamp()`, which
     returns the `(realpath, st_mtime_ns)` tuple, with `OSError` skipped per
     directory;
   - `_man_desc(path)` and `_man_synopsis(path)`, which use `gzip.open` or
     `open` and read at most 8 KB;
   - `_tldr_page(name)`, which returns the description and the examples and
     follows an alias one hop;
   - `_build_path_commands()`;
   - `path_commands() -> dict[str, dict]`, which caches on `_stamp()` in the
     module global `_PATH_COMMANDS`, with each row `{"desc", "src", "examples", "synopsis"}`;
   - `find_commands(query, limit=8) -> list[tuple[float, str, dict]]`;
   - `close_commands(query) -> list[str]`, built on `difflib`, which is
     already imported (`:19`).

   The only new import is `gzip` (stdlib). Nothing in this code calls `_run`,
   `subprocess` or `shutil.which`, and nothing writes to `CACHE_DIR`.
   → verify by: the step 1 parsing, filtering, ranking, staleness,
   no-network and no-exec tests pass.
3. **`src/omarchy_voice/capabilities.py:861-863`: the manifest sentence**
   (decision 11). → verify by: the manifest test passes, and
   `tests/test_find_apps.py:324-330` still passes.
4. **`src/omarchy_voice/tools.py`: the tool.**
   - The `find_command` schema goes after `find_app`'s (`:813`), with the
     spec's description text.
   - `"find_command"` is added to `READ_ONLY_TOOLS` (`:58-60`).
   - A `describe` branch goes after `find_app`'s (`:1880-1881`).
   - `_tool_find_command(query)` goes after `_tool_find_app` (`:2193-2206`),
     following decision 8. Each description is cut at 120 characters and each
     example at 200.

   → verify by: the step 1 wiring tests pass.
5. **`tests/test_policy.py:663`: add `"find_command"` to `READ_FAKES`**, and
   add `("find_command", {"query": q}) for q in ("reboot", "shutdown")` to the
   rows at `:691`. → verify by: `ReadsAreNotActions` passes, and a lookup of
   "reboot" is not held.
6. **`tools/verify_commands.py` (new): the read-only live check**
   (ambiguity B). Its shape follows `tools/verify_find.py`. It only calls
   `capabilities.path_commands()`, `find_commands()` and `close_commands()`,
   with no `Executor` and no policy. It prints:
   - how many commands were indexed and how many are described, with the
     cold build time and the time for one warm `_stamp()` call;
   - for each of the 12 scored requests: the top 8, the time taken, and
     HIT/MISS against the expected tool:

     | Request | Expected |
     |---|---|
     | convert a video | ffmpeg |
     | json query | jq |
     | resize an image | mogrify or magick |
     | check disk usage | duf |
     | download a youtube video | yt-dlp |
     | ssh to a host | ssh |
     | take a screenshot | grim |
     | copy to clipboard | wl-copy |
     | pick a color | hyprpicker |
     | find files by name | fd |
     | search text in files | rg |
     | system monitor | btop |

   - "pdf to text" and "pick a colour", printed and not scored;
   - the exact names `ffmpeg`, `mogrify`, `nix-locate`, `yt-dl` and
     `notarealtool`;
   - totals for top-1 and top-8, and whether each gate held. It exits 1 if
     top-8 is below 10, if an exact installed name is not first, if
     `notarealtool` matches, or if a warm query takes 50 ms or more.

   → verify by: a run on p620 exits 0, prints top-8 ≥ 10/12, and puts
   `ffmpeg`, `mogrify` and `nix-locate` first. `yt-dl` offers `yt-dlp`, and
   `notarealtool` matches nothing.
7. **`README.md:826`: add `find_command`** to the "Never held" lookups, next
   to `find_app`, and add one clause saying it reads PATH, man and tldr files
   and runs nothing. → verify by: reading it. No test covers it.
8. **Mutation checks.** Apply each change below, run
   `pytest tests/test_find_commands.py tests/test_policy.py -q`, confirm that
   at least one test fails, and revert. Record the results in the PR.
   - M1: remove the exact-name-first branch.
   - M2: remove the `/nix/store/` filter.
   - M3: remove the `X_OK` check.
   - M4: make `_stamp()` return a constant.
   - M5: add `subprocess.run(["true"])` inside `_build_path_commands`.
   - M6: add `urllib.request.urlopen("http://x")` inside `_tldr_page`.
   - M7: remove `"find_command"` from `READ_ONLY_TOOLS`.
   - M8: add the example text to the scored text.
   - M9: remove the alias hop.
   - M10: remove the 5-character prefix.
   - M11: change `limit=8` to `limit=20`.

   → verify by: all 11 are caught.
9. **The full gates.** Run the Tests section below. → verify by: every
   result is as expected there.

## Tests

| Command | Expected |
|---|---|
| `nix develop -c pytest tests -q` | 988 plus the new tests pass, with no new warnings |
| `nix develop -c python3 -m unittest discover -s tests` | the same count, OK |
| `nix flake check --no-write-lock-file` | passes (it runs `pytest tests -q`, `flake.nix:145`) |
| `nix develop -c python3 tools/verify_commands.py` on p620 | exits 0, top-8 ≥ 10/12, exact names first, every warm query under 50 ms |
| optional: `unshare -rn nix develop -c python3 -m unittest tests.test_find_commands` | passes, if the host allows unprivileged namespaces |
| by hand, done by the user, not an agent (it calls the real brain): `omarchy-voice -n say --no-confirm "what do I have installed that can resize a batch of images"` | names `mogrify` or `magick` from a `find_command` call, and does not say "likely" |

All of these read from the machine and write nothing to it. None of them runs
a binary found on PATH.

## Rollback

`git revert` the implementation commit. The change adds one tool, two
functions and one manifest sentence, and it has no state on disk: the index
lives only in process memory and is never written to `CACHE_DIR`. After the
revert, restart the daemon with `systemctl --user restart omarchy-voice`. If
#103 has not landed, also remove the cached manifest
(`rm -f ~/.cache/omarchy-voice/manifest-*.md`) so the sentence goes away.
There is nothing to migrate, and no config key is added.
