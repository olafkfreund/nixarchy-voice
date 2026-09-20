---
status: draft
issue: 46
spec: spec/2026-09-20-46-sensitive-windows.md
---

# Plan: refuse the capture, not the window, and say which kind

## The approved decisions, in full

Implementable without opening the intent or the spec.

**The problem.** Nothing declines to capture based on what is in a window, so
`read_screen`, `click_text` and `wait_for(text)` will OCR a 1Password vault, a
`pinentry` prompt or a bank dashboard and hand the model **the text**. Text is
the worse half: a picture must be read, extracted text is already quotable and
already in the transcript. 1Password is installed here and `gcr-prompter` is in
the store.

Decisions carried over:

1. **Refuse, do not redact.** One branch, and it matches what this codebase
   chose twice already: say so rather than hand back something.
2. **The unit is the capture rectangle, not the focused window.** Every mapped,
   non-hidden window on a visible workspace whose rect intersects the capture
   rect is checked. A password manager *beside* the thing being read is caught.
   This is the hard half and it is free: `hyprctl clients` already returns
   `at`, `size`, `workspace`, `mapped` and `hidden`, `_visible_workspaces()`
   exists at `tools.py:1977`, and the capture path already makes that query.
3. **The guard sits at the capture seam, not per tool.** `click_text` and
   `wait_for(text)` inherit it. Refusing `read_screen` while `click_text` OCRs
   the same pixels would leak the same text through word boxes.
4. **Class AND title.** Measured here, a Gmail window reports
   `class='chrome-fmgjjmmmlfnkbppncabfkddbjimcfncm-Profile_4'` — an opaque
   extension id. Only the title identifies it, and this desktop runs most things
   as web apps, so class-only would miss the common case.
5. **The refusal names a category, never the title**, and names the way
   forward. Titles here carry an inbox count, an email address and what is being
   watched. A monitor-wide refusal with no route turns one blocked read into a
   stuck task, and `target` already takes a window address.
6. **A hard-coded floor plus user additions**, in the shape `deny_patterns` /
   `deny_patterns_replace` already use (`config.py:173,389,391`).
7. **It says it is a heuristic.** A blocklist misses and misfires, and an agent
   that believes the screen is safe behaves differently from one that knows it
   is best-effort.
8. `mic_in_use` is out of scope; its own issue.

## Steps

**1. `config.py` — the patterns, beside `DEFAULT_DENY`.**
`DEFAULT_SENSITIVE_CLASSES` and `DEFAULT_SENSITIVE_TITLES`, seeded from
omarchy-hermes-companion `daemon/perception.py:20-33` (MIT, attributed in a
comment). Two config fields mirroring `deny_patterns` /
`deny_patterns_replace` (`config.py:389,391`), so additions extend the floor by
default and replacing it is explicit.
→ verify by a unit test: defaults present, additions extend, replace mode
replaces, and an invalid regex is reported rather than crashing a capture.

**2. `tools.py` — the matcher.**
`_sensitive_kind(cls, title) -> str | None`, returning a short category
(`"a password manager"`, `"a credential prompt"`, `"a private browsing window"`,
`"a banking page"`) or None. It **must not** return, log or embed the matched
text.
→ verify by a unit test per seeded pattern, plus one asserting the returned
string contains neither the class nor the title.

**3. `tools.py` — which windows a rectangle contains.**
`_windows_in(geometry) -> list[dict]`: parse the `"x,y WxH"` string the capture
path already builds, ask `_query_json("clients")` (`tools.py:2517`), keep
entries that are mapped, not hidden, on a workspace in `_visible_workspaces()`
(`tools.py:1977`), and whose `at`/`size` rect intersects.
→ verify by unit tests over fabricated client lists: intersecting, adjacent,
non-visible workspace, other monitor, hidden, unmapped.

**4. `tools.py` — the guard at the seam.**
`_capture_refused(geometry) -> str | None`, called from `_ocr_region`
(`tools.py:1992`) and `_ocr_words` (`tools.py:2083`) immediately beside the
existing `_screen_unavailable()` (`tools.py:2031`) call, which is already where
"do not shoot this" lives. Returns the refusal sentence or None.
→ verify by a test that both seams refuse, and that `click_text` and
`wait_for(text)` therefore refuse without either being touched — that is the
point of the seam.

**5. The refusal text.**
Names the category and the route, never the title:
`"<kind> is visible in that area, so it was not read. Read one window instead —
read_screen with target set to a window address from hypr_query(clients) — or
ask the user to close it."`
→ verify by reading it against what the code does, and a test asserting no
title substring appears.

**6. Say it is a heuristic**, wherever a reader would look: the tool description
for `read_screen` if it is short enough to earn the words, otherwise a comment
at the matcher. One sentence: this is best-effort, it misses things and it
misfires.
→ verify by reading it.

## Tests

```
nix develop -c python3 -m unittest discover -s tests -v
nix flake check
```

Expected green, including the new cases. No test may reach the real desktop:
client lists are fabricated, as `test_reach.py` and `test_policy.py` already do.

## Rollback

`git revert` the implementation commits. The guard is additive — two config
fields and one check at two call sites. Nothing outside the repository changes.
If the floor proves too aggressive before a revert is warranted,
`sensitive_patterns_replace` with an empty list disables it, which is itself a
reason the replace flag exists.
