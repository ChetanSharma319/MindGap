"""Vercel entrypoint: Vercel's FastAPI support looks for an `app` in main.py."""

from api.index import app  # noqa: F401
