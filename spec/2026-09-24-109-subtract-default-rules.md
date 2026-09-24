---
status: approved
issue: 109
intent: intent/2026-09-24-109-subtract-default-rules.md
---

# Spec: drop a built-in deny, confirm or sensitive rule by name, keep the rest

Line references are to origin/main at fc33ae2.

## The intent's open questions, answered

The intent was approved without answers to its six questions. Each answer
below is a decision made here, with its reason. Any of them can be rejected
at this gate. Rejecting one changes the Design section, not the approach.

1. **By regex string, by name, or both?** By name only. Each default becomes
   a `name: pattern` entry in an ordered dict. A regex string is not accepted
   in `*_remove`. Why: p620's curl rule shows the problem with strings. The
   copy there has `(bash|sh)` and the default has `(ba)?sh`. A removal by
   string breaks the same way the first time upstream edits a pattern. Names
   stay fixed when a regex changes. Accepting both would give two spellings
   for one thing, and would still leave the fragile one available. A pasted
   regex is not a name, so it gets the unknown-name warning (answer 5), and
   that warning points at the name list.
2. **Does `sensitive_patterns` get `*_remove` too?** Yes. It goes through the
   same `LIST_UNION_KEYS` loop (`config.py:239-243`, `557-560`), so leaving it
   out would take more code, not less. The likely use is real: the
   `credential-text` rule matches any window title containing "password" and
   will misfire. Removing a sensitive default only lets captures of those
   windows through. It does not let anything run. `tools.py:2379-2386`
   already checks sensitive patterns by membership, so it needs no change.
3. **A config that removes every deny default: refuse or warn?** Neither a
   load error nor a warning alone. The removal is **not applied**: every
   default in that list stays, and `doctor` and the start-up log both say
   why. This is the same rule for all three lists: a `*_remove` that names
   every default of its list is ignored. Why: a load error takes the whole
   assistant down, voice included, over a setting that has a safe
   interpretation. Warning and applying it would allow the exact outcome the
   intent says must not happen silently. Someone who really wants no
   built-in deny rules can still use `deny_patterns_replace = true` with
   their own list. That is an explicit choice, and `doctor` names every
   default it drops (answer 4).
4. **`*_replace`: warn about missing defaults, or deprecate it?** Keep it and
   warn. `doctor` names each shipped default whose exact pattern is not in
   the replaced list, by name, on one ✗ line per list. Why: replace is the
   only way to run a hand-maintained list, and existing configs must keep
   working unchanged (intent constraint). Deprecating it would add a
   warning to configs that are working. The missing-defaults line fixes the
   actual problem, which is that drift is silent. Comparing by exact string
   means an equivalent copy, like p620's curl rule, is reported as missing.
   That is correct: the host is not running the shipped rule.
5. **A removal name that matches nothing: warning or start-up error?** A
   warning in `doctor` (✗) and in the start-up log. The entry removes
   nothing, so the policy is unchanged. That is fail closed. Why: the effect
   of a typo is already the safe one, since the rule stays active. A start-up
   error would stop the daemon over it. This matches how unknown config keys
   are handled today (`cli.py:508-509`).
6. **Should denials and `doctor` name the rule?** Yes. A default's denial
   reads ``blocked by deny rule `ssh` (/\bssh\b/)``. The regex is kept after
   the name, so nothing in today's message is lost. A user-added rule has no
   name and keeps today's text, `blocked by deny rule /<pattern>/`
   (`tools.py:205`). `doctor` uses names everywhere it lists rules. No test
   asserts the current wording (`grep "blocked by" tests/` finds nothing), so
   the change is safe.

Also decided, because the intent requires it:

- **A key in both `deny_patterns` and `deny_patterns_remove`.** Removal
  applies to the defaults only. The user's own list is unioned after it. A
  pattern the user writes in `deny_patterns` is always in the effective
  list, even if it is also a removed default's pattern. The README says so.
- **Both `*_remove` and `*_replace` set.** Replace wins, as it does today.
  The remove list is ignored and `doctor` says so on a ✗ line. Removing
  entries from a list you wrote yourself means deleting lines from it.
- **Key spelling.** `deny_patterns_remove`, `confirm_patterns_remove`,
  `sensitive_patterns_remove`. This follows the `*_replace` naming, and
  `config.py:212-213` already names this shape (`*_remove`) for a future
  `TERMINAL_SECRETS` key.

