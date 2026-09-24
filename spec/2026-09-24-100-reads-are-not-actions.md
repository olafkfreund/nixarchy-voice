---
status: draft
issue: 100
intent: intent/2026-09-24-100-reads-are-not-actions.md
---

# Spec: A read-only lookup is not an action, so the confirm gate must not hold it

Closes #100. Branch `fix/100-reads-are-not-actions`, based on main 72f9ab6.
Every `file:line` below was checked against that commit.

## The intent's open questions, answered

The intent was approved without answers to its six questions. Each one is
decided here and shown working on fakes: an `Executor` subclass whose
handlers return fixed strings, and `ClaudeBrain._decide` called directly with
that executor. No desktop, no subprocess, no model. **You can reject any of
these decisions at this gate.** Each is separate, so rejecting one does not
reopen the others.

| # | Question | Decision |
|---|----------|----------|
| 1 | Our read-only tools: skip confirm only, or confirm and deny? | **Skip confirm only.** Deny still applies, both the defaults and the user's own rules. |
| 2 | Claude Code's `Read` / `WebSearch`: same answer? | **Same answer: skip confirm, keep deny.** The same `read=` flag goes through the same `Policy.check`. |
| 3 | Default deny rules for secret paths? | **In scope.** Twelve patterns go into `DEFAULT_DENY` (list below). #94 relies on this issue to close its `Read` residual. |
| 4 | Is `READ_ONLY_TOOLS` the gate's list too? | **Yes, unchanged, frozen.** Every member was checked and is read-only. `read_notifications` and `clipboard` stay out. |
| 5 | Should #76's release turn let the same reads through? | **Yes, in scope.** A single `_is_read` covers both our reads (`mcp__omarchy__<READ_ONLY_TOOLS>`) and `DRY_RUN_READS`. |
| 6 | `read_terminal` has no sensitive guard | **Out of scope; a follow-up issue.** This change neither causes the gap nor widens it. |

### Why each decision

**Q1: skip confirm only.** The bug is the *hold*. A hold takes the only
confirmation slot (`tools.py:1739-1744`) and switches #71's router off
(`local_engine.py:473`). A deny does neither of those. It refuses, the slot
stays free, and the real action can still be asked for. So skipping confirm
fixes everything the intent's outcome asks for. Keeping deny has these
effects:

- The rule "a user-written deny rule still refuses reads" holds with no new
  machinery. Telling defaults apart from user rules would mean comparing
  against `DEFAULT_DENY`, and that comparison goes wrong the moment a user
  copies a default into their own config.
- The new secret-path rules (Q3) apply to every engine through one list.
- It costs what it cost before: `omarchy_help "sudo"`, `find_app "ssh"` and
  `read_terminal ssh` stay refused. That behaviour is not new, it fails in
  the safe direction, and it does not block anything that follows it.

**Q2: the same answer for Claude Code.** The comment at
`claude_backend.py:69-71` promises that "a deny rule aimed at a path should
still catch a read of it". Keeping deny keeps that promise, and Q3 gives it
default rules to enforce. `Read` is the only reader that takes a path. After
#94 it is also the only reader the voice brain has: #94 drops `WebSearch`
from `--tools` and leaves `DRY_RUN_READS` unchanged (#94 plan, step 11).

**Q3: in scope, in `DEFAULT_DENY`.** #94's approved plan (step 14,
"Residual") keeps `Read` in the brain's allowlist. It names this issue's Q3
as the thing that closes "Read can read `/etc/shadow`, `secrets.env`". If
Q3 moved to a separate issue, that risk would stay open with no tracker
behind it. The rules go into `DEFAULT_DENY` itself, not into a list only
reads can see. That way they also refuse `cat /etc/shadow` through `Bash` or
`run_shell`, and a `Write` into `~/.ssh/authorized_keys`, which is the same
leak or worse. A list only reads could see would guard reads and leave the
actions open. Because `LIST_UNION_KEYS` (`config.py:200-201`) unions the
defaults with the user's list, existing configs gain the rules automatically.
A user who sets `deny_patterns_replace` has opted out of every default and
opts out of these too.

