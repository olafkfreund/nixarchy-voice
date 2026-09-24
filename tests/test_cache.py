"""The manifest and command-index cache (#103).

Writing an entry used to delete every other entry of its kind, and the key used
this file's mtime -- which is 1 for every file in the Nix store, so a rebuild
that changed the template kept serving the old manifest. Now the key is the
file's content plus the resolved stub and bindings paths, the 8 most recently
used entries per kind are kept, and writes are atomic.

Every test works in its own temp CACHE_DIR with `_run` mocked, so nothing
shells out and the real ~/.cache/omarchy-voice is never touched.

Run with: python3 -m unittest discover -s tests
"""

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice import capabilities

ONE_S = 1_000_000_000  # 1970-01-01 00:00:01, every Nix store file's mtime


def _set_mtime(path: Path, seconds: float) -> None:
    ns = int(seconds * ONE_S)
    os.utime(path, ns=(ns, ns))


class CacheCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cache = self.tmp / "cache"
        self.cache.mkdir()
        self.omarchy = self.tmp / "omarchy"
        self.omarchy.mkdir()
        for target, kwargs in [
            ("CACHE_DIR", {"new": self.cache}),
            ("OMARCHY_PATH", {"new": self.omarchy}),
            ("_run", {"return_value": ""}),
            ("_stub_path", {"return_value": None}),
        ]:
            patcher = mock.patch.object(capabilities, target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(capabilities, "essentials", wraps=capabilities.essentials)
        self.essentials = patcher.start()
        self.addCleanup(patcher.stop)

    def keys(self, *keys):
        return mock.patch.object(capabilities, "_cache_key", side_effect=list(keys))

    def manifests(self):
        return sorted(p.name for p in self.cache.glob("manifest-*.md"))

    def indexes(self):
        return sorted(p.name for p in self.cache.glob(f"*-{capabilities.COMMAND_INDEX}"))


class T1TwoKeysCoexist(CacheCase):
    def test_manifest(self):
        a, b = "a" * 16, "b" * 16
        with self.keys(a, b, a):
            capabilities.manifest()
            capabilities.manifest()
            capabilities.manifest()
        self.assertEqual(self.manifests(), [f"manifest-{a}.md", f"manifest-{b}.md"])
        self.assertEqual(self.essentials.call_count, 2, "the third call must be a hit")

    def test_command_index(self):
        a, b = "a" * 16, "b" * 16
        with self.keys(a, b, a):
            capabilities.command_index()
            capabilities.command_index()
            capabilities.command_index()
        self.assertEqual(self.indexes(), [f"{a}-{capabilities.COMMAND_INDEX}",
                                          f"{b}-{capabilities.COMMAND_INDEX}"])
        self.assertEqual(capabilities._run.call_count, 2, "the third call must be a hit")


class T2StaleUpgradeRebuilds(CacheCase):
    def _copy(self, name: str) -> Path:
        path = self.tmp / name / "capabilities.py"
        path.parent.mkdir()
        shutil.copyfile(capabilities.__file__, path)
        _set_mtime(path, 1)
        return path

    def _change_template(self, path: Path) -> None:
        text = path.read_text()
        line = "Every installed application can be opened by the name a person uses"
        self.assertIn(line, text)
        path.write_text(text.replace(line, line.replace("application", "app"), 1))
        _set_mtime(path, 1)

    def test_a_changed_template_changes_the_key(self):
        a = self._copy("a")
        with mock.patch.object(capabilities, "__file__", str(a)):
            k1 = capabilities._cache_key()
        b = self._copy("b")
        with mock.patch.object(capabilities, "__file__", str(b)):
            self.assertEqual(capabilities._cache_key(), k1,
                             "identical checkouts must share a key")
        self._change_template(a)
        with mock.patch.object(capabilities, "__file__", str(a)):
            self.assertNotEqual(capabilities._cache_key(), k1,
                                "a changed template at mtime 1 must move the key")

    def test_a_rebuilt_stub_or_bindings_changes_the_key(self):
        targets = []
        for name in ("stub1.lua", "stub2.lua"):
            target = self.tmp / name
            target.write_text("---@class HL.DspNamespace\n")
            _set_mtime(target, 1)
            targets.append(target)
        link = self.tmp / "hl.meta.lua"
        link.symlink_to(targets[0])
        with mock.patch.object(capabilities, "_stub_path", return_value=link):
            k1 = capabilities._cache_key()
            link.unlink()
            link.symlink_to(targets[1])
            self.assertNotEqual(capabilities._cache_key(), k1,
                                "a rebuilt stub at the same mtime must move the key")

        dirs = []
        for name in ("bindings1", "bindings2"):
            d = self.tmp / name
            d.mkdir()
            _set_mtime(d, 1)
            dirs.append(d)
        (self.omarchy / "default/hypr").mkdir(parents=True)
        bindings = self.omarchy / "default/hypr/bindings"
        bindings.symlink_to(dirs[0])
        k1 = capabilities._cache_key()
        bindings.unlink()
        bindings.symlink_to(dirs[1])
        self.assertNotEqual(capabilities._cache_key(), k1,
                            "a rebuilt Omarchy tree at the same mtime must move the key")

    def test_end_to_end_the_changed_copy_rebuilds(self):
        a = self._copy("a")
        with mock.patch.object(capabilities, "__file__", str(a)):
            capabilities.manifest()
        self._change_template(a)
        with mock.patch.object(capabilities, "__file__", str(a)):
            capabilities.manifest()
        self.assertEqual(self.essentials.call_count, 2)
        self.assertEqual(len(self.manifests()), 2)


class T3BoundEvictsLeastRecentlyUsed(CacheCase):
    def test_lru(self):
        other = self.cache / f"x-{capabilities.COMMAND_INDEX}"
        other.write_text("x\ty")
        _set_mtime(other, 0.5)  # older than everything: a glob of "*" would prune it
        entries = [self.cache / f"manifest-{i:02d}.md" for i in range(1, 13)]
        for i, entry in enumerate(entries, start=1):
            capabilities._store(entry, f"text {i}", "manifest-*.md")
            _set_mtime(entry, i)
            if i == 6:
                os.utime(entries[0])  # a hit on the first entry
        self.assertEqual(self.manifests(), sorted([entries[0].name] + [e.name for e in entries[5:]]))
        self.assertEqual(len(self.manifests()), capabilities.CACHE_KEEP)
        self.assertTrue(other.exists(), "pruning manifests must not touch the index")


class T4HitRefreshesMtime(CacheCase):
    def test_manifest(self):
        entry = self.cache / f"manifest-{'a' * 16}.md"
        entry.write_text("CACHED")
        _set_mtime(entry, 1)
        with self.keys("a" * 16):
            self.assertEqual(capabilities.manifest(), "CACHED")
        self.essentials.assert_not_called()
        self.assertGreater(entry.stat().st_mtime_ns, ONE_S)

    def test_command_index(self):
        entry = self.cache / f"{'a' * 16}-{capabilities.COMMAND_INDEX}"
        entry.write_text("omarchy x\tdoes x")
        _set_mtime(entry, 1)
        with self.keys("a" * 16):
            self.assertEqual(capabilities.command_index(), [("omarchy x", "does x")])
        capabilities._run.assert_not_called()
        self.assertGreater(entry.stat().st_mtime_ns, ONE_S)

    def test_a_read_only_dir_still_hits(self):
        entry = self.cache / f"manifest-{'a' * 16}.md"
        entry.write_text("CACHED")
        with self.keys("a" * 16), \
             mock.patch.object(capabilities.os, "utime", side_effect=PermissionError):
            self.assertEqual(capabilities.manifest(), "CACHED")
        self.essentials.assert_not_called()


class T5AtomicWrite(CacheCase):
    def test_no_partial_file(self):
        cached = self.cache / "manifest-a.md"
        cached.write_text("old")
        new = "new text " * 1000
        real_replace = os.replace
        calls = []

        def replace(src, dst):
            calls.append((src, dst))
            self.assertEqual(cached.read_text(), "old", "cached must not change before the swap")
            name = Path(src).name
            self.assertTrue(name.startswith(".") and name.endswith(".tmp"), name)
            self.assertEqual(Path(src).read_text(), new)
            real_replace(src, dst)

        with mock.patch.object(capabilities.os, "replace", side_effect=replace):
            capabilities._store(cached, new, "manifest-*.md")
        self.assertEqual(cached.read_text(), new)
        self.assertEqual(len(calls), 1)
        self.assertEqual(list(self.cache.glob(".*.tmp")), [])


class T6CommandIndexSamePolicy(CacheCase):
    def test_bound(self):
        manifest = self.cache / "manifest-m.md"
        manifest.write_text("M")
        _set_mtime(manifest, 0.5)
        keys = [f"{i:016d}" for i in range(1, 10)]
        for i, key in enumerate(keys, start=1):
            with self.keys(key):
                capabilities.command_index()
            _set_mtime(self.cache / f"{key}-{capabilities.COMMAND_INDEX}", i)
        self.assertEqual(self.indexes(), [f"{k}-{capabilities.COMMAND_INDEX}" for k in keys[1:]])
        self.assertTrue(manifest.exists(), "pruning indexes must not touch the manifest")

        with self.keys(keys[1]):
            capabilities.command_index()
        self.assertEqual(capabilities._run.call_count, 9, "a hit must not shell out")
        self.assertGreater(
            (self.cache / f"{keys[1]}-{capabilities.COMMAND_INDEX}").stat().st_mtime_ns, 9 * ONE_S)


class T7UpgradeNeedsNoClearing(CacheCase):
    def _old_key(self) -> str:
        # The formula before #103, inlined.
        stamp = json.dumps(capabilities.system_versions(), sort_keys=True)
        for path in [self.omarchy / "default/hypr/bindings", Path(capabilities.__file__)]:
            try:
                stamp += str(path.stat().st_mtime_ns)
            except OSError:
                pass
        return hashlib.sha256(stamp.encode()).hexdigest()[:16]

    def test_old_entry_is_not_served_and_is_pruned(self):
        stale = self.cache / f"manifest-{self._old_key()}.md"
        stale.write_text("STALE")
        _set_mtime(stale, 1)
        self.assertNotEqual(capabilities.manifest(), "STALE")
        self.essentials.assert_called_once()
        with self.keys(*[f"{i:016d}" for i in range(8)]):
            for _ in range(8):
                capabilities.manifest()
        self.assertFalse(stale.exists(), "the old entry is the least recently used")
        self.assertEqual(len(self.manifests()), capabilities.CACHE_KEEP)


class T8VanishedEntry(CacheCase):
    def test_does_not_raise(self):
        cached = self.cache / f"manifest-{'a' * 16}.md"
        cached.write_text("GONE")
        real_read_text = Path.read_text

        def read_text(self, *args, **kwargs):
            if self == cached:
                raise FileNotFoundError(str(self))
            return real_read_text(self, *args, **kwargs)

        with self.keys("a" * 16), mock.patch.object(Path, "read_text", read_text):
            text = capabilities.manifest()
        self.assertNotEqual(text, "GONE")
        self.essentials.assert_called_once()


if __name__ == "__main__":
    unittest.main()