## Rule names

Names are lowercase and hyphenated. They are unique within their list, and
once shipped they never change. A test enforces uniqueness and form. A
pattern may be rewritten under the same name. If a rule is retired, its name
goes into a retired-names set, so an old removal is reported as retired
rather than as a typo. That set is empty today and is not built until a rule
is actually retired.

**DEFAULT_DENY, 29** (`config.py:132-172`, in order):

| name | pattern |
|---|---|
| `rm-rf` | `\brm\s+-[a-zA-Z]*[rf]` |
| `mkfs` | `\bmkfs\b` |
| `dd` | `\bdd\s+if=` |
| `shred-wipefs` | `\b(shred\|wipefs)\b` |
| `write-block-device` | `>\s*/dev/[sn][dv]` |
| `passwd` | `\bpasswd\b` |
| `sudo` | `\bsudo\b` |
| `pkexec` | `\bpkexec\b` |
| `cryptsetup` | `\bcryptsetup\b` |
| `curl-pipe-shell` | `\bcurl\b.*\|\s*(ba)?sh` |
| `git-push` | `\bgit\s+push\b` |
| `ssh` | `\bssh\b` |
| `nix-collect-garbage` | `\bnix-collect-garbage\b` |
| `nix-store-gc` | `\bnix\s+store\s+(delete\|gc)\b` |
| `nix-store-delete` | `\bnix-store\s+--delete\b` |
| `nix-profile-wipe-history` | `\bnix\s+profile\s+wipe-history\b` |
| `nix-env-delete-generations` | `\bnix-env\s+--delete-generations\b` |
| `secret-shadow` | `/etc/g?shadow\b` |
| `secret-ssh-dir` | `/\.ssh(/\|\b)` |
| `secret-gnupg` | `/\.gnupg(/\|\b)` |
| `secret-agenix-sops` | `/run/(agenix\|secrets)(\.d)?(/\|\b)` |
| `secret-dotenv` | the `.env` rule (`config.py:161`) |
| `secret-ssh-key` | `\bid_(rsa\|ecdsa\|ed25519\|dsa)\b(?!\.pub)` |
| `secret-login-stores` | `/\.(netrc\|git-credentials\|pgpass)\b` |
| `secret-aws` | `/\.aws/credentials\b` |
| `secret-gh-token` | `/\.config/gh/hosts\.yml\b` |
| `secret-claude-login` | `/\.claude/\.credentials\.json\b` |
| `secret-pass-store` | `/\.password-store(/\|\b)` |
| `secret-keyrings` | `/\.local/share/keyrings(/\|\b)` |

The `secret-` prefix is only part of the name. It is not a group that can be
removed with one word (see Alternatives). It keeps `ssh` (the command) and
`secret-ssh-dir` (the key directory) clearly apart, so removing `ssh` cannot
be read as opening `~/.ssh`.

**DEFAULT_CONFIRM, 17** (`config.py:108-128`): `shutdown`, `reboot`,
`poweroff`, `suspend`, `hibernate`, `omarchy-update`, `omarchy-drive`,
`omarchy-pkg`, `omarchy-install`, `omarchy-refresh`, `omarchy-reinstall`,
`hyprland-exit` (`\bhl\.dsp\.exit\b`), `close-all`, `nixos-rebuild`,
`home-manager-switch`, `nixarchy-apply`, `nix-flake-update`.

**DEFAULT_SENSITIVE, 5** (`config.py:192-205`): `password-manager`,
`credential-prompt`, `private-browsing`, `credential-text`, `banking`.

Total: 51 names.

## Demonstration

