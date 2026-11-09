"""Data layer: Nifty 500 universe, prices and fundamentals.

Two providers share one interface:
  * YahooProvider - live data from Yahoo Finance (symbol + ".NS"), cached to data/cache/
  * DemoProvider  - synthetic, deterministic data for testing the app offline. Never real.

Everything downstream (valuation, technicals, breadth) only sees:
  prices: dict[symbol -> DataFrame[Open, High, Low, Close, Volume]] indexed by date
  fundamentals: dict[symbol -> dict] in the schema documented in `empty_fundamentals`.
"""
from __future__ import annotations

import hashlib
import pickle
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from . import config


# ---------------------------------------------------------------- universe
def load_universe() -> pd.DataFrame:
    df = pd.read_csv(config.UNIVERSE_CSV, encoding="utf-8-sig")
    df = df.rename(columns={"Company Name": "name", "Industry": "industry", "Symbol": "symbol",
                            "Series": "series", "ISIN Code": "isin"})
    # NSE puts temporary DUMMY placeholders in the list during demergers; they are not tradable.
    df = df[~df["symbol"].str.startswith("DUMMY")].reset_index(drop=True)
    return df


def yahoo_symbol(nse_symbol: str) -> str:
    return f"{nse_symbol}.NS"


# ---------------------------------------------------------------- schema
def empty_fundamentals(symbol: str) -> dict:
    """Annual series are lists ordered OLDEST -> NEWEST. Money in INR (absolute)."""
    return {
        "symbol": symbol, "price": np.nan, "shares": np.nan, "market_cap": np.nan,
        "total_debt": np.nan, "cash": np.nan, "book_equity": np.nan, "beta": np.nan,
        "revenue": [], "ebit": [], "ebitda": [], "net_income": [], "eps": [],
        "ocf": [], "capex": [], "interest_expense": np.nan, "tax_rate": np.nan,
        "dividend_yield": np.nan, "source": "", "as_of": "",
        # quarterly results, aligned by period end (oldest -> newest); NaN where Yahoo has no figure
        "quarters": {"period_end": [], "revenue": [], "ebit": [], "net_income": [], "eps": []},
    }


# ---------------------------------------------------------------- cache
# Local pickle files, mirrored to a private Supabase Storage bucket when Supabase is configured.
# A tiny "<name>.meta" file holds the save time, so the app only re-downloads a big file when it changed
# (e.g. after the scheduled refresh job uploaded new data).
BUCKET = "desk-cache"


def _cache_path(name: str):
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return config.CACHE_DIR / f"{name}.pkl"


def save_cache(name: str, obj) -> None:
    from . import cloud
    blob = {"saved": datetime.now(), "data": obj}
    raw = pickle.dumps(blob)
    _cache_path(name).write_bytes(raw)
    c = cloud.client()
    if c:
        c.upload(BUCKET, f"{name}.pkl", raw)
        c.upload(BUCKET, f"{name}.meta", blob["saved"].isoformat().encode(), "text/plain")


def _local(name: str):
    p = _cache_path(name)
    if not p.exists():
        return None
    with open(p, "rb") as f:
        return pickle.load(f)


def _sync_from_cloud(name: str):
    """Download the cloud copy if it is newer than the local one. Returns the freshest blob or None."""
    from . import cloud
    blob = _local(name)
    c = cloud.client()
    if not c:
        return blob
    try:
        meta = c.download(BUCKET, f"{name}.meta")
        if meta is None:
            return blob
        remote_saved = datetime.fromisoformat(meta.decode())
        if blob is None or blob["saved"] < remote_saved:
            raw = c.download(BUCKET, f"{name}.pkl")
            if raw:
                _cache_path(name).write_bytes(raw)
                blob = pickle.loads(raw)
    except Exception:
        pass  # network trouble: fall back to whatever is local
    return blob


def load_cache(name: str, max_age_hours: float | None = None):
    blob = _sync_from_cloud(name)
    if blob is None:
        return None, None
    if max_age_hours is not None and datetime.now() - blob["saved"] > timedelta(hours=max_age_hours):
        return None, blob["saved"]
    return blob["data"], blob["saved"]


def cache_saved_at(name: str):
    """Save time only (cheap: reads the small meta file, never the data)."""
    from . import cloud
    c = cloud.client()
    if c:
        try:
            meta = c.download(BUCKET, f"{name}.meta")
            if meta:
                return datetime.fromisoformat(meta.decode())
        except Exception:
            pass
    blob = _local(name)
    return blob["saved"] if blob else None


