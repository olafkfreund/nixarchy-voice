---
status: draft
issue: 101
intent: intent/2026-09-24-101-terminal-secrets.md
---

# Spec: secret-shaped lines in a tmux pane are withheld before the model sees them

Closes #101. Line numbers are against `origin/main` at `93c6dac`. The branch
changes no code.

## The intent's open questions, answered

The intent was approved without answers to its six questions, so each one is
decided here with the reasoning shown. **Any of these decisions can be rejected
at this gate.**

All evidence below comes from fakes. No real tmux pane was read. The fake panes
are built in a scratch script by string concatenation (`"ghp_" + "a" * 36`), so
no token-shaped literal exists in the script or in this file. Below, `…` marks
where a fake value was elided. The script put both strategies through the same
detector (defined in Q2) and checked whether any fake secret substring survived
in the output.

### Q1: withhold the whole pane, or redact the matching lines? Redact lines.

| fake pane | lines | flagged | withhold: lines kept / secrets leaked | redact: lines kept / secrets leaked |
| --------- | ----: | ------: | ------------------------------------: | ----------------------------------: |
| `cat ~/.ssh/id_ed25519` (PEM, BEGIN…END) | 7 | 5 | 0 / 0 | 2 / 0 |
| same key, capture starts mid-block (END only) | 6 | 3 | 0 / 0 | 3 / 0 |
| `op read op://Private/GitHub/token` then `ghp_…` | 3 | 1 | 0 / 0 | 2 / 0 |
| `cat .env` (DEBUG, PORT, URL with password, sk-, AKIA, AWS secret, whsec_) | 9 | 5 | 0 / 0 | 4 / 0 |
| `git log` mentioning `~/.ssh/config`, `.env` and "password" | 5 | 0 | 5 / 0 | 5 / 0 |
| Python: `def check(password: str)`, `API_KEY = os.environ["API_KEY"]` | 6 | 0 | 6 / 0 | 6 / 0 |
| 200-line build log, one `export AWS_ACCESS_KEY_ID=ASIA…` line | 201 | 1 | **0** / 0 | **200** / 0 |
| age identity file, plus an age-armored ciphertext | 7 | 1 | 0 / 0 | 6 / 0 |

Both strategies leak **nothing** on this set. That is expected, because both
use the same detector: a secret the detector misses leaks under either one. The
difference is what a hit costs. Withholding throws away the whole 200-line
build log over one line, and it also throws away "did it work" from the watch
announcement (Q3). Redacting keeps 200 of 201 lines.

What redaction still gives away, shown on the `.env` pane:

```
[5 line(s) withheld: a password in a URL, a secret setting, a token; the rest is as on screen]
$ cat .env
DEBUG=True
PORT=8080
[withheld: a password in a URL]
[withheld: a token]
[withheld: a token]
[withheld: a secret setting]
[withheld: a secret setting]
$
```

The key names go too, because **the whole line is replaced, not just the
value**. What is left is the *count* of secret lines and the non-secret
settings. The intent's worry about "the inventory of key names" does not apply
to this form. Replacing the whole line also means an extent problem inside a
line cannot arise: the part of a token before or after the match is gone along
with it. Wrapped tokens are not a problem either, because `_capture_pane`
already passes `-J`, which rejoins wrapped lines into one.

The multi-line extent is solved for PEM blocks as follows (see Design). A block
runs from a BEGIN line to its END line, over base64 and `Header: value` lines.
A capture that *starts* inside a key (END with no BEGIN before it) is walked
backwards from END over the body lines. The second row of the table is that
case: 3 of 3 key lines withheld, and the `ls` after it kept.

**Decision:** redact whole lines, with a header line that names the count and
the kinds. No silent partial read (intent constraint).

### Q2: which signals? (a) and (d), plus two more; (b) and (c) dropped.

The pattern set, matched per line, case-sensitive:

| kind reported | pattern (sketch; the exact regex is in the plan) |
| ------------- | ------------------------------------------------ |
| a private key | a line that is only `-----BEGIN (… )PRIVATE KEY( BLOCK)-----`, through its END line (RSA, EC, OPENSSH, ENCRYPTED, PGP … BLOCK) |
| a private key | `AGE-SECRET-KEY-1` + 58 upper-case bech32 characters (an age / agenix identity) |
| a token | `gh[pousr]_` + 36 or more, `github_pat_` + 22 or more, `sk-ant-…`, `sk-(proj-)…` + 20 or more, `xox[abposr]-…`, `(AKIA\|ASIA)` + 16 upper-case alphanumerics, `AIza` + 35, `glpat-` + 20 or more |
| a password in a URL | `scheme://user:pass@` |
| a secret setting | a whole line of the form `[export ]NAME=value`, where NAME is upper-case and contains `SECRET`, `TOKEN`, `PASSWORD`, `PASSWD`, `API_KEY`/`APIKEY`, `PRIVATE_KEY` or `ACCESS_KEY`, there are no spaces around `=`, and the value is 8 or more token characters, optionally quoted |

**Measured false positives:**

