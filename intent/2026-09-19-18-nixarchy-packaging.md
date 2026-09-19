---
status: draft
issue: 18
author: olafkfreund
---

# Intent: voice installs its plugins the nixarchy way, credits the voice it speaks with, and checks readiness at runtime

## Problem

Three packaging problems keep voice from being a clean opt-in inside nixarchy
(olafkfreund/nixarchy#774).

**Its plugins bypass nixarchy's plugin option.** The Home Manager module
writes them with raw `xdg.configFile` (`nix/hm-module.nix:162-169`). That skips
`programs.nixarchy.plugins`: the build-time validation, the reconcile that
removes plugins dropped from the config, and the once-enable markers that keep
a user's "off" choice. Their ids, `voice.indicator` and `voice.orb`, have no
owner prefix, so another plugin could take them.

**The default voice isn't credited.** `nix/piper-voice.nix` marks every voice
`lib.licenses.mit` (`:23`) and installs only the model and its JSON. The
default, `en_GB-jenny_dioco-medium` (`:41-43`, `:65`), was trained on a dataset
whose terms ask for attribution, "Jenny" or preferably "Jenny (Dioco)",
wherever the voice is heard. Nothing shows it, and the model cards that carry
those terms aren't shipped.

**A missing key is warned about at evaluation.** `nix/hm-module.nix:303-307`
prints "no API key set" on every rebuild wherever the module is imported,
including setups that never start the service. Whether voice is ready to run
is a runtime question: the key may come from a secret that only exists at boot.

## Proposed outcome

- When `programs.nixarchy.plugins` exists, the module registers its plugins
  there, with prefixed ids (e.g. `olafkfreund.voice-indicator`). Otherwise it
  falls back to today's files. Existing homes are migrated without a leftover
  directory blocking the new install.
- Speech output credits "Jenny (Dioco)" where the voice is used: the doctor
  output, the plugin's about text and the README. The model cards ship with
  the models, and each voice carries its own licence metadata instead of a
  blanket MIT.
- The eval-time key warning goes. `omarchy-voice doctor`, and the unit when it
  starts, say clearly when no backend is ready.

## Affected users and systems

Voice's Home Manager module, its Piper packaging, doctor and the plugins, and
every user updating from the unprefixed ids. nixarchy's #774 depends on it.

## Constraints

- **Existing users keep a working setup** after the id change.
- **No secret is read during evaluation.**
- **Tests:** the plugin registration path and the attribution text are
  covered, and each check is shown failing first.

## Open questions

1. **The new ids:** `olafkfreund.voice-indicator` and `olafkfreund.voice-orb`
   match ai-mirror and gltui's `olafkfreund.*`, or `nixarchy.voice-*` to match
   pkg, podman and herdr?
2. **Where the attribution is visible:** doctor, the plugin and the README
   (proposed), or also spoken once at first run?
