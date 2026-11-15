"""Market pulse: the day's context from official / public data, in one place.

  * Oil: Brent and WTI, daily and 5-day move (Yahoo Finance).
  * FII / DII cash-market net buying (NSE, provisional) - table fii_dii.
  * FII positions in index futures, index options and stock futures, with the day's OI build-up
    (NSE "participant-wise open interest", published every evening) - table participant_oi.
  * Market breadth of the Nifty 500 - table breadth_daily.

What 5 years of history say (Nov 2021 - Nov 2026, 1,243 sessions; see EVIDENCE): none of these is a
reliable day-trading signal for Nifty. They are context for position size and caution - not calls. The
bot saves every day's reading in desk_journal so the question can be re-tested on new data.
"""
from __future__ import annotations

import io
from datetime import date, timedelta

import pandas as pd

from . import nse

URL = "https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{d:%d%m%Y}.csv"
PARTICIPANTS = ("FII", "DII", "Client", "Pro")
COLS = {"Future Index Long": "fut_idx_long", "Future Index Short": "fut_idx_short",
        "Future Stock Long": "fut_stk_long", "Future Stock Short": "fut_stk_short",
        "Option Index Call Long": "opt_idx_call_long", "Option Index Put Long": "opt_idx_put_long",
        "Option Index Call Short": "opt_idx_call_short", "Option Index Put Short": "opt_idx_put_short",
        "Option Stock Call Long": "opt_stk_call_long", "Option Stock Put Long": "opt_stk_put_long",
        "Option Stock Call Short": "opt_stk_call_short", "Option Stock Put Short": "opt_stk_put_short"}
MIN_HISTORY = 100       # sessions; with fewer saved, the next run back-fills the whole window
MACRO = {"nifty": "^NSEI", "vix": "^INDIAVIX", "brent": "BZ=F", "wti": "CL=F"}

# Result of the Nov 2026 test: (indicator, what history showed, usable?)
EVIDENCE = [
    ("FII index-options position, daily change",
     "The only one that pointed the same way in all 6 years - but what it predicts is the next morning's "
     "opening gap (about +13 bp after a bullish day vs -1 bp after a bearish one). The data is published "
     "after the close, so the gap cannot be captured; from the open onward there is no edge.", "context"),
    ("FII index-futures position, daily change (OI build-up)",
     "Same pattern, weaker: predicts the overnight gap in 5 of 6 years, nothing after the open.", "context"),
    ("FII index-futures long % (level)", "No consistent relation to the next 1 or 5 days.", "no"),
    ("Oil (Brent) 5-day move",
     "Falling oil was followed by a better Nifty week on average (+46 bp vs -2 bp after a sharp rise), "
     "but the sign flipped in 2 of 6 years.", "weak"),
    ("India VIX level", "High VIX was followed by higher 5-day returns (and bigger swings) in every year "
                        "with data - a reason not to panic-sell, not a timing tool.", "weak"),
    ("Market breadth (% above 50/200 DMA, McClellan, new highs)",
     "No predictive value for Nifty itself. It matters for stock strategies: the Momentum book already "
     "stays in cash when fewer than 50% of stocks are above their 200 DMA.", "filter"),
    ("FII / DII cash net buying", "NSE publishes only the latest day, so there is no long history to test "
                                  "yet. The journal now records it daily.", "untested"),
]


# ------------------------------------------------------------------ participant-wise open interest
def parse_participant(raw: bytes, d: date) -> list[dict]:
    """NSE's csv (a title line, then one row per participant) -> rows for participant_oi."""
    text = raw.decode("utf-8", "replace")
    df = pd.read_csv(io.StringIO(text), skiprows=1)
    df.columns = [str(c).strip() for c in df.columns]
    df["Client Type"] = df["Client Type"].astype(str).str.strip()
    out = []
    for _, r in df[df["Client Type"].isin(PARTICIPANTS)].iterrows():
        row = {"date": d.isoformat(), "participant": r["Client Type"]}
        row.update({col: int(float(r[src])) for src, col in COLS.items()})
        out.append(row)
    if len(out) != len(PARTICIPANTS):
        raise ValueError(f"participant OI {d}: expected {len(PARTICIPANTS)} rows, got {len(out)}")
    return out


def fetch_participant(d: date, s=None) -> list[dict] | None:
    """One day's rows, or None on a holiday / not yet published (NSE answers 404)."""
    s = s or nse.session()
    r = s.get(URL.format(d=d), timeout=40)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return parse_participant(r.content, d)


