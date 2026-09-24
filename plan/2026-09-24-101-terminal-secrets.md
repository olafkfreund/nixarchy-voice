---
status: approved
issue: 101
spec: spec/2026-09-24-101-terminal-secrets.md
---

# Plan: secret-shaped lines in a tmux pane are withheld before the model sees them

Closes #101. Every `file:line` below was checked against `origin/main` at
`93c6dac`. The implementation should not need the intent or the spec open.

All evidence comes from fakes. No real tmux pane is read, now or during
implementation. Any Python run during this work exports
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`.

## Approved decisions, carried over from the spec

1. **Redact whole lines, not the whole pane and not just the matched span.**
   Each flagged line becomes `[withheld: <kind>]`, and the key name on that
   line goes with it. When anything was withheld, the text starts with a
   header line giving the count and the kinds:
   `[N line(s) withheld: <kinds, sorted, comma-separated>; the rest is as on screen]`.
   Nothing is ever withheld silently.
2. **The pattern set.** There are four kinds, reported as `a private key`,
   `a token`, `a password in a URL` and `a secret setting`. Lines are matched
   case-sensitively, and each line is checked against the kinds in the order
   of the table below; the first match wins. These are the regexes the spec
   measured (scratch script `demo101.py`), copied without change:

   | kind | pattern |
   | ---- | ------- |
   | a token | `\b(?:gh[pousr]_[A-Za-z0-9]{36,}\|github_pat_\w{22,}\|sk-ant-[\w-]{20,}\|sk-(?:proj-)?[\w-]{20,}\|xox[abposr]-[A-Za-z0-9-]{10,}\|(?:AKIA\|ASIA)[A-Z0-9]{16}\|AIza[\w-]{35}\|glpat-[\w-]{20,})` |
   | a private key (age identity) | `AGE-SECRET-KEY-1[0-9A-Z]{58}` |
   | a password in a URL | `\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@` |
   | a secret setting | `^\s*(?:export\s+)?[A-Z0-9_]*(?:SECRET\|TOKEN\|PASSWORD\|PASSWD\|API_?KEY\|PRIVATE_?KEY\|ACCESS_?KEY)[A-Z0-9_]*=['"]?[A-Za-z0-9+/_.~-]{8,}['"]?\s*$` |

   (`\|` is a table escape. In the code it is a plain `|`.)

   PEM private keys (`a private key`) have three line patterns:
   - BEGIN: `^\s*-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----\s*$`
   - END: `^\s*-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----\s*$`
   - body: `^\s*(?:[A-Za-z0-9+/=]{16,}|[A-Za-z-]+: .*|)\s*$` (base64, an armor
     header, or a blank line)

   How the PEM lines are walked:
   - A block starts only at a line that is nothing but a BEGIN header. Prose
     that quotes a header does not start one.
   - The block covers the body lines and ends at END, or at the first line
     that is not body. The line that ends it early is then checked against
     the single-line kinds as normal.
   - If the first END comes before any BEGIN, the capture started inside a
     key. The END line is withheld, and so is every body line above it,
     walking back until a line that is not body.
   - Age *armor* (`-----BEGIN AGE ENCRYPTED FILE-----`) is ciphertext and
     stays.

   **Dropped from the pattern set:**
   - secret-printing command lines on screen, such as `op read`,
     `pass show`, `cat .env` or #100's path list;
   - `pane_current_command`;
   - the bare word "password";
   - entropy scoring;
   - any new dependency.

   **Known misses, accepted:**
   - a plain password on a line by itself;
   - lower-case `password=…`;
   - YAML `KEY: value`;
   - JWTs;
   - vendors not in the list;
   - a PEM block with a diff prefix (`+-----BEGIN…`).
3. **One shared guard, in `_capture_pane`** (`tools.py:3578-3589`). All four
   paths go through it:
   - `_tool_read_terminal` (`:3602`)
   - `_tool_run_in_terminal` (`:3715`)
   - `_tool_watch_terminal` on an idle pane (`:3744`)
   - `poll_watches` (`:3800`), whose `tail` goes to `realtime.watch_message`
     (`realtime.py:157-168`)

   No caller changes, and neither does `watch_message`: the header travels
   inside `tail`, so the announcement can still say whether the command
   worked. Sessions are not narrowed, and `_tmux_panes` (`:3524`) and
   `_tool_list_terminals` (`:3609`) are not touched.
4. **Redaction happens before the 6,000-character cut.** The cut is
   `TERMINAL_OUTPUT_LIMIT` (`tools.py:391`), applied at `:3587-3588`.
   Cutting first can slice a token's prefix off, or cut away a PEM BEGIN
   line, and the fragment left behind no longer matches.
5. **The marker never quotes what it hid.** Both the header and the
   per-line marker are built only from the fixed kind strings and a count.
   They never use the matched text, the pane title or the command.
6. **No config key.** The patterns are a fixed floor called
   `TERMINAL_SECRETS` in `config.py`, next to `DEFAULT_SENSITIVE`
   (`config.py:192-208`). It is not a `Config` field and not in
   `LIST_UNION_KEYS` (`config.py:217-221`). There is no user addition and no
   `*_replace`. If a key is ever added:
   - to add patterns, a union key shaped like `sensitive_patterns`;
   - to drop a default that misfires, #109's subtract shape: a `*_remove`
     that drops one named default and keeps the rest, including defaults
     added later.

   A `*_replace` is never the answer.
7. **The measured false-positive rate is the bar.** At spec time the pattern
   set flagged:
   - **0 of 49,634 lines** in this repo (`src/`, `tests/`, `intent/`,
     `spec/`, `plan/`, top-level `*.nix`/`*.md`, 174 files);
   - **0 of 138,024 lines** in the Python 3.14 standard library.

   For comparison, `DEFAULT_SENSITIVE`'s bare-word pattern flags 115 lines of
   the same repo. Every fake secret was flagged, and nothing in the fake
   `git log` or Python panes was.
8. **The `read_terminal` description** (`tools.py:917-933`, the text at
   `:919-923`) gains one sentence covering three things:
   - lines that look like a secret come back as `[withheld: …]`;
   - asking for more `lines` will not show them, and the user reads the
     value themselves;
   - the guard is a heuristic.
9. **Test fakes are built by joining strings** (`"ghp_" + "a" * 36`). No
   token-shaped literal is committed, because push protection and secret
   scanners would trip on one.

### Two points the spec left open, settled here (reviewer: check these)

- **A. How the count reaches the header.** The spec gives the signature
  `withhold_secrets(text) -> tuple[str, list[str]]` "plus the sorted kinds",
  but the header needs a count as well. **Resolution:** the list holds one
  kind per withheld line, sorted, so `len(kinds)` is the count and
  `sorted(set(kinds))` is the list the header names. The signature stays as
  the spec gave it. The count is never recovered by searching the output for
  `[withheld:`, because pane text can contain that string literally.
- **B. Where the header goes when the capture is also cut.** If the header
  were added before the cut, a long capture would lose it. **Resolution:**
  the order is:
  1. withhold;
  2. cut to `TERMINAL_OUTPUT_LIMIT`, with the existing
     `… [earlier output not shown]` line;
  3. add the header **above** everything, outside the 6,000-character
     budget.

  N counts every line withheld from what tmux returned, including any that
  the cut then removed. That is still true: that many lines were withheld.

**Also not in the spec's list, and left alone:** `README.md` "Safety"
(`README.md:815-830`). The spec names the tool description as the text users
and the model see. If the reviewer wants a README bullet, it is one line under
"Denied outright". Say so at approval.

## Steps

0. **Baseline.**
   - Check the branch:
     `git switch fix/101-terminal-secrets && git rebase origin/main`
     → `git status` is clean, and `git log origin/main..` shows only the
     intent, spec and plan commits.
   - Run the suite:
     `nix develop -c pytest tests -q` → **988 passed**.
     `nix develop -c python3 -m unittest discover -s tests` → OK, and record
     its count.
   - Run `nix flake check --no-write-lock-file` → passes.
   - Record all three numbers in the PR.
1. **`tests/test_terminal.py`: write the failing tests first** (listed under
   Tests).
   - Import `withhold_secrets` and `realtime` inside the new test classes
     (a local import, the way `:250` imports `TERMINAL_START_GRACE`), not at
     the top of the module. A top-level import would error the whole module
     on main and hide the tests that already pass.
   - → verify by running both commands on the unchanged code. Every test in
     "Must fail on main" fails, by assertion or ImportError, and every
     existing test still passes.
2. **`src/omarchy_voice/config.py`: add the constants.** They go after
   `DEFAULT_SENSITIVE_PATTERNS` (`:208`), before `PREFIXED_SECTIONS`:
   - `TERMINAL_SECRETS: list[tuple[str, str]]`, holding the four
     `(kind, pattern)` rows of decision 2 in table order;
   - `TERMINAL_PEM_BEGIN`, `TERMINAL_PEM_END`, `TERMINAL_PEM_BODY` (strings).

   The comment says three things: it is a heuristic like `DEFAULT_SENSITIVE`;
   it is deliberately not a `Config` field; and any future key takes #109's
   `*_remove` shape, never `*_replace`.
   → verify by `python3 -c` compiling each pattern with `re.compile`.
   `git diff` shows no change to `Config` or `LIST_UNION_KEYS`.
3. **`src/omarchy_voice/tools.py`: add the pure function** at module level,
   after the terminal constants (after `TERMINAL_ATTACH_TIMEOUT`, about
   `:410`):
   `withhold_secrets(text: str) -> tuple[str, list[str]]`.
   - It compiles the patterns from `config_mod` once, at import time.
   - It runs the PEM walk from decision 2, then the single-line kinds, first
     match wins.
   - It replaces each flagged line with `f"[withheld: {kind}]"`.
   - It returns the joined text and `sorted(kinds)`, one entry per withheld
     line (point A).
   - It makes no tmux calls and takes no config object.

   → verify by the direct `withhold_secrets` tests passing.
4. **`src/omarchy_voice/tools.py` `_capture_pane` (`:3578-3589`): call the
   guard.**
   - The empty-pane check (`:3585-3586`) stays first.
   - Then `text, kinds = withhold_secrets(text)`.
   - Then the existing cut (`:3587-3588`), unchanged.
   - Then, only if `kinds` is not empty, put the header above everything,
     with `N = len(kinds)` and the kinds from `sorted(set(kinds))`
     (point B).

   No caller changes.
   → verify by the per-path tests, the truncation-order test and the
   existing `test_a_huge_scrollback_is_trimmed_from_the_top` (`:132`) all
   passing.
5. **`src/omarchy_voice/tools.py` `read_terminal` description (`:919-923`):
   add the one sentence from decision 8.**
   → verify by a test asserting that the description contains `withheld` and
   `heuristic`. Check that `mcp_server`, the realtime tool list and the claude
   backend still load it: the full suite passes.
6. **Full verification** (see Tests).
   - Run both suite commands and `nix flake check --no-write-lock-file`.
   - Run the mutation checks.
   - `git diff --stat origin/main` touches only `config.py`, `tools.py` and
     `tests/test_terminal.py`, plus `plan/` if a step deviated.
   - Commit: `fix(terminal): withhold secret-shaped lines before a pane
     reaches the model (#101)`.

## Tests

All tests go in `tests/test_terminal.py` and use `FakeTmux(capture=…)`
(`tests/test_terminal.py:34-58`). `import _isolated` stays first (#99). No
test touches real tmux.

**Fakes.** Build them once at class level, every secret by joining strings,
and split at the delimiter as well (`"postgres://app:" + "hunter2" + "@db…"`).
Then the committed source line never matches its own pattern, and the corpus
test stays green. Keep `SECRET_PARTS`: every fake secret value
(`"Q"*64`, `"R"*64`, `"S"*20`, `"a"*36`, `"hunter2"`, `"b"*40`, `"C"*16`,
`"d"*40`, `"e"*24`, `"F"*16`, `"Z"*58`).

The eight panes, with the expected result:

| pane | lines | withheld | kinds | must survive |
| ---- | ----: | -------: | ----- | ------------ |
| `pem`: `cat ~/.ssh/id_ed25519`, BEGIN, 3 body, END, prompt | 7 | 5 | a private key | `cat ~/.ssh/id_ed25519` |
| `pem_scrolled`: 2 body, END, `$ ls`, `a b c`, prompt | 6 | 3 | a private key | `$ ls`, `a b c` |
| `op_read`: `op read op://…`, `ghp_…`, prompt | 3 | 1 | a token | `op read op://Private/GitHub/token` |
| `env`: `cat .env`, DEBUG, PORT, URL with password, `sk-proj-`, AKIA, AWS secret, `whsec_`, prompt | 9 | 5 | a password in a URL, a secret setting, a token | `DEBUG=True`, `PORT=8080` |
| `git_log`: names `~/.ssh/config`, `.env`, "password" | 5 | 0 | none | all of it, and no `withheld` |
| `python`: `def check(password: str)`, `API_KEY = os.environ["API_KEY"]` | 6 | 0 | none | all of it, and no `withheld` |
| `build_log`: `make deploy`, 197 compile lines, `export AWS_ACCESS_KEY_ID=ASIA…`, `deploy ok`, prompt | 201 | 1 | a token | 200 lines, including `deploy ok` |
| `age`: `# public key: age1…`, `AGE-SECRET-KEY-1…`, then `-----BEGIN AGE ENCRYPTED FILE-----` and a body line | 7 | 1 | a private key | the armor header and its body |

**Per path, for each of the 8 panes** (`subTest(pane=…)`):

1. **`read_terminal`**: `ex.call("read_terminal", {"target": "Work:1.1"})`.
2. **`run_in_terminal`**: target `Work:1.1`, command `ls`, with
   `time.sleep` and `omarchy_voice.tools.TERMINAL_START_GRACE` (set to `-1`)
   patched, so the first poll captures. The output starts `ran 'ls'`.
3. **`watch_terminal`, idle pane**: target `Work:1.1`. The result is not ok,
   and its output has `already idle` plus the capture.
4. **`poll_watches` → `watch_message`**:
   - `ex.watch("Work:1.2", "deploy", seen_busy=True)`;
   - set `panes_raw` to idle;
   - `[job] = ex.poll_watches()`;
   - `msg = realtime.watch_message(job)`.

   Assert on `job["tail"]` and on `msg`.

Every path asserts four things:
- no `SECRET_PARTS` entry is in the output;
- the header names exactly the expected count and kinds;
- the "must survive" lines are present;
- for `git_log` and `python`, the output is byte-identical to main's.

For `build_log`: `read_terminal` keeps 200 of 201 lines, and `msg` contains
`deploy ok`.

**Direct `withhold_secrets` tests:**
- `pem_scrolled` (END only): 3 withheld, and `$ ls` is kept.
- Prose that quotes a BEGIN header inside a sentence, followed by 20 lines of
  text, withholds nothing.
- An unclosed BEGIN followed by a non-body line stops there. That line and
  every line after it are kept.
- The age armor is kept, and the age identity is withheld.
- `API_KEY = os.environ["API_KEY"]` and
  `INSIDE_TOKEN = "MAPLE-7731"` (spaces around `=`) are untouched. Lower-case
  `password=…` is untouched too: a known miss, so pin it.
- `export AWS_ACCESS_KEY_ID=ASIA…` is flagged as a token, not a setting
  (first kind wins).
- The returned list has one entry per withheld line (point A).

**Marker test, checked on every pane and every path.**
- Every output line containing `withheld` matches one of two forms:
  - `^\[withheld: (a private key|a token|a password in a URL|a secret setting)\]$`;
  - `^\[\d+ line\(s\) withheld: <kinds>(, <kinds>)*; the rest is as on screen\]$`.
- No `SECRET_PARTS` entry, and no 8-character window of one, appears
  anywhere in the output.

**Truncation-order test (decision 4, point B).**
- Capture: `"ghp_" + "a" * 36`, then a newline, then
  `"y" * (TERMINAL_OUTPUT_LIMIT - 20)`. The old cut would start 21
  characters into the token and leave `"a" * 19` with no prefix.
- Assert:
  - `"a" * 16` is not in the `read_terminal` output;
  - the header is present and names `a token`;
  - `earlier output not shown` is present.
- **Deviation (implementation):** the capture starts with one more line,
  `"$ op read x\n"`. As first written, the redacted text is exactly
  `TERMINAL_OUTPUT_LIMIT` long, so the cut never runs and the
  `earlier output not shown` assertion could not hold. With the extra line
  the old cut still starts 21 characters into the token, and the redacted
  text is still over the limit. The test also asserts the header comes
  before `earlier output`.
- Second case: a PEM block whose BEGIN falls just outside the cut. The body
  is still withheld.

**False-positive corpus test (decision 7).**
- Read every `src/**/*.py` and `tests/**/*.py` under
  `Path(__file__).resolve().parent.parent` at test time. This is about
  25,600 lines at `93c6dac`, and the `unit` flake check copies the whole
  tree (`flake.nix:141-145`).
- Run `withhold_secrets` over each file's text.
- Assert that every file returns `[]`, and name the file and line on
  failure. When this fails, the fix is to tighten the pattern, never to
  allow-list the file.
- Note: the spec's own prose now trips it once
  (`spec/2026-09-24-101-terminal-secrets.md:85`, the documented
  `scheme://user:pass@` example). That is why the test covers code, not
  `spec/`.

**Must fail on main:**
- all four per-path tests, for the 6 panes with a secret;
- every direct test (ImportError);
- the marker test;
- the truncation-order test;
- the description test.

The corpus test and the `git_log`/`python` cases pass on main, and must keep
passing.

**Mutation checks.** Apply each by hand, confirm the named test fails, then
revert.

| mutation | test that must fail |
| -------- | ------------------- |
| call `withhold_secrets` after the cut | truncation-order |
| add the header before the cut | truncation-order (header missing) |
| marker quotes text, e.g. `f"[withheld: {line[:8]}]"` | marker test |
| guard moved from `_capture_pane` into `_tool_read_terminal` only | `run_in_terminal`, `watch_terminal`, `poll_watches` paths |
| drop the backward walk from END | `pem_scrolled` |
| BEGIN matched with `search` anywhere in a line, not the whole line | prose-quoting-BEGIN test |
| allow spaces around `=` in the setting pattern | direct test (`INSIDE_TOKEN = …`); corpus test (`verify_gate.py:39`) |
| add `(?i)` to the setting pattern | `python` pane / lower-case pin |
| delete the `sk-` alternative | `env` pane |

**Commands**, each with the DBus export:
- `nix develop -c pytest tests -q` → 988 plus the new tests, all passing;
- `nix develop -c python3 -m unittest discover -s tests` → OK;
- `nix flake check --no-write-lock-file` → passes.

## Overlaps with other open branches

Both branches below are docs-only so far (intent and spec approved, no plan).
Their specs name these edit sites:

- **`fix/97-compose-checks-apps`**: `tools.py:89`, `:1137-1142`,
  `:1547-2234` (several hunks), `:3308-3353`, plus `tests/test_compose.py` and
  `capabilities.py`.
- **`feat/82-installed-commands`**: `tools.py:58-60` (`READ_ONLY_TOOLS`), a
  new schema after `find_app` at `:797-812`, a handler near `:1771-1784`,
  plus `capabilities.py`, `claude_backend.py`, `persona.py`, `README.md` and a
  new `tests/test_find_commands.py`. It cites `run_in_terminal`
  (`:3676-3697`) and `tests/test_terminal.py` only as evidence, and plans no
  edit there.

This plan edits:
- `config.py:~208-212` (new constants);
- `tools.py:~410` (new function), `:919-923` (description) and `:3578-3589`
  (`_capture_pane`);
- `tests/test_terminal.py` (added at the end).

**No hunk is shared.** #82's schema insertion above `:917` shifts this
plan's line numbers down by its length, and nothing more.

**Landing order: #101 first, then #97, then #82.**
- #101 is a security fix, it is the smallest, and it has no textual
  dependency on either.
- #97 comes next: it is smaller than #82 and does not touch #82's regions.
- #82 is last: it is the widest change, and it adds a new test file.

Whichever branch lands later rebases, re-checks its cited lines, and must keep
this plan's corpus test green. Their new `src/` and `tests/` files come under
it automatically. If #97 or #82 lands first anyway, this branch rebases. The
only expected effect is the line numbers in steps 3-5.

## Rollback

`git revert` the implementation commit. It adds constants, one pure function,
four lines in `_capture_pane`, one sentence in a description, and tests. There
is no config key, no migration, no stored state and no Nix change, so revert
is complete on every host. If one pattern misfires in use, the smaller rollback
is to delete that alternative from `TERMINAL_SECRETS` in its own commit, with
a pinned test for the line that misfired. Do not add a `*_replace` switch
(decision 6).
