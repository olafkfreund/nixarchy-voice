---
status: draft
issue: 121
spec: spec/2026-09-24-121-realtime-engine-future.md
---

# Plan: remove the OpenAI realtime engine, and say so everywhere

Closes #121. Base: `main` `b73a3f4` (v1.0.0 plus #114): 1126 tests, of which
96 are in `tests/test_realtime.py` and 2 in `tests/test_realtime_wire.py`.
Every `file:line` below was checked against `b73a3f4`. #121 lands third
(see Landing order), so step 1 rebases and re-checks them.

## Approved decisions, carried over from the spec

1. **The engine is removed, not kept or frozen.** Nobody runs it (last
   `engine=realtime` start 2026-09-12; p620 and razer both run local and both
   set `ears.barge_in = false`, so native barge-in was never used). Every
   consent fix had to be written twice (#86/#113). The cost accepted: the
   1-2 s realtime path is gone, and the local engine's 1.5-7.6 s is now the
   target for #79 and #80. The README says so plainly.
2. **No "safety fix" rule is needed.** Later issues (#77, #114, ...) say
   "realtime: removed in #121" and stop.
3. **`engine = "openai"` is refused, with no fallback.** `cmd_run`
   (`cli.py:188-201`) starts nothing. It prints `the OpenAI realtime engine
   was removed in 2.0.0 (#121). Delete engine = "openai" under [realtime], or
   set it to "local". The last release with it is v1.0.0.`, sets the bar
   state `unconfigured` with that sentence, sends one notification `voice has
   no backend: run omarchy-voice doctor`, and returns 0. This copies the
   local engine's "not set up" path (`local_engine.py:962-975`). Exit 0,
   because `Restart=on-failure` (`nix/hm-module.nix:305`) with
   `StartLimitIntervalSec = 60`/`StartLimitBurst = 3` (`:269-270`) would
   otherwise restart three times and look like a crash. Falling back to local
   was rejected: it changes the vendor and the voice without asking.
4. **Any other non-`"local"` value (a typo such as `"locl"`) still runs
   local**, as `WiringTests.test_anything_unrecognised_runs_the_local_chain`
   (`tests/test_local_engine.py:1837`) pins today. `doctor` flags any value
   other than `"local"` or `""`.
5. **razer and p620 are unaffected.** Neither sets `engine`. Their comments
   that mention the Realtime API live in the NixOS config repo and are a
   follow-up for the owner (read-only here).
6. **Audio-native hearing is not on this repo's roadmap.** If it returns, it
   returns as a new issue built on the local engine's consent model (#86).
   `realtime.py` stays in git history and the v1.0.0 tag.
7. **Deleted:** `src/omarchy_voice/realtime.py` (1,708 lines),
   `tests/test_realtime.py` (1,197 lines, 96 tests),
   `tests/test_realtime_wire.py` (171 lines, 2 tests),
   `tools/bench_realtime.py` (148 lines).
8. **`websockets` is dropped** from `pyproject.toml:12`,
   `nix/package.nix:57` and both `withPackages` lists at `flake.nix:61` and
   `:121`. Only `realtime.py`, the wire test and a string at `cli.py:464`
   use it.
9. **Surviving helpers move verbatim to their only remaining user.**
   `ECHO_TAIL_SECONDS` (`realtime.py:52`), `WATCH_POLL_SECONDS` (`:142`),
   `watch_headline` (`:148`), `watch_message` (`:157`) and
   `_run_until_done` (`:1678`) go to `local_engine.py`, replacing the import
   at `local_engine.py:43-44`. `_run_until_done`'s annotation becomes
   "anything with `async run() -> int`"; its body is unchanged.
   `default_source`, `_pactl`, `default_sink` (`:1551-1574`) and `echo_risk`
   (`:1576`) go to `listen_local.py`, which already owns the microphone and is
   already imported by `cli.py`. No stub `realtime.py`, and no new `audio.py`.
10. **`cmd_run`** loses the `realtime_mod` import (`cli.py:16`), refuses
    `"openai"` (decision 3) and hands everything else to `local_engine.run`.
    The field `realtime_engine` (`config.py:407`) stays so the value can be
    read and refused.
11. **Config fields removed** (realtime-only, checked by grep):
    `realtime_model`, `realtime_voice`, `realtime_turn_detection`,
    `realtime_sample_rate` (`config.py:408-411`), `realtime_transcribe_model`
    (`:424`), `history_items` (`:385`), `silence_gate` (`:360`) and
    `silence_hold_seconds` (`:369`). `idle_stop_seconds` (`:378`) stays.
    Each removed key goes into `RETIRED_KEYS` (`config.py:186`) under its
    loaded name, with the text "the OpenAI realtime engine was removed in
    2.0.0 (#121)". The comment block at `config.py:387-406` is cut to what is
    still true. `SAFETY_ID_FILE` (`config.py:52`) goes; a file on disk is left
    alone.
12. **doctor** (`cli.py:359-560`): the `openai` section keeps the key line
    and the planner-model line, and loses `realtime model ..., voice ...`
    (`:367`) and the safety-id lines (`:373-374`). The `engine` section always
    prints the local block (`:431-444`). It adds `✗ engine = "openai": the
    OpenAI realtime engine was removed in 2.0.0 (#121). Set it to "local" or
    delete it.` for `"openai"`, and `✗ engine = <value> is not an engine;
    running local` for any other value that is not `"local"` or `""`. The
    else-branch (`:445-450`) goes. The `ears` section loses the
    `realtime_mod.check_ready` lines (`:459-464`), `OpenAI Realtime (speech to
    speech)` and `room audio is streamed to OpenAI` (`:465-469`), the silence
    gate (`:470-476`) and `transcribe_model` (`:483-486`). The wake word,
    `ask`, input/output and `barge_in` lines stay and read from
    `listen_local` (`:504-508`).
13. **README**: "How it works" (`README:52-89`) drops the `engine = "openai"`
    diagram (`:67-89`) and says `omarchy-voice say` is the typed path.
    "Speech without OpenAI" (`:381-433`) becomes the description of the only
    engine, keeping the measured latencies and the plain sentence that local
    is slower than realtime's 1-2 s. The paragraph at `:429-432` becomes: "The
    OpenAI realtime engine was removed in 2.0.0 (#121): unused since
    2026-09-12, and every consent fix had to be written for it twice. v1.0.0
    is the last release with it." The false `[ears] silence_hold_seconds`
    claim (`:419`) goes. "Speed" (`:653-677`) and "What it costs"
    (`:679-735`) are cut to what applies to `say` and the local chain.
    "Speakers, and her hearing herself" (`:737`) and "Credit and what this
    fork changed" (`:1070`) keep their history, reworded where they call
    realtime current. Also reworded or cut: the intro (`:6-7`), the install
    example (`:106`), `:166`, `:199-205`, `:246-252`, `:373-376`,
    Requirements (`:445-447`), Safety (`:884`), Layout (`:1056`, and `:1059`
    lists `realtime.py`). `docs/omarchy-voice.html` gets the same treatment.
    Afterwards every `realtime`/`OpenAI Realtime` mention in the README is
    historical.
14. **`share/config.example.toml`** (installed by `nix/package.nix:125-126`,
    pointed at by `hm-module.nix:47`): `[realtime]` (`:140-165`) is cut to
    `engine = "local"` and a one-line removal note. The `[ears]` keys
    `silence_gate`/`silence_hold_seconds` (`:92-99`, plus `:101`'s mention)
    and `history_items` (`:115-116`) go with their comments. `:6` and `:19`
    stop referring to a realtime session.
15. **HM module**: the example `realtime.voice = "marin"`
    (`nix/hm-module.nix:40`, and `README:106`) becomes
    `ears.wake_word = "oma"`. `apiKeyFile`/`apiKeyEnv` stay (`say` needs the
    key). No option is added or removed, so p620 and razer evaluate
    unchanged.
16. **Comments and `--help` that call realtime current are reworded where
    they are now false**, and kept where they record a measured reason:
    `persona.py:3`, `mcp_server.py:10` and `:52-55`, `session.py:4`,
    `feedback.py:79` and `:130`, `listen_local.py:33` and `:214-216`,
    `planner.py:3-6`, `claude_backend.py:19`, and in `local_engine.py` `:3-11`,
    `:53-70` (`LOCAL_PERSONA` must no longer point at `REALTIME_PERSONA`;
    #77 depends on this), `:143`, `:164`, `:610`, `:651`, `:956`. `--help`:
    `build_parser`'s "with OpenAI Realtime as the router" (`cli.py:625`) and
    `ask`'s "no websocket and no realtime session" (`cli.py:648`).
17. **Version 2.0.0**, because a supported config value stops working:
    `pyproject.toml:7`, `src/omarchy_voice/__init__.py:3`,
    `nix/package.nix:51`, `plugin/olafkfreund.voice-orb/manifest.json:5`,
    `plugin/olafkfreund.voice-indicator/manifest.json:5`.
18. **Out of scope:** the `say` planner and its `gpt-4.1` default
    (`config.py:264`); p620/razer comments; `plugin/` code (no realtime
    references); `HANDOFF.md`, `tools/bench_local.py:4-5,:53`,
    `tools/live_check.py:115` (historical, left alone).

### Decided in this plan (not spelled out by the spec)

- **The refusal is the first thing `cmd_run` does**, before `consent_notice`
  and `policy_notice` (`cli.py:195-196`). `consent_notice` can run
  `notify-send` and write its marker (`cli.py:305-320`). A daemon that will
  not start should neither spend the one-time notice nor send a second
  notification. Test 1 asserts exactly one notification.
- **Doctor's typo flag gets its own test** (test 6 below). The spec's
  decision 3 requires the flag, but its test list has no test for it, and on
  `main` doctor prints nothing for `"locl"`. This adds one test to the
  spec's arithmetic: **1039, not 1038** (see Tests).
- **`DoctorTests` goes in `tests/test_config.py`.** No doctor test exists
  today (`grep -rn cmd_doctor tests` is empty), and doctor is where config
  is reported. This keeps it out of `tests/test_local_engine.py`, which #79
  and #80 edit.
- **Two existing tests assert `silence_hold_seconds`** and are not in the
  spec: `tests/test_config.py:173-176`
  (`test_the_local_hold_is_its_own_and_the_realtime_one_is_untouched`) and
  `tests/test_local_engine.py:1486` (in
  `test_a_turn_ends_on_the_local_hold_not_the_realtime_one`). They are
  edited, not deleted: the `silence_hold_seconds` assertion goes, the
  `end_of_speech_seconds` assertion stays, and each is renamed to drop
  "realtime". The count does not change.
- **One commit per step from step 2 on**, each green, so the branch bisects.

## Landing order

**#121 is third: #111, #110, #121, #79, #80.**

- Before #121: #111 (`fix/111-manifest-key-cost`) edits `capabilities.py`,
  `tests/test_cache.py` and doctor's manifest section (`cli.py:546`), next
  to but not inside the doctor sections this plan rewrites. #110
  (`fix/110-one-rule-every-launch`) edits `tools.py`, `tests/test_compose.py`
  and README. Step 1 rebases onto both and re-checks every line number in
  `cli.py` and `README.md`.
- After #121: **#79 and #80 rebase onto it.** Overlaps they must resolve:
  - `local_engine.py`: gains `ECHO_TAIL_SECONDS`, `WATCH_POLL_SECONDS`,
    `watch_headline`, `watch_message`, `_run_until_done`, and its comments
    change. #79's intent cites `ECHO_TAIL_SECONDS` at `realtime.py:52`/`:771`;
    after #121 it is in `local_engine.py` and has one user.
  - `config.py`: `silence_gate`, `silence_hold_seconds`, `history_items` and
    the `realtime_*` fields are gone, and `RETIRED_KEYS` grows. #80 edits
    end-of-turn config.
  - `listen_local.py`: gains the PipeWire helpers and `echo_risk`. #80 edits
    it.
  - `cli.py`: `cmd_run` and doctor's engine/ears sections are rewritten.
  - `README.md`: "How it works", "Speech without OpenAI", "Speed" and "What
    it costs" are rewritten; #79/#80 update latency figures there.
  - `pyproject.toml` and `nix/package.nix`: `websockets` gone, version
    2.0.0. Any dependency #80 adds goes into the shorter list.

## Steps

0. **Baseline.** `gh issue view 121` shows OPEN, and `git branch
   --show-current` is `docs/121-realtime-engine-future` (or the
   implementation branch cut from it). With
   `export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`, run
   `nix develop -c python3 -m pytest -q` and
   `nix develop -c python3 -m unittest discover -s tests`
   → verify by both passing with **1126** tests at `b73a3f4`, and
   `nix develop -c python3 -m pytest --collect-only -q tests/test_realtime.py
   tests/test_realtime_wire.py | tail -1` giving 98. Record any existing
   failure before continuing.
1. **Precondition: #111 and #110 have merged.** `gh pr list --state merged
   --search "111 in:title"` (and 110), and `git log origin/main --oneline |
   grep -E '#11[01]'`. If either has not merged, **stop and report. Do not
   implement.** Otherwise `git rebase origin/main`, repeat step 0 (the
   baseline becomes 1126 plus whatever they added: call it **B**), and
   re-check every `file:line` here → verify by a clean rebase, a green
   baseline, and corrected line numbers committed to this file together with
   the first code commit.
2. **`cli.py` `cmd_run`: the refusal, with its tests.** In
   `tests/test_local_engine.py` `WiringTests` (`:1826`), replace
   `test_openai_is_still_reachable` (`:1832-1835`) with **test 1**
   `test_openai_is_refused_not_run`: with `local_engine.run`, `consent_notice`
   and `policy_notice` patched to fail if called, and `Feedback.state`/
   `Feedback.notify` recorded, `cmd_run(None, Config(realtime_engine="openai"))`
   returns 0, sets state `unconfigured` with a message containing `removed`
   and `v1.0.0`, and notifies exactly once with a body containing `doctor`.
   Delete `NoBackendNotice.test_the_realtime_engine_says_the_same_thing`
   (`:1936-1942`). Keep `test_anything_unrecognised_runs_the_local_chain`
   unchanged. Run test 1 → **it FAILS** (on this tree it calls
   `realtime_mod.run`). Then change `cmd_run` (`:188-201`): first line
   refuses `"openai"` per decision 3 (`Feedback` from `.feedback`, as
   `local_engine.run` does); rewrite the docstring; drop the
   `realtime_mod.run` call. The `realtime_mod` import stays for now (doctor
   still uses it)
   → verify by test 1 and `WiringTests` passing, and the full suite green at
   B - 1 (one test replaced, one deleted).
3. **Move the helpers, and every test import of them.**
   - `local_engine.py`: replace the import at `:43-44` with the five helpers
     copied verbatim from `realtime.py` (decision 9). `realtime.py` is not
     edited; its own copies go with it in step 5.
   - `listen_local.py`: add `default_source`, `_pactl`, `default_sink`,
     `echo_risk` verbatim (with the imports they need).
   - `cli.py:504-508`: `realtime_mod.default_source/default_sink/echo_risk`
     → `listen_local.*`.
   - Tests: `tests/test_terminal.py:429,447` import `watch_message` from
     `local_engine`. `tests/test_local_engine.py:671` imports from
     `local_engine`; `:1818-1822` drops the `realtime` import and the
     `RealtimeSession` assertion. Copy `EchoRiskTests`
     (`tests/test_realtime.py:818`, 6 tests) into `tests/test_listen_local.py`,
     patching `listen_local.default_sink`. Copy
     `test_the_text_is_shared_with_the_local_engine` (`:746`) into
     `tests/test_local_engine.py` `WatchAnnounceTests` (`:1646`), with the
     `job(**over)` helper (`test_realtime.py:682-685`) as a module function,
     against `local_engine.watch_message`/`watch_headline`. Delete the 7
     originals from `tests/test_realtime.py`, so nothing runs twice.
   → verify by `grep -n "realtime" src/omarchy_voice/local_engine.py | grep
   import` printing nothing, and the full suite green at B - 1 (7 moved,
   same count).
4. **`cli.py` doctor, with tests 4, 5 and 6.** Add `class
   DoctorTests(unittest.TestCase)` to `tests/test_config.py`. A helper runs
   `cli.cmd_doctor(None, config)` under `contextlib.redirect_stdout`, with
   everything that reaches outside the process patched: `choose_backend`,
   `chat_ready`, `claude_backend.check_ready`/`cli_path`,
   `local_engine.check_ready`/`voice_chain`, `listen_local.check_ready`/
   `default_source`/`default_sink`, `hypr_events.socket_path`,
   `shutil.which`, and `capabilities.manifest`/`system_versions`/
   `unreadable_sources`/`verify_essentials`, and `HOME`/`XDG_CONFIG_HOME`
   pointed at a temp dir. Tests:
   - **4** `test_local_doctor_never_mentions_streaming_to_openai`:
     `Config()` output contains neither `OpenAI Realtime` nor
     `streamed to OpenAI`.
   - **5** `test_doctor_names_the_removed_engine`:
     `Config(realtime_engine="openai")` output contains `removed in 2.0.0`
     and the local block (`whisper.cpp`).
   - **6** `test_doctor_flags_an_unknown_engine`:
     `Config(realtime_engine="locl")` output contains `'locl'` (or `locl`)
     and `is not an engine`; `Config(realtime_engine="")` output does not.
   Run them → **all three FAIL** (4 on `:465-467`, 5 and 6 because no such
   line exists). Then rewrite doctor per decision 12, and remove the
   `realtime as realtime_mod` import (`cli.py:16`)
   → verify by 4-6 passing, `grep -n realtime_mod src/omarchy_voice/cli.py`
   empty, and the suite green at B + 2.
5. **Delete the engine, with test 2.** Add **test 2**
   `test_the_realtime_module_is_gone` to `WiringTests`: after importing
   `omarchy_voice.cli` and `omarchy_voice.local_engine`,
   `"omarchy_voice.realtime" not in sys.modules`, and
   `importlib.util.find_spec("omarchy_voice.realtime") is None`. Run it →
   **it FAILS** (`find_spec` finds the file). Then `git rm`
   `src/omarchy_voice/realtime.py`, `tests/test_realtime.py`,
   `tests/test_realtime_wire.py`, `tools/bench_realtime.py`
   → verify by test 2 passing,
   `grep -rnE "from \.realtime|import realtime|omarchy_voice\.realtime|realtime_mod" src tests tools`
   printing only test 2's own string, and the suite green at
   B + 3 - (96 - 7) - 2 = **B - 88**.
6. **`config.py`: retire the keys, with test 3.** Add **test 3**
   `ConfigLoadTests.test_realtime_keys_are_retired_not_typos`
   (`tests/test_config.py:15`): a file with `[realtime] voice = "marin"`,
   `model = "x"` and `[ears] silence_gate = true` loads with
   `unknown_keys == []` and `realtime_voice`, `realtime_model`,
   `silence_gate` all in `retired_keys`. Run it → **it FAILS** (live fields,
   `retired_keys` empty). Then remove the eight fields and `SAFETY_ID_FILE`
   (decision 11), add the eight names to `RETIRED_KEYS`, and cut the comment
   block `:387-406`. In the same step edit the two `silence_hold_seconds`
   tests (see "Decided in this plan")
   → verify by test 3 and `test_a_retired_key_*` (`test_config.py:28-37`)
   passing, `grep -rnE "silence_gate|silence_hold_seconds|history_items|realtime_(model|voice|turn_detection|sample_rate|transcribe_model)|SAFETY_ID" src tests`
   showing only `RETIRED_KEYS` and test 3, and the suite green at **B - 87**.
7. **Dependencies.** Remove `websockets` from `pyproject.toml:12`,
   `nix/package.nix:57`, `flake.nix:61` and `:121`
   → verify by `nix flake check --no-write-lock-file` passing and
   `grep -rn websockets pyproject.toml nix flake.nix src` empty.
8. **User-facing text: HM example, example config, README, docs page.**
   `nix/hm-module.nix:40` per decision 15; `share/config.example.toml` per
   decision 14; `README.md` per decision 13 (including `:106`);
   `docs/omarchy-voice.html` likewise
   → verify by the README/doc grep in Tests, and by
   `python3 -c 'import tomllib,sys; tomllib.load(open("share/config.example.toml","rb"))'`
   plus loading it with `config.load` in a scratch check: `unknown_keys` and
   `retired_keys` both empty.
9. **Comments and `--help`.** Reword every site in decision 16
   → verify by `nix develop -c python3 -m omarchy_voice --help` (or the
   installed `omarchy-voice --help`) not containing `Realtime` or
   `websocket`, and by the `src/` grep in Tests.
10. **Version 2.0.0** in the five places of decision 17
    → verify by `grep -rn '"\?1\.0\.0"\?' pyproject.toml src nix plugin`
    empty, `grep -rn 2.0.0` hitting exactly those five lines, and
    `nix build .#omarchy-voice --no-link --print-out-paths` whose
    `bin/omarchy-voice --version` prints `omarchy-voice 2.0.0` (and
    `nix eval --raw .#omarchy-voice.version` gives `2.0.0`).
11. **Mutation checks.** Apply each alone, run the named tests, revert with
    `git checkout -- src/`:
    - `cmd_run` falls through to `local_engine.run` for `"openai"` → test 1
      fails.
    - The refusal moved after `consent_notice`/`policy_notice` → test 1
      fails (it patches both to fail if called).
    - One key removed from `RETIRED_KEYS` → test 3 fails.
    - The old `ears` lines put back in doctor → test 4 fails.
    - The typo branch in doctor removed → test 6 fails.
    - `echo_risk` returns `""` unconditionally → the moved `EchoRiskTests`
      fail.
    - A stub `realtime.py` plus `from .realtime import ECHO_TAIL_SECONDS` in
      `local_engine.py` → test 2 fails.
    → verify by each turning its tests red, and `git status --short` clean
    of `src/` after each revert.
12. **Full suites, flake, greps.** Run everything in Tests
    → verify by every command giving its expected result, with the count at
    **B - 87** (1039 if B is 1126).

## Tests

Tests that must **fail on `main` first** (each is run red in its step before
the change that makes it pass):

| # | Test | File | Fails on main because |
| - | ---- | ---- | --------------------- |
| 1 | `WiringTests.test_openai_is_refused_not_run` | test_local_engine.py | `cmd_run` calls `realtime_mod.run` |
| 2 | `WiringTests.test_the_realtime_module_is_gone` | test_local_engine.py | `find_spec` finds `realtime.py` |
| 3 | `ConfigLoadTests.test_realtime_keys_are_retired_not_typos` | test_config.py | the keys are live fields |
| 4 | `DoctorTests.test_local_doctor_never_mentions_streaming_to_openai` | test_config.py | `cli.py:465-467` prints both |
| 5 | `DoctorTests.test_doctor_names_the_removed_engine` | test_config.py | no such line |
| 6 | `DoctorTests.test_doctor_flags_an_unknown_engine` | test_config.py | `"locl"` prints nothing |

"A typo still runs local" is the existing
`test_anything_unrecognised_runs_the_local_chain`, kept unchanged; test 6 is
the "doctor flags it" half.

**Expected count: 1039** on `b73a3f4` (B - 87 after rebasing):

```
1126            baseline
 - 98           test_realtime.py (96) + test_realtime_wire.py (2) deleted
 +  7           EchoRiskTests (6) + the shared-text test (1) moved, not lost
 +  6           new tests 1-6
 -  2           test_openai_is_still_reachable, test_the_realtime_engine_says_the_same_thing
= 1039
```

The spec's 1038 has 5 new tests; test 6 is the sixth (see "Decided in this
plan"). Test 1 is counted as new and `test_openai_is_still_reachable` as
removed, as in the spec.

```
export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent
nix develop -c python3 -m pytest -q                      # 1039 passed (B - 87)
nix develop -c python3 -m unittest discover -s tests     # Ran 1039 tests, OK
nix flake check --no-write-lock-file                     # passes, no websockets
nix build .#omarchy-voice --no-link --print-out-paths    # then <out>/bin/omarchy-voice --version → omarchy-voice 2.0.0
```

**Grep check: no live `realtime`/`openai` references remain.**

```
# 1. No module, import or dependency left. Expected: only test 2's string.
grep -rnE "from \.realtime|import realtime|omarchy_voice\.realtime|realtime_mod|websockets?" \
  src tests tools pyproject.toml flake.nix nix
# 2. src/: expected hits are only `realtime_engine`, "realtime" in
#    PREFIXED_SECTIONS (config.py:249), the RETIRED_KEYS entries, and
#    comments containing "#121" or "removed in 2.0.0".
grep -rni "realtime" src | grep -vE "realtime_engine|PREFIXED_SECTIONS|RETIRED_KEYS|#121|removed in 2\.0\.0"
#    → empty (any hit must be reviewed and reworded or justified in the PR)
# 3. The "openai" engine value appears only in the refusal and doctor.
grep -rn '"openai"' src          # → cli.py cmd_run and cmd_doctor only
# 4. Nothing presents the engine as live to a user. Expected: README's
#    removal note, "Speakers, and her hearing herself", "Credit and what
#    this fork changed"; nothing in share/, nix/, plugin/ or src/.
grep -rniE "OpenAI Realtime|speech.to.speech|streamed to OpenAI|engine = \"openai\"" \
  README.md docs share nix plugin src
```

Allowed outside these: `HANDOFF.md`, `tools/bench_local.py:4-5,:53`,
`tools/live_check.py:115` (out of scope, decision 18), and the
intent/spec/plan files.

On p620 after deploy (owner's step, not part of the PR): `omarchy-voice
doctor` shows `omarchy-voice 2.0.0`, the local engine block, and no `OpenAI
Realtime` line; the p620 config is unchanged.

No test opens a websocket, plays audio, reaches D-Bus or contacts OpenAI or
ElevenLabs.

## Rollback

The change is a series of green commits on the implementation branch,
merged as one PR. Before merge, drop the branch. After merge,
`git revert -m 1 <merge sha>` restores `realtime.py`, its tests, the
`websockets` dependency, the eight config fields and version 1.0.0. There is
no migration or state: `~/.config/omarchy-voice/safety-id` was never
deleted, and a config that kept `engine = "openai"` starts the realtime
engine again after the revert. A user who needs it without a revert can pin
the flake input to the `v1.0.0` tag. On p620 and razer nothing needs
undoing, since neither sets `engine`.