Prototype: a scratch script (not committed) that runs the real
`omarchy_voice.config.load()` on temporary `config.toml` files, applies the
removal step below on top, and checks the result with the real
`tools.Policy.check`. Its name tables are built with
`dict(zip(names, config.DEFAULT_DENY, strict=True))`, so the 51 names above
line up one to one with main's lists. System `python3`,
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`. All assertions passed:

```
1 ok: ssh allowed, 28/29 deny defaults active incl. 12 secret-path; ['deny_patterns: removed ssh']
2 ok: later-added default applies
3 ok: unknown name -> policy unchanged; ['deny_patterns_remove: no default named shh; nothing removed for it']
4 ok: remove-all not applied, all defaults stay; ['deny_patterns_remove names every default; not applied, all defaults stay']
5 p620 today: 16 deny; ['deny_patterns_replace: missing defaults curl-pipe-shell, ssh, secret-shadow, secret-ssh-dir, secret-gnupg, secret-agenix-sops, secret-dotenv, secret-ssh-key, secret-login-stores, secret-aws, secret-gh-token, secret-claude-login, secret-pass-store, secret-keyrings']
```

What each case asserted:

1. `[hands] allow_shell = true, deny_patterns_remove = ["ssh"]`:
   `ssh otherhost true` is not denied, although it is denied with no removal.
   The effective deny list is `set(DEFAULT_DENY) - {\bssh\b}`, which is 28
   rules. All 12 `secret-*` patterns are present. `cat ~/.ssh/id_ed25519`,
   `cat /run/agenix/openai`, `cat .env` and `sudo ls` are all denied.
2. A rule is added to `DEFAULT_DENY` after the config was written (a fake
   `future-danger`). The same config denies it and still allows ssh.
3. `deny_patterns_remove = ["shh"]`: the deny list equals `DEFAULT_DENY`,
   ssh is still denied, and one warning names `shh`.
4. A removal that names all 29 defaults: the deny list equals
   `DEFAULT_DENY`, with one warning.
5. p620's deployed TOML
   (`/nix/store/ynyw688wp2p2izk6fbwd03b6kzwxypk1-omarchy-voice-config.toml`):
   the replace warning names the 14 missing defaults. This matches the
   intent's count, and here each one is named.

## Design

### `src/omarchy_voice/config.py`

- `DEFAULT_DENY_RULES` and `DEFAULT_CONFIRM_RULES` become ordered dicts from
  name to pattern. They replace the list literals at `:108` and `:132` and
  keep every existing comment. `DEFAULT_DENY = list(DEFAULT_DENY_RULES.values())`
  and the same for confirm. This follows the `DEFAULT_SENSITIVE_PATTERNS`
  precedent (`:208`). Every reader of the flat lists keeps working unchanged:
  `verify_gate._rules` (`verify_gate.py:142-143`), `LIST_UNION_KEYS`, the
  `Config` defaults at `:480-481`, and the seven test files that import them.
  So the list-shape cost the intent listed under option 2 does not arise.
- `DEFAULT_SENSITIVE` keeps its `(kind, pattern)` pairs (`tools.py:2379`
  reads them). Add `DEFAULT_SENSITIVE_RULES = dict(zip((<5 names>),
  DEFAULT_SENSITIVE_PATTERNS, strict=True))` so a sixth sensitive rule
  without a name fails at import.
- `LIST_UNION_KEYS` (`:239-243`) maps each key to its name→pattern dict
  instead of a list.
- `Config` (`:480-486`) gains `deny_patterns_remove`,
  `confirm_patterns_remove` and `sensitive_patterns_remove`, each a
  `list[str]` defaulting to empty. Because they are real fields, they are not
  reported as unknown keys. It also gains `policy_notes: list[str]`, set by
  `load()` the way `unknown_keys` is (`:554`). Each note is a
  `(ok: bool, text)` pair, so `doctor` can print ✓ or ✗ without re-deriving
  anything.
- `load()`'s loop (`:557-560`) becomes, for each key:
  1. `*_replace` true: take the user's list as now. If `*_remove` is also
     set, add a ✗ note that it is ignored. Add a ✗ note naming each default
     whose pattern is not in the list.
  2. Otherwise: unknown names get a ✗ note and remove nothing. If the known
     names cover every default, add a ✗ note and remove nothing. Otherwise
     the result is `[defaults minus removed names] + user list`,
     deduplicated in order. If anything was removed, add a note listing the
     removed names (informational, not ✗).
  3. The loop runs when `key in data` **or** `f"{key}_remove" in data`.
     Today it only runs on the first, and a remove-only config would
     otherwise be skipped.
  Duplicate names in a remove list are deduplicated. A non-list value is a
  TOML type error: `Config` accepts it today without complaint, and `load()`
  treats a non-list `*_remove` as empty with a ✗ note rather than iterating
  over a string's characters.
- The `TERMINAL_SECRETS` comment at `:210-215` stays true as written.

### `src/omarchy_voice/tools.py`

- `Policy.check` (`:202-205`): when a denying pattern is a default, the
  message becomes ``blocked by deny rule `<name>` (/<pattern>/)``. It uses a
  module-level reverse map, `{pattern: name}` built from
  `DEFAULT_DENY_RULES`. Rules not in the map keep today's text. Confirm is
  unchanged, since `NeedsConfirmation` carries the action, not the rule.

### `src/omarchy_voice/cli.py`

- `shell_status` (`:245-257`) keeps its count line. After it, `doctor`
  prints each `config.policy_notes` entry as
  `  {_tick(ok)} {text}`, next to the unknown-key lines (`:508-509`). Example
  lines:
  - `  ✓ deny rules removed: ssh`
  - `  ✗ deny_patterns_remove: no built-in rule named "shh" (names: README "Rule names")`
  - `  ✗ deny_patterns_remove names every built-in deny rule; not applied`
  - `  ✗ deny_patterns_replace: built-in rules not in your list: curl-pipe-shell, ssh, secret-shadow, …`
    (every name is listed, none are cut)
- `cmd_run` (`:188-200`): next to `consent_notice`, a `policy_notice(config)`
  writes each note to `cfg.LOG_FILE` and to stderr (the journal) on every
  start. Unlike `consent_notice`, this is not once-only. A removed rule or a
  drifted list is current state, not news, and the intent requires it at
  start-up.

### `nix/hm-module.nix`

No change. Verified: `settings` is `tomlFormat.type` (`:35-36`), and
`:197-199` writes it with `tomlFormat.generate`. So
`settings.hands.deny_patterns_remove = [ "ssh" ];` reaches `config.toml` as
`[hands] deny_patterns_remove = ["ssh"]`, which `load()` flattens
(`:543-547`). The module adds nothing to `hands` except `desktop_control`
(`:25-27`, merged with `//`), so the new key passes through. No Nix-side
policy logic, as the intent requires.

