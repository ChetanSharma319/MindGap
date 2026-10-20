# MindGap

*A Concept Check app.* Find where a learner stops understanding and starts reciting.

A learner explains a concept in their own words and gets a fresh case. If they miss it, the app quotes the **one sentence** where their explanation turned into a stock phrase and asks **one** "what breaks?" question. Then it gives a **different** fresh case. **Pass or fail is decided by code against an answer key written before the session, never by the AI.**

## How it's built

| Box | Lives on | Does |
|---|---|---|
| UI (`frontend/`, Gradio) | Learner's browser, served from Hugging Face Spaces | Screens only. Holds no secrets |
| Server (`backend/`, FastAPI) | Vercel | Holds the LLM key and answer keys, checks quotes, decides PASS/FAIL |
| LLM (Groq by default) | AI provider | Finds the recited sentence, writes one follow-up, returns quoted evidence |
| Database (`supabase/`) | Supabase | attempts, gaps, case results. No names |

**Code decides:** concept list, which case comes next, answer keys, that every quote really exists in the learner's text, one follow-up only, PASS/FAIL.
**AI decides:** which sentence is recited, the follow-up wording, yes/no evidence per answer-key point (with a quote).
**No-go:** the AI deciding on its own that the learner understands.

**Proof link:** `attempts.gap_id → gaps.id` (every second try points at the gap it answers).

## Setup

### 1. Database (Supabase)
1. Create a project at supabase.com.
2. In the SQL editor, run `supabase/schema.sql`, then `supabase/seed.sql`.
3. From Project Settings → API, copy the **Project URL** and the **service_role** key.

### 2. LLM key
Get a key from console.groq.com. To use Grok (xAI) instead, change `LLM_BASE_URL` and `LLM_MODEL`.

### 3. Server (Vercel)
1. Push this repo to GitHub and import it in Vercel.
2. Set **Root Directory** to `backend`.
3. Add environment variables: `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `FRONTEND_TOKEN` (any long random string).
4. Deploy, then open `https://<your-app>.vercel.app/api/health`. It should show `{"ok": true}`.

### 4. UI (Hugging Face Spaces)
1. Create a new Space with the **Gradio** SDK.
2. Upload the contents of `frontend/`.
3. In Settings → Variables and secrets, add `BACKEND_URL` (your Vercel URL) and `FRONTEND_TOKEN` (same value as on Vercel, as a **secret**).

## Run locally

```bash
# server
cd backend
pip install -r requirements.txt
cp .env.example .env        # fill in the values
uvicorn api.index:app --reload --port 8000

# UI (new terminal)
cd frontend
pip install -r requirements.txt
python app.py               # BACKEND_URL defaults to http://127.0.0.1:8000
```

## Tests

```bash
cd backend
python -m unittest discover -s tests -v    # judge + full flow, no network

cd ..
python eval/run_eval.py                    # real classmate answers vs my hand scores (needs keys)
```

## The three questions the data answers
See `supabase/questions.sql`:
1. Which concept does this learner keep reciting?
2. Did the second try pass a new case?
3. Which sentence do most learners recite? (across learners, no names)
