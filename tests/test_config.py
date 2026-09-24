"""Config loading: unknown keys, additive policy lists."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice import capabilities, config as cfg
from omarchy_voice.config import Config, DEFAULT_CONFIRM, DEFAULT_DENY


class ConfigLoadTests(unittest.TestCase):
    def write(self, text: str) -> Path:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / "config.toml"
        path.write_text(text)
        return path

    def test_unknown_keys_are_kept_for_doctor(self):
        path = self.write('[ears]\nenginee = "realtime"\n')
        loaded = cfg.load(path)
        self.assertIn("enginee", loaded.unknown_keys)

    def test_a_retired_key_is_not_reported_as_a_typo(self):
        # Listening is toggle-only now. Every config written before that says
        # `mode = "push"`, and none of them should make doctor shout about it.
        path = self.write('[ears]\nmode = "push"\n')
        loaded = cfg.load(path)
        self.assertEqual(loaded.unknown_keys, [])
        self.assertIn("mode", loaded.retired_keys)

    def test_a_retired_key_still_gets_explained(self):
        self.assertIn("toggle", cfg.RETIRED_KEYS["mode"])

    def test_there_is_no_always_on_setting(self):
        path = self.write('[ears]\nmode = "always"\n')
        loaded = cfg.load(path)
        self.assertFalse(hasattr(loaded, "mode"))

    def test_confirm_patterns_union_with_defaults(self):
        path = self.write('[hands]\nconfirm_patterns = ["\\\\bformat\\\\b"]\n')
        loaded = cfg.load(path)
        self.assertIn(r"\bformat\b", loaded.confirm_patterns)
        for builtin in DEFAULT_CONFIRM:
            self.assertIn(builtin, loaded.confirm_patterns)

    def test_confirm_patterns_replace_drops_defaults(self):
        path = self.write(
            '[hands]\n'
            'confirm_patterns = ["\\\\bformat\\\\b"]\n'
            'confirm_patterns_replace = true\n'
        )
        loaded = cfg.load(path)
        self.assertEqual(loaded.confirm_patterns, [r"\bformat\b"])
        self.assertNotIn(r"\breboot\b", loaded.confirm_patterns)

    def test_deny_patterns_union_with_defaults(self):
        path = self.write('[hands]\ndeny_patterns = ["\\\\bwipe\\\\b"]\n')
        loaded = cfg.load(path)
        self.assertIn(r"\bwipe\b", loaded.deny_patterns)
        self.assertIn(DEFAULT_DENY[0], loaded.deny_patterns)



class UnreadableSourceTests(unittest.TestCase):
    """The manifest degrades silently by design; doctor must not.

    Every source here drops a section and lets the manifest build anyway,
    which is right at runtime and wrong to keep quiet about — the symptom is
    Oma not knowing how to do something, three steps from the cause.
    """

    def test_a_missing_omarchy_path_is_named(self):
        with mock.patch.object(capabilities, "OMARCHY_PATH",
                               Path("/nonexistent/omarchy")):
            problems = capabilities.unreadable_sources()
        self.assertTrue(any("OMARCHY_PATH" in p for p in problems), problems)

    def test_no_dispatcher_source_at_all_is_named(self):
        """#64: the compositor is asked first, so this is only a problem when
        BOTH it and every stub are unavailable -- and the message now says so
        rather than naming one file that was never the whole story."""
        capabilities._live_namespaces.cache_clear()
        self.addCleanup(capabilities._live_namespaces.cache_clear)
        with mock.patch.object(capabilities, "_stub_path", return_value=None), \
             mock.patch.object(capabilities, "_run", return_value=""):
            problems = capabilities.unreadable_sources()
        self.assertTrue(any("dispatcher tree" in p for p in problems), problems)

    def test_a_readable_machine_reports_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "default/hypr/bindings").mkdir(parents=True)
            stub = root / "hl.meta.lua"
            # A field line, not just the class: #64 made the check "are there
            # any dispatchers" rather than "does a file exist", because a stub
            # that parses to nothing is no more useful than a missing one.
            stub.write_text("---@class HL.DspNamespace\n"
                            "---@field focus fun(...): HL.Dispatcher\n")
            capabilities._live_namespaces.cache_clear()
            self.addCleanup(capabilities._live_namespaces.cache_clear)
            with mock.patch.object(capabilities, "OMARCHY_PATH", root), \
                 mock.patch.object(capabilities, "_stub_path", lambda: stub), \
                 mock.patch.object(capabilities, "_run", return_value=""), \
                 mock.patch.object(capabilities, "_omarchy_routes",
                                   lambda: {"omarchy theme set"}):
                self.assertEqual(capabilities.unreadable_sources(), [])


class CodingAgentTests(unittest.TestCase):
    """What the manifest says about the agents on this machine.

    The tools to drive them already existed -- run_in_terminal starts one and
    watch_terminal reports back. What was missing is that nothing said they
    are CLIs, so asked to have Claude review a diff she searched the desktop
    application list, found Claude-Desktop, and failed to launch it twice.
    """

    def test_only_what_is_installed_is_offered(self):
        # Naming an absent binary buys a failed command and a confused turn.
        with mock.patch.object(capabilities.shutil, "which",
                               side_effect=lambda b: "/bin/claude" if b == "claude" else None):
            section = capabilities.coding_agents()
        self.assertIn("claude -p", section)
        self.assertNotIn("codex", section)
        self.assertNotIn("ollama", section)

    def test_nothing_installed_means_no_section_at_all(self):
        # Not an empty heading: that is tokens spent every turn to say nothing.
        with mock.patch.object(capabilities.shutil, "which", return_value=None):
            self.assertEqual(capabilities.coding_agents(), "")

    def test_it_says_these_are_not_desktop_apps(self):
        with mock.patch.object(capabilities.shutil, "which", return_value="/bin/x"):
            section = capabilities.coding_agents()
        self.assertIn("not desktop apps", section)
        self.assertIn("run_in_terminal", section)
        self.assertIn("watch_terminal", section)


if __name__ == "__main__":
    unittest.main()


class ConsentDefaults(unittest.TestCase):
    """#17: capabilities that reach the desktop or the disk start off."""

    def test_both_start_off(self):
        self.assertFalse(Config().desktop_control)
        self.assertFalse(Config().allow_notifications)

    def test_load_remembers_whether_the_user_said_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            quiet = Path(tmp) / "quiet.toml"
            quiet.write_text("[mouth]\nnotify = true\n")
            self.assertFalse(cfg.load(quiet).allow_notifications_explicit)
            asked = Path(tmp) / "asked.toml"
            asked.write_text("[hands]\nallow_notifications = false\n")
            self.assertTrue(cfg.load(asked).allow_notifications_explicit)

    def test_desktop_control_is_read_from_the_hands_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.toml"
            path.write_text("[hands]\ndesktop_control = true\n")
            self.assertTrue(cfg.load(path).desktop_control)


