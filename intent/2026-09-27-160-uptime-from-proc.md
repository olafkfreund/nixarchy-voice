---
status: draft
issue: 160
author: olafkfreund
---

# Intent: "how long has it been up" gets an answer on every machine

## Problem

`system_query` with `topic = "uptime"` runs `uptime -p` and `uptime -s`
(`src/omarchy_voice/tools.py:1625`). Those flags belong to procps' `uptime`.
On p620 the `uptime` on PATH is coreutils 9.11's, which rejects both:

```
$ uptime -p
uptime: invalid option -- 'p'
$ readlink -f $(command -v uptime)
/nix/store/…-coreutils-9.11/bin/coreutils
```

So every "how long has the machine been up" or "when did it boot" gets
`nothing on this machine could answer 'uptime'`, although the answer is one
read of a file the kernel keeps. It was found live-testing #158, where an
action step using it stopped with that error.

Which `uptime` wins depends on PATH order and on which packages a machine
happens to have, so the same question works on one host and not another,
with nothing in the config to explain why.

## Proposed outcome

- "How long has it been up" and "when did it boot" answer on p620, razer
  and any Linux machine, whichever `uptime` (if any) is installed.
- The answer reads as it does today: a human duration ("up 3 days, 4 hours")
  and the boot time.

## Affected users and systems

- `system_query` `uptime` only, in `src/omarchy_voice/tools.py`, plus its tests.
- Every engine and the MCP server, since they share the tool.
- p620 (broken today). razer and others, whichever `uptime` they have.

## Constraints

- No new dependency and no binary: read `/proc`, the way `battery` already
  reads `/sys` (`tools.py:1679`).
- Read-only, as the whole `system_query` table is.
- The output shape the model sees stays close to today's, so nothing that
  reads it has to change.

## Open questions

1. **Output format.** Recommended: keep today's two labelled lines. The first
   is procps' `up 3 days, 4 hours, 12 minutes` style, computed from
   `/proc/uptime`. The second is `booted: 2026-09-23 12:17:03`, computed from
   `/proc/stat` `btime` in local time. Any objection to matching procps'
   wording rather than inventing a new one?
