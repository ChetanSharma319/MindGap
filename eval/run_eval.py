"""Check the judge against my hand scores, using real classmate answers.

Runs each real answer through the same path the app uses (AI evidence -> code verdict)
and compares it with the verdict I gave by hand against the answer key.

Needs the backend env vars (LLM_* and SUPABASE_*). Run from the repo root:
    python eval/run_eval.py
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from core import db, judge, llm  # noqa: E402


def main() -> int:
    with open(os.path.join(ROOT, "eval", "real_answers.json"), encoding="utf-8") as f:
        items = json.load(f)

    agree = 0
    print(f"{'who':<4} {'case':<10} {'hand':<5} {'app':<5} match  missing")
    for item in items:
        case = db.select("cases", id=item["case_id"])[0]
        raw = llm.mark_evidence(case["case_text"], case["must_hit"], case["must_not"], item["answer"])
        result = judge.verdict(case["must_hit"], case["must_not"], raw, item["answer"])
        match = result["verdict"] == item["hand_verdict"]
        agree += match
        missing = ", ".join(p["id"] for p in result["points"] if not p["ok"]) or "-"
        print(f"{item['who']:<4} {item['case_id']:<10} {item['hand_verdict']:<5} {result['verdict']:<5} "
              f"{'yes' if match else 'NO ':<6} {missing}")

    print(f"\nAgreement with hand scores: {agree}/{len(items)}")
    return 0 if agree == len(items) else 1


if __name__ == "__main__":
    sys.exit(main())