class HoldTests(unittest.TestCase):
    def test_the_local_hold_is_its_own_and_the_realtime_one_is_untouched(self):
        """Lowering silence_hold_seconds would end every OpenAI turn early:
        server-side turn detection needs the pause to hear it (#72)."""
        self.assertEqual(Config().silence_hold_seconds, 1.5)
        self.assertEqual(Config().end_of_speech_seconds, 0.8)

    def test_spoken_confirm_guard_defaults_to_one_second(self):
        """#86: measured from the end of her last playback, echo tail included."""
        self.assertEqual(Config().spoken_confirm_guard_seconds, 1.0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.toml"
            path.write_text("[ears]\nspoken_confirm_guard_seconds = 1.5\n")
            loaded = cfg.load(path)
        self.assertEqual(loaded.spoken_confirm_guard_seconds, 1.5)
        self.assertEqual(loaded.unknown_keys, [])

    def test_the_new_keys_are_known(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.toml"
            path.write_text('end_of_speech_seconds = 1.0\nwhisper_vocabulary = "herdr"\n')
            loaded = cfg.load(path)
        self.assertEqual((loaded.end_of_speech_seconds, loaded.whisper_vocabulary), (1.0, "herdr"))
        self.assertEqual(loaded.unknown_keys, [])


# p620's hand-maintained replace list, as it stood when #109 was specced: the
# 17 non-secret defaults minus ssh, with its own spelling of the curl rule.
P620_DENY = r"""
[hands]
deny_patterns_replace = true
deny_patterns = [
  '\brm\s+-[a-zA-Z]*[rf]', '\bmkfs\b', '\bdd\s+if=', '\b(shred|wipefs)\b',
  '>\s*/dev/[sn][dv]', '\bpasswd\b', '\bsudo\b', '\bpkexec\b', '\bcryptsetup\b',
  '\bcurl\b.*\|\s*(bash|sh)', '\bgit\s+push\b', '\bnix-collect-garbage\b',
  '\bnix\s+store\s+(delete|gc)\b', '\bnix-store\s+--delete\b',
  '\bnix\s+profile\s+wipe-history\b', '\bnix-env\s+--delete-generations\b',
]
"""


class RemoveDefaultRuleTests(unittest.TestCase):
    """#109: drop one built-in rule by name and keep every other one."""

    write = ConfigLoadTests.write

    def test_ssh_allowed_and_the_other_28_still_apply(self):
        from omarchy_voice.tools import Denied, Policy
        loaded = cfg.load(self.write(
            '[hands]\nallow_shell = true\ndeny_patterns_remove = ["ssh"]\n'))
        self.assertEqual(len(loaded.deny_patterns), 28)
        self.assertNotIn(r"\bssh\b", loaded.deny_patterns)
        for name, pattern in cfg.DEFAULT_DENY_RULES.items():
            if name.startswith("secret-"):
                self.assertIn(pattern, loaded.deny_patterns)
        policy = Policy(loaded)
        policy.check("ssh p510 uptime")  # must not raise
        for action in ["cat ~/.ssh/id_ed25519", "cat /run/agenix/openai",
                       "cat .env", "sudo ls"]:
            with self.subTest(action=action), self.assertRaises(Denied):
                policy.check(action)
        self.assertEqual(loaded.policy_notes, [(True, "deny rules removed: ssh")])

    def test_a_default_added_later_still_applies(self):
        with mock.patch.dict(cfg.LIST_UNION_KEYS["deny_patterns"],
                             {"future-danger": r"\bfuture-danger\b"}):
            loaded = cfg.load(self.write('[hands]\ndeny_patterns_remove = ["ssh"]\n'))
        self.assertIn(r"\bfuture-danger\b", loaded.deny_patterns)
        self.assertNotIn(r"\bssh\b", loaded.deny_patterns)

    def test_an_unknown_name_removes_nothing_and_says_so(self):
        loaded = cfg.load(self.write('[hands]\ndeny_patterns_remove = ["shh"]\n'))
        self.assertEqual(loaded.deny_patterns, DEFAULT_DENY)
        self.assertEqual(len(loaded.policy_notes), 1)
        ok, text = loaded.policy_notes[0]
        self.assertFalse(ok)
        self.assertIn("shh", text)

    def test_removing_every_default_is_not_applied(self):
        for key, rules, default in [("deny_patterns", cfg.DEFAULT_DENY_RULES, DEFAULT_DENY),
                                    ("confirm_patterns", cfg.DEFAULT_CONFIRM_RULES,
                                     DEFAULT_CONFIRM)]:
            with self.subTest(key=key):
                names = ", ".join(f'"{n}"' for n in rules)
                loaded = cfg.load(self.write(f"[hands]\n{key}_remove = [{names}]\n"))
                self.assertEqual(getattr(loaded, key), default)
                self.assertEqual(len(loaded.policy_notes), 1)
                self.assertFalse(loaded.policy_notes[0][0])
                self.assertIn("not applied", loaded.policy_notes[0][1])

    def test_a_replaced_list_names_the_defaults_it_is_missing(self):
        loaded = cfg.load(self.write(P620_DENY))
        self.assertEqual(len(loaded.deny_patterns), 16)
        self.assertEqual(len(loaded.policy_notes), 1)
        ok, text = loaded.policy_notes[0]
        self.assertFalse(ok)
        missing = set(text.rsplit(": ", 1)[1].split(", "))
        expected = {"curl-pipe-shell", "ssh",
                    *(n for n in cfg.DEFAULT_DENY_RULES if n.startswith("secret-"))}
        self.assertEqual(len(expected), 14)
        self.assertEqual(missing, expected)

    def test_replace_wins_over_remove(self):
        loaded = cfg.load(self.write(
            '[hands]\ndeny_patterns_replace = true\n'
            'deny_patterns = ["\\\\bwipe\\\\b"]\ndeny_patterns_remove = ["ssh"]\n'))
        self.assertEqual(loaded.deny_patterns, [r"\bwipe\b"])
        self.assertTrue(any(not ok and "ignored" in text
                            for ok, text in loaded.policy_notes))

    def test_a_pattern_the_user_adds_stays_even_if_its_default_is_removed(self):
        loaded = cfg.load(self.write(
            '[hands]\ndeny_patterns = ["\\\\bssh\\\\b"]\ndeny_patterns_remove = ["ssh"]\n'))
        self.assertIn(r"\bssh\b", loaded.deny_patterns)

    def test_confirm_and_sensitive_rules_can_be_removed_too(self):
        loaded = cfg.load(self.write(
            '[hands]\nconfirm_patterns_remove = ["reboot"]\n'
            'sensitive_patterns_remove = ["credential-text"]\n'))
        self.assertEqual(len(loaded.confirm_patterns), 16)
        self.assertNotIn(cfg.DEFAULT_CONFIRM_RULES["reboot"], loaded.confirm_patterns)
        self.assertEqual(len(loaded.sensitive_patterns), 4)
        self.assertNotIn(cfg.DEFAULT_SENSITIVE_RULES["credential-text"],
                         loaded.sensitive_patterns)

    def test_a_string_is_not_read_as_a_list_of_characters(self):
        loaded = cfg.load(self.write('[hands]\ndeny_patterns_remove = "ssh"\n'))
        self.assertEqual(loaded.deny_patterns, DEFAULT_DENY)
        self.assertEqual(loaded.deny_patterns_remove, [])
        self.assertEqual(len(loaded.policy_notes), 1)
        self.assertFalse(loaded.policy_notes[0][0])

    def test_a_non_string_entry_removes_nothing_and_does_not_crash(self):
        for value in ["[1]", '[["ssh"]]', '"ssh"']:
            with self.subTest(value=value):
                loaded = cfg.load(self.write(f"[hands]\ndeny_patterns_remove = {value}\n"))
                self.assertEqual(loaded.deny_patterns, DEFAULT_DENY)
                self.assertEqual(len(loaded.deny_patterns), 29)
                self.assertEqual(loaded.policy_notes, [
                    (False, "deny_patterns_remove must be a list of rule names; ignored")])

    def test_the_remove_keys_are_known(self):
        loaded = cfg.load(self.write('[hands]\ndeny_patterns_remove = ["ssh"]\n'))
        self.assertEqual(loaded.unknown_keys, [])

    def test_a_config_without_remove_keys_is_unchanged(self):
        loaded = cfg.load(self.write('[hands]\nallow_shell = true\n'))
        self.assertEqual(loaded.policy_notes, [])
        self.assertEqual(loaded.deny_patterns, DEFAULT_DENY)
        self.assertEqual(loaded.confirm_patterns, DEFAULT_CONFIRM)
        self.assertEqual(loaded.sensitive_patterns, cfg.DEFAULT_SENSITIVE_PATTERNS)

    def test_rule_names_are_stable(self):
        """Names are an interface: renaming one breaks every config that uses it."""
        self.assertEqual(list(cfg.DEFAULT_DENY_RULES), [
            "rm-rf", "mkfs", "dd", "shred-wipefs", "write-block-device", "passwd",
            "sudo", "pkexec", "cryptsetup", "curl-pipe-shell", "git-push", "ssh",
            "nix-collect-garbage", "nix-store-gc", "nix-store-delete",
            "nix-profile-wipe-history", "nix-env-delete-generations",
            "secret-shadow", "secret-ssh-dir", "secret-gnupg", "secret-agenix-sops",
            "secret-dotenv", "secret-ssh-key", "secret-login-stores", "secret-aws",
            "secret-gh-token", "secret-claude-login", "secret-pass-store",
            "secret-keyrings"])
        self.assertEqual(list(cfg.DEFAULT_CONFIRM_RULES), [
            "shutdown", "reboot", "poweroff", "suspend", "hibernate",
            "omarchy-update", "omarchy-drive", "omarchy-pkg", "omarchy-install",
            "omarchy-refresh", "omarchy-reinstall", "hyprland-exit", "close-all",
            "nixos-rebuild", "home-manager-switch", "nixarchy-apply",
            "nix-flake-update"])
        self.assertEqual(list(cfg.DEFAULT_SENSITIVE_RULES), [
            "password-manager", "credential-prompt", "private-browsing",
            "credential-text", "banking"])
        for rules in (cfg.DEFAULT_DENY_RULES, cfg.DEFAULT_CONFIRM_RULES,
                      cfg.DEFAULT_SENSITIVE_RULES):
            for name in rules:
                self.assertRegex(name, r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertEqual(list(cfg.DEFAULT_DENY_RULES.values()), DEFAULT_DENY)
        self.assertEqual(list(cfg.DEFAULT_CONFIRM_RULES.values()), DEFAULT_CONFIRM)
        self.assertEqual(list(cfg.DEFAULT_SENSITIVE_RULES.values()),
                         cfg.DEFAULT_SENSITIVE_PATTERNS)


class DoctorTests(unittest.TestCase):
    """What `omarchy-voice doctor` says about the engine (#121).

    Everything that reaches outside the process is patched: no subprocess, no
    network, no PipeWire, no compositor.
    """

    def doctor(self, config: Config) -> str:
        import contextlib
        import io
        import os

        from omarchy_voice import (claude_backend, cli, hypr_events, listen_local,
                                   local_engine)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": tmp.name,
                                          "XDG_CONFIG_HOME": tmp.name}), \
                mock.patch.object(cli, "choose_backend",
                                  return_value=(type("ClaudeBrain", (), {}), "test")), \
                mock.patch.object(cli, "chat_ready", return_value=[]), \
                mock.patch.object(claude_backend, "check_ready", return_value=[]), \
                mock.patch.object(claude_backend, "cli_path", return_value=""), \
                mock.patch.object(local_engine, "check_ready", return_value=[]), \
                mock.patch.object(local_engine, "voice_chain", return_value="piper"), \
                mock.patch.object(listen_local, "check_ready", return_value=[]), \
                mock.patch.object(listen_local, "default_source", return_value=""), \
                mock.patch.object(listen_local, "default_sink", return_value=""), \
                mock.patch.object(hypr_events, "socket_path", return_value=None), \
                mock.patch("shutil.which", return_value=None), \
                mock.patch.object(capabilities, "manifest", return_value=""), \
                mock.patch.object(capabilities, "system_versions",
                                  return_value={"omarchy": "test"}), \
                mock.patch.object(capabilities, "unreadable_sources", return_value=[]), \
                mock.patch.object(capabilities, "verify_essentials", return_value=[]), \
                mock.patch.object(capabilities, "verify_hypr_essentials", return_value=[]), \
                contextlib.redirect_stdout(out):
            cli.cmd_doctor(None, config)
        return out.getvalue()

    def test_local_doctor_never_mentions_streaming_to_openai(self):
        text = self.doctor(Config())
        self.assertNotIn("OpenAI Realtime", text)
        self.assertNotIn("streamed to OpenAI", text)

    def test_doctor_names_the_removed_engine(self):
        text = self.doctor(Config(realtime_engine="openai"))
        self.assertIn("removed in 2.0.0", text)
        self.assertIn("whisper.cpp", text)

    def test_doctor_flags_an_unknown_engine(self):
        text = self.doctor(Config(realtime_engine="locl"))
        self.assertIn("locl", text)
        self.assertIn("is not an engine", text)
        self.assertNotIn("is not an engine", self.doctor(Config(realtime_engine="")))
