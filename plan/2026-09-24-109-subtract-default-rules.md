---
status: approved
issue: 109
spec: spec/2026-09-24-109-subtract-default-rules.md
---

# Plan: drop a built-in deny, confirm or sensitive rule by name, keep the rest

Base: `origin/main` `fc33ae2`. Baseline: 1075 passed under pytest, and 1075
OK under unittest. Every `file:line` below was checked against `fc33ae2`.
Issue #109 was OPEN when this plan was written.

## Approved decisions, carried over from the spec

1. **Remove by stable name only.** Each default becomes a `name: pattern`
   entry in an ordered dict. `*_remove` takes names only. It never takes a
   regex string, and it never takes both. A pasted regex is not a name, so
   it gets the unknown-name warning (decision 7). The reason: p620's copy of
   the curl rule has `(bash|sh)` where the default has `(ba)?sh`, and a
   removal by string breaks the first time upstream edits a pattern. Names
   stay fixed when a pattern changes.
2. **The keys** are `deny_patterns_remove`, `confirm_patterns_remove` and
   `sensitive_patterns_remove`. Each is a `list[str]` that defaults to
   empty, and each is a real `Config` field, so none of them is reported as
   an unknown key.
3. **Names are an interface.** They are lowercase and hyphenated, match
   `^[a-z0-9]+(-[a-z0-9]+)*$`, and are unique within their list. Once shipped
   a name never changes. A pattern may be rewritten under the same name. A
   retired rule's name would go into a retired-names set, which is not built
   until a rule is actually retired (YAGNI). The `secret-` prefix is only part
   of the name, not a group that one word can remove. So `ssh` (the command)
   and `secret-ssh-dir` (the key directory) are clearly separate rules.
4. **All 51 names**, in list order:
   - **DEFAULT_DENY, 29** (`config.py:132-172`): `rm-rf`, `mkfs`, `dd`,
     `shred-wipefs`, `write-block-device`, `passwd`, `sudo`, `pkexec`,
     `cryptsetup`, `curl-pipe-shell`, `git-push`, `ssh`,
     `nix-collect-garbage`, `nix-store-gc`, `nix-store-delete`,
     `nix-profile-wipe-history`, `nix-env-delete-generations`, and the 12
     secret-path rules from #100: `secret-shadow`, `secret-ssh-dir`,
     `secret-gnupg`, `secret-agenix-sops`, `secret-dotenv`, `secret-ssh-key`,
     `secret-login-stores`, `secret-aws`, `secret-gh-token`,
     `secret-claude-login`, `secret-pass-store`, `secret-keyrings`.
   - **DEFAULT_CONFIRM, 17** (`config.py:108-128`): `shutdown`, `reboot`,
     `poweroff`, `suspend`, `hibernate`, `omarchy-update`, `omarchy-drive`,
     `omarchy-pkg`, `omarchy-install`, `omarchy-refresh`,
     `omarchy-reinstall`, `hyprland-exit`, `close-all`, `nixos-rebuild`,
     `home-manager-switch`, `nixarchy-apply`, `nix-flake-update`.
   - **DEFAULT_SENSITIVE, 5** (`config.py:192-205`): `password-manager`,
     `credential-prompt`, `private-browsing`, `credential-text`, `banking`.
5. **`sensitive_patterns` can be removed from too.** It goes through the same
   `LIST_UNION_KEYS` loop. Removing a sensitive rule only lets captures of
   those windows through. It does not let anything run.
   `tools.py:2379-2386` checks sensitive rules by membership, so it needs no
   change.
6. **Removing every default in a list is not applied.** If the known names in
   a `*_remove` cover every default of that list, nothing is removed and
   every default stays. `doctor` and the start-up log both say why. This is
   not a load error and not a warning that still applies the removal. The
   same rule holds for all three lists. Someone who really wants no built-in
   deny rules can use `*_replace = true`, and then decision 8 names every
   default that is dropped.
7. **An unknown name removes nothing and warns.** It gives a ✗ line in
   `doctor` and in the start-up log. The policy is unchanged, so it fails
   closed. This matches how unknown keys are handled (`cli.py:508-509`).
   Duplicate names are deduplicated. A `*_remove` that is not a list is
   treated as empty and gets a ✗ note. It is never iterated character by
   character.
