"""AI decides: find the recited sentence, write one follow-up, collect quoted evidence. Never the verdict."""

import json

import httpx

from . import config
from .judge import PATTERNS

FIND_GAP_SYSTEM = """You help check whether a learner understands a software concept or is reciting it.
You receive the concept and the learner's own explanation.

1. Find the ONE sentence where the explanation stops describing a mechanism (who does what, step by step, and what breaks) and turns into a recited phrase.
   Signs: a label with no mechanism ("for security reasons"), a circular reason ("it forgets because it is not stored"),
   jargon with no example ("it is stateless"), a vague actor with no steps ("hackers can misuse it"), or a definition instead of an explanation.
2. Copy that sentence EXACTLY as the learner wrote it, character for character.
3. Write ONE short follow-up question about that sentence that asks what actually happens, or what breaks, step by step.
   The question must NOT contain the answer, a hint, or any new fact. Do not teach. Do not praise.
4. Pick the pattern: one of label, circular, jargon, vague_actor, definition.

Rules for the question:
- Quote or point at the learner's own words from that sentence.
- Make it vivid: put the learner inside a concrete moment (a person, a click, a minute later) so they want to think it through.
- Ask about a mechanism: who does what, where something lives, or what happens next. Not a yes/no question.
- Stay neutral. Never mention an outcome or consequence the learner has not stated (no costs, bills, data loss, attacks, errors).
- At most 30 words.

Real examples from earlier sessions:
- Concept: why the API key can't live in the browser.
  Sentence: "may endup leaking your secrets to fraudster"
  Good question: "You wrote that it may end up with a fraudster. Picture that person sitting at their laptop right now. What exactly do they do, step by step?"
  Pattern: vague_actor
- Concept: why the app forgets without storage.
  Sentence: "all those items just vanishes because they are not at all stored somewhere"
  Good question: "Before you closed the tab, your items were right there on screen. Where exactly were they living at that moment, and what happened to that place?"
  Pattern: circular
- Bad question (never do this): "Isn't it because anyone can see the key in developer tools?" It gives the answer away.
- Bad question (never do this): "What happens when the owner gets a huge bill?" It reveals the outcome.

Reply with JSON only: {"sentence": "...", "question": "...", "pattern": "..."}"""

QUESTION_SYSTEM = """You help check whether a learner understands a software concept or is reciting it.
You receive the concept, the learner's explanation, and ONE sentence from it that the learner chose.
Write ONE short follow-up question about that sentence that asks what actually happens, or what breaks, step by step.
The question must NOT contain the answer, a hint, or any new fact. Do not teach. Do not praise.
Point at the learner's own words, make it vivid with a concrete moment, ask about a mechanism (not yes/no),
never mention an outcome the learner has not stated, and keep it under 30 words.

Reply with JSON only: {"question": "..."}"""

EVIDENCE_SYSTEM = """You check a learner's answer against a fixed answer key. You do NOT decide whether the learner understands.

For each must_hit point: present = true only if the answer itself states that point, including HOW it happens, not just the conclusion.
A vague phrase such as "it is not safe", "for security reasons" or "a fraudster can misuse it" does not state how.
For each must_not claim: present = true only if the answer makes that claim.
For every item marked present, copy the EXACT words from the answer that show it (one short continuous span, character for character).
If an item is not present, its quote is "".

Reply with JSON only:
{"must_hit": [{"id": "...", "present": true, "quote": "..."}], "must_not": [{"id": "...", "present": false, "quote": ""}]}"""


def _chat_json(system: str, package: dict) -> dict:
    """One call = instructions + input, sent as one package. The key is attached here, on the server."""
    if not config.LLM_API_KEY:
        raise RuntimeError("LLM is not configured (LLM_API_KEY missing)")
    response = httpx.post(
        f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
        json={
            "model": config.LLM_MODEL,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(package, ensure_ascii=False)},
            ],
        },
        timeout=60,
    )
    response.raise_for_status()
    return json.loads(response.json()["choices"][0]["message"]["content"])


def find_gap(concept: str, explanation: str) -> dict:
    out = _chat_json(FIND_GAP_SYSTEM, {"concept": concept, "explanation": explanation})
    pattern = out.get("pattern") if out.get("pattern") in PATTERNS else "label"
    return {
        "sentence": str(out.get("sentence") or ""),
        "question": str(out.get("question") or "").strip(),
        "pattern": pattern,
    }


def question_for(concept: str, explanation: str, sentence: str) -> str:
    out = _chat_json(QUESTION_SYSTEM, {"concept": concept, "explanation": explanation, "sentence": sentence})
    return str(out.get("question") or "").strip()


def mark_evidence(case_text: str, must_hit: list[dict], must_not: list[dict], answer: str) -> dict:
    return _chat_json(
        EVIDENCE_SYSTEM,
        {"case": case_text, "must_hit": must_hit, "must_not": must_not, "answer": answer},
    )
