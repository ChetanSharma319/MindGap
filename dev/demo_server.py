"""Local demo: run the real API with an in-memory database and a fake AI, so the UI can be clicked through
without Supabase or an LLM key. For looking at the UI only. Never deployed.

    python dev/demo_server.py      (then, in another terminal: python frontend/app.py)
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("backend", os.path.join("backend", "api"), os.path.join("backend", "tests")):
    sys.path.insert(0, os.path.join(ROOT, p))

import json  # noqa: E402
import re  # noqa: E402

import uvicorn  # noqa: E402

import index  # noqa: E402
from core import judge  # noqa: E402
from test_flow import FakeDB  # noqa: E402

SQL = r"'((?:[^']|'')*)'"


def _unq(s: str) -> str:
    return s.replace("''", "'")


def load_seed() -> tuple[list[dict], list[dict]]:
    """Read the same concepts and cases the real database gets, from supabase/seed.sql."""
    with open(os.path.join(ROOT, "supabase", "seed.sql"), encoding="utf-8") as f:
        sql = f.read()
    concepts = [{"id": i, "title": _unq(t), "sort_order": int(o)}
                for i, t, o in re.findall(rf"\('(\w+)',\s+{SQL},\s+(\d+)\)", sql)]
    cases = [{"id": i, "concept_id": c, "level": lvl, "case_text": _unq(text),
              "must_hit": json.loads(_unq(hit)), "must_not": json.loads(_unq(nope))}
             for i, c, lvl, text, hit, nope in re.findall(
                 rf"\('(\w+)', '(\w+)', '(\w+)',\s*{SQL},\s*{SQL},\s*{SQL}\)", sql)]
    return concepts, cases


class DemoLLM:
    """Fake AI for the demo: answers of 20+ words count as covering every point."""

    @staticmethod
    def mark_evidence(case_text, must_hit, must_not, answer):
        ok = len(answer.split()) >= 20
        quote = " ".join(answer.split()[:6])
        return {"must_hit": [{"id": p["id"], "present": ok, "quote": quote if ok else ""} for p in must_hit],
                "must_not": []}

    @staticmethod
    def find_gap(concept, explanation):
        sentences = judge.split_sentences(explanation)
        vague = [s for s in sentences if any(w in s.lower() for w in ("secur", "safe", "hack", "expos", "stored"))]
        return {"sentence": (vague or sentences)[-1], "question": "Who exactly does that, and what happens step by step?",
                "pattern": "label"}

    @staticmethod
    def question_for(concept, explanation, sentence):
        return "What actually happens there, step by step?"


db = FakeDB()
db.t["concepts"], db.t["cases"] = load_seed()
print(f"Demo data: {len(db.t['concepts'])} concepts, {len(db.t['cases'])} cases (from supabase/seed.sql)")
index.db = db
index.llm = DemoLLM
index.config.FRONTEND_TOKEN = ""

if __name__ == "__main__":
    uvicorn.run(index.app, host="127.0.0.1", port=8000)
