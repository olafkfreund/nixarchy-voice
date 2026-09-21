---
status: approved
issue: 58
spec: spec/2026-09-21-58-return-the-image.md
---

# Plan: Let the calling agent see the screen

## The approved decisions, carried over

nixarchy-voice is an MCP server, so the model is the caller's. Today no caller
can see the screen: `_tool_read_screen` (`tools.py:2434`) is the only way to look
at anything and it returns tesseract's text, and every MCP return path is
`TextContent` (`mcp_server.py:158,162,164,169`).

1. **A new `screenshot` tool**, not an image added to `read_screen`. Measured on
   one 2560x1440 screen: text is 753 chars, **~188 tokens**; the image is **~1843
   tokens** at Claude's 1568-long-edge cap. ~10x, so the caller opts in.
2. **No downscaling on our side.** Clients cap independently, so scaling first
   double-degrades. Send the target at native resolution.
3. **`target="active"` by default**, deliberately unlike `read_screen`, which maps
   `"screen"` to the focused monitor (`tools.py:2155`). A monitor arrives at the
   cap with 13 px text reduced to 8 px; a window is usually under the cap and
   arrives untouched. Documented in the tool description.
4. **PNG**, matching ai-mirror (`mcp.py:122`) and lossless where it matters most.
   PPM stays for OCR (#26 measured it fastest into tesseract).
5. **The same privacy guard, in the same position, before capture** — the existing
   expression from `_ocr_region` (2167) and `_ocr_words` (2278):
   `self._screen_unavailable() or self._capture_refused(geometry)`. No new guard
   and no second path: a screenshot of a password field discloses more than OCR
   of it.

## Steps

1. **`tools.py:187` `Result`**: add `image: bytes | None = None`. `as_tool_result`
   is untouched, so every existing caller and test is unaffected by default.
   → verify by the full suite passing with no edits to existing tests.

2. **`tools.py`**: add `_capture_png(self, geometry) -> tuple[bytes | None, str]`
   next to `_ocr_region` (2162). Runs `grim -t png -g <geometry> -`, traced as a
   `SUBPROCESS` phase like every other spawn (#39). Returns the bytes, or None
   and a reason.
   → verify by a unit test with a stubbed `subprocess.run`.

   **Revised during implementation.** This step said "through `self._shell`".
   It cannot go through `_shell`: that delegates to `_spawn`, which opens the
   process with `text=True` and returns a decoded `str`, so a PNG would come
   back mangled. The step wanted the *trace phase*, not that particular helper
   — so the phase is marked by hand with `self.trace.mark(trace_mod.SUBPROCESS,
   "grim")` around a binary `subprocess.run`, which is the shape `_ocr_region`
   already uses for its own capture. Same tracing, no decode.

3. **`tools.py`**: add `_tool_screenshot(self, target="active") -> Result`.
   Order is fixed and matters: `_target_geometry(target)`, then the guard
   expression from decision 5, then capture. Returns
   `Result(True, "<what was captured>", image=png)`.
   → verify by unit tests: a permitted target returns bytes; each guard refuses.

4. **`tools.py:590` `TOOL_SCHEMAS`**: add the `screenshot` schema. Also add
   `screenshot` to `READ_ONLY_TOOLS` (47) and a `screenshot` case to the
   confirm-gate description builder — **both beyond this step as written**.
   The first because the tool only looks, and `read_screen` is already there,
   so without it `--dry-run` planning cannot see the screen; the second because
   the builder's fallback is `f'{name} {args}'`, which would show the user a
   raw dict instead of a sentence. `target` is a
   string with the same values `read_screen` accepts. The description states the
   default is the active window and **why**, and states the token cost against
   `read_screen`'s so a caller can choose.
   → verify by `tools_for(config)` including it, and by the existing off-list
   mechanism (1246) being able to disable it.

5. **`mcp_server.py:168`**: in `call_tool`, when `result.image` is set, return
   `[TextContent(...), ImageContent(type="image", data=b64, mimeType="image/png")]`.
   Every other path is unchanged.
   → verify by a unit test asserting both content types and the mimeType.

6. **Tests** in the repo's existing style.
   The guard tests assert **no capture subprocess is spawned**, not merely that
   the message is a refusal: a guard that refuses after grimming would pass a
   message-level assertion while having already taken the picture.
   → verify by the full suite green.

   **Revised during implementation.** This step said to put the tests "on the
   `Base` class so `guard.arm()` and the isolated `XDG_RUNTIME_DIR` apply".
   There is no such class and no `arm()` anywhere in `src/` or `tests/` — the
   reference was carried over in error. The substance of the step survives and
   is what was built: `tests/test_screenshot.py` stubs the two guards and
   `_target_geometry` directly and asserts `run.assert_not_called()` on each
   refusal path, and the `ImageContent` tests were added to `tests/test_mcp.py`
   driven over the **real protocol** (the `create_connected_server_and_client_session`
   harness `GateReleaseTests` uses) rather than by calling the handler — for
   that class's own stated reason: a handler that builds content the transport
   then drops would pass a direct call.

7. `nix flake check`.
   → verify by `all checks passed!`, run before the PR rather than after, and in
   the sandbox rather than trusting a local green (this has bitten three times:
   `hyprctl` and `pw-dump` exist here and not there).

8. **Manual**: call `screenshot` from an MCP client, confirm the image renders and
   its text is legible at the client's own scaling.
   → verify by the result recorded in this file.

## Note, not a step

The spoken voice path has no use for an image. It reads `result.output`, so it
gets the text line and drops the bytes — graceful, and not worth special-casing.
If it ever matters, `tools_for`'s off-list (1246) already disables the tool by
config.

## Tests

```
pytest -q                       # full suite, expect green with no existing test edited
nix flake check                 # expect "all checks passed!"
```

## Rollback

`git revert` the implementation commit. No state, no migration, no config
default changes, and `read_screen` was never touched — a revert restores exactly
today's behaviour.
