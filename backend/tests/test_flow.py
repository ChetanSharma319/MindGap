"""End-to-end flow test with an in-memory database and a fake AI. No network.

Run from the backend folder:  python -m unittest discover -s tests -v
"""

import json
import os
import sys
import unittest
import uuid
from itertools import count

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api"))

from fastapi.testclient import TestClient  # noqa: E402

import index  # noqa: E402

_clock = count()


class FakeDB:
    def __init__(self):
        hit = [{"id": "h1", "text": "point one"}, {"id": "h2", "text": "point two"}]
        self.t = {
            "learners": [], "sessions": [], "attempts": [], "gaps": [], "case_results": [], "feedback": [],
            "concepts": [{"id": "key_in_browser", "title": "Why can't the key live in the browser?", "sort_order": 1}],
            "cases": [
                {"id": f"key_{i}", "concept_id": "key_in_browser", "level": lvl, "case_text": f"Case {i} text",
                 "must_hit": hit, "must_not": []}
                for i, lvl in ((1, "first"), (2, "twin"), (3, "after"))
            ],
        }

    def insert(self, table, row):
        stamp = f"{next(_clock):06d}"
        row = {"id": str(uuid.uuid4()), "created_at": stamp, **({"started_at": stamp} if table == "sessions" else {}), **row}
        self.t[table].append(row)
        return row

    def update(self, table, id_, values):
        for r in self.t[table]:
            if r["id"] == id_:
                r.update(values)
                return r
        return None

    def select(self, table, columns="*", order=None, **eq):
        rows = [r for r in self.t[table] if all(str(r.get(k)) == str(v) for k, v in eq.items())]
        if order:
            field, direction = order.split(".")
            rows = sorted(rows, key=lambda r: r.get(field) or "", reverse=(direction == "desc"))
        return [dict(r) for r in rows]

    def select_in(self, table, column, values, columns="*"):
        return [dict(r) for r in self.t[table] if r.get(column) in values]


class FakeLLM:
    """Marks every point present (quoting the answer) only if the answer contains 'GOOD'."""

    @staticmethod
    def mark_evidence(case_text, must_hit, must_not, answer):
        good = "GOOD" in answer
        return {"must_hit": [{"id": p["id"], "present": good, "quote": answer if good else ""} for p in must_hit],
                "must_not": []}

    @staticmethod
    def find_gap(concept, explanation):
        return {"sentence": "for security reasons", "question": "Who exactly can see it, and how?", "pattern": "label"}

    @staticmethod
    def question_for(concept, explanation, sentence):
        return "What happens step by step?"


