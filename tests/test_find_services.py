"""Know which user services and MCP servers exist, starting and reading nothing else (#83).

systemd is never asked for real: `subprocess.run` is replaced by a fake that
allows only `systemctl --user list-units|show`, records every argv, and
answers from fixtures. Anything else -- start, stop, restart, is-active, a
missing --user -- raises AssertionError, which `capabilities._run` does not
catch, so a forbidden call fails the test instead of reading as "could not ask
systemd". Every other way to start a process is patched to fail.

The unit files and ~/.claude.json are written under the throwaway HOME from
_isolated, with fake secrets ("canaries") that must never reach an answer.

Run with: python3 -m unittest discover -s tests
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice import capabilities
from omarchy_voice.claude_backend import _is_read
from omarchy_voice.config import Config
from omarchy_voice.tools import READ_ONLY_TOOLS, Executor, tools_for

CANARIES = ("FAKE-ENV-SECRET-83", "FAKE-HEADER-SECRET-83", "FAKE-URL-PW-83",
            "FAKE-ARG-TOKEN-83")
HOME = _isolated.ROOT
UNIT_DIR = HOME / ".config" / "systemd" / "user"
CLAUDE_JSON = HOME / ".claude.json"

MINE = """[Unit]
Description=Stream Deck controller

[Service]
Environment=API_KEY=FAKE-ENV-SECRET-83
ExecStart=/bin/x --token FAKE-ARG-TOKEN-83
"""

CLAUDE = {
    "mcpServers": {
        # Split so this file's source is not itself a URL with a password in it
        # (test_terminal's withhold_secrets scan of the repo).
        "github": {"type": "http", "url": "https://u:" + "FAKE-URL-PW-83@h/x",
                   "headers": {"Authorization": "Bearer FAKE-HEADER-SECRET-83"}},
        "local-tool": {"command": "/bin/tool", "args": ["--token", "FAKE-ARG-TOKEN-83"],
                       "env": {"KEY": "FAKE-ENV-SECRET-83"}},
        "Bearer FAKE-HEADER-SECRET-83": {"command": "x"},
    },
    "projects": {
        "/tmp/some/deep/proj": {
            "mcpServers": {"projsrv": {"type": "sse", "url": "https://h/FAKE-URL-PW-83"}},
        },
    },
}
VARIANT = {"mcpServers": {"variant-only": {"type": "ws", "url": "wss://FAKE-URL-PW-83@h"}}}
# The bad byte (the doubled comma) sits right next to a canary.
BAD_JSON = '{"mcpServers": {"x": {"env": {"K": "FAKE-ENV-SECRET-83"},,}}}'


def _unit(unit, load, active, sub, description):
    return {"unit": unit, "load": load, "active": active, "sub": sub,
            "description": description}


class Systemd:
    """The only systemctl there is: list-units and show, as the fixture says."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.fail = False
        self.units = [
            _unit("voxtype.service", "loaded", "active", "running",
                  "Voxtype speech-to-text daemon"),
            _unit("broken.service", "loaded", "failed", "failed", "Broken sync job"),
            _unit("bad-setting.service", "bad", "inactive", "dead", "Bad setting unit"),
            _unit("pipewire.service", "loaded", "inactive", "dead",
                  "PipeWire Multimedia Service"),
            _unit("tpl@.service", "loaded", "inactive", "dead", "Template thing"),
        ]
        self.show = {
            "notarealunit.service": {"LoadState": "not-found", "ActiveState": "inactive",
                                     "UnitFileState": "", "Description": "notarealunit.service"},
            "sleepy.service": {"LoadState": "loaded", "ActiveState": "inactive",
                               "UnitFileState": "disabled", "Description": "Sleepy packaged thing"},
        }

    def __call__(self, cmd, *_a, **_k):
        cmd = list(cmd)
        self.calls.append(cmd)
        if cmd[:2] != ["systemctl", "--user"] or len(cmd) < 3 \
                or cmd[2] not in ("list-units", "show"):
            raise AssertionError(f"forbidden process: {cmd}")
        if cmd[2] == "list-units":
            out = "" if self.fail else json.dumps(self.units)
        elif "-p" not in cmd:
            # What `show` without a property list really prints first.
            out = ("Environment=API_KEY=FAKE-ENV-SECRET-83\n"
                   "ExecStart={ path=/bin/x ; argv[]=/bin/x --token FAKE-ARG-TOKEN-83 }\n"
                   "LoadState=loaded\nActiveState=active\n")
        else:
            props = self.show.get(cmd[-1], {
                "LoadState": "loaded", "ActiveState": "active", "SubState": "running",
                "UnitFileState": "enabled", "Result": "success",
                "ActiveEnterTimestamp": "Thu 2026-09-24 10:00:00 BST",
                "Description": "whatever"})
            out = "".join(f"{k}={v}\n" for k, v in props.items())
            out += "Environment=API_KEY=FAKE-ENV-SECRET-83\n"  # only if asked for
        return subprocess.CompletedProcess(cmd, 0, out, "")


