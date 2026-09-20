---
status: approved
issue: 30
spec: spec/2026-09-20-30-virtual-input.md
---

# Plan: drive the pointer through Wayland, not a root daemon

Worktree `/mnt/data/vmtest/voice-30-input`, branch `feat/30-virtual-input`,
already carrying the intent and the spec. A deviation updates this file in the
same commit as the code.

## The approved decisions, in full

Implementable without opening the intent or the spec.

**Why.** `ydotool` needs `/dev/uinput` and a root daemon; without it `scroll`
degrades to Page Up/Down and `click_text` returns `CLICK_UNAVAILABLE`. And
nothing in this codebase can let go: a turn abandoned mid-chord leaves the key
down, with no equivalent of "release everything" anywhere.

**Decided by the approver:** depend on ai-mirror's flake; remove `ydotool`
entirely rather than keep it as a fallback; honour SUPER+SHIFT+ESC.

**Decided in the spec:**

- **Lifetime — lazily opened, closed at the end of a turn.** Per-turn rather
  than per-action because a turn may click several times; per-turn rather than
  per-process because holding the pipe across turns is exactly the window where
  an abandoned chord stays held. The release becomes structural: nothing calls
  `release_all`, because the thing holding the keys stops existing.
- **The revoke is observed, not obeyed as a grant.** ai-mirror's revoke bumps
  `generation` in `$XDG_RUNTIME_DIR/ai-mirror/state.json`. Voice records the
  generation it saw and closes its helper when that number changes. It does
  **not** require `owner == "agent"` — that would mean voice cannot click until
  another program's agent mode is on, reversing #19 by the back door.
- **Absence of ai-mirror is not a gate.** No state file means no revoke to
  honour, not a refusal.

**The wire protocol**, read from `ai_mirror_input.c` and `input.py`:

```
M <x> <y>          move absolute
B <code> <press>   button; evdev codes, left = 272, press = 1|0
S <dx> <dy>        scroll
K <code> <press>   key
```

A single click is `B 272 1` then `B 272 0`; a double is that pair twice.

## Steps

**1. `flake.nix` and `nix/package.nix`: the helper.**
Add `ai-mirror` as an input with `inputs.nixpkgs.follows = "nixpkgs"`, and put
`ai-mirror.packages.<system>.ai-mirror-input` in the wrapped tool list where
`ydotool` is now (`package.nix:63-83`). Remove `ydotool` in the same commit —
they are the same decision and splitting them would leave a state where both
are wrapped.
→ verify by `nix build` succeeding and `nix path-info -r` on the result naming
`ai-mirror-input` and not `ydotool`.

**2. New `src/omarchy_voice/virtual_input.py`.**
A `Helper` holding the `subprocess.Popen` and its pipe:

- `open()` — spawn, lazily; record ai-mirror's `generation` at that moment.
- `send(lines)` — write, checking the revoke first (below).
- `close()` — terminate. This is the release, and it is why nothing calls
  `release_all`.
- `revoked()` — re-read `state.json`; true when `generation` differs from the
  one recorded at `open()`.

Polling, not inotify: the state file is written by atomic replace, which swaps
the inode out from under a watch. `plugin/voice.orb/VoiceOrb.qml` already
records that trap for the level file and polls for the same reason.

The check runs **immediately before each send**, not only on a tick. If it
misses a bump, voice keeps an input channel the user believes they closed —
that is the only direction of this error that matters.

Nothing in this module imports `tools`. It is a process and a pipe.
→ verify by tests 1–5.

**3. `tools.py`: `_press_button` speaks to the helper.**
`tools.py:2146-2152`. `B <code> 1` / `B <code> 0`, repeated for a double
click. `YDOTOOL_BUTTONS` and `CLICK_UNAVAILABLE` (`:418-424`) go.
→ verify by test 6.

**4. `tools.py`: the wheel speaks to the helper.**
`S 0 <clicks>` in place of `ydotool mousemove --wheel`. The axis swap for
horizontal and `SCROLL_SIGN` stay — they are about direction, not about the
tool. **The Page Up/Down fallback goes**: it existed because `ydotool` was
frequently absent, and the reason for its absence was the root daemon.
→ verify by test 7.