The list. Each pattern is matched with `re.IGNORECASE` against the whole
description, which for `Read` is `read <absolute path>`
(`claude_backend.py:203-205`):

| Pattern | Protects | Why this shape |
|---------|----------|----------------|
| `/etc/g?shadow\b` | password hashes, and the `shadow-` backup | A path, not the word, so a lookup for "shadow" still runs. |
| `/\.ssh(/\|\b)` | private keys, `authorized_keys`, `known_hosts` | Makes the accidental `\bssh\b` catch a deliberate one. It keeps working if a user replaces the word rule. |
| `/\.gnupg(/\|\b)` | `private-keys-v1.d`, `trustdb` | The whole directory. There is no public file in it worth a voice read. |
| `/run/(agenix\|secrets)(\.d)?(/\|\b)` | agenix and sops-nix decrypted secrets | These are the NixOS runtime secret mounts. `/run/user/...` is not matched. |
| `(^\|[\s/"'=])[\w-]*\.env(\.local\|\.production\|\.development)?(?=$\|[\s"';\|&)])` | `.env`, `secrets.env`, `.env.local` | Must be a whole file name. It does not match `.env.example`, `.envrc`, `environment.py`, or `process.env.X` inside a command. |
| `\bid_(rsa\|ecdsa\|ed25519\|dsa)\b(?!\.pub)` | SSH private keys kept outside `~/.ssh` | The `.pub` files are left alone. |
| `/\.(netrc\|git-credentials\|pgpass)\b` | plaintext login stores | Well-known single files. |
| `/\.aws/credentials\b` | cloud keys | A well-known single file. |
| `/\.config/gh/hosts\.yml\b` | GitHub CLI token | This machine uses `gh`. |
| `/\.claude/\.credentials\.json\b` | Claude Code's own login | The brain runs on this login (`_credentials_present`). |
| `/\.password-store(/\|\b)` | `pass` store | Encrypted, but the file names are themselves the secret inventory. |
| `/\.local/share/keyrings(/\|\b)` | GNOME keyring files | Pairs with `DEFAULT_SENSITIVE`'s `gnome-keyring`. |

Left out on purpose:

- `*.age`: ciphertext, harmless to read.
- `*.pem`: mostly public certificates, and a rule would misfire.
- A bare `secret` word: it would refuse `~/secrets/README.md` and every
  lookup for "secret".

The list is a heuristic, the same way `DEFAULT_SENSITIVE` is. Growing it
later is a one-line change. Each row is a pattern in one Python list.

**Q4: `READ_ONLY_TOOLS`, unchanged, as a `frozenset`.** It already means more
than this issue needs: those tools are allowed to *act* under dry-run
(`tools.py:1752`, `:1779`). A second, narrower list would be a second
definition of "read" that could drift from the first. Every member was checked
against its handler:

- `hypr_query` (`tools.py:1984`) and `system_query` (`:4166`) take closed
  enums.
- `find_app` (`:2163`) reads desktop entries.
- `omarchy_help` (`:2217`) searches the command index.
- `screenshot` (`:2513`) and `read_screen` (`:2869`) capture, behind the
  unchanged `_capture_refused` / sensitive-window guard.
- `read_terminal` (`:3537`) and `list_terminals` (`:3548`) run tmux
  capture/list.

None of them sends input, writes a file, or spawns an app. `read_notifications`
(`:2879`) is also read-only, but it has not been seen held. Adding it would
also change dry-run behaviour, so it stays out. Adding it later is a one-word
change. `clipboard` has a write action and stays out. The set becomes a
`frozenset`, because it now gates confirm and must not be mutable at runtime.