class Fixture(unittest.TestCase):
    def setUp(self):
        UNIT_DIR.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, HOME / ".config" / "systemd", ignore_errors=True)
        (UNIT_DIR / "mine.service").write_text(MINE)
        (UNIT_DIR / "tpl@.service").write_text("[Unit]\nDescription=Template thing\n")
        (UNIT_DIR / "voxtype.service").write_text(
            "[Unit]\nDescription=Voxtype speech-to-text daemon\n")
        CLAUDE_JSON.write_text(json.dumps(CLAUDE))
        self.addCleanup(CLAUDE_JSON.unlink, missing_ok=True)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        os.environ.pop("XDG_CONFIG_HOME", None)
        self.systemd = Systemd()
        self._guard()

    def _guard(self):
        def boom(*_a, **_k):
            raise AssertionError("a lookup tried to start a process")
        names = [(subprocess, "Popen")]
        names += [(os, n) for n in dir(os)
                  if n in ("system", "posix_spawn", "posix_spawnp", "fork")
                  or n.startswith(("exec", "spawn"))]
        patches = [mock.patch.object(m, n, boom) for m, n in names]
        patches.append(mock.patch.object(subprocess, "run", self.systemd))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def find(self, query, config=None):
        return Executor(config or Config()).call("find_service", {"query": query})

    def mcp(self, config=None):
        return Executor(config or Config()).call("system_query", {"topic": "mcp"})

    def assertClean(self, text):
        for canary in CANARIES:
            self.assertNotIn(canary, text)
        self.assertNotIn(str(HOME), text)
        self.assertNotIn("/tmp/some/deep", text)


class GuardTests(Fixture):
    def test_only_list_units_and_show_run(self):
        for verb in ("start", "stop", "restart", "is-active"):
            with self.subTest(verb=verb), self.assertRaises(AssertionError):
                subprocess.run(["systemctl", "--user", verb, "x.service"])
        for argv in (["systemctl", "list-units"], ["systemctl", "--user", "list-unit-files"]):
            with self.subTest(argv=argv), self.assertRaises(AssertionError):
                subprocess.run(argv)
        subprocess.run(["systemctl", "--user", "list-units"])
        subprocess.run(["systemctl", "--user", "show", "-p", "LoadState", "--", "x.service"])


class ReadOnlyTests(Fixture):
    def test_every_argv_is_a_read_with_a_fixed_property_list(self):
        for query in ("stream deck", "voxtype", "voxtype.service", "broken",
                      "notarealunit", "sleepy", "pipewire", "nothing at all"):
            self.find(query)
        self.mcp()
        self.assertTrue(self.systemd.calls)
        shows = 0
        for argv in self.systemd.calls:
            with self.subTest(argv=argv):
                self.assertEqual(argv[:2], ["systemctl", "--user"])
                self.assertIn(argv[2], ("list-units", "show"))
                if argv[2] == "show":
                    shows += 1
                    self.assertEqual(len(argv), 7)
                    self.assertEqual(argv[3], "-p")
                    self.assertIn(argv[4], (capabilities._SHOW_EXISTS,
                                            capabilities._SHOW_DETAIL))
                    self.assertEqual(argv[5], "--")
                    self.assertTrue(argv[6].endswith(".service"))
        self.assertGreaterEqual(shows, 3)

    def test_a_query_that_is_not_a_unit_name_is_never_shown(self):
        for query in ("--help", "x;rm", "-p", "a b c d"):
            with self.subTest(query=query):
                self.systemd.calls.clear()
                self.find(query)
                self.assertEqual([a for a in self.systemd.calls if a[2] == "show"], [])

    def test_the_detail_names_the_unit_from_the_index(self):
        self.find("voxtype")
        shows = [a for a in self.systemd.calls if a[2] == "show"]
        self.assertEqual(shows, [["systemctl", "--user", "show", "-p",
                                  capabilities._SHOW_DETAIL, "--", "voxtype.service"]])


