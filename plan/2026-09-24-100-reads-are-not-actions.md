---
status: approved
issue: 100
spec: spec/2026-09-24-100-reads-are-not-actions.md
---

# Plan: A read-only lookup is not an action, so the confirm gate must not hold it

Closes #100. Branch `fix/100-reads-are-not-actions`, rebased on main
`35ef252`, which includes #94. Main has 940 tests passing. Every `file:line`
below was checked against `35ef252`. The spec cited `72f9ab6`. The lines in
`tools.py` and `config.py` are unchanged since then. The lines in
`claude_backend.py` moved by about 17 because #94 added `BUILTIN_TOOLS`. This
plan uses the new numbers.

This file is enough on its own to implement the change. You do not need to
open the intent or the spec.

## Approved decisions, carried over from the spec

1. **Our read-only tools skip confirm only. Deny still applies.**
   `Policy.check(description, *, read=False)` runs every deny pattern exactly
   as now. When `read` is true, it returns before the confirm loop. Both the
   default deny rules and the user's own rules still refuse a read. The
   spec rejected skipping deny, including skipping only the *default* deny
   rules. Telling defaults apart from user rules breaks when a user copies a
   default into their own config.
2. **Claude Code's `Read` / `WebSearch` get the same answer through the same
   flag.** `_decide` calls `self.executor.policy.check(description,
   read=tool in DRY_RUN_READS)`. Deny still applies, which keeps the promise
   in the comment at `claude_backend.py:70-72` that a deny rule aimed at a
   path still catches a read of it. `DRY_RUN_READS` is
   `{"Read", "WebSearch", "ToolSearch"}` (`claude_backend.py:102`), so
   `ToolSearch` gets the flag too. That set was the same at the spec's base
   `72f9ab6`, so this adds no new scope.
3. **Default deny rules for secret paths are in scope.** Twelve patterns are
   appended to `DEFAULT_DENY` (`config.py:132-154`) under the comment
   `# Secret paths (#100)`. They go into `DEFAULT_DENY` itself, not into a
   list only reads can see, so they also refuse `cat /etc/shadow` through
   `Bash`/`run_shell` and a `Write` into `~/.ssh/authorized_keys`.
   `LIST_UNION_KEYS` (`config.py:199-203`) unions them into existing
   configs. A user who sets `deny_patterns_replace` opts out of them along
   with every other default. Each pattern is matched with `re.IGNORECASE`
   against the whole description. For `Read`, the description is
   `read <path>` (`claude_backend.py:216-219`). The full list, as Python raw
   strings:

   ```python
   # Secret paths (#100). A heuristic, like DEFAULT_SENSITIVE: a symlink or an
   # unlisted name gets past it. Paths, not words, so a lookup for "shadow"
   # or "secret" still runs.
   r"/etc/g?shadow\b",                                   # password hashes, shadow- backup
   r"/\.ssh(/|\b)",                                      # keys, authorized_keys, known_hosts
   r"/\.gnupg(/|\b)",                                    # private-keys-v1.d, trustdb
   r"/run/(agenix|secrets)(\.d)?(/|\b)",                 # agenix / sops-nix; not /run/user
   r"""(^|[\s/"'=])[\w-]*\.env(\.local|\.production|\.development)?(?=$|[\s"';|&)])""",
                                                         # .env, secrets.env, .env.local;
                                                         # not .env.example, .envrc,
                                                         # environment.py, process.env.X
   r"\bid_(rsa|ecdsa|ed25519|dsa)\b(?!\.pub)",           # SSH private keys outside ~/.ssh
   r"/\.(netrc|git-credentials|pgpass)\b",               # plaintext login stores
   r"/\.aws/credentials\b",                              # cloud keys
   r"/\.config/gh/hosts\.yml\b",                         # GitHub CLI token
   r"/\.claude/\.credentials\.json\b",                   # Claude Code's own login
   r"/\.password-store(/|\b)",                           # pass store (names are the inventory)
   r"/\.local/share/keyrings(/|\b)",                     # GNOME keyring files
   ```

   Left out on purpose: `*.age` (ciphertext), `*.pem` (mostly public
   certificates) and a bare `secret` word (it would refuse
   `~/secrets/README.md`).
   I re-checked all twelve on `35ef252` with a scratch script, not in the
   repo. All 14 secret paths in step 6 and both `.ssh` paths match. None of
   the 7 ordinary paths match, and neither do `id_rsa.pub`, `/tmp/notes` or
   `/tmp/x`, the paths the current tests read.