- **This repo** (`src/`, `tests/`, `intent/`, `spec/`, `plan/`, top-level
  `*.nix` and `*.md`): 174 files, 49,634 lines, **0 flagged (0.000%)**. For
  contrast, `DEFAULT_SENSITIVE`'s bare-word pattern flags **115** of the same
  lines.
- **The Python 3.14 standard library** from the nix store: 138,024 lines,
  **0 flagged**.
- **Recall on the fake set:** every fake secret was flagged, and nothing was
  flagged in the `git log` or Python panes.

The first draft of the pattern set did not score this well. The measurement
changed two patterns, and both changes are part of the decision:

1. **A BEGIN header anywhere in a line** is not a key. Prose that quotes
   `-----BEGIN OPENSSH PRIVATE KEY-----`, such as this repo's own intent at
   line 63, flagged 116 lines, because the block "ran" to the end of the text.
   Now BEGIN must be the whole line, and the block ends at END or at the first
   line that is not base64, a header or blank.
2. **A `KEY = value` with spaces is code**, not configuration. It flagged
   `INSIDE_TOKEN = "MAPLE-7731"` (`verify_gate.py:39-40`, a test canary). Now
   the rule is `NAME=value` with no spaces, which is the shape of `.env` files,
   `export` and `env` output.

**Dropped:**

