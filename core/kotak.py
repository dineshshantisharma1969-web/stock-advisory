"""Kotak Neo Trade API (free for Kotak Securities clients): daily candles, quotes and F&O lot sizes.

Market data needs ONLY the consumer key - no daily TOTP/MPIN login (that is only for placing orders,
which this app never does). Endpoints and headers follow Kotak's official SDK (Kotak-Neo/kotak-neo-python,
v3.0.x); we call the REST API directly because the SDK pins pandas<3.

Setup (once):  Kotak Neo app/web -> More -> Trade API -> Generate application -> copy the token, then in
Streamlit Secrets:
    [kotak]
    consumer_key = "..."

Limits (Kotak backend): daily candles at most 180 days per request. So Kotak is used to TOP UP the saved
history (one request per stock covers the latest ~6 months), while the first 5-year history comes from Yahoo.
Kotak candles are actual traded prices (verify adjustment for splits/bonuses before relying on long history).
"""
from __future__ import annotations

import io
import os
import time
from datetime import date, timedelta

import pandas as pd

BASE = "https://mis.kotaksecurities.com"
SCRIP_PATHS = "/script-details/1.0/masterscrip/file-paths"
HISTORICAL = "/market-data/1.0/historical/details"
MAX_DAILY_SPAN = 180      # days per daily-candle request (backend limit)
PAUSE = 0.25


def _clean(v):
    return str(v).strip().strip('"').strip("'").strip() if v else None


def consumer_key() -> str | None:
    k = os.environ.get("KOTAK_CONSUMER_KEY")
    if k:
        return _clean(k)
    try:
        import streamlit as st
        return _clean(st.secrets.get("kotak", {}).get("consumer_key"))
    except Exception:
        return None


def configured() -> bool:
    return bool(consumer_key())


def masked_key() -> str:
    k = consumer_key()
    return f"{k[:4]}…{k[-4:]} ({len(k)} chars)" if k and len(k) > 8 else "not set"


class KotakClient:
    def __init__(self, key: str, session=None):
        import requests
        self.s = session or requests.Session()
        # Same headers as Kotak's official SDK (neo_api_client 3.0.x rest client)
        self.h = {"Authorization": key, "Content-Type": "application/x-www-form-urlencoded",
                  "User-Agent": "NeoSDK-Python/3.0.13", "Accept": "application/json",
                  "Accept-Encoding": "gzip, deflate"}

    def _get(self, url, params=None):
        r = self.s.get(url if url.startswith("http") else BASE + url, headers=self.h, params=params, timeout=30)
        if r.status_code == 429:
            raise RuntimeError("Kotak rate limit reached (429) - try again in a few minutes")
        text = self._mask(r.text)
        if r.status_code in (401, 403) or ("consumer key" in text.lower() and "invalid" in text.lower()):
            raise PermissionError("Kotak rejected the consumer key - copy the CURRENT token from the Neo Trade API "
                                  f"page into [kotak] consumer_key. Kotak said: {text[:160]}")
        if r.status_code != 200:
            raise RuntimeError(f"Kotak {url.split('?')[0][-60:]} -> {r.status_code}: {text[:200]}")
        return r

    def _mask(self, text: str) -> str:
        """Never show or store the key: Kotak echoes it back in its error messages."""
        import re
        key = self.h.get("Authorization") or ""
        if len(key) > 8:
            text = text.replace(key, f"{key[:4]}...{key[-4:]}")
        return re.sub(r"([0-9a-fA-F]{4})[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{8}([0-9a-fA-F]{4})",
                      r"\1...\2", text)

    def scrip_file(self, segment: str = "nse_cm") -> pd.DataFrame:
        paths = self._get(SCRIP_PATHS).json()["data"]["filesPaths"]
        # file naming varies (nse_cm-v1.csv, nse_fo.csv); never match nse_com when asked for nse_cm
        want = [p for p in paths if p.rsplit("/", 1)[-1].lower().startswith(segment.lower())
                and not p.rsplit("/", 1)[-1].lower().startswith(segment.lower() + "m")]
        if not want:
            raise RuntimeError(f"Kotak scrip master has no file for {segment}")
        # the file sits on a public file store: send NO Kotak key there (it rejects unknown Authorization headers)
        r = self.s.get(want[0], headers={"User-Agent": self.h["User-Agent"]}, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"Kotak scrip file download -> {r.status_code}")
        df = pd.read_csv(io.StringIO(r.text), low_memory=False)
        df.columns = [c.strip().rstrip(";") for c in df.columns]
        return df

    def daily(self, token, start: date, end: date, segment: str = "nse_cm") -> pd.DataFrame:
        js = self._get(HISTORICAL, {"neosymbol": f"{segment}|{token}", "interval": "D",
                                    "fromdate": f"{start:%Y-%m-%d}", "todate": f"{end:%Y-%m-%d}"}).json()
        if str(js.get("status", "")).lower() not in ("success", "ok"):
            raise RuntimeError(f"Kotak historical: {js.get('fault') or js}")
        candles = (js.get("data") or {}).get("candles") or []
        if not candles:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        df = pd.DataFrame([c[:6] for c in candles], columns=["date", "Open", "High", "Low", "Close", "Volume"])
        df.index = pd.to_datetime(df.pop("date").astype(str).str[:10])
        return df.astype(float).sort_index()


