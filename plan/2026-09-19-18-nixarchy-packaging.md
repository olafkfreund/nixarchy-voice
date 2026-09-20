---
status: approved
issue: 18
spec: spec/2026-09-19-18-nixarchy-packaging.md
---

# Plan: voice installs through nixarchy's plugin option, credits its voice, and checks readiness when it runs

## The decisions being implemented

Carried over from the approved spec so this file stands alone.

**Why.** Three things are wrong at the packaging edge. The plugin ids
(`voice.indicator`, `voice.orb`) are unprefixed, so they collide with anyone
else's plugin of that name and they do not match how nixarchy names plugins.
The module links plugins by hand into `~/.config/omarchy/plugins` instead of
going through `programs.nixarchy.plugins`, so they miss its validation and its
reconcile. And the shipped Jenny voice's dataset requires attribution in any
interface that generates speech — we generate speech and credit nobody, while
`piper-voice.nix:23` marks every voice MIT regardless of its actual card.

**Settled decisions — do not relitigate while implementing:**

1. **The prefix is `olafkfreund.`** (owner's choice): ids and directory names
   become `olafkfreund.voice-indicator` and `olafkfreund.voice-orb`, moving
   together because `omarchy-plugin-validate` requires the directory name to
   equal the id.
2. **Registration goes through `programs.nixarchy.plugins` when that option
   exists,** guarded by `options ? programs.nixarchy.plugins` — the same test
   ai-mirror's module uses, and one that evaluates without nixarchy present.
   The `xdg.configFile` links stay as the fallback, at the **new** ids, for
   Omarchy without nixarchy.
3. **Installed, not enabled.** Enabling stays nixarchy's decision
   (nixarchy#774) or the user's. Do not enable anything from here.
4. **`shell.json` is never written directly.** Migration is a one-time
   `post-boot.d` hook going through `omarchy plugin disable|enable`, because
   the shell rewrites that file from memory (nixarchy#766). The marker is
   written **only after every step succeeds**, so a failure retries next login.
5. **An old id that was off stays off** under its new name.
6. **Per-voice licence metadata**: `mkVoice` gains `license`, `attribution`
   and `modelCardHash`, and ships `MODEL_CARD` into `$out`. Jenny declares
   `attribution = "Jenny (Dioco)"`.
7. **The credit is visible in three places** (owner's choice): `doctor`, the
   indicator's about/tooltip text, and the README. The string comes from
   `passthru.attribution` through an env var the wrapper sets, so it cannot
   drift from the Nix metadata.
8. **The eval-time "no API key set" warning goes,** replaced by a runtime
   "no backend" desktop notification on **both** engines. The PipeWire warning
   stays — it reads `osConfig`, not a secret, and it is true at evaluation.
9. **No secret is read at evaluation.** Paths only, as today.

**Landing order.** #17 and #18 are siblings off `481d3f3`. **#17 lands
first** — it is smaller and it is the security change — and **this branch
rebases onto the new `main`** (`git rebase main`) before it is merged. Two
conflicts are expected, both additive in different regions of the same files:
`nix/hm-module.nix` (#17 adds a `desktopControl` option and a
`settings.hands` write; this branch rewrites the `xdg.configFile` plugin block
at `:162-169` and deletes the warning at `:303-307`) and
`src/omarchy_voice/cli.py` (#17 adds two doctor consent lines under `hands`;
this branch adds the voice credit under `speech`). Take both sides in each.
If the rebase turns messy, replay this branch's own commits onto `main`
rather than merging `main` in (nixarchy CLAUDE.md §8).

## Steps

1. `plugin/`: `git mv voice.indicator olafkfreund.voice-indicator` and
   `git mv voice.orb olafkfreund.voice-orb`; set each `manifest.json` `id` to
   match, and `VoiceIndicator.qml`'s `moduleName` to
   `olafkfreund.voice-indicator`.
   → verify by `omarchy-plugin-validate` on each directory, and by a check
     asserting `id` equals the directory name

2. `nix/package.nix`: the `postInstall` copy lands the plugins at
   `share/omarchy-voice/plugins/<new id>`.
   → verify by `ls "$(nix build .#omarchy-voice --print-out-paths)/share/omarchy-voice/plugins"`

3. `nix/hm-module.nix:162-169`: replace the two `xdg.configFile` plugin
   entries with the `lib.optionalAttrs (options ? programs.nixarchy.plugins)`
   block from the spec, keeping the `xdg.configFile` links (new ids) as the
   fallback branch. `barWidget` and `orb` keep gating each one.
   → verify by the new `hm-plugin-registration` check (step 9)

4. `nix/hm-module.nix:105` and the `barWidget`/`orb` descriptions: the
   `omarchy bar put` example uses the new id.
   → verify by grepping the module for `voice.indicator` finding nothing

5. `nix/hm-module.nix`: generate
   `~/.config/omarchy/hooks/post-boot.d/voice-migrate-ids`. For each old id
   that `omarchy-plugin-list --json` reports **enabled**, run
   `omarchy plugin disable <old>` then `omarchy plugin enable <new> right`
   (the orb goes to `plugins[]`); log through `systemd-cat`; write
   `~/.local/state/omarchy-voice/ids-migrated` only when every step returned
   success. Exit early when the marker exists. Write it as a
   `writeShellApplication` so its `runtimeInputs` are declared — an
   undeclared command here reads as "nothing to migrate", not as an error.
   → verify by the hook's unit test (Tests), and by hand on a machine with the
     old widget on the bar

6. `nix/piper-voice.nix`: `mkVoice` takes `license`, `attribution` and
   `modelCardHash`; drops the blanket `lib.licenses.mit` at `:23`; fetches
   `MODEL_CARD` (hash-pinned, same `base` URL) into `$out`; exposes
   `passthru.attribution`.
   → verify by `nix eval .#…jenny….passthru.attribution` and
     `test -f "$(nix build … --print-out-paths)/MODEL_CARD"`

7. `nix/piper-voice.nix`: per voice — Jenny: MIT model, dataset requiring
   attribution as "Jenny (Dioco)", both named in the licence; Cori:
   public-domain dataset, no attribution. **Lessac: read its `MODEL_CARD` and
   record what it says.** The spec deliberately does not guess; if the card is
   unclear, record the card URL and put the question on the issue rather than
   asserting a licence.
   → verify by reading each fetched `MODEL_CARD` against what the derivation
     claims

8. `nix/package.nix` wrapper + `src/omarchy_voice/cli.py`: the wrapper exports
   the selected voice's `passthru.attribution`; `doctor`'s `speech` section
   prints `voice <name> — voice: <attribution>` when the variable is set and
   nothing when it is not. Same string in the indicator's about/tooltip text,
   and a "Voices and credits" section in `README.md`.
   → verify by `omarchy-voice doctor` showing `Jenny (Dioco)` on a default
     install

9. `flake.nix`: `checks.<system>.hm-plugin-registration` — evaluate the module
   with a stub `programs.nixarchy.plugins` option and assert the plugin is
   declared there with **no** `xdg.configFile` plugin link, then without the
   stub and assert the fallback link at the new id. Plus
   `checks.<system>.plugin-ids` asserting each manifest's `id` equals its
   directory and starts with `olafkfreund.`. CI already runs
   `nix flake check`, so no workflow edit (and none is ours to make).
   → verify by `nix build .#checks.x86_64-linux.hm-plugin-registration
     .#checks.x86_64-linux.plugin-ids`

10. `nix/hm-module.nix:303-307`: delete the `no API key set` warning. Leave
    the PipeWire warning and the `bindsFile` warning alone.
    → verify by the "no warning" check (Tests) and by a rebuild with no key
      printing nothing

11. `src/omarchy_voice/local_engine.py:602-611` and the realtime engine's
    start path: **first confirm** whether realtime runs the same
    `check_ready()`; bring it into line if it does not. Both then send **one**
    desktop notification — "voice has no backend: run `omarchy-voice doctor`"
    — alongside the existing indicator state, and still exit 0 so the unit
    does not flap.
    → verify by the two-engine pytest (Tests)

12. `README.md:142,800,820,847` and `src/omarchy_voice/feedback.py:27`: the
    new ids everywhere, plus release notes saying a scripted
    `omarchy bar put voice.indicator` must change and that the migration is
    automatic at the next login.
    → verify by `grep -rn "voice\.indicator\|voice\.orb"` finding only
      migration code and release notes

## Tests

Runner: `pytest tests -q` (`nix develop`, or `checks.<system>.unit`); the Nix
side through `nix flake check`. Each case is **seen red on `481d3f3`** first
and the failing output goes in the PR (§1).

| Case | Where | Red today because |
|---|---|---|
| with a stub `programs.nixarchy.plugins`, `olafkfreund.voice-indicator` is declared there and no `xdg.configFile` plugin link exists | `checks.hm-plugin-registration` | the module only writes `xdg.configFile`, at the old id |
| without the stub, the fallback link exists at the new id | same | the id is `voice.indicator` |
| each manifest's `id` equals its directory and starts with `olafkfreund.` | `checks.plugin-ids` | they are `voice.*` |
| Jenny's `passthru.attribution == "Jenny (Dioco)"` and `$out/MODEL_CARD` exists | `checks` on the voice derivation | there is no attribution and no card |
| `doctor` output contains `Jenny (Dioco)` when the attribution variable is set | `tests/test_backend_choice.py`, beside the other doctor tests | doctor prints no credit |
| start with no backend ready → exactly one "no backend" notification and exit 0, **for both engines** | `tests/test_local_engine.py` and `tests/test_realtime.py` | only the indicator state is set, and realtime is unverified |
| a module eval with no key set produces **no** warning | `checks.hm-plugin-registration` (or its own tiny check) | it warns today |
| migration hook with `omarchy-plugin-*` stubbed: an enabled old id ends disabled and the new one enabled, marker written; second run is a no-op; an **off** old id is not enabled | `tests/test_migration_hook.py` (new) | there is no hook |

Break each deliberately rather than by deleting the feature: point the
registration at the wrong attribute name, give Jenny the wrong attribution
string, make the hook write its marker before the commands run (the second-run
case then passes for the wrong reason — that is the point of breaking it).

```sh
pytest tests -q
nix flake check
```

**By hand, on a machine with the old widget on the bar:** log out and in, and
confirm the bar still shows the indicator, under the new id, with
`shell.json` rewritten by the shell and `ids-migrated` present. This is the
case no unit test reaches, because the shell must be live for it.

## Rollback

`git revert` the commits. The ids revert with them, and the same post-boot
hook cannot run backwards — a user who has migrated would be left with
`shell.json` naming ids that the reverted package no longer provides, which
shows nothing on the bar until they run `omarchy plugin enable
voice.indicator right` by hand. Say that in the revert commit. The only other
residue is `~/.local/state/omarchy-voice/ids-migrated`; deleting it makes the
hook run again on a re-apply.

**Dependants:** nixarchy#774 registers voice as an opt-in setup and expects
these ids; #17 is this branch's base once it lands.