8. **`*_replace` stays, and `doctor` names the missing defaults.** When
   replace is true, `doctor` gives one ✗ line per list naming every default
   whose exact pattern string is not in the replaced list. No name is cut
   from the line. Because the comparison is by exact string, an equivalent
   copy such as p620's curl rule is reported as missing. That is correct:
   the host is not running the shipped rule. Replace is not deprecated.
9. **Replace wins over remove.** If both are set, the remove list is ignored
   and a ✗ note says so.
10. **Add and remove of the same pattern.** Removal applies to the defaults
    only. The user's own `*_patterns` list is unioned after it, so a pattern
    the user writes is always in the effective list, even if it is also a
    removed default's pattern.
11. **Effective list** (no replace): `[defaults minus removed names] + user
    list`, deduplicated in order. If anything was removed, there is an
    informational ✓ note naming the removed rules.
12. **Configs that set none of the new keys behave exactly as today.** razer
    and p510 get byte-for-byte the same effective lists.
13. **Denials name the rule.** A default's denial reads
    ``blocked by deny rule `ssh` (/\bssh\b/)``, so the regex is still in the
    message. A user-added rule that is not a default pattern keeps today's
    text, `blocked by deny rule /<pattern>/` (`tools.py:205`). Confirm
    prompts are unchanged.
14. **Notes appear at start-up on every start.** They go to `LOG_FILE` and
    stderr, and they are not once-only: a removed rule is current state.
15. **No HM module change.** `settings` is `tomlFormat.type`
    (`nix/hm-module.nix:36`) and is written with `tomlFormat.generate`
    (`:199`). `hands` only gets `desktop_control` merged into it (`:26`), so
    `settings.hands.deny_patterns_remove` passes through to `config.toml`.
    No policy logic goes into Nix.
16. **The flat lists stay.** `DEFAULT_DENY = list(DEFAULT_DENY_RULES.values())`,
    and the same for confirm. Every reader keeps working unchanged:
    `verify_gate._rules` (`verify_gate.py:142-143`), `Config` defaults
    (`config.py:480-481`), and the test files that import the lists.
    `DEFAULT_SENSITIVE` keeps its `(kind, pattern)` pairs.
17. **p620 migration snippet.** The user applies it after bumping the
    omarchy-voice input. This plan does **not** edit the user's NixOS
    config. In `~/.config/nixos/hosts/p620/nixos/nixarchy.nix` (lines
    263-287 when the spec was written), replace the
    `hands.deny_patterns_replace = true;` line, the 16-entry
    `hands.deny_patterns = [ … ];` list and the comments above them with:

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

    After the switch, `omarchy-voice doctor` shows `28 deny rules` and
    `✓ deny rules removed: ssh`, and no ✗ policy line. If the snippet is
    applied before the input is bumped, the key is unknown and ignored, so
    ssh is denied again and `doctor` reports an unknown key. That fails
    closed.

## Resolutions of points the spec left open

These are marked **(R)** where they are used below. None of them changes a
decision above.

- **R1. Which list a replace uses.** The replace branch uses the value
  already loaded into the field (`getattr(cfg, key)`), which is exactly
  today's behaviour. With replace set and no list, that value is the defaults.
- **R2. A `doctor` function for the notes.** The spec prints the notes
  inline in `cmd_doctor`. To make that testable the way `shell_status` is,
  add `policy_status(config) -> list[str]` and call it from `cmd_doctor`.
  `shell_status` stays unchanged, and its three `len(lines) == 1` tests
  (`tests/test_backend_choice.py:348-360`) keep passing.
- **R3. One ✗ note per list for unknown names.** A single note lists every
  unknown name in that list, which satisfies "one ✗ note".
- **R4. Test style.** The spec says `tmp_path`, but `tests/test_config.py`
  and `tests/test_backend_choice.py` are `unittest.TestCase` suites, and they
  must run under both runners. Use `tempfile` and
  `mock.patch.object(cfg, "LOG_FILE", …)`, following `ConfigLoadTests.write`
  (`tests/test_config.py:16-21`).
- **R5. The spec's "seven test files"** that import the flat lists are
  actually four: `test_config`, `test_policy`, `test_claude_backend` and
  `test_verify_gate`. This makes no difference, because the flat lists stay.
- **R6. A user-added pattern that is identical to a default's pattern** is
  named after that default in a denial, because the reverse map is keyed by
  pattern. The text is still accurate.
