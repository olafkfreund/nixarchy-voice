---
status: draft
issue: 109
author: olafkfreund
---

# Intent: drop one default deny or confirm rule without freezing the rest

Closes #109.

## Problem

Dropping one built-in deny rule means copying all the others. That hand-made
copy then stops receiving new rules, and nothing reports it.

How the lists are built today (origin/main, fc33ae2):

- `src/omarchy_voice/config.py:108` `DEFAULT_CONFIRM` (17 rules) and
  `config.py:132` `DEFAULT_DENY` (29 rules, including #100's 12 secret-path
  rules at `config.py:154-171`).
- `config.py:239-243` `LIST_UNION_KEYS` maps `confirm_patterns`,
  `deny_patterns` and `sensitive_patterns` to their defaults. `load()` at
  `config.py:557-560` unions the user's list after the defaults (deduplicated,
  in order) unless `<key>_replace` is true. With `_replace` set, the user's
  list is the whole list. You can add rules or replace the whole list.
  You cannot remove one rule.
- `[hands]` is a flattened section, so `hands.deny_patterns_replace` in TOML
  is the `deny_patterns_replace` field (`config.py:480-486`).
- `Policy.check` (`src/omarchy_voice/tools.py:202-210`) tests every deny
  pattern first, with `re.IGNORECASE`. A match raises `Denied`. The confirm
  patterns come next, and are skipped for reads (#100).
- The Home Manager module passes settings straight through:
  `nix/hm-module.nix:35-36` `settings` is `tomlFormat.type`, and
  `nix/hm-module.nix:197-199` writes it to `config.toml`. The module knows
  nothing about the policy keys, so a new key needs no module change.
- `doctor` counts rules and nothing else: `src/omarchy_voice/cli.py:255-257`
  prints "N deny rules, M confirm rules". It does not say whether a replace
  flag is set or whether any default is missing.
- Docs describe only add and replace: `README.md:888-890` and
  `share/config.example.toml:207-212`. The example uses `\\bssh\\b` as its
  sample deny rule.
- `config.py:210-215` (above `TERMINAL_SECRETS` at `config.py:216`) already points at this issue: if `TERMINAL_SECRETS`
  ever becomes configurable, dropping a default "takes #109's `*_remove`
  shape ... never a `*_replace`".

What that costs on p620 (read only:
`~/.config/nixos/hosts/p620/nixos/nixarchy.nix:260-287`, deployed as
`/nix/store/ynyw688wp2p2izk6fbwd03b6kzwxypk1-omarchy-voice-config.toml`).
p620 sets `hands.allow_shell = true` and `hands.deny_patterns_replace = true`
with a 16-rule list, written to drop `\bssh\b` only. The comment there names
the cost: "a rule added upstream later does not reach this host until copied
here." I loaded that TOML with main's `config.load()` and compared the result
against `DEFAULT_DENY`. The effective list is missing 14 of the 29 defaults:

- `\bssh\b`: the one rule meant to be dropped.
- `\bcurl\b.*\|\s*(ba)?sh`: the copy has `(bash|sh)` instead. It matches the
  same commands, but the string differs, so removal by exact string would
  already see this as a different rule.
- All 12 of #100's secret-path rules, which the copy predates:
  `/etc/g?shadow\b`, `/\.ssh(/|\b)`, `/\.gnupg(/|\b)`,
  `/run/(agenix|secrets)(\.d)?(/|\b)`, the `.env` rule,
  `\bid_(rsa|ecdsa|ed25519|dsa)\b(?!\.pub)`,
  `/\.(netrc|git-credentials|pgpass)\b`, `/\.aws/credentials\b`,
  `/\.config/gh/hosts\.yml\b`, `/\.claude/\.credentials\.json\b`,
  `/\.password-store(/|\b)`, `/\.local/share/keyrings(/|\b)`.

`allow_shell` is on, so on p620 `run_shell` and the read tools can reach
`~/.ssh/`, `/run/agenix` and `.env` files. #100 added its rules to block
exactly that. p620's `confirm_patterns` is not replaced: all 17 defaults
apply.

Other hosts, read only:
- razer (`~/.config/nixos/hosts/razer/nixos/nixarchy.nix:168-193`) has
  `allow_shell = false` and no deny or confirm keys, so it gets every default.
  Nothing is missing.
- p510 runs nixarchy but does not configure `programs.omarchy-voice` in its
  host files. Nothing is missing.

## Proposed outcome

- A host can name the defaults it does not want, and every other default
  still applies, including defaults added in later releases. For p620 that
  is one entry for ssh instead of a 16-line copy.
- `deny_patterns` and `confirm_patterns` work the same way.
  `sensitive_patterns` uses the same union code (`LIST_UNION_KEYS`), so it
  gets the same treatment unless the approver excludes it.
- `deny_patterns_replace` still works for anyone who wants a hand-maintained
  list. `doctor` then names the shipped defaults that list lacks, so drift is
  visible. Today drift is silent.
- `doctor` also names each default the host has removed on purpose, so a
  removed rule is visible and not just one fewer in a count.
- A removal entry that matches no default is reported, for example a typo or
  a rule renamed upstream. It must not be ignored silently.
- README and `share/config.example.toml` show removal as the way to drop a
  default, with `ssh` as the example, and describe replace as the
  hand-maintained option.

Design space for the spec (not decided here):

1. Remove by exact regex string, e.g.
   `deny_patterns_remove = [ "\\bssh\\b" ]`. No new structure, but it is
   fragile. p620's curl drift shows that one character changed upstream turns
   a removal into a no-op. If that happens the rule comes back, which fails
   closed and is safe, but it will look like a bug.
2. Remove by a stable rule name, e.g. `deny_rules_remove = [ "ssh" ]`. Each
   default needs a name, the way `DEFAULT_SENSITIVE` and `TERMINAL_SECRETS`
   already carry a label. Upstream can then change a regex without breaking
   anyone's removal. `doctor` and the `Denied` message can name the rule. The
   cost is changing two list shapes that tests and `verify_gate._rules`
   (`src/omarchy_voice/verify_gate.py:134-143`) read directly.
3. Remove by tag or group, e.g. `"secret-paths"`. This is coarse, and it
   makes it easy to remove many rules with one word, which is the opposite of
   what we want here. Could be layered on top of names later.

The deny rules and the confirm rules might use different options.

## Affected users and systems

- `src/omarchy_voice/config.py`: the default lists, `LIST_UNION_KEYS`,
  `load()`, and new `*_remove` fields.
- `src/omarchy_voice/cli.py`: `doctor` output (`shell_status`, the lines
  after it).
- `src/omarchy_voice/tools.py`: only if denials name the rule.
- `src/omarchy_voice/verify_gate.py` and the tests that build `Config` with
  pattern lists: only if the list shape changes (option 2).
- `nix/hm-module.nix`: no change expected, because settings pass straight
  through. Its option docs could mention the new key.
- `README.md`, `share/config.example.toml`.
- p620: after release, its NixOS config can go from a 16-rule replacement to
  a one-entry removal. That change is in the NixOS config repo, not in this
  repo or this task. Until then, #109 says p620 should copy #100's rules into
  its list, also in the NixOS repo.
- razer and p510: no change in behavior.

## Constraints

- Removal must fail closed. An entry that matches nothing removes nothing and
  is reported. It never widens what runs.
- There must be no way to remove every default silently. Removing all of
  them, or all the deny rules, has to show up clearly in `doctor` and in the
  log at start-up. The approver decides whether it should also be refused
  (see below).
- Removal must not be able to lift the non-configurable guards: the #94
  Bash-tool withdrawal, `TERMINAL_SECRETS`, and `refuse_while_recording`.
  This only covers the pattern lists.
- Existing configs must keep working unchanged. `*_replace` keeps its current
  meaning, and adding a user rule still unions.
- If a key is both added and removed, the result must be defined and
  documented.
- The TOML stays readable by hand. The Home Manager module stays a
  passthrough, with no Nix-side policy logic, so non-Nix installs get the
  same behavior.
- p620's NixOS config (`~/.config/nixos/hosts/p620/nixos/nixarchy.nix`) is
  the user's and read only for this task.

## Open questions

1. Remove by exact regex string, by stable rule name, or both (name
   preferred, string accepted)? If by name, should the defaults become
   `(name, pattern)` pairs like `DEFAULT_SENSITIVE`?
2. Should `*_remove` also cover `sensitive_patterns`, or only deny and
   confirm, as the issue says?
3. If a config removes every deny default, should that be refused at load,
   or allowed with a loud `doctor` and start-up warning?
4. With `*_replace` set, should `doctor` only warn about missing defaults, or
   should `*_replace` be deprecated for the deny list in favour of remove?
5. Should a removal entry that matches nothing be only a `doctor` warning,
   or an error at start-up?
6. Should the `Denied` message and `doctor` name the rule ("blocked by the
   secret-paths `.ssh` rule") rather than print the raw regex, as
   `tools.py:205` does today?
