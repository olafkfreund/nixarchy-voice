---
status: draft
issue: 160
intent: intent/2026-09-27-160-uptime-from-proc.md
---

# Spec: uptime answered from /proc

Intent approved without an answer to its one question, so it is decided as
recommended: procps' wording, today's two labelled lines.

## Evidence

- p620 **and razer** both have coreutils 9.11's `uptime` on PATH, and both
  reject `-p` and `-s`. The bug is not host-specific.
- The target wording, from the real procps-ng 4.0.7 (`nix shell
  nixpkgs#procps`), on p620 at the same moment:

  ```
  $ uptime -p          up 4 days, 18 hours, 41 minutes
  $ uptime -s          2026-09-22 11:30:07
  /proc/uptime         412868.90 …        → 4 d 18 h 41 m
  /proc/stat btime     1790073007         → 2026-09-22 11:30:07 local
  ```

  The two `/proc` values alone reproduce both lines exactly.

## Design

In `src/omarchy_voice/tools.py`, follow `battery`'s pattern (`tools.py:1614`,
`1679`, `5033`):

- `SYSTEM_QUERIES["uptime"] = None` in the table, filled in below with
  `lambda executor: executor._uptime()`, the same way `battery` is.
- Module function `_pretty_uptime(seconds: float) -> str`, procps-ng's
  `uptime -p` wording:
  - units: years (365 days), weeks, days, hours, minutes. Seconds are
    dropped, as procps drops them;
  - zero units omitted; singular or plural per unit ("1 day", "2 days");
    comma-separated; prefixed `up `;
  - under a minute: `up 0 minutes`.
- `Executor._uptime() -> Result`:
  - reads the first field of `/proc/uptime`, and the `btime` line of
    `/proc/stat` (epoch seconds);
  - returns `f"{pretty}\n\nbooted:\n{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(btime))}"`,
    the exact shape the recipe produced before (a label-less first part, then
    `booted:`);
  - if `/proc/uptime` is unreadable, `Result(False, "nothing on this machine
    could answer 'uptime'")`, today's message;
  - if only `btime` is missing, the first part alone.

No other topic changes. No binary is run.

## Alternatives rejected

- **Calling `uptime` and parsing either dialect** (coreutils' `up 4 days,
  18:41` or procps' `-p`): still depends on PATH order and on a binary's
  wording, which is the bug.
- **`procps` as a dependency:** a package for two numbers the kernel already
  exposes, and it would still lose to coreutils on PATH on these hosts.
- **`psutil.boot_time()`:** a new Python dependency for one line.

## Risks

- **The weeks and years wording** comes from procps-ng's source, not from a
  live run: only days/hours/minutes could be observed at today's uptime. The
  tests pin the rule; if procps words it differently, only the week and year
  cases change.
- **Local time for `booted`** matches `uptime -s`. A machine whose timezone
  changed since boot shows the new zone, as procps does.
- **Containers:** `/proc/uptime` is the host's uptime inside most
  containers, which is also what procps shows. Not a regression.

## Verification

- Unit tests (fake `/proc` files via a patched path or reader):
  - 412868.9 → `up 4 days, 18 hours, 41 minutes` (the live-observed case);
  - 59 → `up 0 minutes`;
  - 3600 → `up 1 hour`;
  - 90061 → `up 1 day, 1 hour, 1 minute`;
  - 8 days → `up 1 week, 1 day`;
  - 400 days → `up 1 year, 5 weeks`;
  - btime 1790073007, formatted in a fixed timezone (`TZ` set in the test)
    → the expected `booted:` line;
  - an unreadable `/proc/uptime` → today's failure message;
  - no `uptime` binary is ever run (`_shell` not called).
- Mutation: removing the zero-unit omission fails a test.
- Live on p620: `system_query uptime` equals `nix shell nixpkgs#procps -c
  uptime -p` and `-s` at the same moment.