- **R7. A user's `policy_notes` key.** It is a `Config` field, so a
  `policy_notes` key in TOML would be accepted as known. `load()` always
  overwrites the field. `unknown_keys` has the same property today.

## Steps

0. **Baseline.** `git switch feat/109-subtract-default-rules` rebased on
   `origin/main` (`fc33ae2`), then, with
   `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`:
   `nix develop -c python -m pytest -q tests` and
   `nix develop -c python3 -m unittest discover -s tests`
   → verify by: 1075 passed, and `Ran 1075 tests … OK`.
1. **`src/omarchy_voice/config.py:106-128`:** replace the `DEFAULT_CONFIRM`
   list literal with `DEFAULT_CONFIRM_RULES = {name: pattern, …}` (17 entries
   in order, comments kept), then
   `DEFAULT_CONFIRM = list(DEFAULT_CONFIRM_RULES.values())`
   → verify by: `DEFAULT_CONFIRM` is equal to the list in
   `git show origin/main:src/omarchy_voice/config.py`, loaded as a scratch
   module in the scratchpad, and a pytest run stays green.
2. **`config.py:130-172`:** the same for deny. `DEFAULT_DENY_RULES` gets 29
   entries in order, and every comment is kept, including the `.env` rule's
   trailing comment lines. Then `DEFAULT_DENY = list(DEFAULT_DENY_RULES.values())`
   → verify by: the same comparison, 29 entries, 12 names starting `secret-`.
3. **`config.py:208`:** after `DEFAULT_SENSITIVE_PATTERNS`, add
   `DEFAULT_SENSITIVE_RULES = dict(zip(("password-manager", "credential-prompt",
   "private-browsing", "credential-text", "banking"), DEFAULT_SENSITIVE_PATTERNS,
   strict=True))` → verify by: import succeeds. Temporarily adding a sixth
   `DEFAULT_SENSITIVE` entry raises `ValueError` at import (then revert).
4. **`config.py:236-243`:** `LIST_UNION_KEYS` maps each key to its
   `*_RULES` dict. Update the comment above it to mention `*_remove`
   → verify by: `grep -n LIST_UNION_KEYS src/` shows only `config.py` uses it.
5. **`config.py:480-486` and `:528`:** add
   `confirm_patterns_remove`, `deny_patterns_remove` and
   `sensitive_patterns_remove` (`list[str] = field(default_factory=list)`),
   and `policy_notes: list[tuple[bool, str]] = field(default_factory=list)`
   next to `unknown_keys` → verify by: `test_the_new_keys_are_known`-style
   check that `[hands] deny_patterns_remove = ["ssh"]` is not in
   `unknown_keys`.
6. **`config.py:557-560`, the `load()` loop.** For each `key, rules`, run
   when `key in data or f"{key}_remove" in data` (the second condition is
   new):
   - Read `remove = data.get(f"{key}_remove", [])`. If it is not a list, add
     the note `(False, f"{key}_remove must be a list of rule names; ignored")`
     and use `[]`. Deduplicate it in order.
   - If `data.get(f"{key}_replace")`: keep `getattr(cfg, key)` **(R1)**. If
     `remove` is set, add `(False, f"{key}_remove is ignored because {key}_replace = true")`.
     Let `missing = [n for n, p in rules.items() if p not in the list]`. If
     it is not empty, add
     `(False, f"{key}_replace: built-in rules not in your list: {', '.join(missing)}")`.
   - Otherwise: `unknown = [n for n in remove if n not in rules]`. If it is
     not empty, add
     `(False, f'{key}_remove: no built-in rule named {", ".join(unknown)} (names: README "Rule names")')`
     **(R3)**. Let `known = set(remove) & rules.keys()`. If `known` equals
     `rules.keys()`, add
     `(False, f"{key}_remove names every built-in {label} rule; not applied")`
     and set `known = set()`. The result is
     `list(dict.fromkeys([*(p for n, p in rules.items() if n not in known), *data.get(key, [])]))`.
     If `known` is not empty, add
     `(True, f"{label} rules removed: {', '.join(n for n in rules if n in known)}")`.
   - Here `label` is `deny`, `confirm` or `sensitive`, from
     `key.removesuffix("_patterns")`. Write both the list and the normalised
     remove list back with `replace(cfg, …)`. Set
     `policy_notes` unconditionally **(R7)**.

   → verify by: every existing `ConfigLoadTests` passes unchanged, and the
   new tests in step 10 pass.