### Docs

- `README.md:888-890`: remove by name is the way to drop a default, with
  `deny_patterns_remove = ["ssh"]` as the example. Include the name tables
  above, the add-and-remove rule, the remove-all rule, and replace as the
  hand-maintained option with its `doctor` warning.
- `share/config.example.toml:207-212`: add the three commented `*_remove`
  lines. The sample becomes `# deny_patterns_remove = ["ssh"]`. The sample
  add stays, with a different rule than `\bssh\b`, so it no longer reads as a
  way to change ssh.

## Migration for p620 (the user applies this after release)

This is `~/.config/nixos/hosts/p620/nixos/nixarchy.nix`, currently lines
263-287. It was read only and has not been edited. Apply it only after the
host's omarchy-voice input includes this change. On an older build the key
is unknown and ignored, so ssh is denied again. That fails closed, and
`doctor` reports the key as unknown.

Before:

```nix
        # Full control by voice: the shell tool and hl.dsp.exec are on. The
        # deny list (sudo, rm -rf, dd, ssh, git push, nix gc) and the confirm
        # list (shutdown, reboot, nixos-rebuild) still apply to every command.
        hands.allow_shell = true;

        # ssh allowed by voice. Config deny rules are ADDED to the built-in list
        # and none can be removed, so this is the built-in list (omarchy_voice
        # config.py DEFAULT_DENY) minus \bssh\b, replacing it. The ceiling: a
        # rule added upstream later does not reach this host until copied here.
        hands.deny_patterns_replace = true;
        hands.deny_patterns = [
          "\\brm\\s+-[a-zA-Z]*[rf]"
          "\\bmkfs\\b"
          "\\bdd\\s+if="
          "\\b(shred|wipefs)\\b"
          ">\\s*/dev/[sn][dv]"
          "\\bpasswd\\b"
          "\\bsudo\\b"
          "\\bpkexec\\b"
          "\\bcryptsetup\\b"
          "\\bcurl\\b.*\\|\\s*(bash|sh)"
          "\\bgit\\s+push\\b"
          "\\bnix-collect-garbage\\b"
          "\\bnix\\s+store\\s+(delete|gc)\\b"
          "\\bnix-store\\s+--delete\\b"
          "\\bnix\\s+profile\\s+wipe-history\\b"
          "\\bnix-env\\s+--delete-generations\\b"
        ];
```

After:

```nix
        # Full control by voice: the shell tool and hl.dsp.exec are on. The
        # deny list (sudo, rm -rf, dd, git push, nix gc, secret paths) and the
        # confirm list (shutdown, reboot, nixos-rebuild) still apply to every
        # command.
        hands.allow_shell = true;

        # ssh allowed by voice: drop the one built-in deny rule named "ssh".
        # Every other built-in rule still applies, including rules added
        # upstream later. `omarchy-voice doctor` shows "deny rules removed: ssh".
        hands.deny_patterns_remove = [ "ssh" ];
```

