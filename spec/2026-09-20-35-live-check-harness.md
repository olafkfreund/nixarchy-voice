---
status: draft
issue: 35
intent: intent/2026-09-20-35-live-check-harness.md
---

# Spec: a harness that takes a machine, not a repository that owns one

## The intent's open questions, answered

1. **Build it now, scoped to the OCR and window-targeting checks.** That is the
   part that corrupted published numbers, and it works in `.#vm` today.
   **Accessibility is explicitly out of scope**: there is no accessibility bus
   in that guest (olafkfreund/nixarchy#823), so `QT_ACCESSIBILITY=1` would have
   nothing to talk to, and #31 is already closed as blocked on it.
2. **A fixed window layout launched inside the guest**, not a static image. A
   still image OCR'd directly would be perfectly reproducible and would stop
   testing the capture path — and a timing taken from it is precisely the
   component benchmark #23 refused to let drive decisions. The layout is
   launched, captured and measured, and its variance is honest rather than
   engineered away.
3. **`tools/live_check.py`, reporting #39's trace phases.** It does not replace
   `bench_local.py` or `bench_realtime.py`, which measure the speech pipeline
   rather than the desktop loop and predate the trace.
4. **Nothing is copied into the guest.** The run script shares the host store:
   `-device vhost-user-fs-pci,chardev=nix-store,tag=nix-store`. So a path from
   `nix build .#omarchy-voice` — a 6.7 GiB closure — is directly runnable
   inside without a `nix copy`, which would otherwise repeat on every boot,
   since `.#vm` sets `diskImage = null` and discards its root each time.
5. **Not better spent on nixarchy#821.** The harness takes an **ssh target** and
   a store path. It works against `.#vm` today and against #821's template if
   that lands, with no change. That is the whole reason to keep it here and the
   VM there.

## Design

### 1. The harness takes a machine; it does not make one

Arguments: an ssh target (default `omarchy@localhost:2222`, which is `.#vm`),
and a store path to exercise (default: a fresh `nix build .#omarchy-voice`).
No `nixosConfigurations` entry is added, no nixarchy input, nothing that
duplicates olafkfreund/nixarchy#821.

If the target is unreachable it says so and exits non-zero. It never falls back
to the local desktop — that is the entire point of the issue.

### 2. A fixed layout, launched in the guest

Before measuring, it launches a known set of windows over ssh — a terminal with
fixed text and a browser on a local page — and waits for them. The same layout
every run is what makes two runs comparable, and it is the thing the real
session could never give.

### 3. It measures whole tasks, through the trace

With `trace_timings` on, it runs a small fixed list of desktop tasks and
collects the `TIMING` lines. What it reports is #39's phases — `ocr`,
`capture`, `subprocess`, `tool`, `model-turn` — because #23 requires changes be
argued from task completion rather than a stopwatch on one component.

It reports the spread across repeats, not only a mean. A number whose spread is
wide is the finding, and hiding it behind an average is how 3.97 s got
published.

### 4. It is never part of anything automatic

Not a flake check, not in CI, not a test. `nix flake check` must keep passing
with no compositor, and the unit suite must keep running with none. #28
demonstrated the cost of blurring that: wiring its listener in unconditionally
sent unit tests to the live compositor and a 25 s cap became a 25 s wait.

### 5. It announces itself

Booting a VM on a host that carries four CI runners is not free. The harness
prints what it is about to start and how long it expects to take, and refuses
to run two at once by holding the ssh port as its lock.

## Alternatives rejected

- **Own a `nixosConfigurations` entry here.** Requires adding nixarchy as an
  input, against `flake.nix:82` and `:94` which exist to avoid exactly that,
  and duplicates olafkfreund/nixarchy#821.
- **`nix copy` the closure into the guest.** 6.7 GiB, repeated every boot
  because the guest's root is discarded. Unnecessary: the store is already
  shared.
- **A static image fixture.** Reproducible and it stops testing capture, and it
  is the component benchmark #23 rejected.
- **Extending `bench_local.py`.** It measures transcribe/brain/synth and does
  not use the trace. A desktop-loop harness bolted onto it would be two tools
  in one file.
- **Running it in CI.** The runners are on this host and a VM is not free; and a
  measurement that must pass is a measurement people will tune rather than read.

## Risks

- **The guest is not the user's desktop.** Different applications, no
  accessibility, a different window set. Numbers from it are comparable **to
  each other**, never to the host. The output must say so, or it will be quoted
  as though it were.
- **It depends on a VM this repository does not own.** If `.#vm` changes shape,
  the harness breaks. Mitigated by taking the target as an argument rather than
  assuming one.
- **Boot costs about 40 seconds** before anything is measured, so this is a
  deliberate act and never a quick check.
- **Another session may be using the host or the VM.** The port lock covers the
  VM; the host is a matter of announcing.
- **It may go stale.** Nothing in CI runs it, which is deliberate, and means it
  can rot unnoticed. A cheap evaluation check that it still parses is worth
  more than it costs.

## Verification

**End to end, by running it.** Against a booted `.#vm`, it launches the layout,
runs the tasks, and prints `TIMING` lines with `ocr` and `capture` populated.

**Twice, and comparably.** Two runs against the same layout produce numbers
whose spread is reported and visibly narrower than the 1.1-5.3 s the real
session gave. That is the issue's actual claim and the thing to demonstrate.

**It refuses rather than falls back.** With no VM running, it exits non-zero
saying the target is unreachable, and measures nothing locally.

**Nothing regressed.** `nix flake check` green **without a VM running**, and the
unit suite green with no compositor.

**Not verified here.** Anything requiring accessibility in the guest, which does
not exist (nixarchy#823); and whether the layout is stable across a guest
restart, which needs more than one boot to establish.
