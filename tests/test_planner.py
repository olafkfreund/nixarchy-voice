"""What the planner says out loud when a request does not come back.

The spoken line is the only part most people ever see, and it is what sends
them somewhere. Every failure here used to say "My planner isn't configured
yet", including an account that was configured perfectly and simply out of
credit.

Run with: python3 -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice import planner
from omarchy_voice.config import Config
from omarchy_voice.planner import (
    NOT_CONFIGURED, PlannerUnavailable, _spoken_for, chat_url, is_local)

OUT_OF_CREDIT = (
    '{"error": {"message": "You have no credits remaining.", '
    '"type": "insufficient_quota", "code": "credit_balance_exhausted"}}'
)
RATE_LIMITED = (
    '{"error": {"message": "Rate limit reached for gpt-4.1", '
    '"type": "requests", "code": "rate_limit_exceeded"}}'
)


class SpokenLineTests(unittest.TestCase):
    def test_out_of_credit_is_not_reported_as_misconfiguration(self):
        said = _spoken_for(429, OUT_OF_CREDIT)
        self.assertIn("credit", said.lower())
        self.assertNotEqual(said, NOT_CONFIGURED)

    def test_rate_limiting_says_to_wait_not_to_pay(self):
        # Same status code as the case above, opposite problem: one needs a
        # card, the other needs ten seconds. Only the body tells them apart.
        said = _spoken_for(429, RATE_LIMITED)
        self.assertNotIn("credit", said.lower())
        self.assertIn("moment", said.lower())

    def test_a_refused_key_says_so(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.assertIn("key", _spoken_for(status, "").lower())

    def test_a_server_fault_is_not_blamed_on_the_user(self):
        said = _spoken_for(503, "")
        self.assertIn("openai", said.lower())
        self.assertNotEqual(said, NOT_CONFIGURED)


class SpokenAndLoggedAreDifferentTests(unittest.TestCase):
    def test_the_detail_stays_out_of_the_spoken_line(self):
        exc = PlannerUnavailable(f"OpenAI HTTP 429: {OUT_OF_CREDIT}",
                                 _spoken_for(429, OUT_OF_CREDIT))
        # The log gets the body; she does not read a JSON blob aloud.
        self.assertIn("insufficient_quota", str(exc))
        self.assertNotIn("insufficient_quota", exc.spoken)

    def test_a_missing_key_still_says_not_configured(self):
        # The one case the original message was right about.
        self.assertEqual(PlannerUnavailable("OPENAI_API_KEY is not set").spoken,
                         NOT_CONFIGURED)


class EndpointTests(unittest.TestCase):
    """Where the planner sends its completions, and whether it needs a key.

    Only the typed path moves: `run` thinks with Claude however this is set.
    """

    def test_the_default_is_openai(self):
        self.assertEqual(chat_url(Config()),
                         "https://api.openai.com/v1/chat/completions")

    def test_a_custom_endpoint_keeps_its_own_path(self):
        for base in ("http://localhost:11434/v1", "http://localhost:11434/v1/"):
            with self.subTest(base=base):
                # Trailing slash or not, one slash in the result.
                self.assertEqual(chat_url(Config(base_url=base)),
                                 "http://localhost:11434/v1/chat/completions")

    def test_a_local_endpoint_needs_no_api_key(self):
        # The offline path must not depend on the account it exists to work
        # around: a model on this machine wants no credential at all.
        for host in ("localhost", "127.0.0.1", "0.0.0.0"):
            with self.subTest(host=host):
                self.assertTrue(is_local(Config(base_url=f"http://{host}:11434/v1")))

    def test_a_remote_endpoint_still_does(self):
        for base in ("https://api.openai.com/v1",
                     "https://openrouter.ai/api/v1"):
            with self.subTest(base=base):
                self.assertFalse(is_local(Config(base_url=base)))



class SystemPromptTests(unittest.TestCase):
    """The Claude brains send the desktop per turn, so they ask for it left out (#69)."""

    def prompt(self, **kwargs):
        with mock.patch.object(planner.capabilities, "manifest", return_value="MANIFEST"), \
             mock.patch.object(planner.capabilities, "live_state", return_value="LIVE"):
            return planner._system_prompt(**kwargs)

    def test_the_planner_still_gets_the_desktop(self):
        self.assertIn("# The desktop right now\n\nLIVE", self.prompt())

    def test_live_false_leaves_it_out(self):
        text = self.prompt(live=False)
        # The persona names the note in prose, so look for the section itself.
        self.assertNotIn("# The desktop right now\n\n", text)
        self.assertNotIn("LIVE", text)
        self.assertIn("MANIFEST", text)


if __name__ == "__main__":
    unittest.main()
