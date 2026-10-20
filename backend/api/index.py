"""MindGap API (runs on Vercel). Holds the LLM key and the answer keys; never sends either to the browser."""

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Header, HTTPException  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from core import auth, config, db, judge, llm  # noqa: E402

app = FastAPI(title="MindGap API")


class _ApiPrefix:
    """Hosts may strip the /api prefix before the request reaches us. Put it back so every route still matches."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/" and not scope["path"].startswith("/api"):
            path = "/api" + scope["path"]
            scope = {**scope, "path": path, "raw_path": path.encode()}
        await self.inner(scope, receive, send)


app.add_middleware(_ApiPrefix)

MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15


# ---------- request bodies ----------

class RegisterIn(BaseModel):
    handle: str = Field(max_length=40)
    pin: str = Field(max_length=12)
    consent: bool


class LoginIn(BaseModel):
    handle: str = Field(max_length=40)
    pin: str = Field(max_length=12)

class StartIn(BaseModel):
    concept_id: str
    consent: bool
    learner_id: str | None = None


class ExplainIn(BaseModel):
    session_id: str
    text: str = Field(min_length=1, max_length=4000)


class AnswerIn(BaseModel):
    session_id: str
    case_id: str
    stage: str  # "before" | "after"
    text: str = Field(min_length=1, max_length=4000)
    gap_id: str | None = None


class NotMeantIn(BaseModel):
    gap_id: str
    sentence: str


class RetryIn(BaseModel):
    session_id: str
    gap_id: str
    text: str = Field(min_length=1, max_length=4000)


class FeedbackIn(BaseModel):
    rating: int | None = Field(default=None, ge=1, le=5)
    message: str = Field(default="", max_length=2000)
    learner_id: str | None = None
    source: str = "tab"


# ---------- helpers ----------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _guard(token: str | None) -> None:
    if config.FRONTEND_TOKEN and token != config.FRONTEND_TOKEN:
        raise HTTPException(401, "Unauthorized")


def _one(table: str, id_: str) -> dict:
    rows = db.select(table, id=id_)
    if not rows:
        raise HTTPException(404, f"{table[:-1]} not found")
    return rows[0]


def _seen_case_ids(learner_id: str) -> set[str]:
    session_ids = [s["id"] for s in db.select("sessions", columns="id", learner_id=learner_id)]
    return {r["case_id"] for r in db.select_in("case_results", "session_id", session_ids, columns="case_id")}


def _pick_case(session: dict, levels: list[str]) -> dict | None:
    """Code picks the next case: in level order, never one this learner has already seen."""
    seen = _seen_case_ids(session["learner_id"])
    cases = sorted(db.select("cases", concept_id=session["concept_id"]), key=lambda c: c["id"])
    for level in levels:
        for c in cases:
            if c["level"] == level and c["id"] not in seen:
                return c
    return None


def _public_case(c: dict, stage: str) -> dict:
    # Only the case text leaves the server. must_hit / must_not stay here.
    return {"case_id": c["id"], "case_text": c["case_text"], "stage": stage}


def _finish(session_id: str, outcome: str) -> dict:
    """Session over: now (and only now) the key points are shown, as feedback and as the MindMap."""
    db.update("sessions", session_id, {"completed_at": _now()})
    session = _one("sessions", session_id)
    results = []
    for r in db.select("case_results", session_id=session_id, order="created_at.asc"):
        checked = (r.get("evidence") or {}).get("checked", {})
        results.append({
            "stage": r["stage"],
            "case_text": _one("cases", r["case_id"])["case_text"],
            "verdict": r["verdict"],
            "points": [{"text": p["text"], "ok": bool(p.get("ok")), "quote": p.get("quote", "")}
                       for p in checked.get("points", [])],
            "missing": [p["text"] for p in checked.get("points", []) if not p.get("ok")],
            "wrong_claims": [n["text"] for n in checked.get("must_not_hit", [])],
        })
    attempt_ids = [a["id"] for a in db.select("attempts", session_id=session_id, type="explanation", columns="id")]
    gaps = db.select_in("gaps", "attempt_id", attempt_ids, columns="recited_sentence,follow_up_question")
    gap = {"sentence": gaps[0]["recited_sentence"], "question": gaps[0]["follow_up_question"]} if gaps else None
    return {"next": "done", "outcome": outcome, "concept": _one("concepts", session["concept_id"])["title"],
            "gap": gap, "results": results}


# ---------- endpoints ----------

@app.get("/")
def home():
    return {"app": "MindGap API", "health": "/api/health"}


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/concepts")
def concepts(x_frontend_token: str | None = Header(default=None)):
    _guard(x_frontend_token)
    return db.select("concepts", columns="id,title", order="sort_order.asc")


@app.post("/api/register")
def register(body: RegisterIn, x_frontend_token: str | None = Header(default=None)):
    _guard(x_frontend_token)
    if not body.consent:
        raise HTTPException(400, "Consent is needed to create an account.")
    handle = body.handle.strip()
    if not auth.HANDLE_RE.match(handle):
        raise HTTPException(400, "Nicknames are 3 to 20 letters, numbers or underscores.")
    if not auth.PIN_RE.match(body.pin):
        raise HTTPException(400, "Your PIN must be 4 to 6 digits.")
    if db.select("learners", columns="id", handle=handle):
        raise HTTPException(409, "That nickname is taken. Try another one.")
    learner = db.insert("learners", {"consent_at": _now(), "handle": handle, "pin_hash": auth.hash_pin(body.pin)})
    return {"learner_id": learner["id"], "handle": handle}


@app.post("/api/login")
def login(body: LoginIn, x_frontend_token: str | None = Header(default=None)):
    _guard(x_frontend_token)
    no_match = HTTPException(401, "That nickname and PIN don't match.")
    handle = body.handle.strip()
    if not auth.HANDLE_RE.match(handle):
        raise no_match
    rows = db.select("learners", handle=handle)
    if not rows or not rows[0].get("pin_hash"):
        raise no_match
    learner = rows[0]

    now = datetime.now(timezone.utc)
    locked = learner.get("locked_until")
    if locked and datetime.fromisoformat(str(locked).replace("Z", "+00:00")) > now:
        raise HTTPException(429, f"Too many wrong PINs. Please wait {LOCK_MINUTES} minutes and try again.")

    if not auth.verify_pin(body.pin, learner["pin_hash"]):
        fails = (learner.get("failed_attempts") or 0) + 1
        if fails >= MAX_FAILED_LOGINS:
            db.update("learners", learner["id"], {
                "failed_attempts": 0, "locked_until": (now + timedelta(minutes=LOCK_MINUTES)).isoformat()})
        else:
            db.update("learners", learner["id"], {"failed_attempts": fails})
        raise no_match

    if learner.get("failed_attempts") or learner.get("locked_until"):
        db.update("learners", learner["id"], {"failed_attempts": 0, "locked_until": None})
    return {"learner_id": learner["id"], "handle": learner["handle"]}


@app.get("/api/history/{learner_id}")
def history(learner_id: str, x_frontend_token: str | None = Header(default=None)):
    """Past sessions for this anonymous learner. No login, no name: the ID lives only in their browser."""
    _guard(x_frontend_token)
    try:
        uuid.UUID(learner_id)
    except ValueError:
        raise HTTPException(400, "Unknown learner")

    titles = {c["id"]: c["title"] for c in db.select("concepts", columns="id,title")}
    out = []
    for s in db.select("sessions", learner_id=learner_id, order="started_at.desc")[:20]:
        results = db.select("case_results", session_id=s["id"], columns="stage,verdict", order="created_at.asc")
        attempt_ids = [a["id"] for a in db.select("attempts", session_id=s["id"], type="explanation", columns="id")]
        gaps = db.select_in("gaps", "attempt_id", attempt_ids, columns="recited_sentence,follow_up_question")
        after = [r for r in results if r["stage"] == "after"]
        if after:
            outcome = "closed" if after[-1]["verdict"] == "PASS" else "not_closed"
        elif s.get("completed_at") and results and all(r["verdict"] == "PASS" for r in results):
            outcome = "no_gap"
        else:
            outcome = "in_progress"
        out.append({
            "concept": titles.get(s["concept_id"], s["concept_id"]),
            "started_at": s["started_at"],
            "outcome": outcome,
            "verdicts": [f'{r["stage"]}:{r["verdict"]}' for r in results],
            "recited_sentence": gaps[0]["recited_sentence"] if gaps else None,
            "follow_up_question": gaps[0]["follow_up_question"] if gaps else None,
        })
    return out


@app.post("/api/start")
def start(body: StartIn, x_frontend_token: str | None = Header(default=None)):
    _guard(x_frontend_token)
    if not body.consent:
        raise HTTPException(400, "Consent is needed before we start.")
    _one("concepts", body.concept_id)

    learner = db.select("learners", columns="id", id=body.learner_id) if body.learner_id else []
    learner_id = learner[0]["id"] if learner else db.insert("learners", {"consent_at": _now()})["id"]

    session = db.insert("sessions", {"learner_id": learner_id, "concept_id": body.concept_id})
    return {"learner_id": learner_id, "session_id": session["id"]}


@app.post("/api/explain")
def explain(body: ExplainIn, x_frontend_token: str | None = Header(default=None)):
    _guard(x_frontend_token)
    session = _one("sessions", body.session_id)
    if len(body.text.split()) < 12:
        raise HTTPException(400, "Walk me through it a bit more: who does what, and what happens next?")

    db.insert("attempts", {"session_id": session["id"], "type": "explanation", "learner_text": body.text})

    case = _pick_case(session, ["first", "twin", "after"])
    if not case:
        return _finish(session["id"], "no_case_left")
    return {"next": "case", "case": _public_case(case, "before")}


@app.post("/api/answer")
def answer(body: AnswerIn, x_frontend_token: str | None = Header(default=None)):
    _guard(x_frontend_token)
    if body.stage not in ("before", "after"):
        raise HTTPException(400, "stage must be 'before' or 'after'")
    if body.stage == "after" and not body.gap_id:
        raise HTTPException(400, "gap_id is required for the case after the follow-up")

    session = _one("sessions", body.session_id)
    case = _one("cases", body.case_id)
    if case["concept_id"] != session["concept_id"]:
        raise HTTPException(400, "This case belongs to another concept")

    # AI collects evidence. Code checks the quotes and decides.
    raw = llm.mark_evidence(case["case_text"], case["must_hit"], case["must_not"], body.text)
    checked = judge.verdict(case["must_hit"], case["must_not"], raw, body.text)

    db.insert("case_results", {
        "session_id": session["id"],
        "case_id": case["id"],
        "stage": body.stage,
        "gap_id": body.gap_id if body.stage == "after" else None,
        "answer_text": body.text,
        "evidence": {"raw": raw, "checked": checked},
        "verdict": checked["verdict"],
    })

    if body.stage == "after":
        return _finish(session["id"], "closed" if checked["verdict"] == "PASS" else "not_closed")

    if checked["verdict"] == "PASS":
        if case["level"] == "first":
            twin = _pick_case(session, ["twin"])
            if twin:
                return {"verdict": "PASS", "next": "case", "case": _public_case(twin, "before")}
        return _finish(session["id"], "no_gap")

    # FAIL before any help: now point at the recited sentence.
    explanation = db.select("attempts", session_id=session["id"], type="explanation", order="created_at.desc")[0]
    concept = _one("concepts", session["concept_id"])
    found = llm.find_gap(concept["title"], explanation["learner_text"])
    sentence = judge.match_sentence(found["sentence"], explanation["learner_text"])
    question = found["question"] or llm.question_for(concept["title"], explanation["learner_text"], sentence)

    gap = db.insert("gaps", {
        "attempt_id": explanation["id"],
        "recited_sentence": sentence,
        "follow_up_question": question,
        "pattern": found["pattern"],
    })
    return {
        "verdict": "FAIL",
        "next": "gap",
        "gap": {"gap_id": gap["id"], "sentence": sentence, "question": question},
        "sentences": judge.split_sentences(explanation["learner_text"]),
    }


@app.post("/api/not-meant")
def not_meant(body: NotMeantIn, x_frontend_token: str | None = Header(default=None)):
    """Learner says 'That's not what I meant' and picks their own sentence. Does not use up the follow-up."""
    _guard(x_frontend_token)
    gap = _one("gaps", body.gap_id)
    explanation = _one("attempts", gap["attempt_id"])
    if not judge.quote_in(body.sentence, explanation["learner_text"]):
        raise HTTPException(400, "Pick a sentence from your own explanation.")
    session = _one("sessions", explanation["session_id"])
    concept = _one("concepts", session["concept_id"])
    question = llm.question_for(concept["title"], explanation["learner_text"], body.sentence)
    db.update("gaps", gap["id"], {"recited_sentence": body.sentence.strip(), "follow_up_question": question})
    return {"gap_id": gap["id"], "sentence": body.sentence.strip(), "question": question}