# ---------------------------------------------------------------- Yahoo provider
def _row(df: pd.DataFrame, *names) -> list:
    """First matching statement row as an oldest->newest list of floats."""
    if df is None or df.empty:
        return []
    for n in names:
        if n in df.index:
            s = pd.to_numeric(df.loc[n], errors="coerce")
            s = s.sort_index()  # columns are dates
            return [float(x) for x in s.values if pd.notna(x)]
    return []


def _quarters(df: pd.DataFrame) -> dict:
    """Quarterly statement -> aligned lists. Unlike _row, keeps NaNs so the columns stay in step."""
    q = {"period_end": [], "revenue": [], "ebit": [], "net_income": [], "eps": []}
    if df is None or df.empty:
        return q
    df = df.T.sort_index()
    pick = {"revenue": ("Total Revenue", "Operating Revenue"), "ebit": ("EBIT", "Operating Income"),
            "net_income": ("Net Income Common Stockholders", "Net Income"), "eps": ("Diluted EPS", "Basic EPS")}
    q["period_end"] = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in df.index]
    for k, names in pick.items():
        col = next((n for n in names if n in df.columns), None)
        q[k] = [float(v) if pd.notna(v) else np.nan for v in
                (pd.to_numeric(df[col], errors="coerce") if col else [np.nan] * len(df))]
    return q


def _last(lst, default=np.nan):
    return lst[-1] if lst else default


