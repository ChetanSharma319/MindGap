"""Code decides: quote checks and the PASS/FAIL verdict. Same input, same answer, every time."""

import difflib
import re
import unicodedata

PATTERNS = ("label", "circular", "jargon", "vague_actor", "definition")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = re.sub(r"[\u2018\u2019\u0060]", "'", text)
    text = re.sub(r"[\u201c\u201d]", '"', text)
    return re.sub(r"\s+", " ", text).strip()


def quote_in(quote: str, text: str) -> bool:
    """True only if the quote really appears in the learner's text (ignoring case and spacing)."""
    q = normalize(quote).strip(" .,;:!?\"'")
    t = normalize(text)
    if len(q) < 2:
        return False
    if len(q) < 6:  # short quotes like "no" must be whole words, not part of "not"
        return re.search(rf"(?<!\w){re.escape(q)}(?!\w)", t) is not None
    return q in t


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", (text or "").strip())
    return [p.strip() for p in parts if len(p.strip()) >= 3]


def match_sentence(candidate: str, text: str) -> str:
    """Return a sentence that really exists in the learner's text.

    Uses the AI's pick if it is a real quote, otherwise the closest real sentence.
    """
    if candidate and quote_in(candidate, text):
        return candidate.strip()
    sentences = split_sentences(text)
    if not sentences:
        return (text or "").strip()
    normalized = [normalize(s) for s in sentences]
    close = difflib.get_close_matches(normalize(candidate or ""), normalized, n=1, cutoff=0.0)
    return sentences[normalized.index(close[0])] if close else sentences[-1]


def verdict(must_hit: list[dict], must_not: list[dict], evidence: dict, answer: str) -> dict:
    """PASS only if every must-hit point is present with a real quote, and no must-not claim is."""
    hit_ev = {e.get("id"): e for e in evidence.get("must_hit", []) if isinstance(e, dict)}
    not_ev = {e.get("id"): e for e in evidence.get("must_not", []) if isinstance(e, dict)}

    points = []
    for p in must_hit:
        e = hit_ev.get(p["id"], {})
        quote = e.get("quote") or ""
        ok = bool(e.get("present")) and quote_in(quote, answer)
        points.append({"id": p["id"], "text": p["text"], "ok": ok, "quote": quote if ok else ""})

    wrong = []
    for n in must_not:
        e = not_ev.get(n["id"], {})
        quote = e.get("quote") or ""
        if e.get("present") and quote_in(quote, answer):
            wrong.append({"id": n["id"], "text": n["text"], "quote": quote})

    passed = all(p["ok"] for p in points) and not wrong
    return {"verdict": "PASS" if passed else "FAIL", "points": points, "must_not_hit": wrong}