def client() -> KotakClient | None:
    k = consumer_key()
    return KotakClient(k) if k else None


def equity_tokens(kc: KotakClient) -> dict:
    """NSE symbol -> Kotak instrument token, preferring the EQ series (then BE)."""
    df = kc.scrip_file("nse_cm")
    trd = df["pTrdSymbol"].astype(str).str.strip()
    out = {}
    for series in ("BE", "EQ"):          # EQ written last so it wins
        m = trd.str.endswith("-" + series)
        for t, s in zip(df.loc[m, "pSymbol"], trd[m]):
            out[s[: -len(series) - 1]] = int(t)
    return out


def top_up(prices: dict, symbols: list[str], kc: KotakClient | None = None, today: date | None = None,
           progress=None, pause: float = PAUSE) -> tuple[dict, int]:
    """Append the sessions missing since each stock's last saved bar (up to 180 days back).
    Returns (updated prices, number of stocks updated). Existing history is kept as is."""
    kc = kc or client()
    if kc is None:
        raise RuntimeError("Kotak consumer key not set")
    tokens = equity_tokens(kc)
    end = today or date.today()
    out, n = dict(prices), 0
    for i, sym in enumerate(symbols):
        old = prices.get(sym)
        if sym in tokens:
            last = old.index.max().date() if old is not None and len(old) else end - timedelta(days=MAX_DAILY_SPAN)
            start = max(last + timedelta(days=1), end - timedelta(days=MAX_DAILY_SPAN - 1))
            if start <= end:
                new = kc.daily(tokens[sym], start, end)
                if len(new):
                    out[sym] = new if old is None else pd.concat([old[old.index < new.index[0]], new])
                    n += 1
                time.sleep(pause)
        if progress:
            progress((i + 1) / len(symbols))
    return out, n


def lot_sizes(kc: KotakClient | None = None) -> dict:
    kc = kc or client()
    if kc is None:
        return {}
    df = kc.scrip_file("nse_fo")
    lot_col = next((c for c in ("lLotSize", "iLotSize") if c in df.columns), None)
    if lot_col is None or "pInstType" not in df.columns:
        return {}
    fut = df[df["pInstType"].astype(str).str.startswith("FUT")]
    return {str(n).strip(): int(l) for n, l in fut.drop_duplicates("pSymbolName")[["pSymbolName", lot_col]]
            .itertuples(index=False) if pd.notna(l)}
