"""MindGap UI (Gradio), a Concept Check app. Holds no secrets: no LLM key, no answer keys. It only talks to our server.

The thread looks like a conversation, but it runs in fixed steps: one explanation, one case,
one question, one second try, one new case. No free chat, so no room for hints.
"""

import functools
import html
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import gradio as gr
import requests

BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
FRONTEND_TOKEN = os.environ.get("FRONTEND_TOKEN", "")
GRADIO_6 = int(gr.__version__.split(".")[0]) >= 6
HERE = os.path.dirname(os.path.abspath(__file__))
BOT_AVATAR = os.path.join(HERE, "assets", "bot.svg")
LOGO_PATH = os.path.join(HERE, "assets", "logo.svg")
with open(LOGO_PATH, encoding="utf-8") as _f:
    LOGO = f'<span class="cc-logo">{_f.read()}</span>'
# Greeting time zone. Hosting servers usually run on UTC, so use the learners' zone, not the server's clock.
APP_TZ = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Kolkata"))

MIN_WORDS = {"explain": 12, "case": 5, "gap": 5}
PLACEHOLDER = {
    "idle": "Choose a concept and press Start…",
    "explain": "Walk me through what actually happens…",
    "case": "Work it out in your own words…",
    "gap": "Try again: what actually happens, step by step?",
    "done": "Session complete. Choose another concept to go again.",
}
METER_HINT = {
    "explain": "say who does what, and what happens next",
    "case": "say what happens, and why",
    "gap": "walk through it step by step",
}
STEPS = ("Pick", "Explain", "Case", "Look closer", "New case", "Result")
OUTCOMES = {
    "closed": ("Gap closed", "You missed the first case, then worked out a new one after a single question."),
    "not_closed": ("Not closed yet", "The new case still missed something. Here is exactly what."),
    "no_gap": ("No gap found", "You worked out the cases on your own. That's understanding, not reciting."),
    "no_case_left": ("All cases seen", "You've already seen every case for this concept. Try another one."),
    "in_progress": ("In progress", "This session wasn't finished."),
}
TITLES: dict[str, str] = {}


# ---------- server ----------

class ApiError(Exception):
    def __init__(self, message: str, tone: str = "warn"):
        super().__init__(message)
        self.tone = tone


def api(method: str, path: str, payload: dict | None = None):
    try:
        r = requests.request(method, f"{BACKEND_URL}{path}", json=payload,
                             headers={"x-frontend-token": FRONTEND_TOKEN}, timeout=90)
    except requests.RequestException:
        raise ApiError("We couldn't reach the server. Check your connection and try again.", "error")
    if r.status_code >= 500:
        raise ApiError("Something went wrong on our side. Your text is still here, so try again.", "error")
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", "")
        except ValueError:
            detail = ""
        raise ApiError(detail if isinstance(detail, str) and detail else "Please check and try again.")
    return r.json()


# ---------- helpers ----------

def esc(text) -> str:
    return html.escape(str(text or ""))


def notice_html(message: str, tone: str = "warn") -> str:
    return f'<div class="cc-notice {tone}">{esc(message)}</div>'


def friendly(n_outputs: int):
    """On a server problem, keep the screen as it is and show one calm line. No red pop-ups."""
    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args):
            try:
                return fn(*args)
            except ApiError as e:
                return [gr.update()] * (n_outputs - 1) + [notice_html(str(e), e.tone)]
        return inner
    return wrap


def progress_html(step: int) -> str:
    dots = "".join(
        f'<span class="cc-pdot {"done" if i < step else "now" if i == step else ""}" title="{name}"></span>'
        for i, name in enumerate(STEPS)
    )
    return (f'<div class="cc-progress"><span class="cc-pname">Step {step + 1} of {len(STEPS)} · <b>{STEPS[step]}</b></span>'
            f'<span class="cc-pdots">{dots}</span></div>')


def box(phase: str):
    return gr.update(value="", placeholder=PLACEHOLDER[phase], interactive=phase in MIN_WORDS)


def meter(text: str, phase: str):
    if phase not in MIN_WORDS:
        return "", gr.update(interactive=False)
    n, need = len((text or "").split()), MIN_WORDS[phase]
    if n >= need:
        return f'<div class="cc-meter ok">{n} words · ready to send</div>', gr.update(interactive=True)
    return f'<div class="cc-meter">{n} / {need} words · {METER_HINT[phase]}</div>', gr.update(interactive=False)


def bot(text: str) -> dict:
    return {"role": "assistant", "content": text}


def me(text: str) -> dict:
    return {"role": "user", "content": esc(text)}


def case_msg(case: dict, number: int) -> dict:
    return bot(f"**Case {number}**\n\n{esc(case['case_text'])}")


def msg_text(m) -> str:
    """Chat content can come back as a string, a dict, or a list of parts, depending on the Gradio version."""
    content = m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")
    if isinstance(content, list):
        return " ".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    if isinstance(content, dict):
        return str(content.get("text") or content.get("value") or "")
    return str(content or "")


def case_count(chat: list) -> int:
    return sum(1 for m in chat if (m.get("role") if isinstance(m, dict) else getattr(m, "role", "")) == "assistant"
               and msg_text(m).lstrip().startswith("**Case "))


TYPING = '<span class="cc-typing"><i></i><i></i><i></i></span>'


def gap_msg(gap: dict, lead: str = "Look at one thing you wrote:") -> dict:
    return bot(f"{lead}\n\n> {esc(gap['sentence'])}\n\n**{esc(gap['question'])}**")


def result_msg(res: dict) -> dict:
    title, text = OUTCOMES.get(res.get("outcome"), ("Done", ""))
    lines = [f"### {title}", text, ""]
    for i, r in enumerate(res.get("results", []), 1):
        lines.append(f"- Case {i}: **{'PASS' if r['verdict'] == 'PASS' else 'FAIL'}**")
    if res.get("results"):
        lines += ["", "💡 **Your MindMap is ready below.** It shows what you worked out, where it slipped, and the missing pieces."]
    return bot("\n".join(lines))


# ---------- MindMap: the feedback at the end ----------

BULB_SVG = ('<svg class="mm-bulb {cls}" viewBox="0 0 24 24" aria-hidden="true">'
            '<g class="mm-rays"><path d="M12 0.6v1.8M3.9 3.9l1.3 1.3M20.1 3.9l-1.3 1.3M0.8 11h1.8M21.4 11h1.8"/></g>'
            '<path class="mm-glass" d="M12 4a6 6 0 0 0-3.6 10.8c.7.5 1.1 1.3 1.1 2.1V18h5v-1.1c0-.8.4-1.6 1.1-2.1A6 6 0 0 0 12 4z"/>'
            '<path class="mm-base" d="M9.6 19.6h4.8M10.4 22h3.2"/></svg>')


def bulb(cls: str) -> str:
    return BULB_SVG.format(cls=cls)