4. **`READ_ONLY_TOOLS` is the gate's list, with the same eight members, now a
   `frozenset`.** The members are `hypr_query`, `read_screen`,
   `omarchy_help`, `system_query`, `read_terminal`, `list_terminals`,
   `screenshot` and `find_app` (`tools.py:57-58`). `read_notifications` and
   `clipboard` stay out. It is frozen because it now gates confirm as well as
   dry-run (`tools.py:1752`, `:1779`). The flag is `name in READ_ONLY_TOOLS`,
   where `name` is the tool resolved by the handler lookup at
   `tools.py:1722`. It never comes from the description.
5. **#76's release turn lets the same reads through, using one helper,
   `_is_read(tool)`.** It is true when `tool in DRY_RUN_READS`, or when
   `tool` is `mcp__omarchy__<name>` with `<name> in READ_ONLY_TOOLS`. The
   prefix is required, so a bare `screenshot` is not a read. The release
   branch (`claude_backend.py:378`) changes from `tool not in DRY_RUN_READS`
   to `not _is_read(tool)`. A read in a release turn then takes the ordinary
   path. Our own reads are allowed at `:389` and gated by the Executor as
   reads. `Read` goes through the deny rules at `:399`. The approval is still
   spent only by `_same_call`.
6. **`read_terminal` content is out of scope.** It is issue #101 (OPEN):
   "read_terminal has no sensitive-content guard". Removing confirm from
   `read_terminal` loses no protection, because the gate only ever saw
   `read terminal <target>` and never the text in the pane.
7. **Nothing else moves.** That covers `describe`, `describe_tool`,
   `_sensitive_kind` / `DEFAULT_SENSITIVE`, `_same_call`, `run_pending`, the
   router, the dry-run check (`claude_backend.py:439`), the members of
   `DRY_RUN_READS`, and the full check inside `compose_windows`
   (`tools.py:3291`).

### How #94 on main affects this plan

#94 offers the voice brain only `BUILTIN_TOOLS = ("Read", "ToolSearch")`
(`claude_backend.py:115`). `WebSearch` is no longer passed to `--tools`, but
it is still in `DRY_RUN_READS`. **The plan keeps the WebSearch handling
anyway**, for three reasons:

- Decision 2 is written as `read=tool in DRY_RUN_READS`. It names no tool, so
  keeping `WebSearch` costs no code. Removing it would mean changing the
  members of `DRY_RUN_READS`, which decision 7 forbids and #94 left alone on
  purpose.
- `_decide` gates whatever the CLI actually calls. `verify-gate` case C
  already widens `--tools` for one instance (`extra_tools`), and the spec's
  demonstration table has `WebSearch` rows.
- The Executor paths (`planner.py:199`, `realtime.py:1284`,
  `mcp_server.py:169`, `local_engine.py:499`) and `omarchy-voice say`
  (`cli.py:115`, which runs `brain_cls.think` on whichever backend is
  chosen) all reach `Policy.check`. Steps 1-2 cover every one of them with a
  single change.

The `WebSearch` test in step 7 therefore tests `_decide` directly, not
anything the brain is offered. #94 also makes `Read` the brain's only path
reader. That makes decision 3 the fix for #94's recorded residual, which is
that `Read` can read `/etc/shadow` or `secrets.env`.

## Steps

0. **Baseline**: run `git log -1 origin/main` and
   `nix develop -c pytest tests -q` → verify by: the log shows `35ef252`,
   the tests report **940 passed**, and `git status` is clean. Record the
   count and any failures. Every later step is compared against them.

