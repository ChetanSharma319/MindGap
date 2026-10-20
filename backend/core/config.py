import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# LLM: any OpenAI-compatible endpoint. Defaults to Groq; switch to Grok (xAI) by changing these two:
#   LLM_BASE_URL=https://api.x.ai/v1   LLM_MODEL=<grok model name>
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# Shared secret between the Gradio app and this server, so strangers can't call the API directly.
FRONTEND_TOKEN = os.environ.get("FRONTEND_TOKEN", "")