**5. `tools.py`: an honest refusal.**
When the helper will not start — binary missing, or the compositor lacks
`zwlr_virtual_pointer_v1` — say so and fail. No silent no-op, and no Page Down
pretending to be a scroll. The helper prints a readiness line on start
(`control.py:170-177` reads one); use it rather than assuming.
→ verify by test 8.

**6. Lifetime wiring.**
Close the helper when a turn ends, wherever a turn can end:
`local_engine._answer`'s `finally`, the same in `realtime`, on listening
stopping, and on daemon shutdown beside the other teardown.

`Executor` gains the helper the way it gained `waker` in #28 — attached by the
entry points, `None` by default — so a unit test never spawns a process.
→ verify by tests 9 and 10.

**7. `README.md` and `share/config.example.toml`.**
Say plainly that ai-mirror's helper is now a build dependency, that
SUPER+SHIFT+ESC stops voice's input too, and that `ydotool` is gone. The last
one matters for anyone who enabled `programs.ydotool` because this package
asked them to.
→ verify by reading it.

**8. Measure the spawn.** A per-turn helper pays a spawn per clicking turn.
The spec says it should disappear against a 1.5–7.6 s turn, and "should" is not
a measurement.
→ verify by the number appearing in this file.

## Tests

No test may spawn a real helper or need a compositor — `nix flake check` has
neither. The seam is the `Helper` object, injected.

1. **A click is the right two lines.** `B 272 1`, `B 272 0` — asserted on what
   is written to the pipe, with the pipe a fake.
2. **A double click is that pair twice**, not `--repeat 2`.
3. **Closing is the release.** `close()` terminates the process; asserted on
   the fake, since the actual release is the compositor's response to the
   client going away.
4. **A generation bump closes the helper and refuses the send.** The case the
   panic key rests on.
5. **No state file is not a revoke.** With `$XDG_RUNTIME_DIR/ai-mirror` absent,
   input proceeds. This is the test that stops "honour the key" becoming "need
   ai-mirror installed".
6. **`click_text` reaches the helper**, and `CLICK_UNAVAILABLE` no longer
   exists — asserted by name, so the string cannot linger unreferenced.
7. **`scroll` reaches the helper**, and the Page Up/Down fallback is gone:
   with the helper unavailable, `scroll` **fails** rather than pressing a key.
   Fails against today's code, which succeeds by pressing Page Down.
8. **An unstartable helper refuses honestly** — a message naming the cause, not
   a silent success.
9. **A turn that ends closes the helper**, including when it ends by an
   exception. This is the bug in the issue and the reason for the whole change.
10. **An executor built in a test has no helper**, as `waker` does in #28, so
    the suite cannot spawn a process by accident.

Commands:

```
nix develop -c python3 -m unittest discover -s tests   # green; 689 before
nix flake check                                        # green, no compositor
nix develop -c python3 -m omarchy_voice verify-gate    # exit 0
```

**Tests 7 and 9 must be seen failing against the pre-change source.** Three
tests passed for the wrong reason in this repo this week — a geometry test
asserting only the first read, eight tests stubbing a wrapper after the
primitive moved, and a trace phase declared and never recorded. A regression
test nobody has watched fail proves nothing.

**On the live desktop, by hand**, because no unit test reaches them:

- `ydotoold` stopped, `click_text` and `scroll` still work. The headline.
- SUPER+SHIFT+ESC during a clicking turn stops it.

**Not tested here, deliberately:** whether a SIGKILL releases held keys.
`release_all()` runs on a graceful exit; the unclean case is Hyprland's
behaviour, and proving it means holding a key down and killing the process on
a live session. It is a VM check — #35, nixarchy#821.

## Rollback

One branch in a worktree; the shared checkout never moves.

`git revert` the merge restores `ydotool` in both `package.nix` and `tools.py`,
and the flake input goes with it. The one thing to know: anyone who removed
`programs.ydotool.enable` from their NixOS configuration after this lands will
need it back, so the README note is part of the change and not decoration.

Steps 2–6 are independent of step 1 in the sense that the module and the call
sites can be reverted while the flake input stays — harmless, just unused. The
reverse is not true, so step 1 is the one to keep if only part comes out.
