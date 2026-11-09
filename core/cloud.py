"""Optional Supabase backend, so the track record and data survive Streamlit Cloud restarts.

Turned on when credentials are present, either as environment variables
    SUPABASE_URL=https://<ref>.supabase.co   SUPABASE_KEY=<secret key>
or in .streamlit/secrets.toml
    [supabase]
    url = "https://<ref>.supabase.co"
    key = "<secret key>"
Use the SECRET key (Supabase dashboard -> Project Settings -> API Keys -> sb_secret_...). The tables have
row-level security with no policies, so the public/publishable key can read or write nothing; only this
server-side key can. It is never sent to the browser. Without credentials the app uses local files/SQLite.

Talks to Supabase's REST (PostgREST) and Storage APIs with plain `requests`; no extra SDK needed.
"""
from __future__ import annotations

import os

import requests

_client = None
_PAGE = 1000


def settings() -> tuple[str | None, str | None]:
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if url and key:
        return url.rstrip("/"), key
    try:
        import streamlit as st
        s = st.secrets.get("supabase", {})
        if s.get("url") and s.get("key"):
            return s["url"].rstrip("/"), s["key"]
    except Exception:
        pass
    return None, None


class SupabaseError(RuntimeError):
    def __init__(self, status: int, text: str):
        super().__init__(f"Supabase {status}: {text[:300]}")
        self.status = status


class Supa:
    def __init__(self, url: str, key: str, session: requests.Session | None = None, timeout: float = 30):
        self.url, self.timeout = url, timeout
        self.s = session or requests.Session()
        self.h = {"apikey": key}
        if key.startswith("eyJ"):  # legacy JWT service_role key also needs the Authorization header
            self.h["Authorization"] = f"Bearer {key}"

    def _req(self, method, path, ok=(200, 201, 204), **kw) -> requests.Response:
        headers = {**self.h, **kw.pop("headers", {})}
        r = self.s.request(method, f"{self.url}{path}", headers=headers, timeout=self.timeout, **kw)
        if r.status_code not in ok:
            raise SupabaseError(r.status_code, r.text)
        return r

    # ---- tables
    def select(self, table: str, params: dict | None = None) -> list[dict]:
        rows, offset = [], 0
        while True:
            p = {"select": "*", **(params or {}), "limit": _PAGE, "offset": offset}
            batch = self._req("GET", f"/rest/v1/{table}", params=p).json()
            rows += batch
            if len(batch) < _PAGE:
                return rows
            offset += _PAGE

    def insert(self, table: str, rows, upsert_on: str | None = None) -> list[dict]:
        prefer = "return=representation"
        params = {}
        if upsert_on:
            prefer += ",resolution=merge-duplicates"
            params["on_conflict"] = upsert_on
        return self._req("POST", f"/rest/v1/{table}", json=rows, params=params,
                         headers={"Prefer": prefer, "Content-Type": "application/json"}).json()

    def update(self, table: str, match: dict, values: dict) -> list[dict]:
        params = {k: f"eq.{v}" for k, v in match.items()}
        return self._req("PATCH", f"/rest/v1/{table}", json=values, params=params,
                         headers={"Prefer": "return=representation", "Content-Type": "application/json"}).json()

    # ---- storage
    def upload(self, bucket: str, path: str, data: bytes, content_type="application/octet-stream"):
        self._req("POST", f"/storage/v1/object/{bucket}/{path}", data=data,
                  headers={"x-upsert": "true", "Content-Type": content_type})

    def download(self, bucket: str, path: str) -> bytes | None:
        try:
            return self._req("GET", f"/storage/v1/object/{bucket}/{path}").content
        except SupabaseError as e:
            if e.status in (400, 404):  # Storage answers 400 "Object not found" for missing files
                return None
            raise


def client() -> Supa | None:
    """Shared client, or None when Supabase is not configured (local mode)."""
    global _client
    if _client is None:
        url, key = settings()
        if url and key:
            _client = Supa(url, key)
    return _client


def set_client(c: Supa | None):
    """For tests: inject a client (or None to force local mode)."""
    global _client
    _client = c