1. `src/omarchy_voice/tools.py:57-58`: change `READ_ONLY_TOOLS = {...}` to
   `frozenset({...})` with the same eight members. Update the comment above
   it (`:56`): "Read-only tools: they still run under --dry-run, and the
   confirm gate never holds them (#100); deny rules still apply." → verify by:
   `nix develop -c pytest tests -q` reports the step 0 count, and
   `python -c "from omarchy_voice.tools import READ_ONLY_TOOLS as R;
   print(type(R).__name__, sorted(R))"` prints `frozenset` and the eight
   names.

2. `src/omarchy_voice/tools.py:200-205`: change
   `Policy.check(self, description)` to
   `check(self, description: str, *, read: bool = False) -> None`. Keep the
   deny loop first and unchanged, then add
   `if read: return  # a lookup is not the thing it looks up (#100)`, then
   the confirm loop, unchanged. At `tools.py:1734`, change
   `self.policy.check(description)` to
   `self.policy.check(description, read=name in READ_ONLY_TOOLS)`. Leave
   `:3291` (`compose_windows`) as it is. → verify by: the step 5 tests go
   green, and the rest of the suite reports the step 0 count.

3. `src/omarchy_voice/claude_backend.py`:
   - `:38`: add `READ_ONLY_TOOLS` to the `from .tools import` line.
   - After `DRY_RUN_READS` (`:102`): add
     ```python
     def _is_read(tool: str) -> bool:
         """A Claude Code built-in reader, or one of our read-only tools (#100)."""
         return tool in DRY_RUN_READS or (
             tool.startswith("mcp__omarchy__")
             and tool.removeprefix("mcp__omarchy__") in READ_ONLY_TOOLS)
     ```
   - `:378`: change `if tool not in DRY_RUN_READS:` to
     `if not _is_read(tool):`. The comment at `:387` stays as it is.
   - `:399`: change `self.executor.policy.check(description)` to
     `self.executor.policy.check(description, read=tool in DRY_RUN_READS)`.
     This is `DRY_RUN_READS` and not `_is_read`, because `mcp__omarchy__*`
     has already returned at `:389`.
   - Comment at `:70-72`: add one sentence: "Deny rules still apply to
     reads; confirm rules do not (#100)."

   → verify by: the step 7 tests go green, and the rest of the suite
   reports the step 0 count.

4. `src/omarchy_voice/config.py:153`: after the last `nix-env` rule and
   before the closing `]` at `:154`, append the twelve patterns from
   decision 3 exactly as written there, comment included. → verify by: the
   step 6 tests go green, and `tests/test_config.py` passes unchanged (it
   checks `DEFAULT_DENY[0]` and the union, not the length).

5. `tests/test_policy.py`: add a class `ReadsAreNotActions`. Its executor is
   `Executor(Config(dry_run=False))`, with each read handler replaced by a
   fake through
   `mock.patch.object(Executor, "_tool_<name>", lambda self, **a: Result(True, "fake"))`.
   No subprocess and no desktop. Tests, each marked by whether it must
   **fail on main**:
   - (fail on main) `test_every_held_lookup_now_runs`: one subTest per row
     from the intent and spec. `omarchy_help` × `reboot`, `shutdown`,
     `poweroff`, `suspend`, `hibernate`, `omarchy update`,
     `omarchy pkg install`, `close all windows`, `nixos-rebuild`;
     `find_app` × `shutdown`, `reboot`; `read_screen` `{"query": "reboot"}`;
     `read_terminal` `{"target": "nixos-rebuild"}`. Each has `result.ok`,
     output `"fake"`, and `executor.pending is None`.
   - (fail on main) `test_a_lookup_then_the_real_reboot_holds_the_reboot`:
     on one executor, `omarchy_help {"query": "reboot"}` runs, then
     `omarchy_cli {"command": "system reboot"}` is held.
     `executor.pending == ("omarchy_cli", {"command": "system reboot"})`, and
     the output does not contain "another action is already waiting".
   - (fail on main) `test_dry_run_lookup_reaches_the_handler`: with
     `Config(dry_run=True)`, `omarchy_help {"query": "reboot"}` returns
     `"fake"`, and `pending is None`.
   - (passes on main, a guard) `test_default_deny_still_refuses_reads`:
     `omarchy_help sudo` and `read_terminal {"target": "ssh"}` are refused,
     and the fake is never called.
   - (passes on main, a guard) `test_user_word_rule_still_refuses_reads`:
     `deny_patterns=[*DEFAULT_DENY, r"\bpayroll\b"]`. Both `omarchy_help payroll`
     and `read_terminal payroll` are refused.
   - (fail on main) `test_user_confirm_rule_on_a_read_runs_on_an_action_holds`:
     `confirm_patterns=[*DEFAULT_CONFIRM, r"\bpayroll\b"]`.
     `omarchy_help payroll` runs, and `omarchy_cli payroll` is held.
   - (fail on main) `test_policy_check_read_still_denies`:
     `Policy(Config()).check("read /etc/shadow", read=True)` raises `Denied`,
     and `check("omarchy help reboot", read=True)` returns `None`. The
     `read=` keyword does not exist on main, so this is a `TypeError` there.
   - (fail on main) `test_read_only_tools_is_frozen_and_disjoint`:
     `isinstance(READ_ONLY_TOOLS, frozenset)`, and
     `not READ_ONLY_TOOLS & INPUT_TOOLS`.
   - (passes on main, a guard) `test_an_action_is_still_held`:
     `omarchy_cli "system reboot"` and `run_shell "systemctl reboot"` are
     held.

   → verify by: run
   `nix develop -c pytest tests/test_policy.py -q -k ReadsAreNotActions`,
   first with steps 1-4 stashed out and then with them applied. See the
   Tests section. The tests marked "fail on main" fail, and the guards pass.

6. `tests/test_policy.py`: add a class `SecretPaths`, using `Policy(Config())`
   only:
   - (fail on main) `test_secret_reads_are_refused`: one subTest each for
     `read <p>` over the 14 paths `/etc/shadow`, `/etc/gshadow`,
     `/home/u/.config/omarchy-voice/secrets.env`, `/home/u/proj/.env`,
     `/home/u/proj/.env.local`, `/home/u/keys/id_rsa`,
     `/home/u/.gnupg/private-keys-v1.d/AB12.key`,
     `/run/agenix/github-token`, `/run/secrets/api`, `/home/u/.netrc`,
     `/home/u/.aws/credentials`, `/home/u/.config/gh/hosts.yml`,
     `/home/u/.claude/.credentials.json` and
     `/home/u/.password-store/bank.gpg`. Each raises `Denied`, with
     `read=True` and with `read=False`.
   - (passes on main, a guard) `test_ssh_reads_are_refused`:
     `/home/u/.ssh/id_ed25519` and `/home/u/.ssh/config` raise `Denied`.
   - (passes on main, a guard) `test_ordinary_reads_pass`: the 7 paths
     `/home/u/proj/.env.example`, `/home/u/proj/.envrc`,
     `/home/u/proj/src/environment.py`, `/etc/hosts`,
     `/home/u/proj/README.md`, `/home/u/secrets/README.md` and
     `/run/user/1000/omarchy-voice/state.json`, plus
     `/home/u/keys/id_rsa.pub`, do not raise with `read=True`.
   - (fail on main) `test_secret_paths_also_stop_actions`:
     `run_shell "cat /etc/shadow"` is refused through
     `Executor(Config(dry_run=True))`, and so is `cp .env.example .env`.
     The second one records the known risk; see Risks.
   - (fail on main) `test_user_path_rule_still_refuses_a_read`:
     `deny_patterns=[*DEFAULT_DENY, "/home/u/private/"]` refuses
     `read /home/u/private/diary.md` with `read=True`. It fails on main only
     because `read=` does not exist there. The rule itself worked before.

   → verify by: run `pytest -k SecretPaths` before and after, as in step 5.

7. `tests/test_claude_backend.py`: use the existing `brain()`, `gate()` and
   `release()` helpers (`:30`, `:57`, `:61`), which call `_decide` through
   the hook. Add:
   - In `HookTests` (`:419`):
     - (fail on main) `Read /home/u/notes/reboot-checklist.md` → `allow`.
     - (fail on main) `WebSearch {"query": "how do I reboot hyprland"}` →
       `allow`. This checks `_decide` itself; #94 no longer offers
       `WebSearch`.
     - (passes on main, a guard) `WebSearch "sudo password prompt"` →
       `deny`.
     - (fail on main) every one of the 14 secret paths from step 6 as
       `Read` → `deny`, with "Refused" in the message.
     - (passes on main, a guard) a user path rule `/home/u/private/` denies
       `Read /home/u/private/diary.md`.
     - (passes on main, a guard) `Bash systemctl reboot` →
       `deny` with `subject.pending == "systemctl reboot"`, which means it
       is held.
   - In `ReleaseTurnGateTests` (`:167`), with the approved call
     `mcp__omarchy__omarchy_cli {"command": "system reboot"}`:
     - (fail on main) `mcp__omarchy__omarchy_help {"query": "reboot"}` →
       `allow`.
     - (passes on main, a guard) `mcp__omarchy__launch_app` and `Bash ls` →
       `deny`.
     - (fail on main) `Read /etc/shadow` → `deny`.
     - (passes on main, a guard) the approved call → `allow`, once.
     - `test_nothing_else_runs_in_a_release_turn` (`:188`) stays unchanged
       and still passes, because `notify` is not a read.
   - (fail on main) `test_is_read`: `_is_read("screenshot")` and
     `_is_read("mcp__omarchy__notify")` are false.
     `_is_read("mcp__omarchy__screenshot")`, `_is_read("Read")` and
     `_is_read("ToolSearch")` are true. On main `_is_read` does not exist,
     so the import fails.

   → verify by: run `pytest tests/test_claude_backend.py -q` before and
   after, as in step 5.

8. `README.md`, the Safety section (`:815-872`):
   - Add a bullet after "Held for confirmation" (`:822-823`): "**Never
     held**: lookups (`omarchy_help`, `find_app`, `read_screen`,
     `read_terminal`, `hypr_query`, `system_query`, `screenshot`,
     `list_terminals`, and Claude Code's `Read`). Asking *about* a reboot is
     not a reboot. Deny rules still apply to them, so to stop a read you
     write a deny rule, not a confirm rule."
   - Extend "Denied outright" (`:820-821`) with: "and reads or writes of
     well-known secret files (`/etc/shadow`, `~/.ssh`, `~/.gnupg`, `.env`,
     `/run/agenix`, `/run/secrets`, cloud and CLI credentials)."

   → verify by: reading the rendered diff. There are no other README
   examples to change. On `35ef252`, `rg '\.env\b|/etc/g?shadow|id_rsa|\.netrc|\.gnupg' README.md tests/`
   finds only `options.env` at `test_claude_backend.py:615`. That is a
   Python attribute and never a policy description.

9. **Full suite and flake check**: run `nix develop -c pytest tests -q`, then
   `nix flake check --no-write-lock-file` → verify by: the count is 940 plus
   the new tests, with no failure beyond those recorded in step 0, and the
   flake check exits 0.

10. **Mutation checks**. Make each edit by hand, run the named test file,
    confirm that at least one test goes red, then `git checkout -- <file>`.
    Record the red test's name in the PR.
    - a. `tools.py:1734`: drop `read=...`. Expect red in `ReadsAreNotActions`.
    - b. `claude_backend.py:399`: drop `read=...`. Expect red on
      `Read …reboot-checklist.md` / `WebSearch reboot`.
    - c. `claude_backend.py:378`: revert to `tool not in DRY_RUN_READS`.
      Expect red on the release-turn `omarchy_help` test.
    - d. `config.py`: delete any one of the twelve secret patterns, repeated
      for each. Expect red in `SecretPaths.test_secret_reads_are_refused`
      and in the `Read` secret test. The `.ssh` rule is the exception: with
      it deleted, `.ssh` reads are still refused by `\bssh\b`, and nothing
      goes red. Kill it with a test on
      `deny_patterns=[r"/\.ssh(/|\b)"]` alone, taken from `DEFAULT_DENY`
      by index. Add that test to step 6 if this mutation survives.
    - e. `tools.py` `Policy.check`: move `if read: return` above the deny
      loop. Expect red on `test_policy_check_read_still_denies`,
      `test_default_deny_still_refuses_reads` and the secret tests.
    - f. `_is_read`: drop the `mcp__omarchy__` prefix requirement, so a
      bare name counts. Expect red on `test_is_read`.

11. **Commit and PR**: one `fix(policy): …` commit (#100). The PR links
    `intent/`, `spec/` and `plan/`, and lists the red test for each mutation
    in step 10. No live desktop run is needed. `verify-gate` needs no
    change: case A's `grocery-list` rule and case B's
    `<tmp>/shared/pantry.txt` keep their verdicts, because neither path
    matches a new rule.

## Tests

| Command | Expected |
|---------|----------|
| `nix develop -c pytest tests -q` (step 0) | 940 passed |
| `git stash push -m plan100-mut -- src/` then `nix develop -c pytest tests/test_policy.py tests/test_claude_backend.py -q`, then `git stash apply <sha>` (use the unique-tag procedure) | every test marked "fail on main" in steps 5-7 fails; the guards pass |
| `nix develop -c pytest tests/test_policy.py tests/test_claude_backend.py -q` (after) | all pass |
| `nix develop -c pytest tests -q` (after) | 940 plus the new tests, no new failures |
| mutations a-f (step 10) | each turns at least one named test red |
| `nix flake check --no-write-lock-file` | exit 0 |

All of these run on fakes: patched `_tool_*` handlers, `Policy` on strings,
and `ClaudeBrain._decide` through `gate()`. Nothing touches the live desktop.

## Risks

- **The new deny rules can refuse shell actions.** `cp .env.example .env`
  and `source .env && make` through `run_shell`/`Bash`, and
  `type_text "cat .env"`, are now refused. This is intended, since it is the
  same disclosure, and step 6 pins `cp .env.example .env` as a test. Lookups
  that name the file are refused too, such as `omarchy_help ".env"` and
  `WebSearch "what is a .env file"`. That fails in the safe direction, like
  `omarchy_help sudo` does today. The only README change is step 8. No
  existing test uses `.env`, `shadow`, `id_rsa`, `.netrc`, `.gnupg` or
  `/run/secrets`, so no existing test changes. The existing reads use
  `/tmp/x`, `/tmp/notes` and `/home/u/docs/grocery-list.txt`, and none of
  them match.
- **A user's confirm rule aimed at a read stops holding it.** To stop a
  read, the user must write a deny rule. Step 8 documents this, and step 5
  tests it.
- **The path rules are a heuristic.** They do not catch a symlink to a
  secret, a secret under a name not on the list, or a path spelled
  indirectly in `Bash` (`cat /etc/sha""dow`). #94 removed `Bash` from the
  brain, so `Read` with a literal path is the realistic case.
- **The release turn now admits our reads.** A read there cannot be held.
  It still goes through the Executor's deny check, and only `_same_call`
  spends the approval.
- **Hosts**: every host that runs omarchy-voice. It matters most on p620,
  which runs the claude-code backend and `Read`.

## Rollback

The rollback is `git revert <fix commit>`. It restores the mutable set, the
confirm gate on reads and the old `DEFAULT_DENY`. It needs no data or config
migration. The new rules live in code, not in user config: `LIST_UNION_KEYS`
unions them at load time, so reverting removes them everywhere. For a
partial rollback that keeps the secret paths but brings back holding reads,
revert only the `read=` arguments at `tools.py:1734` and
`claude_backend.py:399` and the predicate at `:378`, and keep the
`config.py` hunk. A user who wants the secret rules off without a revert can
set `deny_patterns_replace = true`, which drops every default.
