"""Tests for the code side of the line: quote checks and the verdict. No network, no AI.

Run from the backend folder:  python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import judge  # noqa: E402

KEY_2_HIT = [
    {"id": "h1", "text": "No, it is not safe"},
    {"id": "h2", "text": "Any logged-in user can read the key in developer tools and copy it"},
    {"id": "h3", "text": "Login protects the page, not the key"},
]
KEY_2_NOT = [{"id": "n1", "text": "A frontend .env file hides the key"}]

C2_ANSWER = "not safe either, a logged user can also be a fraudster"


class QuoteTests(unittest.TestCase):
    def test_real_quote_ignores_case_and_spacing(self):
        self.assertTrue(judge.quote_in("A Logged  user can", C2_ANSWER))

    def test_invented_quote_is_rejected(self):
        self.assertFalse(judge.quote_in("they copy the key from developer tools", C2_ANSWER))

    def test_tiny_quote_is_rejected(self):
        self.assertFalse(judge.quote_in("a", C2_ANSWER))

    def test_match_sentence_keeps_real_quote(self):
        text = "The key sits in the code. It may end up leaking your secrets to fraudster."
        self.assertEqual(
            judge.match_sentence("It may end up leaking your secrets to fraudster.", text),
            "It may end up leaking your secrets to fraudster.",
        )

    def test_match_sentence_falls_back_to_a_real_sentence(self):
        text = "The key sits in the code. It may end up leaking your secrets to fraudster."
        picked = judge.match_sentence("it might leak secrets to fraudsters", text)
        self.assertIn(picked, judge.split_sentences(text))


class VerdictTests(unittest.TestCase):
    def test_c2_partial_answer_fails_even_if_ai_is_lenient(self):
        # A lenient AI marks every point present, but quotes words that are not in the answer.
        lenient = {"must_hit": [
            {"id": "h1", "present": True, "quote": "not safe"},
            {"id": "h2", "present": True, "quote": "they can copy the key in developer tools"},
            {"id": "h3", "present": True, "quote": "login does not protect the key"},
        ]}
        result = judge.verdict(KEY_2_HIT, KEY_2_NOT, lenient, C2_ANSWER)
        self.assertEqual(result["verdict"], "FAIL")
        self.assertEqual([p["ok"] for p in result["points"]], [True, False, False])

    def test_all_points_with_real_quotes_pass(self):
        answer = "No. Any logged-in user opens developer tools and copies the key. Login protects the page, not the key."
        ev = {"must_hit": [
            {"id": "h1", "present": True, "quote": "No."},
            {"id": "h2", "present": True, "quote": "Any logged-in user opens developer tools and copies the key"},
            {"id": "h3", "present": True, "quote": "Login protects the page, not the key"},
        ], "must_not": [{"id": "n1", "present": False, "quote": ""}]}
        self.assertEqual(judge.verdict(KEY_2_HIT, KEY_2_NOT, ev, answer)["verdict"], "PASS")

    def test_must_not_claim_fails(self):
        answer = "No. Any logged-in user opens developer tools and copies the key. Login protects the page, not the key. Use a .env file in the frontend."
        ev = {"must_hit": [
            {"id": "h1", "present": True, "quote": "No."},
            {"id": "h2", "present": True, "quote": "Any logged-in user opens developer tools and copies the key"},
            {"id": "h3", "present": True, "quote": "Login protects the page, not the key"},
        ], "must_not": [{"id": "n1", "present": True, "quote": "Use a .env file in the frontend"}]}
        self.assertEqual(judge.verdict(KEY_2_HIT, KEY_2_NOT, ev, answer)["verdict"], "FAIL")

    def test_missing_evidence_fails(self):
        self.assertEqual(judge.verdict(KEY_2_HIT, KEY_2_NOT, {}, C2_ANSWER)["verdict"], "FAIL")


if __name__ == "__main__":
    unittest.main()