def update_participant(client, today: date | None = None, backfill_days: int = 180, fetch=None,
                       log=lambda m: None) -> int:
    """Add every missing session up to `today` (the first run back-fills about 6 months). Returns days added."""
    today = today or date.today()
    if fetch is None:
        s = nse.session()
        fetch = lambda d: fetch_participant(d, s)        # noqa: E731
    have = {str(r["date"])[:10] for r in client.select(
        "participant_oi", {"select": "date", "participant": "eq.FII", "order": "date.desc", "limit": "400"})}
    start = today - timedelta(days=backfill_days)
    if len(have) >= MIN_HISTORY:                    # history is in place: only the days after the last one
        start = max(start, date.fromisoformat(max(have)) + timedelta(days=1))
    added, d = 0, start
    while d <= today:
        if d.weekday() < 5 and d.isoformat() not in have:
            try:
                rows = fetch(d)
            except Exception as e:
                log(f"participant OI {d}: {e}")
                rows = None
            if rows:
                client.insert("participant_oi", rows, upsert_on="date,participant")
                added += 1
        d += timedelta(days=1)
    return added


def positions(rows: list[dict], participant: str = "FII") -> pd.DataFrame:
    """Daily net positions (contracts) and their day-on-day change for one participant, oldest first."""
    df = pd.DataFrame([r for r in rows if r["participant"] == participant])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").set_index("date").astype({c: float for c in COLS.values()})
    out = pd.DataFrame(index=df.index)
    out["fut_long"], out["fut_short"] = df["fut_idx_long"], df["fut_idx_short"]
    out["fut_net"] = df["fut_idx_long"] - df["fut_idx_short"]
    out["fut_long_pct"] = df["fut_idx_long"] / (df["fut_idx_long"] + df["fut_idx_short"]) * 100
    # bullish option exposure: (calls bought - calls sold) - (puts bought - puts sold)
    out["call_net"] = df["opt_idx_call_long"] - df["opt_idx_call_short"]
    out["put_net"] = df["opt_idx_put_long"] - df["opt_idx_put_short"]
    out["opt_net"] = out["call_net"] - out["put_net"]
    out["stk_fut_net"] = df["fut_stk_long"] - df["fut_stk_short"]
    for c in ("fut_long", "fut_short", "fut_net", "opt_net", "stk_fut_net"):
        out[c + "_chg"] = out[c].diff()
    out["fut_action"] = [fut_action(a, b) for a, b in zip(out["fut_long_chg"], out["fut_short_chg"])]
    return out


def fut_action(d_long: float, d_short: float) -> str:
    """Name the day's OI build-up from the change in long and short contracts (the bigger move wins)."""
    if pd.isna(d_long) or pd.isna(d_short) or (d_long == 0 and d_short == 0):
        return "-"
    if abs(d_long) >= abs(d_short):
        return "Long build-up" if d_long > 0 else "Long unwinding"
    return "Short build-up" if d_short > 0 else "Short covering"


# ------------------------------------------------------------------ oil, Nifty, VIX
def fetch_macro(period: str = "3mo") -> pd.DataFrame:
    """Daily closes of Nifty, India VIX, Brent and WTI (columns nifty, vix, brent, wti)."""
    import yfinance as yf
    raw = yf.download(list(MACRO.values()), period=period, auto_adjust=True, progress=False)["Close"]
    return raw.rename(columns={v: k for k, v in MACRO.items()}).sort_index()


def _move(s: pd.Series, upto=None) -> dict:
    s = s.dropna()
    if upto is not None:
        s = s[s.index <= pd.Timestamp(upto)]
    if len(s) < 6:
        return {}
    return {"last": float(s.iloc[-1]), "chg_pct": float(s.iloc[-1] / s.iloc[-2] - 1) * 100,
            "chg_5d_pct": float(s.iloc[-1] / s.iloc[-6] - 1) * 100, "date": s.index[-1].date()}


# ------------------------------------------------------------------ the pulse
def build(participant_rows: list[dict], fii_dii_rows: list[dict], breadth_rows: list[dict],
          macro: pd.DataFrame | None = None) -> dict:
    """Everything the desk looks at, as one plain dict (missing parts are simply absent)."""
    p = {}
    fii = positions(participant_rows, "FII")
    if len(fii):
        last = fii.iloc[-1]
        p["oi_date"] = fii.index[-1].date()
        p["fii"] = {k: (None if pd.isna(v) else (v if isinstance(v, str) else float(v))) for k, v in last.items()}
        cl = positions(participant_rows, "Client")
        if len(cl):
            p["client_fut_long_pct"] = float(cl["fut_long_pct"].iloc[-1])
    if fii_dii_rows:
        df = pd.DataFrame(fii_dii_rows)
        df["date"] = pd.to_datetime(df["date"])
        piv = df.pivot_table(index="date", columns="category", values="net", aggfunc="last").sort_index()
        p["cash_date"] = piv.index[-1].date()
        p["cash"] = {c.lower(): float(piv[c].iloc[-1]) for c in piv.columns if pd.notna(piv[c].iloc[-1])}
        p["cash_5d"] = {c.lower(): float(piv[c].tail(5).sum()) for c in piv.columns}
        p["cash_days"] = int(len(piv))
    if breadth_rows:
        b = max(breadth_rows, key=lambda r: str(r["date"]))
        p["breadth_date"] = date.fromisoformat(str(b["date"])[:10])
        p["breadth"] = {k: b.get(k) for k in ("advances", "declines", "pct_above_50dma", "pct_above_200dma",
                                              "new_52w_highs", "new_52w_lows", "mcclellan", "regime")}
    if macro is not None and len(macro):
        for k in MACRO:
            if k in macro:
                m = _move(macro[k])
                if m:
                    p[k] = m
    return p