@app.post("/api/feedback")
def feedback(body: FeedbackIn, x_frontend_token: str | None = Header(default=None)):
    """Honest feedback from any visitor. Stored for the owner to read; never shown to other visitors."""
    _guard(x_frontend_token)
    message = body.message.strip()
    if not body.rating and len(message) < 3:
        raise HTTPException(400, "Pick a face or write a few words.")
    learner_id = None
    if body.learner_id:
        try:
            uuid.UUID(body.learner_id)
            learner_id = body.learner_id if db.select("learners", columns="id", id=body.learner_id) else None
        except ValueError:
            learner_id = None
    db.insert("feedback", {
        "learner_id": learner_id,
        "rating": body.rating,
        "message": message or None,
        "source": body.source if body.source in ("tab", "session_end") else "tab",
    })
    return {"ok": True}


@app.post("/api/retry")
def retry(body: RetryIn, x_frontend_token: str | None = Header(default=None)):
    _guard(x_frontend_token)
    session = _one("sessions", body.session_id)
    gap = _one("gaps", body.gap_id)
    if _one("attempts", gap["attempt_id"])["session_id"] != session["id"]:
        raise HTTPException(400, "This gap belongs to another session")

    # The second try points at the gap it answers: the proof link.
    db.insert("attempts", {"session_id": session["id"], "type": "retry", "learner_text": body.text, "gap_id": gap["id"]})

    case = _pick_case(session, ["after", "twin", "first"])
    if not case:
        return _finish(session["id"], "no_case_left")
    return {"next": "case", "case": _public_case(case, "after")}