**Q5: the release turn lets the same reads through.** Today the release
branch (`claude_backend.py:339-369`) refuses anything not in `DRY_RUN_READS`,
so `mcp__omarchy__omarchy_help` is refused. Under this design a read cannot be
held, so allowing it cannot spend or displace the approval. A read in a
release turn then takes the ordinary path. For our reads that is the
Executor's gate. For `Read` it is the deny rules. The approved call is still
the only non-read that can run, and it runs once. One predicate, `_is_read`,
is used here. `_decide` uses `tool in DRY_RUN_READS` for Claude Code's own
tools, because `mcp__omarchy__*` has already returned early at `:372`.

**Q6: `read_terminal` guard as a follow-up issue.** The deny list only ever
saw `read terminal <target>`, never the pane text. So confirm on
`read_terminal` never protected contents, and removing it loses no
protection. A real guard is a different mechanism: a pane-content or
pane-title check, like `_sensitive_kind` for windows. It needs its own
design. Proposed issue title: "read_terminal has no sensitive-content guard
(a pane can show `op read` / `cat .env`)". It is left for the approver to
open, because this teammate does not file issues.

### Demonstration (fakes)

Run on origin/main (**before**) and on a scratch copy with exactly the Design
diff below applied (**after**). The executor handlers return fixed strings.
Every `Executor` and `ClaudeBrain` is fresh unless noted.

Our tools, through `Executor.call`:

| Call | Before | After |
|------|--------|-------|
| `omarchy_help` reboot / shutdown / nixos-rebuild / close all windows / omarchy pkg install | HOLD | **RUN** |
| `find_app` shutdown | HOLD | **RUN** |
| `read_screen` query reboot | HOLD | **RUN** |
| `read_terminal` nixos-rebuild | HOLD | **RUN** |
| `omarchy_help` sudo, `read_terminal` ssh (default deny) | DENY | DENY |
| `omarchy_cli` "system reboot" (an action) | HOLD | HOLD |
| Same executor: `omarchy_help` reboot, then `omarchy_cli` "system reboot" | HOLD, then "another action is already waiting" | **RUN, then HOLD**; `pending = ('omarchy_cli', ...)` |
| `dry_run=True`: `omarchy_help` reboot | HOLD | **RUN** (reaches the fake, as `READ_ONLY_TOOLS` intends) |

Claude Code, through `_decide`:

| Call | Before | After |
|------|--------|-------|
| `Read /home/u/notes/reboot-checklist.md` | HOLD | **ALLOW** |
| `WebSearch "how do I reboot hyprland"` | HOLD | **ALLOW** |
| `WebSearch "sudo password prompt"` | DENY | DENY |
| `Read` `/etc/shadow`, `/etc/gshadow`, `…/omarchy-voice/secrets.env`, `proj/.env`, `proj/.env.local`, `keys/id_rsa`, `.gnupg/private-keys-v1.d/AB12.key`, `/run/agenix/github-token`, `/run/secrets/api`, `.netrc`, `.aws/credentials`, `.config/gh/hosts.yml`, `.claude/.credentials.json`, `.password-store/bank.gpg` | **ALLOW** (all 14) | **DENY** (all 14) |
| `Read` `.ssh/id_ed25519`, `.ssh/config` | DENY (by `\bssh\b`) | DENY (now also by `/\.ssh`) |
| `Read` `proj/.env.example`, `proj/.envrc`, `proj/src/environment.py`, `/etc/hosts`, `proj/README.md`, `~/secrets/README.md`, `/run/user/1000/omarchy-voice/state.json` | ALLOW | ALLOW |
| `Bash systemctl reboot` (an action) | HOLD | HOLD |

User-written rules, with `deny_patterns = DEFAULT_DENY + ["/home/u/private/", "\bpayroll\b"]`:

| Call | Before | After |
|------|--------|-------|
| `Read /home/u/private/diary.md` (user path rule) | DENY | DENY |
| `omarchy_help payroll`, `read_terminal payroll` (user word rule) | DENY | DENY |