def load(client, macro: pd.DataFrame | None = None, days: int = 90) -> dict:
    since = (date.today() - timedelta(days=days)).isoformat()
    return build(client.select("participant_oi", {"date": f"gte.{since}", "order": "date.asc"}),
                 client.select("fii_dii", {"date": f"gte.{since}", "order": "date.asc"}),
                 client.select("breadth_daily", {"order": "date.desc", "limit": "1"}), macro)


def _cr(x: float) -> str:
    return f"{'+' if x >= 0 else '−'}₹{abs(x):,.0f} cr"


def _n(x: float) -> str:
    return f"{'+' if x >= 0 else '−'}{abs(x):,.0f}"


def text(p: dict) -> list[str]:
    """WhatsApp-ready lines."""
    out = ["*Market pulse*"]
    if "nifty" in p:
        out.append(f"Nifty {p['nifty']['last']:,.0f} ({p['nifty']['chg_pct']:+.2f}%)"
                   + (f" · VIX {p['vix']['last']:.1f}" if "vix" in p else ""))
    if "brent" in p:
        out.append(f"Oil: Brent ${p['brent']['last']:.1f} ({p['brent']['chg_pct']:+.1f}% day, "
                   f"{p['brent']['chg_5d_pct']:+.1f}% 5 days)")
    if p.get("cash"):
        c = p["cash"]
        out.append(f"Cash market ({p['cash_date']:%d %b}): "
                   + ", ".join(f"{k.upper()} {_cr(v)}" for k, v in sorted(c.items(), reverse=True)))
    if p.get("fii"):
        f = p["fii"]
        line = f"FII index futures ({p['oi_date']:%d %b}): {f['fut_long_pct']:.0f}% long, net {_n(f['fut_net'])} contracts"
        if f.get("fut_net_chg") is not None:
            line += f" ({_n(f['fut_net_chg'])} today: {f['fut_action']})"
        out.append(line)
        if f.get("opt_net_chg") is not None:
            out.append(f"FII index options: net {_n(f['opt_net'])} ({_n(f['opt_net_chg'])} today, "
                       f"{'more bullish' if f['opt_net_chg'] > 0 else 'more bearish'})")
    if p.get("breadth"):
        b = p["breadth"]
        out.append(f"Breadth: {b['advances']} up / {b['declines']} down · {float(b['pct_above_50dma']):.0f}% above 50 DMA, "
                   f"{float(b['pct_above_200dma']):.0f}% above 200 DMA · {b['regime']}")
    return out if len(out) > 1 else []


# ------------------------------------------------------------------ the daily journal ("experience")
def journal_row(p: dict, session: date, macro: pd.DataFrame | None = None, books: dict | None = None,
                note: str | None = None) -> dict:
    """One row of desk_journal for `session`: only values that belong to that session are stored, so the
    table can later be used to test each indicator against what the market did next."""
    r: dict = {"date": session.isoformat(), "books": books, "note": note}
    if macro is not None and len(macro):
        for k, cols in (("nifty", ("nifty_close", "nifty_chg_pct", None)), ("brent", ("brent", "brent_chg_pct", "brent_5d_pct")),
                        ("vix", ("vix", None, None))):
            m = _move(macro[k], session) if k in macro else {}
            if m and (k != "nifty" and k != "vix" or m["date"] == session):
                r[cols[0]] = round(m["last"], 2)
                if cols[1]:
                    r[cols[1]] = round(m["chg_pct"], 3)
                if cols[2]:
                    r[cols[2]] = round(m["chg_5d_pct"], 3)
    if p.get("cash") and p.get("cash_date") == session:
        r["fii_cash_net"], r["dii_cash_net"] = p["cash"].get("fii"), p["cash"].get("dii")
    if p.get("fii") and p.get("oi_date") == session:
        f = p["fii"]
        i = lambda x: None if x is None else int(x)                 # noqa: E731
        r.update({"fii_fut_net": i(f["fut_net"]), "fii_fut_net_chg": i(f.get("fut_net_chg")),
                  "fii_fut_long_pct": round(f["fut_long_pct"], 2), "fii_fut_action": f.get("fut_action"),
                  "fii_opt_net": i(f["opt_net"]), "fii_opt_net_chg": i(f.get("opt_net_chg")),
                  "fii_stk_fut_net_chg": i(f.get("stk_fut_net_chg"))})
    if p.get("breadth") and p.get("breadth_date") == session:
        b = p["breadth"]
        r.update({k: b.get(k) for k in ("advances", "declines", "pct_above_50dma", "pct_above_200dma",
                                        "new_52w_highs", "new_52w_lows", "regime")})
    return r


def save_journal(client, row: dict) -> None:
    client.insert("desk_journal", [row], upsert_on="date")