def short(text, n: int = 110) -> str:
    text = str(text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def leaf(state: str, main: str, sub: str = "") -> str:
    return (f'<div class="mm-leaf {state}">{bulb(state)}<div><div class="mm-leaf-main">{main}</div>'
            f'{f"<div class=mm-leaf-sub>{sub}</div>" if sub else ""}</div></div>')


def branch(kind: str, title: str, tag: str, leaves: list[str], delay: int) -> str:
    return (f'<div class="mm-branch {kind}" style="animation-delay:{delay * 0.12:.2f}s">'
            f'<div class="mm-node"><span>{title}</span>{tag}</div><div class="mm-leaves">{"".join(leaves)}</div></div>')


def mindmap_html(res: dict) -> str:
    results = res.get("results") or []
    if not results:
        return ""
    outcome = res.get("outcome", "")
    title, _ = OUTCOMES.get(outcome, ("Done", ""))
    hits = sum(p["ok"] for r in results for p in r.get("points", []))
    total = sum(len(r.get("points", [])) for r in results)
    root_state = "lit" if outcome in ("closed", "no_gap") else "half"

    branches = []
    gap = res.get("gap")
    if gap:
        branches.append(branch("gap", "🔍 Where it slipped", '<span class="mm-chip warn">recited</span>', [
            leaf("dim", f'“{esc(short(gap["sentence"], 160))}”', "Sounded right, but it was a stock phrase, not the mechanism."),
            leaf("ask", f'<b>{esc(gap["question"])}</b>', "The one question you got. No hints."),
        ], len(branches)))

    to_fill, seen = [], set()
    for i, r in enumerate(results, 1):
        when = "before the question" if r["stage"] == "before" else "after the question"
        leaves = []
        for p in r.get("points", []):
            if p["ok"]:
                leaves.append(leaf("lit", esc(p["text"]), f'You said: “{esc(short(p["quote"]))}”'))
            else:
                leaves.append(leaf("dim", esc(p["text"]), "Missing from your answer"))
                if p["text"] not in seen:
                    seen.add(p["text"])
                    to_fill.append(leaf("fill", esc(p["text"]), f"The piece Case {i} needed"))
        for w in r.get("wrong_claims", []):
            leaves.append(leaf("wrong", f"Mix-up: {esc(w)}", "This one isn't true"))
            if w not in seen:
                seen.add(w)
                to_fill.append(leaf("wrong", f"Not true: {esc(w)}", "Let go of this one"))
        ok = r["verdict"] == "PASS"
        tag = f'<span class="mm-chip {"ok" if ok else "bad"}">{"PASS" if ok else "FAIL"}</span>'
        branches.append(branch("case", f"Case {i} · {when}", tag, leaves, len(branches)))

    if not to_fill:
        to_fill = [leaf("lit", "Nothing to fill.", "Every key point came from you, on cases you'd never seen.")]
    branches.append(branch("fill", "💡 Filling the gap", '<span class="mm-chip ok">remember</span>', to_fill, len(branches)))

    return f"""
<div class="mm">
  <div class="mm-head">
    <div><div class="mm-kicker">From MindGap to MindMap</div><div class="mm-title">Your MindMap</div></div>
    <div class="mm-legend"><span>{bulb("lit")} worked out</span><span>{bulb("dim")} missed or recited</span>
      <span>{bulb("wrong")} mix-up</span><span>{bulb("fill")} the missing piece</span></div>
  </div>
  <div class="mm-root-wrap"><div class="mm-root">{bulb(root_state)}<div>
    <div class="mm-root-title">{esc(res.get("concept", ""))}</div>
    <div class="mm-root-sub"><span class="cc-tag {esc(outcome)}">{esc(title)}</span> {hits} of {total} key points worked out</div>
  </div></div></div>
  <div class="mm-branches">{"".join(branches)}</div>
</div>"""


# ---------- HTML blocks ----------

TOGGLE = """
<button class="cc-theme-toggle" type="button" aria-label="Switch between light and dark theme">
  <span class="tt-star s1"></span><span class="tt-star s2"></span><span class="tt-star s3"></span>
  <span class="tt-knob">
    <svg class="tt-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
      <circle cx="12" cy="12" r="4.2"/><path d="M12 2v2.2M12 19.8V22M4.9 4.9l1.6 1.6M17.5 17.5l1.6 1.6M2 12h2.2M19.8 12H22M4.9 19.1l1.6-1.6M17.5 6.5l1.6-1.6"/>
    </svg>
    <svg class="tt-moon" viewBox="0 0 24 24" fill="currentColor"><path d="M20.5 14.2A8.5 8.5 0 1 1 9.8 3.5a6.6 6.6 0 0 0 10.7 10.7z"/></svg>
  </span>
</button>
"""

THEME_JS = """
<script>
(function () {
  function saved() { try { return localStorage.getItem('cc-theme') || 'light'; } catch (e) { return 'light'; } }
  function apply(mode) { if (document.body) document.body.classList.toggle('dark', mode === 'dark'); }
  function enforce() {
    if (!document.body) return;
    var wantDark = saved() === 'dark';
    if (document.body.classList.contains('dark') !== wantDark) apply(saved());
  }
  function start() {
    enforce();
    // Gradio sets its own theme class after it mounts; keep the learner's choice.
    new MutationObserver(enforce).observe(document.body, { attributes: true, attributeFilter: ['class'] });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
  document.addEventListener('click', function (e) {
    if (!e.target.closest('.cc-theme-toggle')) return;
    var mode = document.body.classList.contains('dark') ? 'light' : 'dark';
    try { localStorage.setItem('cc-theme', mode); } catch (err) {}
    apply(mode);
  });
})();
</script>
"""

LOGIN_ART = """
<div class="cc-art">
  <div class="cc-orb o1"></div><div class="cc-orb o2"></div><div class="cc-orb o3"></div>
  <div class="cc-art-brand">__LOGO__ MindGap</div>
  <div class="cc-art-title">Find where understanding stops and reciting starts.</div>
  <div class="cc-art-sub">Explain a concept in your own words. Get one sharp question. Prove it on a case you've never seen.</div>
  <div class="cc-preview">
    <div class="cc-pv bot">Walk me through it, in your own words.</div>
    <div class="cc-pv me">Here's how I understand it…</div>
    <div class="cc-pv bot strong">One question about one sentence you wrote.</div>
    <div class="cc-pv pass">✓ Gap closed</div>
  </div>
  <div class="cc-art-foot">A Concept Check app · Mind the gap</div>
</div>
""".replace("__LOGO__", LOGO)

HOW_IT_WORKS = """
<div class="cc-how">
  <div><span class="cc-num">1</span><b>Explain it</b><p>Pick a concept you feel you understand and walk through what actually happens, in your own words.</p></div>
  <div><span class="cc-num">2</span><b>A fresh case</b><p>You get a situation you haven't seen. Can you work out what happens?</p></div>
  <div><span class="cc-num">3</span><b>One question</b><p>If not, we quote the one sentence where your explanation turned into a stock phrase, and ask what breaks. No hints.</p></div>
  <div><span class="cc-num">4</span><b>A different case</b><p>A new case decides whether the gap closed. Not a better phrase: a worked-out answer.</p></div>
</div>
<div class="cc-how two">
  <div><b>⚖️ Who decides?</b><p>An answer key written before any session. The AI only points at your sentence, asks the question and quotes evidence. Code gives the verdict.</p></div>
  <div><b>🔒 What we store</b><p>Your typed answers and results. A nickname and a hashed PIN if you make an account. No real name, no email.</p></div>
</div>
"""

CHAT_HEAD = f"""
<div class="cc-chat-head">
  <div class="cc-bot-avatar"></div>
  <div><div class="cc-chat-title">MindGap Coach</div>
  <div class="cc-chat-sub">Asks one question. Never gives the answer.</div></div>
  <span class="cc-online">● Ready</span>
</div>
"""


def nav_user(user: dict | None) -> str:
    name = (user or {}).get("handle") or "Guest"
    return (f'<div class="cc-nav-right"><div class="cc-user"><span class="cc-ava">{esc(name[:1].upper())}</span>'
            f'<span>{esc(name)}</span></div>{TOGGLE}</div>')


def hero_html(user: dict | None, rows: list | None) -> str:
    name = (user or {}).get("handle") or "there"
    hour = datetime.now(APP_TZ).hour
    greet = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
    rows = rows or []
    closed = sum(1 for r in rows if r.get("outcome") in ("closed", "no_gap"))
    return (f'<div class="cc-hero"><div class="cc-hero-l"><div class="cc-hello">{greet}, {esc(name)} 👋</div>'
            '<div class="cc-hero-sub">Pick a concept you feel sure about. Let\'s see if it holds on a case you\'ve never seen.</div></div>'
            f'<div class="cc-stats"><div class="cc-stat"><b>{len(rows)}</b><span>sessions</span></div>'
            f'<div class="cc-stat"><b>{closed}</b><span>understood</span></div>'
            f'<div class="cc-stat"><b>{len(TITLES) or 5}</b><span>concepts</span></div></div></div>')


def history_html(rows: list | None, user: dict | None) -> str:
    if not (user or {}).get("learner_id") or not rows:
        return '<div class="cc-empty">No sessions yet. Your history appears here after your first session.</div>'
    cards = []
    for r in rows:
        title, _ = OUTCOMES.get(r["outcome"], ("", ""))
        try:
            when = datetime.fromisoformat(str(r["started_at"]).replace("Z", "+00:00")).strftime("%d %b %Y · %H:%M")
        except ValueError:
            when = ""
        quote = (f'<div class="cc-hist-quote">"{esc(r["recited_sentence"])}"</div>'
                 f'<div class="cc-hist-q">↳ {esc(r["follow_up_question"])}</div>') if r.get("recited_sentence") else ""
        cards.append(f'<div class="cc-hist"><div class="cc-hist-top"><span class="cc-hist-title">{esc(r["concept"])}</span>'
                     f'<span class="cc-tag {esc(r["outcome"])}">{esc(title)}</span></div>'
                     f'<div class="cc-hist-when">{esc(when)}</div>{quote}</div>')
    return '<div class="cc-hist-grid">' + "".join(cards) + "</div>"


def fetch_history(user: dict | None) -> list:
    learner_id = (user or {}).get("learner_id")
    if not learner_id:
        return []
    try:
        return api("GET", f"/api/history/{learner_id}")
    except ApiError:
        return []


# ---------- handlers: entry ----------

def load_concepts():
    try:
        items = api("GET", "/api/concepts")
    except ApiError:
        return gr.update()
    TITLES.update({c["id"]: c["title"] for c in items})
    return gr.update(choices=[(c["title"], c["id"]) for c in items], value=None)


def enter(user: dict) -> list:
    rows = fetch_history(user)
    return [user, gr.update(visible=False), gr.update(visible=True), nav_user(user), hero_html(user, rows),
            history_html(rows, user), ""]


def on_load(user):
    concepts = load_concepts()
    if user:
        return [concepts, *enter(user)]
    return [concepts, None, gr.update(visible=True), gr.update(visible=False), nav_user(None), "", "", ""]


# outputs: user, login_view, app_view, nav, hero, history, login_notice
@friendly(7)
def on_signin(handle, pin):
    res = api("POST", "/api/login", {"handle": handle, "pin": pin})
    return enter({"learner_id": res["learner_id"], "handle": res["handle"]})


@friendly(7)
def on_register(handle, pin, consent):
    res = api("POST", "/api/register", {"handle": handle, "pin": pin, "consent": bool(consent)})
    return enter({"learner_id": res["learner_id"], "handle": res["handle"]})


def on_guest():
    return enter({"learner_id": None, "handle": None, "guest": True})


def on_logout():
    return [None, gr.update(visible=True), gr.update(visible=False), [], "idle", None,
            box("idle"), progress_html(0), gr.update(visible=False), gr.update(visible=False),
            gr.update(value="", visible=False)]


def gate_signin(handle, pin):
    return gr.update(interactive=bool((handle or "").strip() and (pin or "").strip()))


def gate_register(handle, pin, consent):
    ok = bool(re.fullmatch(r"[A-Za-z0-9_]{3,20}", (handle or "").strip()) and re.fullmatch(r"\d{4,6}", pin or "") and consent)
    return gr.update(interactive=ok)


# ---------- handlers: session ----------

# outputs: user, session, phase, case, gap, chat, msg, progress, picker, restart, mindmap, notice
@friendly(12)
def on_start(concept_id, user):
    user = dict(user or {})
    res = api("POST", "/api/start", {"concept_id": concept_id, "consent": True, "learner_id": user.get("learner_id")})
    user["learner_id"] = res["learner_id"]
    intro = bot(f"**{esc(TITLES.get(concept_id, ''))}**\n\nWalk me through it. Don't define it. "
                "Explain what actually happens: who does what, and what breaks without it.")
    return [user, {"session_id": res["session_id"]}, "explain", None, None, [intro],
            box("explain"), progress_html(1), gr.update(visible=False), gr.update(visible=False),
            gr.update(value="", visible=False), ""]


# Step 1 of sending: show the learner's message and a typing bubble straight away (no server call).
# outputs: pending, chat, msg, send_btn, meter
def on_echo(text, chat):
    chat = list(chat or [])
    return [text, chat + [me(text), bot(TYPING)], gr.update(value="", interactive=False),
            gr.update(interactive=False), ""]


# Step 2: call the server, then replace the typing bubble with the reply.
# outputs: phase, case, gap, chat, msg, progress, picker, sentence_dd, restart, mindmap, notice
def on_send(text, phase, session, case, gap, chat):
    base = list(chat or [])[:-1]  # drop the typing bubble; the learner's message stays
    hidden, keep = gr.update(visible=False), gr.update()

    def finish(res):
        mm = mindmap_html(res)
        return ["done", None, None, base + [result_msg(res)], box("done"),
                progress_html(5), hidden, keep, gr.update(visible=True), gr.update(value=mm, visible=bool(mm)), ""]

    try:
        if phase == "explain":
            res = api("POST", "/api/explain", {"session_id": session["session_id"], "text": text})
            if res["next"] == "done":
                return finish(res)
            return ["case", res["case"], gap, base + [case_msg(res["case"], case_count(base) + 1)], box("case"),
                    progress_html(2), hidden, keep, hidden, keep, ""]

        if phase == "case":
            payload = {"session_id": session["session_id"], "case_id": case["case_id"], "stage": case["stage"], "text": text}
            if case["stage"] == "after":
                payload["gap_id"] = gap["gap_id"]
            res = api("POST", "/api/answer", payload)
            if res["next"] == "case":
                new_case = res["case"]
                return ["case", new_case, gap, base + [case_msg(new_case, case_count(base) + 1)], box("case"),
                        progress_html(2), hidden, keep, hidden, keep, ""]
            if res["next"] == "gap":
                new_gap = res["gap"]
                return ["gap", case, new_gap, base + [gap_msg(new_gap)], box("gap"), progress_html(3),
                        gr.update(visible=True), gr.update(choices=res["sentences"], value=None), hidden, keep, ""]
            return finish(res)

        if phase == "gap":
            res = api("POST", "/api/retry", {"session_id": session["session_id"], "gap_id": gap["gap_id"], "text": text})
            if res["next"] == "done":
                return finish(res)
            return ["case", res["case"], gap, base + [case_msg(res["case"], case_count(base) + 1)], box("case"),
                    progress_html(4), hidden, keep, hidden, keep, ""]
    except ApiError as e:
        # Put things back exactly as they were: remove the echoed message and return the text to the box.
        return [keep, keep, keep, base[:-1], gr.update(value=text, interactive=True), keep, keep, keep, keep, keep,
                notice_html(str(e), e.tone)]

    return [keep] * 10 + [""]


# outputs: gap, chat, notice
@friendly(3)
def on_not_meant(sentence, gap, chat):
    res = api("POST", "/api/not-meant", {"gap_id": gap["gap_id"], "sentence": sentence})
    return [res, list(chat or []) + [gap_msg(res, lead="Okay. Look at the sentence you chose:")], ""]


def on_restart(user):
    rows = fetch_history(user)
    return [None, "idle", None, None, [], box("idle"), progress_html(0), gr.update(visible=False),
            gr.update(visible=False), gr.update(value=None), "", hero_html(user, rows), history_html(rows, user),
            gr.update(value="", visible=False)]


def on_history(user):
    rows = fetch_history(user)
    return history_html(rows, user), hero_html(user, rows)


# ---------- feedback ----------

FACES = [("😞", 1), ("😕", 2), ("😐", 3), ("🙂", 4), ("🤩", 5)]
THANKS = ('<div class="cc-thanks"><span>🙏</span><div><b>Thank you for your feedback!</b>'
          '<p>It\'s saved, and I read every note. It goes straight into making MindGap better.</p></div></div>')


def fb_head(title: str, sub: str) -> str:
    return f'<div class="cc-fb-head"><div class="cc-fb-title">{title}</div><div class="cc-fb-sub">{sub}</div></div>'


def gate_feedback(rating, text):
    return gr.update(interactive=bool(rating) or len((text or "").strip()) >= 3)


def feedback_sender(source: str):
    # outputs: rating, text, send button, note
    @friendly(4)
    def send(rating, text, user):
        api("POST", "/api/feedback", {"rating": rating or None, "message": text or "", "source": source,
                                      "learner_id": (user or {}).get("learner_id")})
        return [gr.update(value=None), gr.update(value=""), gr.update(interactive=False), THANKS]
    return send


def reset_end_feedback(mindmap_value):
    # Show the feedback card under the MindMap only when a MindMap is shown; fresh for every session.
    return [gr.update(visible=bool(mindmap_value)), gr.update(value=None), gr.update(value=""), ""]


# ---------- look and feel ----------

THEME = gr.themes.Base(
    primary_hue="teal",
    secondary_hue="sky",
    neutral_hue="slate",
    radius_size="lg",
    font=[gr.themes.GoogleFont("Plus Jakarta Sans"), "Inter", "system-ui", "sans-serif"],
).set(
    body_background_fill="#F1F7FA",
    body_background_fill_dark="#07131A",
    block_background_fill="#FFFFFF",
    block_background_fill_dark="#0E1F28",
    block_border_color="#DDEBF0",
    block_border_color_dark="#1B3440",
    border_color_primary="#DDEBF0",
    border_color_primary_dark="#1B3440",
    input_background_fill="#F6FAFC",
    input_background_fill_dark="#0B1A22",
    body_text_color="#0F172A",
    body_text_color_dark="#E3EEF3",
    body_text_color_subdued="#5B6E7C",
    body_text_color_subdued_dark="#8FA7B4",
)

CSS = """
:root { --cc-grad: linear-gradient(135deg, #14B8A6 0%, #0EA5E9 55%, #2563EB 100%);
  --cc-teal: #0D9488; --cc-ink: #0F172A; --cc-muted: #5B6E7C; --cc-line: #DDEBF0; --cc-card: #FFFFFF;
  --cc-soft: #E9F6F8; --cc-bubble: #EEF6F9; }
.dark { --cc-ink: #E3EEF3; --cc-muted: #8FA7B4; --cc-line: #1B3440; --cc-card: #0E1F28; --cc-soft: #12303A; --cc-bubble: #142C37; }

.gradio-container { max-width: 1180px !important; margin: 0 auto !important; }
footer { display: none !important; }
.cc-bare, .cc-bare > .block, .cc-bare .html-container { padding: 0 !important; border: none !important; background: transparent !important; box-shadow: none !important; }

/* Primary buttons */
button.primary, .primary > button { background: var(--cc-grad) !important; color: #fff !important; border: none !important;
  font-weight: 700 !important; box-shadow: 0 8px 20px rgba(14, 165, 233, .28) !important; transition: transform .15s, box-shadow .15s !important; }
button.primary:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 12px 26px rgba(14, 165, 233, .36) !important; }
button:disabled { opacity: .45 !important; cursor: not-allowed !important; box-shadow: none !important; }
button.secondary { border-radius: 12px !important; }
textarea, input[type=text], input[type=password] { border-radius: 14px !important; }

/* Theme toggle: sun and moon */
.cc-theme-toggle { position: relative; width: 64px; height: 34px; border-radius: 999px; padding: 0; cursor: pointer;
  border: 1px solid var(--cc-line); background: linear-gradient(135deg, #BAE6FD, #A7F3D0); overflow: hidden;
  transition: background .5s ease; flex-shrink: 0; }
.tt-knob { position: absolute; top: 3px; left: 3px; width: 26px; height: 26px; border-radius: 50%; background: #fff;
  box-shadow: 0 2px 8px rgba(0,0,0,.18); display: grid; place-items: center;
  transition: transform .6s cubic-bezier(.68,-0.55,.27,1.55), background .5s ease; }
.tt-sun, .tt-moon { position: absolute; width: 17px; height: 17px; transition: opacity .45s ease, transform .6s ease; }
.tt-sun { color: #F59E0B; animation: cc-spin 12s linear infinite; }
.tt-moon { color: #C7D2FE; opacity: 0; transform: rotate(-120deg) scale(.3); }
.tt-star { position: absolute; width: 3px; height: 3px; border-radius: 50%; background: #fff; opacity: 0; transition: opacity .5s ease; }
.tt-star.s1 { left: 12px; top: 9px; } .tt-star.s2 { left: 20px; top: 20px; width: 2px; height: 2px; } .tt-star.s3 { left: 8px; top: 22px; width: 2px; height: 2px; }
.dark .cc-theme-toggle { background: linear-gradient(135deg, #0B1E2D, #1E3A5F); }
.dark .tt-knob { transform: translateX(30px) rotate(360deg); background: #1E293B; }
.dark .tt-sun { opacity: 0; transform: rotate(120deg) scale(.3); animation: none; }
.dark .tt-moon { opacity: 1; transform: none; }
.dark .tt-star { opacity: .9; animation: cc-twinkle 2.4s ease-in-out infinite; }
.dark .tt-star.s2 { animation-delay: .8s; } .dark .tt-star.s3 { animation-delay: 1.5s; }
@keyframes cc-spin { to { transform: rotate(360deg); } }
@keyframes cc-twinkle { 0%, 100% { opacity: .2; } 50% { opacity: 1; } }
@keyframes cc-float { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-14px); } }
@keyframes cc-rise { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: none; } }

/* Login */
#cc-login { padding: 28px 0; animation: cc-rise .5s ease; }
#cc-login-card { max-width: 1040px; margin: 0 auto; border-radius: 28px; overflow: hidden; gap: 0 !important;
  background: var(--cc-card); border: 1px solid var(--cc-line); box-shadow: 0 30px 80px rgba(2, 40, 60, .14); }
#cc-login-art { background: var(--cc-grad); padding: 0 !important; min-height: 600px; }
.cc-art { position: relative; height: 100%; min-height: 600px; padding: 44px 40px; color: #fff; overflow: hidden; }
.cc-orb { position: absolute; border-radius: 50%; background: rgba(255,255,255,.14); animation: cc-float 7s ease-in-out infinite; }
.cc-orb.o1 { width: 260px; height: 260px; right: -80px; top: -60px; }
.cc-orb.o2 { width: 160px; height: 160px; left: -50px; bottom: 40px; animation-delay: 1.5s; }
.cc-orb.o3 { width: 70px; height: 70px; right: 60px; bottom: 150px; animation-delay: 3s; background: rgba(255,255,255,.22); }
.cc-art-brand { display: flex; align-items: center; gap: 10px; font-weight: 800; font-size: 1.1rem; position: relative; }
.cc-art .cc-logo { background: rgba(8, 30, 40, .35); border: 1px solid rgba(255,255,255,.25); }
.cc-art-title { font-size: 2.15rem; font-weight: 800; line-height: 1.15; letter-spacing: -.02em; margin-top: 70px; position: relative; }
.cc-art-sub { margin-top: 14px; font-size: 1.02rem; opacity: .92; max-width: 420px; position: relative; }
.cc-preview { margin-top: 36px; display: flex; flex-direction: column; gap: 10px; position: relative; max-width: 380px; }
.cc-pv { padding: 10px 14px; border-radius: 16px; font-size: .9rem; backdrop-filter: blur(8px); animation: cc-rise .6s ease both; }
.cc-pv.bot { background: rgba(255,255,255,.18); border: 1px solid rgba(255,255,255,.28); border-bottom-left-radius: 4px; align-self: flex-start; }
.cc-pv.strong { font-weight: 700; animation-delay: .15s; }
.cc-pv.me { background: #fff; color: #0F172A; border-bottom-right-radius: 4px; align-self: flex-end; animation-delay: .3s; }
.cc-pv.pass { background: rgba(16,185,129,.9); align-self: flex-start; font-weight: 700; animation-delay: .45s; }
#cc-login-form { padding: 40px 44px !important; justify-content: center; }
.cc-form-top { display: flex; justify-content: space-between; align-items: center; }
.cc-form-title { font-size: 1.7rem; font-weight: 800; letter-spacing: -.02em; margin-top: 18px; }
.cc-form-sub { color: var(--cc-muted); margin-top: 4px; }
.cc-or { display: flex; align-items: center; gap: 12px; color: var(--cc-muted); font-size: .85rem; margin: 6px 0; }
.cc-or::before, .cc-or::after { content: ""; flex: 1; height: 1px; background: var(--cc-line); }
.cc-fine { font-size: .8rem; color: var(--cc-muted); }
.cc-art-foot { position: absolute; left: 40px; bottom: 26px; font-size: .82rem; opacity: .8; letter-spacing: .02em; }

/* App nav */
#cc-nav { align-items: center; padding: 14px 4px 6px; }
.cc-brand { display: flex; align-items: center; gap: 10px; font-weight: 800; font-size: 1.2rem; letter-spacing: -.01em; }
.cc-logo { width: 36px; height: 36px; border-radius: 11px; background: linear-gradient(135deg, #0F766E, #1D4ED8);
  display: grid; place-items: center; box-shadow: 0 6px 16px rgba(14,165,233,.3); flex-shrink: 0; }
.cc-logo svg { width: 25px; height: 25px; filter: drop-shadow(0 0 4px rgba(253, 224, 71, .55)); }
.cc-logo svg g { animation: cc-glow 2.8s ease-in-out infinite; transform-origin: 12px 10px; }
@keyframes cc-glow { 0%, 100% { opacity: .75; } 50% { opacity: 1; } }
.cc-nav-right { display: flex; align-items: center; justify-content: flex-end; gap: 12px; }
.cc-user { display: flex; align-items: center; gap: 8px; padding: 4px 12px 4px 4px; border-radius: 999px;
  background: var(--cc-card); border: 1px solid var(--cc-line); font-weight: 600; font-size: .9rem; }
.cc-ava { width: 28px; height: 28px; border-radius: 50%; background: var(--cc-grad); color: #fff; display: grid; place-items: center; font-weight: 800; font-size: .85rem; }
#cc-logout { max-width: 110px; }

/* Tabs */
button[role="tab"] { font-weight: 650 !important; }
button[role="tab"][aria-selected="true"] { color: var(--cc-teal) !important; }
.dark button[role="tab"][aria-selected="true"] { color: #5EEAD4 !important; }

/* Hero */
.cc-hero { position: relative; overflow: hidden; display: flex; justify-content: space-between; align-items: center; gap: 20px;
  flex-wrap: wrap; padding: 26px 28px; border-radius: 24px; color: #fff; background: var(--cc-grad);
  box-shadow: 0 18px 40px rgba(14,165,233,.22); animation: cc-rise .5s ease; }
.cc-hero::before, .cc-hero::after { content: ""; position: absolute; border-radius: 50%; background: rgba(255,255,255,.12); }
.cc-hero::before { width: 220px; height: 220px; right: -60px; top: -90px; }
.cc-hero::after { width: 120px; height: 120px; right: 220px; bottom: -70px; }
.cc-hello { font-size: 1.6rem; font-weight: 800; letter-spacing: -.02em; position: relative; }
.cc-hero-sub { opacity: .92; margin-top: 4px; max-width: 520px; position: relative; }
.cc-stats { display: flex; gap: 10px; position: relative; }
.cc-stat { min-width: 92px; padding: 10px 14px; border-radius: 16px; background: rgba(255,255,255,.18);
  border: 1px solid rgba(255,255,255,.28); backdrop-filter: blur(6px); text-align: center; }
.cc-stat b { display: block; font-size: 1.4rem; } .cc-stat span { font-size: .78rem; opacity: .9; }

/* Concept tiles */
.cc-section { font-size: .78rem; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; color: var(--cc-muted); margin: 6px 2px; }
#concept-tiles { background: transparent !important; border: none !important; padding: 0 !important; }
#concept-tiles .wrap { display: grid !important; grid-template-columns: 1fr; gap: 10px; counter-reset: concept; }
#concept-tiles label { display: flex !important; align-items: center !important; gap: 14px; width: 100%; padding: 14px !important;
  border-radius: 18px !important; border: 1px solid var(--cc-line) !important; background: var(--cc-card) !important;
  box-shadow: 0 4px 14px rgba(2, 40, 60, .05); cursor: pointer; counter-increment: concept;
  transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease; }
#concept-tiles label::before { content: counter(concept); width: 44px; height: 44px; flex-shrink: 0; border-radius: 14px;
  display: grid; place-items: center; font-size: 1.15rem; font-weight: 800; color: var(--cc-teal);
  background: var(--cc-soft); transition: background .2s ease, color .2s ease; }
.dark #concept-tiles label::before { color: #5EEAD4; }
#concept-tiles label:hover { transform: translateY(-2px); border-color: #5EEAD4 !important; box-shadow: 0 10px 24px rgba(13,148,136,.14); }
#concept-tiles label.selected { border-color: #14B8A6 !important; box-shadow: 0 0 0 3px rgba(20,184,166,.22), 0 10px 24px rgba(13,148,136,.14); }
#concept-tiles label.selected::before { background: var(--cc-grad); color: #fff; }
#concept-tiles input[type=radio] { display: none !important; }
#concept-tiles label span { font-size: .96rem; font-weight: 650; line-height: 1.35; color: var(--cc-ink); }

/* Chat card */
#cc-chat-card { background: var(--cc-card); border: 1px solid var(--cc-line); border-radius: 24px; padding: 14px !important;
  box-shadow: 0 18px 40px rgba(2, 40, 60, .08); gap: 10px !important; }
.cc-chat-head { display: flex; align-items: center; gap: 12px; padding: 4px 4px 10px; border-bottom: 1px solid var(--cc-line); }
.cc-bot-avatar { width: 44px; height: 44px; border-radius: 50%; background: var(--cc-grad); position: relative; box-shadow: 0 6px 14px rgba(14,165,233,.3); }
.cc-bot-avatar::before { content: ""; position: absolute; inset: 11px 9px 13px; border-radius: 8px; background: #0B2530; }
.cc-bot-avatar::after { content: ""; position: absolute; left: 15px; top: 17px; width: 5px; height: 5px; border-radius: 50%;
  background: #5EEAD4; box-shadow: 9px 0 0 #5EEAD4; animation: cc-twinkle 3s ease-in-out infinite; }
.cc-chat-title { font-weight: 800; }
.cc-chat-sub { font-size: .82rem; color: var(--cc-muted); }
.cc-online { margin-left: auto; font-size: .78rem; font-weight: 700; color: #10B981; background: rgba(16,185,129,.1);
  padding: 4px 10px; border-radius: 999px; }
.cc-progress { display: flex; justify-content: space-between; align-items: center; padding: 0 4px; font-size: .82rem; color: var(--cc-muted); }
.cc-pname b { color: var(--cc-teal); } .dark .cc-pname b { color: #5EEAD4; }
.cc-pdots { display: flex; gap: 6px; }
.cc-pdot { width: 22px; height: 6px; border-radius: 6px; background: var(--cc-line); transition: background .3s ease, width .3s ease; }
.cc-pdot.done { background: #14B8A6; } .cc-pdot.now { width: 34px; background: var(--cc-grad); }

#thread { border: none !important; background: transparent !important; }
#thread button[aria-label="Clear"] { display: none !important; }
#thread .message-wrap, #thread .bubble-wrap { background: transparent !important; }
#thread .message.user, #thread .user .message, #thread [data-testid="user"] {
  background: linear-gradient(135deg, #99F6E4 0%, #A5E3FB 55%, #BFDBFE 100%) !important; color: #0A0F1A !important;
  border: none !important; border-radius: 18px 18px 4px 18px !important; }
#thread .message.user *, #thread .user .message *, #thread [data-testid="user"] * { color: #0A0F1A !important; }
#thread .message.bot, #thread .bot .message, #thread [data-testid="bot"] { background: var(--cc-bubble) !important; color: var(--cc-ink) !important;
  border: none !important; border-radius: 18px 18px 18px 4px !important; }
#thread blockquote { border-left: 3px solid #F59E0B; background: rgba(245,158,11,.1); padding: 8px 12px; border-radius: 8px; margin: 8px 0; font-style: italic; }
.cc-typing { display: inline-flex; gap: 5px; padding: 4px 2px; }
.cc-typing i { width: 8px; height: 8px; border-radius: 50%; background: #14B8A6; opacity: .35; animation: cc-dot 1.2s ease-in-out infinite; }
.cc-typing i:nth-child(2) { animation-delay: .15s; } .cc-typing i:nth-child(3) { animation-delay: .3s; }
@keyframes cc-dot { 0%, 80%, 100% { opacity: .3; transform: translateY(0); } 40% { opacity: 1; transform: translateY(-4px); } }
/* No queue / progress overlays anywhere */
.progress-text, .eta-bar, .progress-level, .meta-text, .meta-text-center { display: none !important; }

#cc-composer { align-items: flex-end; gap: 8px; background: var(--cc-soft); border: 1px solid var(--cc-line); border-radius: 20px; padding: 6px !important; }
#cc-composer textarea { background: transparent !important; border: none !important; box-shadow: none !important; }
#cc-composer .block { background: transparent !important; border: none !important; box-shadow: none !important; }
#cc-send { min-width: 56px !important; height: 48px; border-radius: 16px !important; font-size: 1.2rem !important; }
.cc-meter { font-size: .8rem; color: var(--cc-muted); padding: 0 8px; }
.cc-meter.ok { color: #059669; font-weight: 650; } .dark .cc-meter.ok { color: #6EE7B7; }
.cc-notice { padding: 10px 14px; border-radius: 12px; font-size: .9rem; animation: cc-rise .3s ease; }
.cc-notice.warn { background: #FFFBEB; color: #92400E; border: 1px solid #FDE68A; }
.cc-notice.error { background: #F1F5F9; color: #334155; border: 1px solid #CBD5E1; }
.dark .cc-notice.warn { background: rgba(245,158,11,.12); color: #FCD34D; border-color: rgba(245,158,11,.3); }
.dark .cc-notice.error { background: rgba(148,163,184,.12); color: #E2E8F0; border-color: rgba(148,163,184,.3); }

/* History */
.cc-empty { padding: 40px; text-align: center; color: var(--cc-muted); border: 1.5px dashed var(--cc-line); border-radius: 20px; }
.cc-hist-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }
.cc-hist { background: var(--cc-card); border: 1px solid var(--cc-line); border-radius: 18px; padding: 16px 18px; animation: cc-rise .4s ease; }
.cc-hist-top { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
.cc-hist-title { font-weight: 700; }
.cc-hist-when { font-size: .8rem; color: var(--cc-muted); margin-top: 2px; }
.cc-hist-quote { margin-top: 12px; font-style: italic; color: #B45309; } .dark .cc-hist-quote { color: #FCD34D; }
.cc-hist-q { margin-top: 4px; font-size: .9rem; color: var(--cc-muted); }
.cc-tag { font-size: .74rem; font-weight: 800; padding: 4px 10px; border-radius: 999px; background: var(--cc-soft); color: var(--cc-muted); white-space: nowrap; }
.cc-tag.closed, .cc-tag.no_gap { background: rgba(16,185,129,.14); color: #059669; }
.cc-tag.not_closed { background: rgba(245,158,11,.15); color: #B45309; }

/* How it works */
.cc-how { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; margin-top: 10px; }
.cc-how > div { background: var(--cc-card); border: 1px solid var(--cc-line); border-radius: 20px; padding: 20px; }
.cc-how b { display: block; margin: 8px 0 6px; font-size: 1.02rem; }
.cc-how p { margin: 0; color: var(--cc-muted); font-size: .92rem; line-height: 1.5; }
.cc-num { width: 34px; height: 34px; border-radius: 11px; background: var(--cc-grad); color: #fff; display: grid; place-items: center; font-weight: 800; }

/* MindMap */
.mm { margin-top: 18px; padding: 24px; border-radius: 24px; background: var(--cc-card); border: 1px solid var(--cc-line);
  box-shadow: 0 18px 40px rgba(2, 40, 60, .08); animation: cc-rise .5s ease; }
.mm-head { display: flex; justify-content: space-between; align-items: flex-end; flex-wrap: wrap; gap: 12px; }
.mm-kicker { font-size: .75rem; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; color: var(--cc-teal); }
.dark .mm-kicker { color: #5EEAD4; }
.mm-title { font-size: 1.5rem; font-weight: 800; letter-spacing: -.02em; }
.mm-legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: .8rem; color: var(--cc-muted); }
.mm-legend span { display: inline-flex; align-items: center; gap: 4px; }
.mm-legend .mm-bulb { width: 18px; height: 18px; animation: none; }
.mm-root-wrap { display: flex; justify-content: center; margin-top: 22px; position: relative; }
.mm-root-wrap::after { content: ""; position: absolute; bottom: -22px; left: 50%; width: 2px; height: 22px; background: var(--cc-line); }
.mm-root { display: flex; align-items: center; gap: 14px; padding: 14px 22px; border-radius: 22px; color: #fff;
  background: var(--cc-grad); box-shadow: 0 14px 32px rgba(14,165,233,.28); }
.mm-root .mm-bulb { width: 46px; height: 46px; }
.mm-root-title { font-weight: 800; font-size: 1.1rem; }
.mm-root-sub { font-size: .85rem; opacity: .95; margin-top: 4px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.mm-root .cc-tag { background: rgba(255,255,255,.22); color: #fff; }
.mm-branches { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 16px; margin-top: 22px;
  padding-top: 22px; border-top: 2px solid var(--cc-line); }
.mm-branch { position: relative; animation: cc-rise .5s ease both; }
.mm-branch::before { content: ""; position: absolute; top: -24px; left: 24px; width: 2px; height: 24px; background: var(--cc-line); }
.mm-node { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 10px 14px; border-radius: 14px;
  font-weight: 750; font-size: .92rem; background: var(--cc-soft); border: 1px solid var(--cc-line); }
.mm-branch.gap .mm-node { background: rgba(245,158,11,.12); border-color: rgba(245,158,11,.35); }
.mm-branch.fill .mm-node { background: rgba(250,204,21,.16); border-color: rgba(234,179,8,.45); }
.mm-chip { font-size: .7rem; font-weight: 800; padding: 3px 9px; border-radius: 999px; white-space: nowrap; }
.mm-chip.ok { background: rgba(16,185,129,.16); color: #059669; } .dark .mm-chip.ok { color: #6EE7B7; }
.mm-chip.bad { background: rgba(239,68,68,.14); color: #DC2626; } .dark .mm-chip.bad { color: #FCA5A5; }
.mm-chip.warn { background: rgba(245,158,11,.18); color: #B45309; } .dark .mm-chip.warn { color: #FCD34D; }
.mm-leaves { margin: 6px 0 0 24px; padding-left: 14px; border-left: 2px solid var(--cc-line); display: flex; flex-direction: column; gap: 8px; }
.mm-leaf { position: relative; display: flex; gap: 10px; align-items: flex-start; padding: 9px 12px; border-radius: 12px;
  background: var(--cc-card); border: 1px solid var(--cc-line); font-size: .88rem; line-height: 1.4; }
.mm-leaf::before { content: ""; position: absolute; left: -16px; top: 20px; width: 14px; height: 2px; background: var(--cc-line); }
.mm-leaf-sub { font-size: .78rem; color: var(--cc-muted); margin-top: 2px; font-style: italic; }
.mm-leaf.lit { border-color: rgba(16,185,129,.45); background: rgba(16,185,129,.07); }
.mm-leaf.dim { opacity: .78; border-style: dashed; }
.mm-leaf.wrong { border-color: rgba(239,68,68,.4); background: rgba(239,68,68,.06); }
.mm-leaf.fill { border-color: rgba(234,179,8,.55); background: rgba(250,204,21,.1); font-weight: 600; }
.mm-leaf.ask { border-color: rgba(14,165,233,.4); }

.mm-bulb { width: 24px; height: 24px; flex-shrink: 0; fill: none; stroke-linecap: round; stroke-linejoin: round; stroke-width: 1.6; }
.mm-bulb .mm-glass { stroke: #94A3B8; fill: rgba(148,163,184,.18); }
.mm-bulb .mm-base { stroke: #94A3B8; stroke-width: 1.8; }
.mm-bulb .mm-rays { stroke: #FACC15; opacity: 0; }
.mm-bulb.lit .mm-glass { fill: #FDE047; stroke: #EAB308; }
.mm-bulb.lit .mm-rays { opacity: 1; }
.mm-bulb.lit { filter: drop-shadow(0 0 6px rgba(16,185,129,.55)); animation: cc-glow 2.6s ease-in-out infinite; }
.mm-bulb.half .mm-glass { fill: rgba(253,224,71,.45); stroke: #EAB308; }
.mm-bulb.dim { opacity: .55; }
.mm-bulb.wrong .mm-glass { fill: rgba(239,68,68,.2); stroke: #EF4444; } .mm-bulb.wrong .mm-base { stroke: #EF4444; }
.mm-bulb.ask .mm-glass { fill: rgba(14,165,233,.18); stroke: #0EA5E9; }
.mm-bulb.fill .mm-glass { stroke: #EAB308; animation: mm-light 1.6s ease .6s both; }
.mm-bulb.fill .mm-rays { animation: mm-rays 1.6s ease .6s both; }
.mm-bulb.fill { filter: drop-shadow(0 0 6px rgba(250,204,21,.6)); }
.mm-root .mm-bulb .mm-glass, .mm-root .mm-bulb .mm-base { stroke: #fff; }
@keyframes mm-light { from { fill: rgba(148,163,184,.18); } to { fill: #FDE047; } }
@keyframes mm-rays { from { opacity: 0; } to { opacity: 1; } }

/* Feedback */
.cc-fb-card { background: var(--cc-card); border: 1px solid var(--cc-line); border-radius: 24px; padding: 20px !important;
  box-shadow: 0 18px 40px rgba(2, 40, 60, .08); gap: 12px !important; margin-top: 16px; animation: cc-rise .5s ease; }
.cc-fb-title { font-size: 1.25rem; font-weight: 800; letter-spacing: -.01em; }
.cc-fb-sub { color: var(--cc-muted); font-size: .9rem; margin-top: 2px; }
.cc-faces { background: transparent !important; border: none !important; }
.cc-faces .wrap { display: flex !important; gap: 10px !important; flex-wrap: wrap; }
.cc-faces label { min-width: 58px; justify-content: center; font-size: 1.6rem !important; padding: 8px 12px !important;
  border-radius: 16px !important; border: 1px solid var(--cc-line) !important; background: var(--cc-soft) !important;
  cursor: pointer; transition: transform .15s ease, box-shadow .15s ease; }
.cc-faces label:hover { transform: translateY(-2px) scale(1.06); }
.cc-faces label.selected { border-color: #14B8A6 !important; box-shadow: 0 0 0 3px rgba(20,184,166,.25); transform: scale(1.1); }
.cc-faces input[type=radio] { display: none !important; }
.cc-thanks { display: flex; gap: 12px; align-items: center; padding: 14px 16px; border-radius: 16px; animation: cc-rise .4s ease;
  background: rgba(16,185,129,.1); border: 1px solid rgba(16,185,129,.35); }
.cc-thanks span { font-size: 1.8rem; }
.cc-thanks p { margin: 2px 0 0; color: var(--cc-muted); font-size: .9rem; }
"""

STYLE = {"css": CSS, "theme": THEME, "head": THEME_JS}


# ---------- layout ----------

chat_kwargs = {"buttons": ["copy"]} if GRADIO_6 else {"type": "messages"}

with gr.Blocks(title="MindGap", **({} if GRADIO_6 else STYLE)) as demo:
    # Fixed secret so a signed-in learner stays signed in after the app restarts. Set BROWSER_STATE_SECRET when deployed.
    user = gr.BrowserState(None, storage_key="mindgap_user",
                           secret=os.environ.get("BROWSER_STATE_SECRET", "concept-check-local-dev"))
    session, phase, case, gap = gr.State(None), gr.State("idle"), gr.State(None), gr.State(None)

    # ----- Login -----
    with gr.Column(visible=True, elem_id="cc-login") as login_view:
        with gr.Row(elem_id="cc-login-card", equal_height=True):
            with gr.Column(scale=11, elem_id="cc-login-art"):
                gr.HTML(LOGIN_ART, elem_classes="cc-bare")
            with gr.Column(scale=10, elem_id="cc-login-form"):
                gr.HTML(f'<div class="cc-form-top"><span></span>{TOGGLE}</div>'
                        '<div class="cc-form-title">Welcome 👋</div>'
                        '<div class="cc-form-sub">Sign in to keep your history, or try it as a guest.</div>',
                        elem_classes="cc-bare")
                with gr.Tabs():
                    with gr.Tab("Sign in"):
                        in_handle = gr.Textbox(label="Nickname", placeholder="e.g. quiet_owl")
                        in_pin = gr.Textbox(label="PIN", type="password", placeholder="4 to 6 digits")
                        signin_btn = gr.Button("Sign in", variant="primary", interactive=False)
                    with gr.Tab("Create account"):
                        new_handle = gr.Textbox(label="Pick a nickname", placeholder="3 to 20 letters, numbers or _")
                        new_pin = gr.Textbox(label="Choose a PIN", type="password", placeholder="4 to 6 digits")
                        reg_consent = gr.Checkbox(label="Save my typed answers under this nickname. No real name, no email.")
                        register_btn = gr.Button("Create account", variant="primary", interactive=False)
                        gr.HTML('<div class="cc-fine">Use a nickname, not your real name. We only keep a hashed PIN.</div>',
                                elem_classes="cc-bare")
                gr.HTML('<div class="cc-or">or</div>', elem_classes="cc-bare")
                guest_consent = gr.Checkbox(label="Save my typed answers anonymously.")
                guest_btn = gr.Button("Continue as guest", variant="secondary", interactive=False)
                login_notice = gr.HTML(elem_classes="cc-bare")

    # ----- App -----
    with gr.Column(visible=False) as app_view:
        with gr.Row(elem_id="cc-nav", equal_height=True):
            gr.HTML(f'<div class="cc-brand">{LOGO}MindGap</div>', elem_classes="cc-bare")
            nav = gr.HTML(elem_classes="cc-bare")
            logout_btn = gr.Button("Log out", size="sm", variant="secondary", elem_id="cc-logout", scale=0)

        with gr.Tabs():
            with gr.Tab("Practice"):
                hero = gr.HTML(elem_classes="cc-bare")
                with gr.Row(equal_height=False):
                    with gr.Column(scale=5, min_width=300):
                        gr.HTML('<div class="cc-section">Choose a concept</div>', elem_classes="cc-bare")
                        concept = gr.Radio(choices=[], show_label=False, elem_id="concept-tiles", container=False)
                        start_btn = gr.Button("Start session  →", variant="primary", size="lg", interactive=False)

                    with gr.Column(scale=7, elem_id="cc-chat-card"):
                        gr.HTML(CHAT_HEAD, elem_classes="cc-bare")
                        progress = gr.HTML(progress_html(0), elem_classes="cc-bare")
                        chat = gr.Chatbot(value=[], height=430, show_label=False, elem_id="thread",
                                          avatar_images=(None, BOT_AVATAR),
                                          placeholder="**Choose a concept** on the left and press **Start session**.",
                                          **chat_kwargs)
                        notice = gr.HTML(elem_classes="cc-bare")
                        with gr.Row(elem_id="cc-composer", equal_height=False):
                            msg = gr.Textbox(lines=2, max_lines=6, show_label=False, container=False,
                                             placeholder=PLACEHOLDER["idle"], interactive=False, scale=9)
                            send_btn = gr.Button("➤", variant="primary", interactive=False, elem_id="cc-send", scale=0)
                        meter_html = gr.HTML(elem_classes="cc-bare")
                        with gr.Column(visible=False) as picker:
                            with gr.Accordion("That's not what I meant", open=False):
                                sentence_dd = gr.Radio(label="Pick the sentence you meant", choices=[])
                                not_meant_btn = gr.Button("Use this sentence", interactive=False)
                        restart_btn = gr.Button("Start another concept", visible=False, variant="secondary")
                mindmap = gr.HTML(visible=False, elem_classes="cc-bare", elem_id="cc-mindmap")
                with gr.Column(visible=False, elem_classes="cc-fb-card") as end_fb:
                    gr.HTML(fb_head("How was this session?", "Be honest. Good or bad, it helps me fix what's broken."),
                            elem_classes="cc-bare")
                    end_rating = gr.Radio(FACES, show_label=False, elem_classes="cc-faces", container=False)
                    end_text = gr.Textbox(lines=2, max_lines=6, show_label=False, max_length=2000,
                                          placeholder="What worked? What confused you? What would you change?")
                    end_send = gr.Button("Send feedback", variant="primary", interactive=False)
                    end_note = gr.HTML(elem_classes="cc-bare")

            with gr.Tab("History") as history_tab:
                gr.HTML('<div class="cc-section">Your past sessions</div>', elem_classes="cc-bare")
                history = gr.HTML(elem_classes="cc-bare")

            with gr.Tab("How it works"):
                gr.HTML(HOW_IT_WORKS, elem_classes="cc-bare")

            with gr.Tab("Feedback"):
                with gr.Column(elem_classes="cc-fb-card"):
                    gr.HTML(fb_head("Tell me what you really think 💬",
                                    "Confusing, boring, useful, broken? Every honest note helps. Only I can read it."),
                            elem_classes="cc-bare")
                    tab_rating = gr.Radio(FACES, label="How did MindGap feel?", elem_classes="cc-faces")
                    tab_text = gr.Textbox(lines=5, max_lines=10, label="Your feedback", max_length=2000,
                                          placeholder="What worked? What confused you? What would you change or add?")
                    tab_send = gr.Button("Send feedback", variant="primary", interactive=False)
                    tab_note = gr.HTML(elem_classes="cc-bare")

    # ----- events (no queue or progress overlays: show_progress="hidden") -----
    entry_out = [user, login_view, app_view, nav, hero, history, login_notice]
    quiet = {"show_progress": "hidden"}
    instant = {"show_progress": "hidden", "queue": False, "trigger_mode": "always_last"}
    pending = gr.State("")

    demo.load(on_load, user, [concept, *entry_out], **quiet)
    in_handle.change(gate_signin, [in_handle, in_pin], signin_btn, **instant)
    in_pin.change(gate_signin, [in_handle, in_pin], signin_btn, **instant)
    for comp in (new_handle, new_pin, reg_consent):
        comp.change(gate_register, [new_handle, new_pin, reg_consent], register_btn, **instant)
    signin_btn.click(on_signin, [in_handle, in_pin], entry_out, **quiet)
    guest_consent.change(lambda c: gr.update(interactive=bool(c)), guest_consent, guest_btn, **instant)
    register_btn.click(on_register, [new_handle, new_pin, reg_consent], entry_out, **quiet)
    guest_btn.click(on_guest, None, entry_out, **quiet)
    logout_btn.click(on_logout, None,
                     [user, login_view, app_view, chat, phase, session, msg, progress, picker, restart_btn, mindmap],
                     **quiet)

    concept.change(lambda c: gr.update(interactive=bool(c)), concept, start_btn, **instant)
    msg.change(meter, [msg, phase], [meter_html, send_btn], **instant)
    sentence_dd.change(lambda s: gr.update(interactive=bool(s)), sentence_dd, not_meant_btn, **instant)

    start_btn.click(on_start, [concept, user],
                    [user, session, phase, case, gap, chat, msg, progress, picker, restart_btn, mindmap, notice], **quiet)
    send_btn.click(on_echo, [msg, chat], [pending, chat, msg, send_btn, meter_html], **instant).then(
        on_send, [pending, phase, session, case, gap, chat],
        [phase, case, gap, chat, msg, progress, picker, sentence_dd, restart_btn, mindmap, notice], **quiet)
    not_meant_btn.click(on_not_meant, [sentence_dd, gap, chat], [gap, chat, notice], **quiet)
    restart_btn.click(on_restart, user,
                      [session, phase, case, gap, chat, msg, progress, picker, restart_btn, concept, notice, hero, history,
                       mindmap],
                      **quiet)
    history_tab.select(on_history, user, [history, hero], **quiet)

    mindmap.change(reset_end_feedback, mindmap, [end_fb, end_rating, end_text, end_note], **instant)
    for rating, text, send, note, source in ((end_rating, end_text, end_send, end_note, "session_end"),
                                             (tab_rating, tab_text, tab_send, tab_note, "tab")):
        rating.change(gate_feedback, [rating, text], send, **instant)
        text.change(gate_feedback, [rating, text], send, **instant)
        send.click(feedback_sender(source), [rating, text, user], [rating, text, send, note], **quiet)


if __name__ == "__main__":
    demo.launch(allowed_paths=[os.path.join(HERE, "assets")], favicon_path=LOGO_PATH, **(STYLE if GRADIO_6 else {}))
