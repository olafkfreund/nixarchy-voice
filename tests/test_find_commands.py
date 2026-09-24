"""Find an installed command-line tool by name or purpose, running nothing (#82).

Every PATH directory, man page and tldr page here is written by the test, and
PATH, MANPATH and the tldr directory are pointed at them, so nothing from the
real machine is read. Every way to start a process or open a socket is patched
to fail for the lookups: finding a tool must never become running one.

Run with: python3 -m unittest discover -s tests
"""

import gzip
import os
import socket
import subprocess
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice import capabilities
from omarchy_voice.claude_backend import _is_read
from omarchy_voice.config import Config
from omarchy_voice.tools import READ_ONLY_TOOLS, Executor, tools_for

MAN_VCONV = """.TH VCONV 1
.SH NAME
vconv \\- Video conversion tool
.SH SYNOPSIS
.B vconv
[\\fI\\,OPTION\\/\\fR]... \\fIINPUT\\fR \\fIOUTPUT\\fR
.SH DESCRIPTION
Converts things.
"""
MAN_MDOC = """.Dd 2024
.Dt MDOCTOOL 8
.Sh NAME
.Nm mdoctool
.Nd manage the document store
.Sh SYNOPSIS
.Nm
"""
TLDR_VPLAY = """# vplay

> Play media files.
> See also: `vconv`.
> More information: <https://example.org>.

- Convert a video before playing it:

`vplay --convert {{path/to/video}}`

- Play a file:

`vplay {{path/to/file}}`
"""
TLDR_VPLAY_COMMON = "# vplay\n\n> The common page, which linux beats.\n"
TLDR_MDOC = "# mdoctool\n\n> Keep documents in order.\n"
TLDR_MOG = """# mog

> This command is an alias of `vconv mog`.

- View documentation for the original command:

`tldr vconv mog`
"""
TLDR_VCONV_MOG = """# vconv mog

> Resize and transform images in place.

- Resize every PNG:

`mog -resize 50% *.png`
"""
# Twelve commands with the same description: ties and the limit of 8.
FROB = [f"frob{'x' * i}" for i in range(10)]           # man pages
FROB_TLDR = ["frobtldrlong", "frobtldrlongest"]        # tldr pages


def _exe(path: Path, mode: int = 0o755) -> None:
    path.write_text("#!/bin/sh\n")
    path.chmod(mode)


