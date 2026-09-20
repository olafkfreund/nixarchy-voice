---
status: draft
issue: 35
spec: spec/2026-09-20-35-live-check-harness.md
---

# Plan: a harness that takes a machine, not a repository that owns one

## The approved decisions, in full

Implementable without opening the intent or the spec.

**The problem.** Every measurement in #26/#27/#28 ran against the real session:
probe windows spawned, a throwaway Chrome launched, the pointer moved, and
whatever happened to be on screen OCR'd. It is disruptive, and it corrupted
numbers — full-screen OCR measured 1.1 s to 5.3 s in one session on content
alone, 3.97 s was published and had to be corrected, and today #12 step 7 had to
record "unmeasured" because Chrome sat near 72% of a core playing video.

Decisions carried over:

1. **The harness takes an ssh target and a store path.** It works against
   `.#vm` today and nixarchy#821's template later, unchanged. That is why the
   harness lives here and the VM does not.
2. **Nothing is copied into the guest.** The run script shares the host store —
   `-device vhost-user-fs-pci,chardev=nix-store,tag=nix-store` — so a path from
   `nix build .#omarchy-voice`, a 6.7 GiB closure, runs directly inside. A
   `nix copy` would repeat every boot, because `.#vm` sets `diskImage = null`.
3. **A fixed layout launched in the guest, not a static image.** A still would
   be reproducible, would stop testing capture, and its timing is the component
   benchmark #23 rejected.
4. **It reports spread, not only a mean.** Hiding spread behind an average is
   how 3.97 s was published.
5. **Accessibility is out of scope.** No accessibility bus exists in that guest
   (nixarchy#823), so the flags would have nothing to talk to.
6. **It is never automatic.** Not a flake check, not CI, not a test. `nix flake
   check` keeps passing with no compositor and the unit suite keeps running with
   none — #28 showed the cost of blurring that, when a 25 s cap became a 25 s
   wait.

## Narrowed on implementation: it does not manage the VM

The spec spoke of holding the ssh port as a lock, which implied owning the boot.
**It does not boot or stop anything.** It checks the target, and if nothing
answers it prints the command to start one and exits non-zero. That drops
lifecycle, teardown and locking altogether, and is the honest reading of "takes
a machine, not owns one". Recorded here rather than left as a silent difference.

## Steps

**1. `flake.nix:58` — the devShell gains `sshpass` and `openssh`.**
The guest is password-authenticated only: `ssh -p 2222 omarchy@localhost`,
password `omarchy`, no keys. Without this the harness cannot connect from a
clean shell.
→ verify by `nix develop -c sshpass -V` succeeding.

**2. `tools/live_check.py` — connect, or refuse.**
Shaped like `bench_local.py`: `argparse`, `main() -> int`,
`if __name__ == "__main__"`. Arguments: `--target` (default
`omarchy@localhost`), `--port` (2222), `--package` (default: a fresh
`nix build .#omarchy-voice --print-out-paths`).
Unreachable target prints the headless launch line from nixarchy's own config
and exits non-zero. **It never measures locally.**
→ verify by running it with no VM: non-zero, names the launch command, and
nothing on this desktop is touched.

**3. The layout, launched in the guest.**
Over ssh: a terminal showing fixed text, and a second window, then wait for both
to appear. The same windows every run is what makes two runs comparable.
→ verify by listing the guest's windows after the launch and getting the same
set twice.

**4. Run the tasks with the trace on, and collect.**
`trace_timings` enabled through an isolated `XDG_CONFIG_HOME` inside the guest —
never the guest's own config, so a run leaves nothing behind. A small fixed list
of desktop tasks, and the `TIMING` lines collected from stdout.
→ verify by the lines arriving with `ocr` and `capture` populated.

**5. Report phases and spread.**
Per phase: median and the range across repeats. The range is the finding when it
is wide, and the output says plainly that these numbers are comparable to each
other and not to the user's desktop — different applications, no accessibility,
a different window set.
→ verify by reading the output against what it measured.

**6. Say how to run it, where a reader will look.**
`README.md` or `HANDOFF.md`, one short block: start the VM headless, run the
harness, what the numbers mean and do not mean.
→ verify by following it from a clean shell.

## Tests

```
nix develop -c python3 -m unittest discover -s tests -v
nix flake check
```

Expected green **with no VM running** — that is the point of step 6 of the
decisions. The harness itself is not unit tested beyond the argument parsing and
the refusal path; it is a measurement tool, and a test that needs a VM would be
the thing this plan refuses to create.

## Rollback

`git revert`. The harness is a new file under `tools/` and two packages in the
devShell. Nothing under `src/` changes, so the assistant behaves identically
whether this exists or not.
