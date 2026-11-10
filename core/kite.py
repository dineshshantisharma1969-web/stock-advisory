"""Zerodha Kite Connect: daily login, 5-year daily history and F&O lot sizes (plain REST, no SDK).

Setup (once):
  1. Subscribe to Kite Connect at developers.kite.trade and create an app.
  2. Set the app's Redirect URL to this Streamlit app's address, e.g. https://<your-app>.streamlit.app/
  3. Add to Streamlit Secrets:
         [kite]
         api_key = "..."
         api_secret = "..."
Daily:
  Tap "Connect Zerodha" (admin sidebar) -> log in on Zerodha's page -> Zerodha sends you back to the app
  with a one-time request_token -> the app exchanges it (server side, using api_secret) for an access token.
  Kite access tokens expire at 6:00 am IST the next day. The token is stored in the private Supabase bucket
  (or locally without Supabase); the api_secret never leaves the server and your password is typed only at Zerodha.

Use is for the research desk. Showing live exchange data to clients may need an NSE data licence.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd

API = "https://api.kite.trade"
LOGIN = "https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
IST = timezone(timedelta(hours=5, minutes=30))
SESSION_FILE = "kite_session.json"
HIST_PAUSE = 0.35  # Kite allows ~3 historical requests per second


def settings() -> tuple[str | None, str | None]:
    k, s = os.environ.get("KITE_API_KEY"), os.environ.get("KITE_API_SECRET")
    if k and s:
        return k, s
    try:
        import streamlit as st
        sec = st.secrets.get("kite", {})
        return sec.get("api_key"), sec.get("api_secret")
    except Exception:
        return None, None


def configured() -> bool:
    k, s = settings()
    return bool(k and s)


def login_url() -> str | None:
    k, _ = settings()
    return LOGIN.format(api_key=k) if k else None


def checksum(api_key: str, request_token: str, api_secret: str) -> str:
    return hashlib.sha256((api_key + request_token + api_secret).encode()).hexdigest()


def valid_until(created: datetime) -> datetime:
    """Kite tokens expire at the next 6:00 am IST after login."""
    c = created.astimezone(IST)
    six = c.replace(hour=6, minute=0, second=0, microsecond=0)
    return six if c < six else six + timedelta(days=1)


# ------------------------------------------------------------------ session storage
def _store_put(blob: dict):
    from . import cloud, config
    raw = json.dumps(blob).encode()
    c = cloud.client()
    if c:
        c.upload("desk-cache", SESSION_FILE, raw, "application/json")
    else:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (config.CACHE_DIR / SESSION_FILE).write_bytes(raw)


def _store_get() -> dict | None:
    from . import cloud, config
    c = cloud.client()
    raw = c.download("desk-cache", SESSION_FILE) if c else None
    if raw is None and (config.CACHE_DIR / SESSION_FILE).exists() and not c:
        raw = (config.CACHE_DIR / SESSION_FILE).read_bytes()
    return json.loads(raw) if raw else None


def exchange_token(request_token: str, session=None) -> dict:
    """Swap the one-time request_token for an access token and store it."""
    import requests
    api_key, api_secret = settings()
    if not (api_key and api_secret):
        raise RuntimeError("Kite api_key / api_secret are not set in Secrets")
    s = session or requests.Session()
    r = s.post(f"{API}/session/token", headers={"X-Kite-Version": "3"}, timeout=20,
               data={"api_key": api_key, "request_token": request_token,
                     "checksum": checksum(api_key, request_token, api_secret)})
    js = r.json()
    if r.status_code != 200 or js.get("status") != "success":
        raise RuntimeError(f"Kite login failed: {js.get('message', r.text[:200])}")
    now = datetime.now(timezone.utc)
    blob = {"access_token": js["data"]["access_token"], "user_id": js["data"].get("user_id"),
            "created_utc": now.isoformat(), "valid_until": valid_until(now).isoformat()}
    _store_put(blob)
    return blob


def session_info(now: datetime | None = None) -> dict | None:
    """The stored session if it is still valid, else None."""
    try:
        blob = _store_get()
    except Exception:
        return None
    if not blob:
        return None
    now = now or datetime.now(timezone.utc)
    return blob if datetime.fromisoformat(blob["valid_until"]) > now else None


# ------------------------------------------------------------------ data
class KiteClient:
    def __init__(self, api_key: str, access_token: str, session=None):
        import requests
        self.s = session or requests.Session()
        self.h = {"X-Kite-Version": "3", "Authorization": f"token {api_key}:{access_token}"}

    def _get(self, path, params=None):
        r = self.s.get(f"{API}{path}", headers=self.h, params=params, timeout=30)
        if r.status_code == 403:
            raise PermissionError("Kite session expired - tap Connect Zerodha again")
        if r.status_code != 200:
            raise RuntimeError(f"Kite {path} -> {r.status_code}: {r.text[:200]}")
        return r

    def instruments(self, exchange: str = "NSE") -> pd.DataFrame:
        return pd.read_csv(io.StringIO(self._get(f"/instruments/{exchange}").text))

    def historical(self, token: int, start: date, end: date, interval: str = "day") -> pd.DataFrame:
        js = self._get(f"/instruments/historical/{int(token)}/{interval}",
                       {"from": f"{start:%Y-%m-%d} 00:00:00", "to": f"{end:%Y-%m-%d} 23:59:59"}).json()
        candles = js.get("data", {}).get("candles", [])
        if not candles:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        df = pd.DataFrame(candles).iloc[:, :6]
        df.columns = ["date", "Open", "High", "Low", "Close", "Volume"]
        df.index = pd.to_datetime(df.pop("date").str[:10])
        return df.astype(float)

    def quote(self, instruments: list[str]) -> dict:
        out = {}
        for i in range(0, len(instruments), 400):
            out.update(self._get("/quote", [("i", x) for x in instruments[i:i + 400]]).json().get("data", {}))
        return out


def client() -> KiteClient | None:
    api_key, _ = settings()
    info = session_info()
    return KiteClient(api_key, info["access_token"]) if api_key and info else None


def prices(symbols: list[str], years: int = 5, kc: KiteClient | None = None, progress=None,
           today: date | None = None, pause: float = HIST_PAUSE) -> dict:
    """Daily OHLCV for NSE equities. Kite returns at most 2000 days per call, so 5 years is one call each."""
    kc = kc or client()
    if kc is None:
        raise RuntimeError("Zerodha is not connected today")
    inst = kc.instruments("NSE")
    eq = inst[(inst["segment"] == "NSE") & (inst["instrument_type"] == "EQ")]
    tokens = dict(zip(eq["tradingsymbol"], eq["instrument_token"]))
    end = today or date.today()
    start = end - timedelta(days=int(365.25 * years))
    out = {}
    for i, sym in enumerate(symbols):
        if sym in tokens:
            df = kc.historical(tokens[sym], start, end)
            if len(df):
                out[sym] = df
            time.sleep(pause)
        if progress:
            progress((i + 1) / len(symbols))
    return out


def lot_sizes(kc: KiteClient | None = None) -> dict:
    """Stock and index F&O lot sizes from the NFO futures list (nearest expiry)."""
    kc = kc or client()
    if kc is None:
        return {}
    nfo = kc.instruments("NFO")
    fut = nfo[nfo["instrument_type"] == "FUT"].sort_values("expiry")
    return {str(n): int(l) for n, l in fut.drop_duplicates("name")[["name", "lot_size"]].itertuples(index=False)}