class Fixture(unittest.TestCase):
    """A fake PATH, MANPATH and tldr cache, and nothing that runs or connects."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = root = Path(tmp.name).resolve()
        self.bin1, self.bin2, bin3 = root / "bin1", root / "bin2", root / "bin3"
        store = root / "store" / "bin"
        for d in (self.bin1, self.bin2, bin3, store):
            d.mkdir(parents=True)
        for name in ("vconv", "vplay", "jqx", "mdoctool", "mog", "omarchy-foo",
                     ".hidden", *FROB, *FROB_TLDR):
            _exe(self.bin1 / name)
        _exe(self.bin1 / "notexec", 0o644)
        _exe(self.bin2 / "vconv")
        _exe(store / "storeonly")
        _exe(bin3 / "retargeted")
        self.bin3 = bin3
        self.profile = root / "profile"
        self.profile.symlink_to(self.bin1)
        # A PATH entry that starts with /nix/store/ and still resolves here:
        # the filter is on the string, and this cannot be written under the
        # real store.
        store_entry = "/nix/store/../.." + str(store)

        man = root / "man"
        (man / "man1").mkdir(parents=True)
        (man / "man8").mkdir()
        with gzip.open(man / "man1" / "vconv.1.gz", "wt") as f:
            f.write(MAN_VCONV)
        (man / "man8" / "mdoctool.8").write_text(MAN_MDOC)
        for name in FROB:
            (man / "man1" / f"{name}.1").write_text(
                f".SH NAME\n{name} \\- Frobnicate widgets\n")

        self.tldr = tldr = root / "tldr"
        (tldr / "linux").mkdir(parents=True)
        (tldr / "common").mkdir()
        (tldr / "linux" / "vplay.md").write_text(TLDR_VPLAY)
        (tldr / "common" / "vplay.md").write_text(TLDR_VPLAY_COMMON)
        (tldr / "linux" / "mdoctool.md").write_text(TLDR_MDOC)
        (tldr / "common" / "mog.md").write_text(TLDR_MOG)
        (tldr / "common" / "vconv-mog.md").write_text(TLDR_VCONV_MOG)
        for name in FROB_TLDR:
            (tldr / "common" / f"{name}.md").write_text(
                f"# {name}\n\n> Frobnicate widgets.\n")

        path = os.pathsep.join([str(self.bin1), str(self.bin2), store_entry,
                                str(self.profile)])
        for patcher in (mock.patch.dict(os.environ, {"PATH": path, "MANPATH": str(man)}),
                        mock.patch.object(capabilities, "TLDR_PAGES", tldr),
                        mock.patch.object(capabilities, "_PATH_COMMANDS", None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self._forbid()

    def _forbid(self):
        """Finding a tool must never run one, or reach the network."""
        def boom(*_a, **_k):
            raise AssertionError("a lookup tried to run a process or open a socket")
        names = [(socket, "socket"), (socket, "create_connection"),
                 (urllib.request, "urlopen"), (subprocess, "Popen"),
                 (subprocess, "run"), (capabilities, "_run")]
        names += [(os, n) for n in dir(os)
                  if n in ("system", "posix_spawn", "posix_spawnp", "fork")
                  or n.startswith(("exec", "spawn"))]
        for module, name in names:
            patcher = mock.patch.object(module, name, boom)
            patcher.start()
            self.addCleanup(patcher.stop)

    def names(self, query, **kw):
        return [name for _, name, _ in capabilities.find_commands(query, **kw)]


class ParsingTests(Fixture):
    def test_descriptions_come_from_man_mdoc_and_tldr(self):
        index = capabilities.path_commands()
        self.assertEqual(index["vconv"]["desc"], "Video conversion tool")
        self.assertIn("manage the document store", index["mdoctool"]["desc"])
        self.assertIn("Play media files.", index["vplay"]["desc"])

    def test_tldr_leaves_out_see_also_and_more_information(self):
        desc = capabilities.path_commands()["vplay"]["desc"]
        self.assertNotIn("See also", desc)
        self.assertNotIn("More information", desc)

    def test_linux_beats_common(self):
        self.assertNotIn("common page", capabilities.path_commands()["vplay"]["desc"])

    def test_an_alias_page_is_followed_one_hop(self):
        row = capabilities.path_commands()["mog"]
        self.assertIn("Resize and transform images", row["desc"])
        self.assertEqual(row["examples"][0][1], "mog -resize 50% *.png")

    def test_tldr_and_man_are_joined(self):
        desc = capabilities.path_commands()["mdoctool"]["desc"]
        self.assertIn("Keep documents in order.", desc)
        self.assertIn("manage the document store", desc)

    def test_examples_are_parsed(self):
        examples = capabilities.path_commands()["vplay"]["examples"]
        self.assertEqual(examples[0], ("Convert a video before playing it",
                                       "vplay --convert {{path/to/video}}"))
        self.assertEqual(len(examples), 2)

    def test_an_exact_man_only_command_shows_its_synopsis(self):
        output = Executor(Config()).call("find_command", {"query": "vconv"}).output
        self.assertIn("vconv [OPTION]... INPUT OUTPUT", output)


class FilterTests(Fixture):
    def test_what_is_left_out(self):
        index = capabilities.path_commands()
        for name in ("notexec", "omarchy-foo", ".hidden", "storeonly"):
            with self.subTest(name=name):
                self.assertNotIn(name, index)
        self.assertIn("vconv", index)

    def test_the_first_path_directory_wins(self):
        self.assertEqual(capabilities.path_commands()["vconv"]["path"],
                         str(self.bin1 / "vconv"))


class ExactNameTests(Fixture):
    def test_an_undescribed_command_is_found_by_its_name(self):
        self.assertEqual(self.names("jqx")[0], "jqx")

    def test_the_exact_name_beats_descriptions_that_mention_it(self):
        (self.tldr / "common" / "vplay.md").write_text("# vplay\n\n> jqx jqx jqx.\n")
        (self.tldr / "linux" / "vplay.md").unlink()
        self.assertEqual(self.names("jqx")[0], "jqx")

    def test_an_undescribed_command_is_not_found_by_purpose(self):
        self.assertNotIn("jqx", self.names("convert a video"))


class RankingTests(Fixture):
    def test_the_purpose_finds_the_tool(self):
        # "convert" meets "conversion" through the prefix, and vplay's examples
        # say "Convert a video" but are never scored.
        found = self.names("convert a video")
        self.assertEqual(found[0], "vconv")
        self.assertNotIn("vplay", found)
        self.assertEqual(self.names("convert"), ["vconv"])

    def test_ties_go_to_tldr_then_the_shorter_name(self):
        found = self.names("frobnicate widgets")
        self.assertEqual(found[:3], ["frobtldrlong", "frobtldrlongest", "frob"])

    def test_eight_are_returned(self):
        self.assertEqual(len(self.names("frobnicate widgets")), 8)


class CloseSpellingTests(Fixture):
    def test_a_near_miss_offers_the_real_name(self):
        self.assertEqual(self.names("vcon"), [])
        self.assertIn("vconv", capabilities.close_commands("vcon"))
        output = Executor(Config()).call("find_command", {"query": "vcon"}).output
        self.assertIn("not installed under that name", output)
        self.assertIn("vconv", output)


class StalenessTests(Fixture):
    def counted(self):
        spy = mock.patch.object(capabilities, "_build_path_commands",
                                wraps=capabilities._build_path_commands)
        built = spy.start()
        self.addCleanup(spy.stop)
        return built

    def test_nothing_changed_nothing_rebuilt(self):
        built = self.counted()
        capabilities.path_commands()
        capabilities.path_commands()
        self.assertEqual(built.call_count, 1)

    def test_a_new_install_is_found_without_a_restart(self):
        capabilities.path_commands()
        _exe(self.bin2 / "newtool")
        os.utime(self.bin2, ns=(1, 1))
        self.assertIn("newtool", capabilities.path_commands())

    def test_a_retargeted_profile_rebuilds(self):
        built = self.counted()
        capabilities.path_commands()
        self.profile.unlink()
        self.profile.symlink_to(self.bin3)
        self.assertIn("retargeted", capabilities.path_commands())
        self.assertEqual(built.call_count, 2)


class WiringTests(Fixture):
    def test_it_is_a_read_and_every_engine_gets_it(self):
        self.assertIn("find_command", READ_ONLY_TOOLS)
        self.assertIn("find_command", [s["name"] for s in tools_for(Config())])
        self.assertTrue(_is_read("mcp__omarchy__find_command"))

    def test_deny_rules_still_apply(self):
        result = Executor(Config()).call("find_command", {"query": "ssh"})
        self.assertFalse(result.ok)
        self.assertIn("refused", result.output)

    def test_dry_run_still_looks(self):
        result = Executor(Config(dry_run=True)).call("find_command",
                                                      {"query": "convert a video"})
        self.assertTrue(result.ok)
        self.assertIn("vconv — Video conversion tool", result.output)

    def test_an_exact_hit_shows_its_examples_and_no_path(self):
        output = Executor(Config()).call("find_command", {"query": "vplay"}).output
        self.assertTrue(output.startswith("  vplay — Play media files."))
        self.assertIn("vplay --convert {{path/to/video}}", output)
        for query in ("vplay", "vconv", "jqx", "frobnicate", "vcon", "mog"):
            with self.subTest(query=query):
                output = Executor(Config()).call("find_command", {"query": query}).output
                self.assertNotIn(str(self.root), output)

    def test_undescribed_is_said(self):
        output = Executor(Config()).call("find_command", {"query": "jqx"}).output
        self.assertIn("jqx — (no description)", output)


class ManifestTests(unittest.TestCase):
    def test_the_manifest_points_at_find_command(self):
        with mock.patch.object(capabilities, "CACHE_DIR", Path(tempfile.mkdtemp())), \
             mock.patch.object(capabilities, "_run", return_value=""):
            text = capabilities.manifest(refresh=True)
        self.assertIn("find_command", text)


if __name__ == "__main__":
    unittest.main()