7. **`src/omarchy_voice/tools.py:199-205`:** add a module-level
   `_DEFAULT_DENY_NAMES = {p: n for n, p in config_mod.DEFAULT_DENY_RULES.items()}`
   (or read it from the imported module the file already uses). In
   `Policy.check`, look the name up with `name = _DEFAULT_DENY_NAMES.get(pattern)`
   and raise ``Denied(f"blocked by deny rule `{name}` (/{pattern}/)")`` when
   there is a name, or keep today's text when there is not
   → verify by: `grep -rn "blocked by" src tests` shows only this site plus
   the new tests. The `claude_backend.py:424` and `tools.py:1851` consumers
   pass the text through unchanged.
8. **`src/omarchy_voice/cli.py`:**
   - Add `policy_status(config) -> list[str]` returning
     `[f"  {_tick(ok)} {text}" for ok, text in config.policy_notes]`
     **(R2)**. In `cmd_doctor`, call it right after the `shell_status` loop
     (`:504-505`), before the unknown-keys line (`:508-509`).
   - Add `policy_notice(config) -> None`. It writes each note to
     `cfg.LOG_FILE` in `consent_notice`'s timestamp format (`:306-308`),
     prefixed `✓`/`✗`, and prints it to `sys.stderr`. There is no marker
     file, so it runs on every start. Call it in `cmd_run` right after
     `consent_notice(config)` (`:196`).

   → verify by: `omarchy-voice doctor` with a scratch
   `XDG_CONFIG_HOME` config containing `deny_patterns_remove = ["ssh"]`
   prints `28 deny rules` and `✓ deny rules removed: ssh`.
9. **Docs.**
   - `README.md:888-890`: explain that removing by name is how to drop a
     default, with the example `deny_patterns_remove = ["ssh"]`. Add a
     "Rule names" section with the three tables from decision 4 (name and
     pattern). State the add-and-remove rule (decision 10), the remove-all
     rule (decision 6), that `ssh` does not cover `~/.ssh`, and that replace
     is the hand-maintained option with its `doctor` warning.
   - `share/config.example.toml:207-212`: add commented
     `# confirm_patterns_remove = []`, `# deny_patterns_remove = ["ssh"]` and
     `# sensitive_patterns_remove = []`. Change the sample deny add from
     `\\bssh\\b` to another rule (`\\bterraform\\s+destroy\\b`), so it no
     longer reads as a way to change ssh.

   → verify by: `tomllib` parses the example with the comments removed, and
   the `[hands]` keys it contains are all known.
10. **Tests** (see below) → verify by: both runners green.
11. **`nix flake check --no-write-lock-file`** → verify by: exit 0 (it runs
    `pytest tests -q` in the sandbox, `flake.nix:145`, and evaluates the
    module).

## Tests

Every new test imports `_isolated` first. Loader tests write a temporary
`config.toml` and call the real `cfg.load(path)`, using the existing
`ConfigLoadTests.write` helper.

`tests/test_config.py`, new class `RemoveDefaultRuleTests`:

- **ssh allowed, 28 others active.** `[hands]\nallow_shell = true\ndeny_patterns_remove = ["ssh"]`:
  `len(deny_patterns) == 28`, `\bssh\b` is not in it, and all 12 `secret-*`
  patterns are in it. `Policy(loaded).check("ssh p510 uptime")` does not
  raise. `cat ~/.ssh/id_ed25519`, `cat /run/agenix/openai`, `cat .env` and
  `sudo ls` all raise `Denied`. `policy_notes == [(True, "deny rules removed: ssh")]`.
- **A later-added default still applies.** Patch
  `cfg.LIST_UNION_KEYS["deny_patterns"]` with
  `mock.patch.dict` to add `"future-danger": r"\bfuture-danger\b"`. The same
  remove-only config includes that pattern and does not include `\bssh\b`.
- **Unknown name.** `deny_patterns_remove = ["shh"]` gives
  `deny_patterns == DEFAULT_DENY`, exactly one ✗ note, and that note contains
  `shh`.
- **Remove-all is not applied.** A remove list with all 29 names gives
  `deny_patterns == DEFAULT_DENY` and one ✗ note containing `not applied`.
  The same holds for confirm with all 17 names.