Release turn (#76). The approved call is `mcp__omarchy__omarchy_cli`
`{"command": "system reboot"}`:

| Call | Before | After |
|------|--------|-------|
| `mcp__omarchy__omarchy_help` reboot | DENY | **ALLOW** (then gated by the Executor as a read) |
| `mcp__omarchy__launch_app`, `Bash ls` | DENY | DENY |
| `Read /etc/shadow` | ALLOW | **DENY** |
| the approved `omarchy_cli` call | ALLOW | ALLOW |

The whole existing suite (860 tests) was also run on the scratch copy. It
added no failure beyond the ones main already has outside the dev shell
(xkb/keys) and three that come from the copy having no repo root
(`eval_router`, `nix/`).

## Design

Three files. The first two diffs are the whole logic change.

1. **`src/omarchy_voice/tools.py`**
   - `Policy.check` (`:200-205`) gains a keyword `read: bool = False`. Deny
     patterns run exactly as now. When `read` is true, it returns before the
     confirm loop.
   - `Executor._call_locked` (`:1734`) passes
     `read=name in READ_ONLY_TOOLS`. `name` is the tool the handler lookup at
     `:1722` already resolved, never anything taken from the description.
   - `READ_ONLY_TOOLS` (`:57-58`) becomes a `frozenset` with the same eight
     members. Its comment says it now also exempts these tools from confirm.
   - The `compose_windows` defence-in-depth check (`:3291`) is untouched and
     keeps its full check.

   ```python
   def check(self, description: str, *, read: bool = False) -> None:
       for pattern in self.config.deny_patterns:
           if re.search(pattern, description, re.IGNORECASE):
               raise Denied(f"blocked by deny rule /{pattern}/")
       if read:
           return  # a lookup is not the thing it looks up (#100)
       for pattern in self.config.confirm_patterns:
           ...
   ```

   Every engine goes through `Executor.call`: `planner.py:199`,
   `realtime.py:1284`, `mcp_server.py:169` and `local_engine.py:499`. So one
   change covers all of them, including Claude Code's calls to our tools.

2. **`src/omarchy_voice/claude_backend.py`**
   - A new function `_is_read(tool)`, beside `DRY_RUN_READS` (`:101`):
     `tool in DRY_RUN_READS`, or `tool` is `mcp__omarchy__<name>` with
     `<name> in READ_ONLY_TOOLS`. The prefix is required, so a Claude Code tool
     that happens to be called `screenshot` is not a read. `READ_ONLY_TOOLS`
     is imported from `.tools`.
   - The release branch (`:361`) changes from `tool not in DRY_RUN_READS` to
     `not _is_read(tool)`.
   - The ordinary check (`:382`) becomes
     `self.executor.policy.check(description, read=tool in DRY_RUN_READS)`.
   - The comment at `:69-71` stays true and gains a sentence: deny still
     applies to reads, and confirm does not.
   - The dry-run check (`:422`) is unchanged.

3. **`src/omarchy_voice/config.py`**: the twelve patterns from Q3 are appended
   to `DEFAULT_DENY` (`:132-154`) under one comment, "Secret paths (#100)".

Nothing else moves. That covers `describe`, `describe_tool`, the
sensitive-window guards (`_sensitive_kind`, `DEFAULT_SENSITIVE`) and
`_same_call`, as well as `run_pending`, the router and `DRY_RUN_READS`'s
members.

## Alternatives rejected

- **Skip confirm and deny for reads (Q1/Q2 "both").** Default deny rules
  never protect anything on a read query, but user rules and the new
  secret-path rules do. Skipping only the *default* deny rules would need
  default-versus-user bookkeeping that breaks when a user copies a default.
- **Match the confirm patterns against the tool's argument, or anchor them
  (`^…`).** That is still inferred from text. `omarchy_help "system reboot"`
  would still match any argument-based rule, and anchoring changes every
  user's custom rule. The intent requires "a closed list of tools, never
  inferred from the description".
- **Reword the read descriptions so no pattern matches, e.g. by quoting the
  query.** The patterns are unanchored `re.search`, so any wording that still
  contains the word matches. Dropping the query from the description would
  also blind the user's deny rules to it.
- **A separate `READ_GATE_TOOLS` list.** That would be a second definition of
  read-only next to the dry-run one, and the two would drift.
- **Secret-path rules in a reads-only list.** They would leave
  `cat /etc/shadow` through `Bash` / `run_shell` open, and those leak the
  same data.
- **Q3 as a separate issue.** #94's plan names this issue as the fix for
  `Read`'s secret-path residual. Moving it out leaves that open with nothing
  tracking it.
- **A `read_terminal` guard here (Q6).** It is a different mechanism
  (content, not a description) and needs its own design. Skipping confirm
  does not widen the gap.

## Risks

- **A user's confirm rule aimed at a read stops holding it.** Shown on fakes:
  `confirm_patterns = ["\bpayroll\b"]` held `omarchy_help payroll` before and
  runs it after, while `omarchy_cli payroll` is still held. A user who wants
  a read stopped must write a deny rule. The README's policy section says so
  in this change.
- **New deny rules can misfire on actions.** For example, `cp .env.example
  .env` through `run_shell` is now refused, and so is `type_text "cat .env"`.
  Those refusals are intended: they are the same disclosure. `process.env.X`
  and `.env.example` were checked and do not match. `test_policy.py`'s
  ordinary-action cases still pass.
- **The path rules are a heuristic.** They do not catch:
  - a symlink to a secret (`Read ~/link` → `/etc/shadow`);
  - a secret under an unlisted name;
  - `Bash` spelling a path indirectly (`cat /etc/sha""dow`).

  #94 removes `Bash` from the voice brain, so `Read` with a literal path is
  the realistic case. Resolving symlinks in `describe_tool` is possible but
  out of scope.
- **The release turn now admits our reads.** A read in a release turn can't
  be held and still goes through the Executor's deny check, and the approval
  is spent only by `_same_call`. So it cannot spend or displace the approval.
- **`omarchy_help "sudo"` and similar lookups stay refused.** This is
  unchanged and fails in the safe direction.
- **Hosts:** every host that runs omarchy-voice, because this is the shared
  gate. The secret-path rules matter most on p620, where the claude-code
  backend and `Read` run.

## Verification

Unit tests on fakes only, in the existing files. No live desktop.

- `tests/test_policy.py`:
  - Each intent HOLD row (`omarchy_help` × reboot / shutdown / poweroff /
    suspend / hibernate / omarchy update / omarchy pkg install / close all
    windows / nixos-rebuild; `find_app` shutdown / reboot; `read_screen`
    query reboot; `read_terminal` nixos-rebuild) reaches a fake handler, with
    `executor.pending is None`.
  - `omarchy_help` reboot, then `omarchy_cli` "system reboot" on one executor
    holds the reboot.
  - `omarchy_help sudo` and a user word rule are still DENY.
  - A user *confirm* rule on a read runs, and the same rule on an action
    holds.
  - `Policy.check(..., read=True)` still raises `Denied`.
  - `READ_ONLY_TOOLS` is a `frozenset` and disjoint from `INPUT_TOOLS`.
  - Secret-path table: each denied path above is refused as `read <path>`,
    and each ordinary path above passes.
- `tests/test_claude_backend.py` (through the hook, as the file already
  does):
  - `Read …reboot-checklist.md` and `WebSearch "…reboot…"` are allowed.
  - `Read /etc/shadow` and the other 13 are denied, and a user path rule
    denies.
  - `Bash systemctl reboot` is still held.
  - Release turn: `mcp__omarchy__omarchy_help` is allowed, while
    `mcp__omarchy__launch_app`, `Bash` and `Read /etc/shadow` are denied.
  - `_is_read("screenshot")` is false.
- Mutation checks, each of which must turn a test red:
  - drop `read=` in `_call_locked`;
  - drop `read=` in `_decide`;
  - revert the release predicate;
  - delete any one secret-path pattern;
  - move `if read: return` above the deny loop.
- `python3 -m unittest discover -s tests` in the dev shell has no new
  failures.
- `nix flake check --no-write-lock-file` passes.
- `verify-gate` needs no change: case A (a user deny rule on a read) and case
  B (an ordinary read under a temp dir) keep their verdicts. It is not
  re-run live for this issue.
