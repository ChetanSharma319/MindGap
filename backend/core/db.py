"""Database access through Supabase's REST API, using the service key (server only)."""

import httpx

from . import config


def _client() -> httpx.Client:
    if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_KEY:
        raise RuntimeError("Supabase is not configured (SUPABASE_URL / SUPABASE_SERVICE_KEY missing)")
    key = config.SUPABASE_SERVICE_KEY
    return httpx.Client(
        base_url=f"{config.SUPABASE_URL.rstrip('/')}/rest/v1",
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=20,
    )


def insert(table: str, row: dict) -> dict:
    with _client() as c:
        r = c.post(f"/{table}", json=row, headers={"Prefer": "return=representation"})
        r.raise_for_status()
        return r.json()[0]


def update(table: str, id_: str, values: dict) -> dict | None:
    with _client() as c:
        r = c.patch(f"/{table}", params={"id": f"eq.{id_}"}, json=values, headers={"Prefer": "return=representation"})
        r.raise_for_status()
        rows = r.json()
        return rows[0] if rows else None


def select(table: str, columns: str = "*", order: str | None = None, **eq) -> list[dict]:
    params = {"select": columns, **{k: f"eq.{v}" for k, v in eq.items()}}
    if order:
        params["order"] = order
    with _client() as c:
        r = c.get(f"/{table}", params=params)
        r.raise_for_status()
        return r.json()


def select_in(table: str, column: str, values: list, columns: str = "*") -> list[dict]:
    if not values:
        return []
    params = {"select": columns, column: f"in.({','.join(str(v) for v in values)})"}
    with _client() as c:
        r = c.get(f"/{table}", params=params)
        r.raise_for_status()
        return r.json()