- **(b) Secret-printing command lines on screen** (`op read`, `pass show`,
  `cat .env`, and the #100 path list). They match the *intent* to show a secret,
  not the secret. The fake `git log` shows the cost: it names `~/.ssh/config`
  and `.env` and holds nothing secret. The line that *follows* `op read` is
  caught by its token shape anyway.
- **(c) `pane_current_command` in {`op`, `pass`, `gpg`, `pinentry`, …}.**
  While these programs are still running they are at a masked passphrase
  prompt, so nothing secret is on screen yet. Once they print, they have exited
  and (c) no longer fires. It adds a signal that is almost never true at the
  moment it matters.
- **The bare word "password"**, as the intent already required.
- **Age *armor*** (`-----BEGIN AGE ENCRYPTED FILE-----`) is ciphertext. It is
  what agenix commits to git on purpose. The fake set keeps it, and it is not
  flagged. The age *identity* is the secret, and that is flagged.

**Known misses** (the guard is a heuristic, and the tool description says so):
a password on its own line with no key name (`pass show` of a plain password),
a lower-case `password=…` line, `KEY: value` YAML, JWTs, vendors not in the
list, and a PEM block with a diff prefix (`+-----BEGIN…`).

### Q3: are watch announcements in scope? Yes, and they keep "did it work".

Watch announcements are the only unprompted path, so leaving them out would
leave the worst case open. Because Q1 redacts instead of withholding, the tail
that `poll_watches` (`tools.py:3800`) hands to `watch_message`
(`realtime.py:157`) still holds the non-secret lines. The model can still judge
"did it work", and the header tells it lines were held back. `watch_message`
needs no change, because the header travels inside `tail`.

### Q4: is `run_in_terminal` output in scope? Yes.

The #100 gate stops the model's own `cat .env` before it runs. It does not stop
the 200 lines of scrollback captured after a harmless command, which can
include a secret the user printed earlier. The shared guard (Design) covers
this path at no extra cost. Leaving it out would take code, not save code.

### Q5: all sessions, or only `Work`? All sessions stay readable.

The guard looks at content, so it applies the same way in every session.
Narrowing `_tmux_panes` (`tools.py:3524`) to one session is a different
question: whether the assistant should see the user's other work at all. That
is a privacy scope, not secret detection. It would also change
`list_terminals`, target resolution and `run_in_terminal`, and none of that is
this issue. If it is wanted, it gets its own issue.

### Q6: can the list be configured? No config key in this issue.

The pattern set is a fixed floor, `TERMINAL_SECRETS` in `config.py` next to
`DEFAULT_SENSITIVE`. It is not a `Config` field.

- **No user additions for now.** Nobody has asked for them, and the measured
  false-positive rate is 0 on 188k lines. If one is added later, it should be a
  union key like `sensitive_patterns` (`config.py:217-221`), with no
  `*_replace`.
- **No `*_replace`**, deliberately. #109 describes exactly what goes wrong with
  one: a replaced list silently loses every default added upstream afterwards.
  If a default ever misfires for someone, the fix is #109's shape, a
  `*_remove` that subtracts one named default and keeps the rest, including
  future ones.

## Design

**One guard, at the shared point.** All four paths call `_capture_pane`
(`tools.py:3578`): `_tool_read_terminal` (`:3602`), `run_in_terminal` (`:3715`),
`_tool_watch_terminal` on an idle pane (`:3744`), and `poll_watches` (`:3800`).
The guard goes inside `_capture_pane`, so no caller can skip it and no caller
changes.

1. **`config.py`**: `TERMINAL_SECRETS: list[tuple[str, str]]`, holding
   `(kind, pattern)` for the single-line kinds in Q2, placed next to
   `DEFAULT_SENSITIVE` (`config.py:192`). Also two PEM line patterns (BEGIN, END)
   and a body pattern. The comment says it is a heuristic, like the one above
   `DEFAULT_SENSITIVE`.
2. **`tools.py`**: a module-level function
   `withhold_secrets(text) -> tuple[str, list[str]]`. It returns the text with
   each flagged line replaced by `[withheld: <kind>]`, plus the sorted kinds.
   It uses the PEM walk from Q1, and each line is tested against
   `TERMINAL_SECRETS` in order, first kind wins. It is pure: no tmux and no
   config object, so it can be tested directly.
3. **`_capture_pane`**: calls `withhold_secrets` on the joined text **before**
   the `TERMINAL_OUTPUT_LIMIT` cut at `tools.py:3587`. Order matters. Cutting
   first can slice a token in half (`…aaaa` with its `ghp_` prefix gone), or cut
   a PEM block's BEGIN off, and the fragment no longer matches. When anything
   was withheld, the text is prefixed with
   `[N line(s) withheld: <kinds>; the rest is as on screen]`.
4. **The `read_terminal` tool description** (`tools.py:917-933`): one sentence.
   Lines that look like a secret come back as `[withheld: …]`. Asking for more
   `lines` will not show them. If the user needs the value, they read it
   themselves. It also says the guard is a heuristic.

The marker text is built only from the fixed kind strings and a count. It never
uses the matched text, the pane title or the command.

**Not changed:** `realtime.py` `watch_message` (Q3), `_tool_list_terminals`
(it shows `pane_title[:40]`, which carries the last command such as
`op read op://…`: a reference, not a secret), `_tmux_panes` (Q5), and `Config`
(Q6).

## Alternatives rejected

- **Withhold the whole pane** (the `read_screen` model). Same leakage, measured
  in Q1, and it costs the whole pane on one hit. It also removes "did it work"
  from watch announcements.
- **Redact only the matched span.** It keeps the key name and anything next to
  the value on the same line. For a `.env` that is the inventory, and for a
  PEM block it leaves the extent of the key to guesswork.
- **A guard in each caller.** Four copies, and the next caller of
  `_capture_pane` would get none. The intent requires the shared point.
- **Reusing `DEFAULT_DENY`'s #100 path list, or `DEFAULT_SENSITIVE`, on pane
  text.** Paths and bare words both misfire (Q2 (b); 115 corpus hits for the
  bare word) and miss the token itself.
- **Keying on `pane_current_command`.** Rejected in Q2 (c).
- **Entropy scoring.** It needs a threshold to tune and flags hashes, nix store
  paths and base64 test fixtures, which a developer's panes are full of. Known
  shapes give zero corpus hits with no tuning.
- **A dependency such as `detect-secrets`.** The intent says no new
  dependency. The shapes that matter fit in a short regex list.

## Risks

- **The marker must never quote what it withheld.** It is built only from the
  fixed kind strings and a count, never from the matched text, the title or
  the command. A test asserts that no fake secret substring appears anywhere in
  the output of any of the four paths.
- **Misses leak exactly as today** (Q2 "known misses"). The tool description
  and the config comment call this a heuristic, not a promise.
- **A false positive hides a line the user wanted read.** The measured corpus
  rate is 0, but real panes are not source code. The header makes the loss
  visible, so the model can say so rather than guess. If a default misfires
  in use, the fix is #109's `_remove` shape, not a `_replace`.
- **An unclosed PEM block with a long base64-looking tail** (for example a
  `head` that cut off END, followed by base64 output) is withheld through that
  tail. This errs on the side of withholding, and it is bounded by the first
  line that does not look like base64.
- **Test fixtures that look like tokens trip GitHub push protection** and
  secret scanners. The tests build fakes by concatenation (`"ghp_" + "a" * 36`),
  as the scratch script did, and never commit a literal.
- **Cost:** a handful of compiled regexes over at most 2000 lines per capture.
  Negligible next to the tmux subprocess. The same code runs on every host.

## Verification

- **Fake-pane unit tests in `tests/test_terminal.py`, one per path**, using
  `FakeTmux` with `capture=` set to each fake pane from Q1:
  `read_terminal`, `run_in_terminal` (quick command), `watch_terminal` on an
  idle pane, and `poll_watches` followed by `realtime.watch_message`. Each test
  asserts three things: no fake secret substring is in the output, the header
  names the right kind, and the non-secret lines are still there (the build
  log keeps 200 lines, and the announcement still contains `deploy ok`).
- **`withhold_secrets` direct tests:** the mid-block PEM capture (END only);
  prose that quotes a BEGIN header stays readable; the age armor stays and the
  age identity goes; `API_KEY = os.environ[...]` and `def check(password: str)`
  are untouched; a secret placed just inside the 6000-character cut is still
  withheld (redaction happens before truncation).
- **False-positive corpus test:** runs `withhold_secrets` over every
  `src/**/*.py` and `tests/**/*.py` file in the repo and asserts that nothing
  is withheld. New code that trips the guard fails CI, and the fix is to tighten
  the pattern, not to allow-list the file.
- **`nix flake check --no-write-lock-file`** passes.
- No test touches the real tmux. Any Python run during development exports
  `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`.