class SecretTests(Fixture):
    def test_no_canary_or_path_in_any_answer(self):
        outputs = [self.find(q).output for q in (
            "stream deck", "mine", "mine.service", "voxtype", "broken", "tpl",
            "notarealunit", "sleepy", "controller", "token", "api key")]
        outputs += [Executor.describe("find_service", {"query": "stream deck"})]
        outputs.append(self.mcp().output)
        variant = Path(tempfile.mkdtemp(dir=HOME))
        (variant / ".claude.json").write_text(json.dumps(VARIANT))
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(variant)}):
            outputs.append(self.mcp().output)
        CLAUDE_JSON.write_text(BAD_JSON)
        outputs.append(self.mcp().output)
        CLAUDE_JSON.unlink()
        outputs.append(self.mcp().output)
        self.systemd.fail = True
        outputs.append(self.find("stream deck").output)
        for text in outputs:
            with self.subTest(text=text[:60]):
                self.assertClean(text)

    def test_mcp_servers_returns_three_short_strings_per_server(self):
        rows = capabilities.mcp_servers()
        self.assertIn(("user", "github", "http"), rows)
        self.assertIn(("user", "local-tool", "stdio"), rows)
        self.assertIn(("user", "(unnamed)", "stdio"), rows)
        self.assertIn(("local: proj", "projsrv", "sse"), rows)
        for row in rows:
            self.assertEqual(len(row), 3)
            self.assertClean(repr(row))


class FindingTests(Fixture):
    def test_an_own_unit_file_is_found_by_purpose_as_not_loaded(self):
        output = self.find("stream deck").output
        self.assertIn("mine.service — Stream Deck controller: not loaded (own)", output)

    def test_an_exact_name_comes_first_with_its_detail(self):
        for query in ("voxtype", "voxtype.service"):
            with self.subTest(query=query):
                output = self.find(query).output
                self.assertTrue(output.startswith(
                    "  voxtype.service — Voxtype speech-to-text daemon: active (running) (own)"),
                    output)
                self.assertIn("ActiveEnterTimestamp=Thu 2026-09-24 10:00:00 BST", output)

    def test_failed_and_bad_load_states_are_said(self):
        self.assertIn("broken.service — Broken sync job: FAILED", self.find("broken").output)
        self.assertIn("load state bad", self.find("bad setting").output)

    def test_templates_are_left_out(self):
        rows, ok = capabilities.find_services("template thing")
        self.assertTrue(ok)
        self.assertEqual(rows, [])
        self.assertNotIn("tpl@", self.find("template").output)

    def test_ties_go_to_own_then_the_shorter_name(self):
        (UNIT_DIR / "zz-sync-long.service").write_text("[Unit]\nDescription=Sync\n")
        self.systemd.units.append(_unit("a-sync.service", "loaded", "active", "running", "Sync"))
        self.systemd.units.append(_unit("b-sync-longer.service", "loaded", "active", "running",
                                        "Sync"))
        rows, _ = capabilities.find_services("sync")
        self.assertEqual([r["unit"] for r in rows][:3],
                         ["zz-sync-long.service", "a-sync.service", "broken.service"])

    def test_no_such_unit_is_said(self):
        self.assertIn("no user service named notarealunit", self.find("notarealunit").output)
        self.assertIn("no user service matches", self.find("x;rm").output)

    def test_an_unlisted_packaged_unit_exists(self):
        output = self.find("sleepy").output
        self.assertIn("sleepy.service — Sleepy packaged thing", output)
        self.assertIn("disabled", output)
        self.assertNotIn("no user service", output)

    def test_every_call_reads_again(self):
        self.assertIn("active (running)", self.find("voxtype").output)
        self.systemd.units[0] = _unit("voxtype.service", "loaded", "failed", "failed",
                                      "Voxtype speech-to-text daemon")
        self.assertIn("FAILED", self.find("voxtype").output)

    def test_systemd_not_answering_is_not_nothing_found(self):
        self.systemd.fail = True
        output = self.find("stream deck").output
        self.assertIn("could not ask systemd", output)
        self.assertIn("mine.service", output)
        for query in ("notarealunit", "pipewire"):
            with self.subTest(query=query):
                output = self.find(query).output
                self.assertIn("could not ask systemd", output)
                self.assertNotIn("no user service", output)