- **A replaced list's missing defaults are named.** A fixture string in the
  test holds p620's 16-rule list (with `(bash|sh)` in the curl rule) and
  `deny_patterns_replace = true`. The single ✗ note names exactly these 14:
  `curl-pipe-shell`, `ssh` and the 12 `secret-*` names. The test parses the
  names out of the note and compares them as a set of size 14.
- **Replace and remove together.** Replace wins. The list is the user's, and
  there is a ✗ "ignored" note.
- **Add and remove of the same pattern.** `deny_patterns = ["\\bssh\\b"]` and
  `deny_patterns_remove = ["ssh"]` keep `\bssh\b` in the list.
- **The other two lists.** `confirm_patterns_remove = ["reboot"]` gives 16
  confirm rules. `sensitive_patterns_remove = ["credential-text"]` gives 4
  sensitive rules.
- **Bad type.** `deny_patterns_remove = "ssh"` gives `DEFAULT_DENY` and a ✗
  note. The value is not treated as the characters `s`, `s`, `h`.
- **No new keys.** A config without any `*_remove` key has no notes, and its
  lists equal today's. The existing union and replace tests stay untouched.
- **Names are stable.** Literal lists of all 29, 17 and 5 names, in order,
  equal `list(DEFAULT_*_RULES)`, so renaming a rule breaks this test. Every
  name matches `^[a-z0-9]+(-[a-z0-9]+)*$`. Also check
  `list(DEFAULT_DENY_RULES.values()) == DEFAULT_DENY`, the same for confirm,
  and `list(DEFAULT_SENSITIVE_RULES.values()) == DEFAULT_SENSITIVE_PATTERNS`.

`tests/test_policy.py`:

- A default denial names the rule: `Policy(Config()).check("ssh host")`
  raises a message equal to ``blocked by deny rule `ssh` (/\bssh\b/)``.
- A user-added rule keeps today's text:
  `Config(deny_patterns=[r"\bwipe\b"])` raises exactly
  `blocked by deny rule /\bwipe\b/`.

`tests/test_backend_choice.py`:

- `policy_status` returns the ✓ removed line, and the ✗ unknown, remove-all
  and replace-missing lines, each built from a loaded config.
- `policy_notice` with `cfg.LOG_FILE` patched to a temp file **(R4)** writes
  one line per note to that file and to stderr (captured with
  `contextlib.redirect_stderr`), and writes the same again on a second call.
  It writes nothing when there are no notes.

Commands, with `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` exported:

```
nix develop -c python -m pytest -q tests          # 1075 + new, all pass
nix develop -c python3 -m unittest discover -s tests   # same count, OK
nix flake check --no-write-lock-file              # exit 0
```

### Mutation checks

Make each mutation by hand, run the named test, see it fail, then revert.

| Mutation | Test that must fail |
|---|---|
| Loop condition back to `key in data` only | ssh allowed (remove-only config) |
| Drop the remove-all guard | remove-all is not applied |
| Add unknown names to the removed set, or remove by pattern string | unknown name |
| Apply remove before replace (replace does not win) | replace and remove together |
| Replace-missing compares by name instead of pattern | p620 fixture (14, needs `curl-pipe-shell`) |
| Union the user list before the subtraction | add and remove of the same pattern |
| Iterate a string `*_remove` | bad type |
| Rename `ssh` to `ssh-command` | names are stable |
| Drop the name from `Denied` | default denial names the rule |
| Always use the named format | user-added rule keeps today's text |
| Make `policy_notice` once-only (marker file) | second-call assertion |

## Landing order

This is independent of #120, #114 and #112. None of them has a branch on
the remote yet. Whichever lands second rebases. The likely conflict points
are `config.py` around `LIST_UNION_KEYS` and the `load()` loop, and
`cmd_doctor` in `cli.py`. #100 is already on main (`59c2348`) and added the
12 `secret-*` defaults, so all of them are named here. If another default is
added to `DEFAULT_DENY` before this lands, it needs a name in the dict and
in the stable-names test, which fails until it has one.

## Rollback

`git revert` the implementation commit. The flat `DEFAULT_*` lists and
`*_replace` behave exactly as before. A config that already uses
`*_remove` then gets an unknown key in `doctor` and the rule stays active,
which fails closed. p620 is only affected if the user has applied the
migration. If they have, either revert that snippet to the old replace list
or accept that ssh is denied until the change is re-landed.
