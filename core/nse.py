"""NSE end-of-day prices from the official daily bhavcopy (one file = every stock for one day).

Used as a fallback when Yahoo Finance refuses requests, and to build history from scratch if needed.
File (UDiFF format, used by NSE since July 2024):
    https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_<YYYYMMDD>_F_0000.csv.zip

Caveat: bhavcopy prices are the actual traded prices, NOT adjusted for splits, bonuses or dividends.
Yahoo's history is adjusted. Appending NSE days to Yahoo history is correct until a corporate action;
the next successful Yahoo refresh re-syncs the full adjusted history.
"""
from __future__ import annotations

import io
import time
import zipfile
from datetime import date, timedelta

import pandas as pd

URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/124.0 Safari/537.36",
           "Accept": "*/*", "Referer": "https://www.nseindia.com/all-reports"}
SERIES = {"EQ", "BE", "BZ"}


def session():
    import requests
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get("https://www.nseindia.com", timeout=10)  # cookies; archives usually work without, so ignore failure
    except Exception:
        pass
    return s


def parse_bhavcopy(raw: bytes) -> pd.DataFrame:
    """Zip or CSV bytes -> DataFrame indexed by symbol with Open/High/Low/Close/Volume and the trade date."""
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            raw = z.read(z.namelist()[0])
    df = pd.read_csv(io.BytesIO(raw))
    df.columns = [c.strip() for c in df.columns]
    df = df[df["SctySrs"].astype(str).str.strip().isin(SERIES)]
    out = pd.DataFrame({
        "symbol": df["TckrSymb"].astype(str).str.strip(),
        "date": pd.to_datetime(df["TradDt"]).dt.normalize(),
        "Open": pd.to_numeric(df["OpnPric"], errors="coerce"),
        "High": pd.to_numeric(df["HghPric"], errors="coerce"),
        "Low": pd.to_numeric(df["LwPric"], errors="coerce"),
        "Close": pd.to_numeric(df["ClsPric"], errors="coerce"),
        "Volume": pd.to_numeric(df["TtlTradgVol"], errors="coerce"),
    })
    return out.dropna(subset=["Close"]).drop_duplicates("symbol").set_index("symbol")


def fetch_day(d: date, s=None) -> pd.DataFrame | None:
    """One day's bhavcopy, or None on a holiday / not yet published (NSE answers 404)."""
    s = s or session()
    r = s.get(URL.format(d=d), timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return parse_bhavcopy(r.content)


def _weekdays(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def extend_prices(prices: dict, symbols: list[str], fetch=fetch_day, today: date | None = None,
                  max_days: int = 15, pause: float = 0.3) -> tuple[dict, list[date]]:
    """Append every trading day after the last date in `prices` (up to `max_days` back) from NSE."""
    today = today or date.today()
    last = max((df.index.max() for df in prices.values() if len(df)), default=None)
    start = (last.date() + timedelta(days=1)) if last is not None else today - timedelta(days=max_days)
    start = max(start, today - timedelta(days=max_days))
    s = None if fetch is not fetch_day else session()
    added, out = [], {k: v for k, v in prices.items()}
    for d in _weekdays(start, today):
        day = fetch(d, s) if s is not None else fetch(d)
        if day is None or day.empty:
            continue
        for sym in symbols:
            if sym in day.index:
                row = day.loc[sym]
                new = pd.DataFrame([[row.Open, row.High, row.Low, row.Close, row.Volume]],
                                   columns=["Open", "High", "Low", "Close", "Volume"], index=[pd.Timestamp(row.date)])
                base = out.get(sym)
                out[sym] = new if base is None else pd.concat([base[base.index < new.index[0]], new])
        added.append(d)
        time.sleep(pause if fetch is fetch_day else 0)
    return out, added


def build_history(symbols: list[str], days: int = 600, fetch=fetch_day, today: date | None = None,
                  progress=None, pause: float = 0.3) -> dict:
    """Full history from scratch (about 420 trading days for 600 calendar days; a few minutes)."""
    today = today or date.today()
    s = None if fetch is not fetch_day else session()
    frames, wanted = [], set(symbols)
    days_list = list(_weekdays(today - timedelta(days=days), today))
    for i, d in enumerate(days_list):
        try:
            day = fetch(d, s) if s is not None else fetch(d)
        except Exception:
            day = None
        if day is not None and len(day):
            frames.append(day[day.index.isin(wanted)].reset_index())
        if progress:
            progress((i + 1) / len(days_list))
        time.sleep(pause if fetch is fetch_day else 0)
    if not frames:
        return {}
    allp = pd.concat(frames)
    return {sym: g.set_index("date")[["Open", "High", "Low", "Close", "Volume"]].sort_index()
            for sym, g in allp.groupby("symbol")}


LOTS_URL = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"


def parse_lot_sizes(text: str) -> dict:
    """NSE fo_mktlots.csv: UNDERLYING, SYMBOL, then one column per expiry month. Uses the nearest month."""
    df = pd.read_csv(io.StringIO(text))
    df.columns = [c.strip() for c in df.columns]
    month_cols = [c for c in df.columns if c.upper() not in ("UNDERLYING", "SYMBOL")]
    out = {}
    for _, r in df.iterrows():
        sym = str(r["SYMBOL"]).strip()
        for c in month_cols:
            v = str(r[c]).strip()
            if v.isdigit():
                out[sym] = int(v)
                break
    return {k: v for k, v in out.items() if k and k.upper() != "SYMBOL"}


def fetch_lot_sizes(s=None) -> dict:
    s = s or session()
    r = s.get(LOTS_URL, timeout=30)
    r.raise_for_status()
    return parse_lot_sizes(r.text)