Check after the switch: `omarchy-voice doctor` shows `28 deny rules`,
`✓ deny rules removed: ssh`, and no ✗ policy line.

## Alternatives rejected

- **Remove by exact regex string** (intent option 1). This breaks silently
  whenever upstream edits a pattern, as p620's curl copy shows. It also
  makes the TOML need regex escaping for what is a removal.
- **Names and strings both.** Two spellings for one thing, and the fragile
  one stays in use.
- **Remove by group, such as `secret-paths`** (intent option 3). One word
  removes twelve rules. Can be added on top of names later if someone asks.
- **Refuse to load when every default is removed, or on an unknown name.**
  Takes voice control down over a setting whose safe reading is obvious.
- **Deprecate `*_replace`.** It warns configs that work today. The
  missing-names line addresses drift without it.
- **Changing `DEFAULT_DENY` and `DEFAULT_CONFIRM` to pair lists** (the
  `DEFAULT_SENSITIVE` shape). This breaks `verify_gate._rules` and seven test
  files for nothing. A dict plus a derived list keeps every reader as it is.
- **Nix-side subtraction in the HM module.** Non-Nix installs would not get
  it, and the intent forbids policy logic in Nix.
- **Leaving `sensitive_patterns` out.** More code (a special case in the
  loop) for a less consistent result.

## Risks

- **A name is wrong or collides.** The shipped names become an interface.
  Mitigated by the uniqueness and form test and the review of the table
  above at this gate. After release a name cannot be changed, only retired.
- **Someone reads `ssh` as covering `~/.ssh`.** It does not. `secret-ssh-dir`
  and `secret-ssh-key` stay active, and the README says so. Demonstration
  case 1 shows `cat ~/.ssh/id_ed25519` is still denied.
- **p620 applies the migration before the input is bumped.** ssh is denied
  again and `doctor` shows an unknown key. Nothing is widened.
- **Start-up notes every morning on p620** ("deny rules removed: ssh") are
  one log line. That is accepted: the intent asks for removed rules to be
  visible.
- **Hosts:** razer and p510 set none of these keys, so their effective lists
  are byte-for-byte what they are today. p620 is unchanged until the user
  applies the migration. Until then its `doctor` gains the ✗ replace line
  naming 14 rules, which is the intended effect.
- **A Denied message consumer that parses the text.** None found
  (`claude_backend.py:424` and `tools.py:1851` log or return the text as is).

## Verification

- `tests/test_config.py`, loader tests on temporary `config.toml` files
  (the five demonstration cases, as tests):
  - remove `ssh` gives 28 deny rules and keeps all 12 `secret-*` rules;
  - a default added to the dict at test time (monkeypatched) is present
    under a remove-only config;
  - an unknown name leaves `deny_patterns == DEFAULT_DENY` and adds one ✗ note;
  - naming all deny defaults leaves `deny_patterns == DEFAULT_DENY` and adds one ✗ note;
  - replace with p620's 16-rule list adds a ✗ note naming exactly the 14;
  - replace and remove together: replace wins, remove is noted;
  - add and remove of the same pattern: the pattern is present;
  - `confirm_patterns_remove = ["reboot"]` and
    `sensitive_patterns_remove = ["credential-text"]` each remove one rule;
  - no `*_remove` key: effective lists are unchanged from today (the
    existing union tests keep passing untouched);
  - names are unique, match `^[a-z0-9]+(-[a-z0-9]+)*$`, and
    `list(DEFAULT_DENY_RULES.values()) == DEFAULT_DENY`.
- `tests/test_policy.py`: a default denial names the rule and keeps the
  regex; a user rule's denial text is unchanged.
- `tests/test_backend_choice.py` (where `shell_status` is tested today): the `doctor`
  lines for removed, unknown, remove-all and replace-missing, and
  `policy_notice` writing to the log file (with `LOG_FILE` in `tmp_path`).
- The full suite: `python -m pytest -q` with
  `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`.
- `nix flake check --no-write-lock-file` (builds the package and the
  module's eval checks).
- On p620, after the user applies the migration: `omarchy-voice doctor` as
  in the migration section, and a spoken "ssh to p510 and run uptime" runs.
