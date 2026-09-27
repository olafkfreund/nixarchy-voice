---
status: approved
issue: 160
spec: spec/2026-09-27-160-uptime-from-proc.md
---

# Plan: uptime answered from /proc

## Approved decisions (from the spec, complete)

1. `system_query uptime` reads `/proc` and runs no binary, following
   `battery`'s pattern: `SYSTEM_QUERIES["uptime"] = None` in the table,
   filled in below with `lambda executor: executor._uptime()`.
2. The wording is procps-ng's `uptime -p`, via `_pretty_uptime(seconds)`:
   - years (365 days), weeks, days, hours, minutes;
   - seconds dropped, zero units omitted, singular or plural;
   - comma-separated, prefixed `up `;
   - under a minute is `up 0 minutes`.
3. The output is exactly today's shape:
   `"{pretty}\n\nbooted:\n{%Y-%m-%d %H:%M:%S of btime, local time}"`.
   - `/proc/uptime` unreadable → `Result(False, "nothing on this machine could
     answer 'uptime'")`.
   - `btime` missing → the first part alone.
4. The paths are module constants `PROC_UPTIME` and `PROC_STAT`, so tests can
   point them at fake files.
5. Accepted risk: the week and year wording comes from procps-ng's source,
   not from a live run.

## Steps

1. **`src/omarchy_voice/tools.py`**:
   - add `PROC_UPTIME` and `PROC_STAT`, `_pretty_uptime()`, and
     `Executor._uptime()` next to `_battery`;
   - replace the `uptime` recipe (`tools.py:1625`) with `None`, and fill it in
     beside `battery` (`tools.py:1679`).

   **`tests/test_reach.py`**, `SystemQueryTests` gets:
   - `_pretty_uptime` cases: 412868.9 → `up 4 days, 18 hours, 41 minutes`;
     59 → `up 0 minutes`; 3600 → `up 1 hour`;
     90061 → `up 1 day, 1 hour, 1 minute`; 8 days → `up 1 week, 1 day`;
     400 days → `up 1 year, 5 weeks`;
   - a fake-`/proc` run through `executor.call("system_query", {"topic": "uptime"})`,
     with `TZ=Europe/London` and `time.tzset()`: btime 1790073007 →
     `booted:\n2026-09-22 11:30:07`;
   - an unreadable `/proc/uptime` gives today's failure message;
   - `_shell` is never called.

   → Verify: `nix develop -c pytest tests -q` all green.
   Mutation: removing the zero-unit omission fails a test.

2. **Live on p620:** `system_query uptime` through the real Executor matches
   `nix shell nixpkgs#procps -c sh -c 'uptime -p; uptime -s'` at the same
   minute.

   → Verify: identical lines, recorded here.

## Tests

- `nix develop -c pytest tests -q`: green, with the new tests included.
- `nix flake check`: all checks pass.

## Rollback

Revert the merge: one recipe line and two small functions in `tools.py`,
plus tests. Nothing is written anywhere.