class YahooProvider:
    name = "Yahoo Finance"

    def prices(self, symbols: list[str], period: str = "2y", chunk: int = 80, progress=None) -> dict:
        import yfinance as yf
        out: dict[str, pd.DataFrame] = {}
        for i in range(0, len(symbols), chunk):
            batch = symbols[i:i + chunk]
            raw = yf.download([yahoo_symbol(s) for s in batch], period=period, interval="1d",
                              group_by="ticker", auto_adjust=True, threads=True, progress=False)
            for s in batch:
                try:
                    df = raw[yahoo_symbol(s)] if isinstance(raw.columns, pd.MultiIndex) else raw
                    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
                    if len(df):
                        out[s] = df
                except KeyError:
                    pass
            if progress:
                progress(min(i + chunk, len(symbols)) / len(symbols))
        return out

    def fundamentals(self, symbol: str) -> dict:
        import yfinance as yf
        t = yf.Ticker(yahoo_symbol(symbol))
        f = empty_fundamentals(symbol)
        info = {}
        try:
            info = t.info or {}
        except Exception:
            pass
        inc, bs, cf = t.income_stmt, t.balance_sheet, t.cashflow
        f.update({
            "price": info.get("currentPrice") or info.get("regularMarketPrice") or np.nan,
            "shares": info.get("sharesOutstanding") or np.nan,
            "market_cap": info.get("marketCap") or np.nan,
            "beta": info.get("beta") or np.nan,
            "dividend_yield": (info.get("dividendYield") or 0) / 100 if (info.get("dividendYield") or 0) > 1
                              else (info.get("dividendYield") or np.nan),
            "revenue": _row(inc, "Total Revenue", "Operating Revenue"),
            "ebit": _row(inc, "EBIT", "Operating Income"),
            "ebitda": _row(inc, "EBITDA", "Normalized EBITDA"),
            "net_income": _row(inc, "Net Income Common Stockholders", "Net Income"),
            "eps": _row(inc, "Diluted EPS", "Basic EPS"),
            "ocf": _row(cf, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities"),
            "capex": _row(cf, "Capital Expenditure"),
            "interest_expense": abs(_last(_row(inc, "Interest Expense"))),
            "total_debt": _last(_row(bs, "Total Debt"), info.get("totalDebt") or np.nan),
            "cash": _last(_row(bs, "Cash Cash Equivalents And Short Term Investments",
                               "Cash And Cash Equivalents"), info.get("totalCash") or np.nan),
            "book_equity": _last(_row(bs, "Stockholders Equity", "Common Stock Equity")),
            "source": self.name, "as_of": datetime.now().strftime("%Y-%m-%d"),
        })
        try:
            f["quarters"] = _quarters(t.quarterly_income_stmt)
        except Exception:
            pass
        pretax, tax = _last(_row(inc, "Pretax Income")), _last(_row(inc, "Tax Provision"))
        if pretax and pretax > 0 and pd.notna(tax):
            f["tax_rate"] = float(np.clip(tax / pretax, 0, 0.4))
        if np.isnan(f["market_cap"]) and pd.notna(f["price"]) and pd.notna(f["shares"]):
            f["market_cap"] = f["price"] * f["shares"]
        return f

    def all_fundamentals(self, symbols: list[str], progress=None, pause: float = 0.3) -> dict:
        out = {}
        for i, s in enumerate(symbols):
            try:
                out[s] = self.fundamentals(s)
            except Exception as e:  # keep going; one bad ticker should not stop 500
                f = empty_fundamentals(s)
                f["source"] = f"error: {e}"
                out[s] = f
            if progress:
                progress((i + 1) / len(symbols))
            time.sleep(pause)
        return out


# ---------------------------------------------------------------- Demo provider
def _seed(symbol: str) -> int:
    return int(hashlib.md5(symbol.encode()).hexdigest()[:8], 16)


class DemoProvider:
    """Synthetic data with realistic shapes. Lets the whole app run with no internet."""
    name = "DEMO (synthetic - not real market data)"

    def __init__(self, days: int = 500, end: str | None = None):
        self.days = days
        self.end = pd.Timestamp(end) if end else pd.Timestamp.today().normalize()
        self.dates = pd.bdate_range(end=self.end, periods=days)
        # common market factor so breadth looks like a real market
        rng = np.random.default_rng(42)
        self.market = rng.normal(0.0004, 0.009, days)

    def _series(self, symbol: str) -> pd.DataFrame:
        rng = np.random.default_rng(_seed(symbol))
        beta = rng.uniform(0.6, 1.4)
        drift = rng.normal(0.0003, 0.0006)
        idio = rng.normal(0, rng.uniform(0.008, 0.022), self.days)
        rets = drift + beta * self.market + idio
        close = rng.uniform(80, 4000) * np.exp(np.cumsum(rets))
        spread = np.abs(rng.normal(0, 0.008, self.days))
        open_ = close * (1 + rng.normal(0, 0.004, self.days))
        high = np.maximum(open_, close) * (1 + spread)
        low = np.minimum(open_, close) * (1 - spread)
        vol = rng.lognormal(13, 0.5, self.days).astype(int)
        return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
                            index=self.dates)

    def prices(self, symbols, period="2y", progress=None) -> dict:
        return {s: self._series(s) for s in symbols}

    def fundamentals(self, symbol: str, industry: str = "") -> dict:
        rng = np.random.default_rng(_seed(symbol) + 1)
        f = empty_fundamentals(symbol)
        price = float(self._series(symbol)["Close"].iloc[-1])
        shares = rng.uniform(5e7, 3e9)
        rev0 = rng.uniform(5e9, 5e11)
        g = rng.normal(0.12, 0.08)
        revenue = [rev0 * (1 + g + rng.normal(0, 0.04)) ** i for i in range(4)]
        margin = rng.uniform(0.06, 0.3)
        ebit = [r * (margin + rng.normal(0, 0.015)) for r in revenue]
        ni = [e * 0.72 for e in ebit]
        f.update({
            "price": price, "shares": shares, "market_cap": price * shares,
            "beta": rng.uniform(0.6, 1.4), "dividend_yield": rng.uniform(0, 0.03),
            "revenue": revenue, "ebit": ebit, "ebitda": [e * 1.25 for e in ebit], "net_income": ni,
            "eps": [n / shares for n in ni],
            "ocf": [e * rng.uniform(0.8, 1.2) for e in ebit],
            "capex": [-r * rng.uniform(0.02, 0.08) for r in revenue],
            "interest_expense": ebit[-1] * rng.uniform(0.01, 0.3),
            "total_debt": revenue[-1] * rng.uniform(0, 0.6), "cash": revenue[-1] * rng.uniform(0.02, 0.2),
            "book_equity": revenue[-1] * rng.uniform(0.3, 1.2), "tax_rate": 0.25,
            "source": self.name, "as_of": self.end.strftime("%Y-%m-%d"),
        })
        # 8 synthetic quarters ending at the last completed quarter-end
        ends = pd.date_range(end=self.end - pd.offsets.QuarterEnd(0) if self.end.is_quarter_end
                             else self.end - pd.offsets.QuarterEnd(1), periods=8, freq="QE")
        qrev = [revenue[-1] / 4 * (1 + g) ** ((i - 7) / 4) * (1 + rng.normal(0, 0.05)) for i in range(8)]
        qm = [margin + rng.normal(0, 0.02) for _ in range(8)]
        qebit = [r * m for r, m in zip(qrev, qm)]
        qni = [e * 0.72 for e in qebit]
        f["quarters"] = {"period_end": [d.strftime("%Y-%m-%d") for d in ends], "revenue": qrev, "ebit": qebit,
                         "net_income": qni, "eps": [n / shares for n in qni]}
        return f

    def all_fundamentals(self, symbols, progress=None) -> dict:
        return {s: self.fundamentals(s) for s in symbols}


def get_provider(demo: bool):
    return DemoProvider() if demo else YahooProvider()