class McpTests(Fixture):
    def test_the_rows_and_what_they_are_not(self):
        output = self.mcp().output
        self.assertTrue(output.startswith(
            "Configured for your Claude Code. This assistant is not connected to these."))
        self.assertIn("  github (http) — user", output)
        self.assertIn("  local-tool (stdio) — user", output)
        self.assertIn("  projsrv (sse) — local: proj", output)
        self.assertIn("  (unnamed) (stdio) — user", output)
        self.assertIn("plugin-provided servers and Claude Desktop's are not listed", output)
        self.assertNotIn("ai-mirror", output)

    def test_claude_config_dir_is_where_it_looks(self):
        variant = Path(tempfile.mkdtemp(dir=HOME))
        (variant / ".claude.json").write_text(json.dumps(VARIANT))
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(variant)}):
            output = self.mcp().output
        self.assertIn("variant-only (ws) — user", output)
        self.assertNotIn("github", output)

    def test_desktop_control_is_the_assistants_own(self):
        output = self.mcp(Config(desktop_control=True)).output
        self.assertIn("(ai-mirror desktop control is this assistant's own)", output)

    def test_bad_json_and_a_missing_file(self):
        CLAUDE_JSON.write_text(BAD_JSON)
        self.assertIsNone(capabilities.mcp_servers())
        self.assertEqual(self.mcp().output, "could not read Claude Code's MCP configuration")
        CLAUDE_JSON.unlink()
        self.assertEqual(capabilities.mcp_servers(), [])
        self.assertEqual(self.mcp().output, "no MCP servers are configured for Claude Code")


class WiringTests(Fixture):
    def test_it_is_a_read_and_every_engine_gets_it(self):
        self.assertIn("find_service", READ_ONLY_TOOLS)
        self.assertIn("find_service", [s["name"] for s in tools_for(Config())])
        self.assertTrue(_is_read("mcp__omarchy__find_service"))

    def test_mcp_is_a_system_query_topic(self):
        schema = next(s for s in tools_for(Config()) if s["name"] == "system_query")
        self.assertIn("mcp", schema["input_schema"]["properties"]["topic"]["enum"])
        self.assertIn("which MCP servers are configured", schema["description"])

    def test_the_schema(self):
        schema = next(s for s in tools_for(Config()) if s["name"] == "find_service")
        self.assertEqual(schema["input_schema"]["required"], ["query"])
        self.assertFalse(schema["input_schema"]["additionalProperties"])
        self.assertIn("Starts and stops nothing", schema["description"])

    def test_deny_rules_still_apply(self):
        result = self.find("ssh")
        self.assertFalse(result.ok)
        self.assertIn("refused", result.output)

    def test_dry_run_still_looks(self):
        result = self.find("voxtype", Config(dry_run=True))
        self.assertTrue(result.ok)
        self.assertIn("voxtype.service", result.output)

    def test_describe(self):
        self.assertEqual(Executor.describe("find_service", {"query": "sync"}),
                         "find services: 'sync'")


class ManifestTests(unittest.TestCase):
    def test_the_manifest_points_at_both(self):
        with mock.patch.object(capabilities, "CACHE_DIR", Path(tempfile.mkdtemp())), \
             mock.patch.object(capabilities, "_run", return_value=""):
            text = capabilities.manifest(refresh=True)
        self.assertIn("call find_service before saying whether", text)
        self.assertIn("system_query mcp", text)


if __name__ == "__main__":
    unittest.main()