EXPLANATION = "The key is in the frontend code so it is not safe at all for security reasons and hackers can misuse it."


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        index.db = self.db
        index.llm = FakeLLM
        index.config.FRONTEND_TOKEN = ""
        self.client = TestClient(index.app)
        self.responses = []

    def post(self, path, body):
        r = self.client.post(path, json=body)
        self.assertEqual(r.status_code, 200, r.text)
        self.responses.append(r.text)
        return r.json()

    def start(self):
        s = self.post("/api/start", {"concept_id": "key_in_browser", "consent": True})
        first = self.post("/api/explain", {"session_id": s["session_id"], "text": EXPLANATION})
        return s, first

    def test_fail_then_follow_up_then_pass_closes_the_gap(self):
        s, first = self.start()
        self.assertEqual(first["case"]["case_id"], "key_1")

        res = self.post("/api/answer", {"session_id": s["session_id"], "case_id": "key_1", "stage": "before",
                                        "text": "it is not safe"})
        self.assertEqual((res["verdict"], res["next"]), ("FAIL", "gap"))
        self.assertEqual(res["gap"]["sentence"], "for security reasons")
        gap_id = res["gap"]["gap_id"]

        nxt = self.post("/api/retry", {"session_id": s["session_id"], "gap_id": gap_id, "text": "second try"})
        self.assertEqual((nxt["case"]["case_id"], nxt["case"]["stage"]), ("key_3", "after"))

        done = self.post("/api/answer", {"session_id": s["session_id"], "case_id": "key_3", "stage": "after",
                                         "text": "GOOD answer", "gap_id": gap_id})
        self.assertEqual(done["outcome"], "closed")

        retry = [a for a in self.db.t["attempts"] if a["type"] == "retry"][0]
        self.assertEqual(retry["gap_id"], gap_id)  # the proof link
        after = [r for r in self.db.t["case_results"] if r["stage"] == "after"][0]
        self.assertEqual(after["gap_id"], gap_id)

    def test_pass_first_and_twin_means_no_gap(self):
        s, _ = self.start()
        res = self.post("/api/answer", {"session_id": s["session_id"], "case_id": "key_1", "stage": "before",
                                        "text": "GOOD"})
        self.assertEqual(res["case"]["case_id"], "key_2")  # harder twin before saying "no gap"
        done = self.post("/api/answer", {"session_id": s["session_id"], "case_id": "key_2", "stage": "before",
                                         "text": "GOOD"})
        self.assertEqual(done["outcome"], "no_gap")

    def test_not_meant_must_quote_the_learner(self):
        s, _ = self.start()
        res = self.post("/api/answer", {"session_id": s["session_id"], "case_id": "key_1", "stage": "before",
                                        "text": "nope"})
        gap_id = res["gap"]["gap_id"]
        bad = self.client.post("/api/not-meant", json={"gap_id": gap_id, "sentence": "words I never wrote"})
        self.assertEqual(bad.status_code, 400)
        ok = self.post("/api/not-meant", {"gap_id": gap_id, "sentence": "hackers can misuse it"})
        self.assertEqual(ok["sentence"], "hackers can misuse it")

    def test_answer_keys_never_leave_the_server(self):
        self.test_fail_then_follow_up_then_pass_closes_the_gap()
        for body in self.responses:
            self.assertNotIn("must_hit", body)
            self.assertNotIn("must_not", body)

    def test_returning_learner_never_sees_a_case_twice(self):
        s, _ = self.start()
        self.post("/api/answer", {"session_id": s["session_id"], "case_id": "key_1", "stage": "before", "text": "GOOD"})
        s2 = self.post("/api/start", {"concept_id": "key_in_browser", "consent": True, "learner_id": s["learner_id"]})
        nxt = self.post("/api/explain", {"session_id": s2["session_id"], "text": EXPLANATION})
        self.assertNotEqual(nxt["case"]["case_id"], "key_1")

    def test_consent_is_required(self):
        r = self.client.post("/api/start", json={"concept_id": "key_in_browser", "consent": False})
        self.assertEqual(r.status_code, 400)

    def test_history_shows_outcome_without_any_name(self):
        self.test_fail_then_follow_up_then_pass_closes_the_gap()
        learner_id = self.db.t["learners"][0]["id"]
        rows = self.client.get(f"/api/history/{learner_id}").json()
        self.assertEqual(rows[0]["outcome"], "closed")
        self.assertEqual(rows[0]["recited_sentence"], "for security reasons")
        self.assertEqual(self.client.get("/api/history/not-a-uuid").status_code, 400)

    def test_register_login_and_lockout(self):
        reg = self.post("/api/register", {"handle": "quiet_owl", "pin": "4821", "consent": True})
        stored = self.db.t["learners"][0]
        self.assertNotIn("4821", stored["pin_hash"])  # only a hash is stored
        self.assertNotIn("pin_hash", json.dumps(reg))

        taken = self.client.post("/api/register", json={"handle": "quiet_owl", "pin": "1111", "consent": True})
        self.assertEqual(taken.status_code, 409)
        bad_pin = self.client.post("/api/register", json={"handle": "other", "pin": "12", "consent": True})
        self.assertEqual(bad_pin.status_code, 400)

        ok = self.post("/api/login", {"handle": "quiet_owl", "pin": "4821"})
        self.assertEqual(ok["learner_id"], reg["learner_id"])

        for _ in range(5):
            self.assertEqual(self.client.post("/api/login", json={"handle": "quiet_owl", "pin": "0000"}).status_code, 401)
        locked = self.client.post("/api/login", json={"handle": "quiet_owl", "pin": "4821"})
        self.assertEqual(locked.status_code, 429)

    def test_feedback_is_stored_and_empty_is_refused(self):
        self.post("/api/feedback", {"rating": 4, "message": "The MindMap was clear.", "source": "session_end"})
        self.post("/api/feedback", {"message": "Too long cases", "learner_id": "not-a-uuid"})
        self.assertEqual(self.client.post("/api/feedback", json={"message": " "}).status_code, 400)
        self.assertEqual(self.client.post("/api/feedback", json={"rating": 9}).status_code, 422)
        rows = self.db.t["feedback"]
        self.assertEqual([r["rating"] for r in rows], [4, None])
        self.assertIsNone(rows[1]["learner_id"])


if __name__ == "__main__":
    unittest.main()
