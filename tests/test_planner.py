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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice.planner import NOT_CONFIGURED, PlannerUnavailable, _spoken_for

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


if __name__ == "__main__":
    unittest.main()
