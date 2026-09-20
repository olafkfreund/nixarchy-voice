---
status: approved
issue: 35
author: olafkfreund
---

# Intent: the live checks run on the user's desktop, and that corrupted a number

## Problem

Every measurement in the #26/#27/#28 work ran against the real session on this
machine. It spawned `foot` probe windows, launched a throwaway Chrome, moved the
pointer, and OCR'd whatever happened to be on screen.

Two costs, and the second is the one that matters:

1. **It is disruptive.** Driving input and opening windows on a desktop somebody
   is using rules out running any of it unattended.
2. **It is not reproducible, and it corrupted published numbers.** Full-screen
   OCR measured 1.1 s to 5.3 s across one session, and the variance was screen
   *content*, not code. A figure of 3.97 s went into an intent and had to be
   corrected. It happened again today: #12 step 7's measurement of what
   accessibility costs produced two paired rounds that disagreed, because Chrome
   sat near 72% of a core playing video, and the plan had to record "unmeasured"
   rather than a number.

**What scoping established, and it moves the boundary the issue drew:**

1. **A suitable VM already exists and already works for this.** `nix run .#vm`
   boots headless with `QEMU_OPTS="-display none"`, ssh on 2222, and I confirmed
   today that Hyprland 0.56 and quickshell genuinely run inside it, with
   `wayland-1` present and chromium, nautilus and foot installed. The
   OCR-side checks — `read_screen`, `click_text`, `wait_for` — can run there
   now.
2. **Owning a VM here would reverse a deliberate decision.** This flake takes
   `ai-mirror`, `home-manager`, `nixpkgs` and `systems` — **not nixarchy** — and
   says why in as many words: `flake.nix:82`, "check can stand in for nixarchy
   without depending on it", and `flake.nix:94`, a stub of nixarchy's own option
   "declared here so the module can be checked against it without nixarchy as an
   input". Getting the omarchy shell into a VM defined here means adding that
   input and undoing that.
3. **And it would duplicate work already proposed next door.**
   olafkfreund/nixarchy#821 asks for a graphical microvm template, for the same
   reason and citing this issue. nixarchy already carries `microvm` and
   `nixarchy-microvm` as inputs; this repository carries neither.
4. **The accessibility half cannot be built here at all.** The issue asks for
   `QT_ACCESSIBILITY=1` and Chromium launched with
   `--force-renderer-accessibility`. Measured today, nixarchy's VM has **no
   accessibility bus**: `org.a11y.Bus` is not activatable, so those settings
   would have nothing to talk to. That is olafkfreund/nixarchy#823, and #31 —
   the issue this was meant to unblock — has since been closed as blocked on it.

So the thing that is genuinely this repository's is the **harness**: what a live
check runs, against what layout, and what it reports. The machine it runs
against belongs next door.

## Proposed outcome

- A live check can be run against a machine that is not the user's, without
  touching their session.
- The same check run twice gives the same number, because the windows on screen
  are chosen rather than whatever was open.
- A measurement that cannot be trusted says so, rather than being published and
  corrected later.
- The unit suite is unaffected and still needs no compositor.

## Affected users and systems

- Anyone measuring or changing the OCR and window-targeting paths — the whole of
  #23's remaining chain.
- `tools/`, which today holds `bench_local.py` and `bench_realtime.py`, neither
  of which uses the trace from #39.
- `flake.nix`, if the harness is reachable as an app or from the devShell.
- Not `src/`. This must not change what the assistant does.

## Constraints

- **The unit suite must keep running with no compositor.** `nix flake check`
  has none and that is correct. #28 demonstrated the hazard: wiring its event
  listener in unconditionally made unit tests reach the live compositor, and a
  test asserting a 25 s cap then waited 25 real seconds. A VM would have made
  that slower to find, not prevented it.
- **Must not add nixarchy as an input** without that being an explicit decision,
  for the reasons in finding 2.
- **Must not duplicate nixarchy#821.** If a VM is needed beyond `.#vm`, the
  request belongs there.
- Must not assume accessibility. It is absent in the VM today (finding 4).
- Must not require the VM to exist in order to run the unit tests, `nix flake
  check`, or anything in CI.
- p620 carries four nixarchy CI runners, and a VM is not free. Whatever this is
  must be run deliberately, never as a side effect of something else.

## Open questions

1. **Is the harness worth building before the VM it wants exists?** `.#vm` works
   for the OCR checks today and lacks accessibility. A harness that covers only
   what works now is useful immediately and might be the whole of it.
2. **What is "a known layout"?** The issue asks for comparability. Launching a
   fixed set of windows inside the guest is one answer; shipping a static image
   and OCRing that is another, and is reproducible in a way a live desktop can
   never be — but it stops testing the capture path.
3. **Where does it live, and what does it report?** `tools/` holds two benches
   that predate the #39 trace and do not use it. A third that does is either a
   third thing to maintain or the beginning of replacing them.
4. **How does the working tree get in?** The package builds from the flake, so a
   check runs against a build rather than against uncommitted work. That is
   either fine or the main reason this gets skipped in practice.
5. **Is this better spent on nixarchy#821?** If that lands with accessibility
   enabled, it answers finding 4 and this becomes a thin script. If it does not,
   this issue inherits the whole problem.
