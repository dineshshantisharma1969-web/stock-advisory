"""Idea book (published calls) and trade journal, stored in SQLite.

The idea book is APPEND-ONLY and HASH-CHAINED: each record stores the SHA-256 of its own content
plus the previous record's hash. Editing or deleting any past call breaks the chain, which
`verify_chain()` detects. That gives you an auditable track record - what clients and SEBI's
performance-verification rules expect - instead of a cherry-picked one.

Outcomes of ideas are computed from price data (entry at the next session's open after the call,
exit at stop / target / horizon), exactly like the backtester, so live results and backtests are comparable.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import config

GENESIS = "0" * 64
IDEA_FIELDS = ["created_utc", "kind", "symbol", "setup", "side", "entry", "stop", "target",
               "horizon_days", "instrument", "lot_size", "rationale"]


def _conn(path=None):
    c = sqlite3.connect(path or config.JOURNAL_DB)
    c.execute("""CREATE TABLE IF NOT EXISTS ideas (id INTEGER PRIMARY KEY, created_utc TEXT, kind TEXT,
        symbol TEXT, setup TEXT, side TEXT, entry REAL, stop REAL, target REAL, horizon_days INTEGER,
        instrument TEXT, lot_size INTEGER, rationale TEXT, prev_hash TEXT, hash TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, segment TEXT, symbol TEXT,
        side TEXT, qty REAL, entry_date TEXT, entry REAL, exit_date TEXT, exit REAL, charges REAL,
        idea_id INTEGER, notes TEXT)""")
    c.execute("""CREATE TRIGGER IF NOT EXISTS ideas_no_update BEFORE UPDATE ON ideas
        BEGIN SELECT RAISE(ABORT, 'idea book is append-only'); END""")
    c.execute("""CREATE TRIGGER IF NOT EXISTS ideas_no_delete BEFORE DELETE ON ideas
        BEGIN SELECT RAISE(ABORT, 'idea book is append-only'); END""")
    return c


def _digest(rec: dict, prev: str) -> str:
    payload = json.dumps({k: rec[k] for k in IDEA_FIELDS}, sort_keys=True, default=str)
    return hashlib.sha256((prev + payload).encode()).hexdigest()


def publish_idea(rec: dict, path=None) -> int:
    rec = {k: rec.get(k) for k in IDEA_FIELDS}
    rec["created_utc"] = rec["created_utc"] or datetime.now(timezone.utc).isoformat(timespec="seconds")
    for k in ("entry", "stop", "target"):
        rec[k] = round(float(rec[k]), 2) if rec[k] is not None else None
    with _conn(path) as c:
        row = c.execute("SELECT hash FROM ideas ORDER BY id DESC LIMIT 1").fetchone()
        prev = row[0] if row else GENESIS
        h = _digest(rec, prev)
        cur = c.execute(f"INSERT INTO ideas ({','.join(IDEA_FIELDS)},prev_hash,hash) VALUES "
                        f"({','.join('?' * len(IDEA_FIELDS))},?,?)", [rec[k] for k in IDEA_FIELDS] + [prev, h])
        return cur.lastrowid


def ideas(path=None) -> pd.DataFrame:
    with _conn(path) as c:
        return pd.read_sql("SELECT * FROM ideas ORDER BY id", c)


def verify_chain(path=None) -> tuple[bool, str]:
    df = ideas(path)
    prev = GENESIS
    for _, r in df.iterrows():
        rec = r.to_dict()
        rec["lot_size"] = None if pd.isna(rec["lot_size"]) else int(rec["lot_size"])
        rec["horizon_days"] = None if pd.isna(rec["horizon_days"]) else int(rec["horizon_days"])
        if r["prev_hash"] != prev or _digest(rec, prev) != r["hash"]:
            return False, f"Chain broken at idea #{r['id']}"
        prev = r["hash"]
    return True, f"{len(df)} ideas verified, chain intact"


def evaluate_ideas(df: pd.DataFrame, prices: dict) -> pd.DataFrame:
    """Mark each idea open / target / stop / expired with P&L, using the same fill rules as the backtest."""
    out = []
    for _, r in df.iterrows():
        res = {"id": r["id"], "status": "no data", "fill": np.nan, "exit": np.nan, "exit_date": None,
               "ret": np.nan, "r_multiple": np.nan, "pnl_per_lot": np.nan}
        px = prices.get(r["symbol"])
        if px is not None and r["kind"] != "fundamental":
            after = px[px.index > pd.Timestamp(r["created_utc"]).tz_localize(None).normalize()]
            if len(after):
                side = 1 if r["side"] == "long" else -1
                fill = float(after["Open"].iloc[0])
                risk = abs(r["entry"] - r["stop"])
                stop = fill - side * risk
                tgt = fill + side * abs(r["target"] - r["entry"])
                status, exit_px, exit_d = "open", float(after["Close"].iloc[-1]), None
                for j, (d, bar) in enumerate(after.head(int(r["horizon_days"] or 20)).iterrows()):
                    if (bar["Low"] <= stop) if side > 0 else (bar["High"] >= stop):
                        status, exit_px, exit_d = "stop", stop, d
                        break
                    if (bar["High"] >= tgt) if side > 0 else (bar["Low"] <= tgt):
                        status, exit_px, exit_d = "target", tgt, d
                        break
                else:
                    if len(after) >= int(r["horizon_days"] or 20):
                        status, exit_d = "expired", after.index[int(r["horizon_days"] or 20) - 1]
                        exit_px = float(after["Close"].iloc[int(r["horizon_days"] or 20) - 1])
                ret = (exit_px / fill - 1) * side
                lot = r["lot_size"] if pd.notna(r["lot_size"]) and r["lot_size"] else 1
                res.update({"status": status, "fill": fill, "exit": exit_px, "exit_date": exit_d, "ret": ret,
                            "r_multiple": ret * fill / risk if risk else np.nan,
                            "pnl_per_lot": (exit_px - fill) * side * lot})
        elif px is not None and r["kind"] == "fundamental":
            after = px[px.index >= pd.Timestamp(r["created_utc"]).tz_localize(None).normalize()]
            if len(after):
                fill, last = float(after["Open"].iloc[0]), float(after["Close"].iloc[-1])
                res.update({"status": "holding", "fill": fill, "exit": last, "ret": last / fill - 1})
        out.append(res)
    return df.merge(pd.DataFrame(out), on="id", how="left")


# ------------------------------------------------------------------ manual journal
def add_trade(t: dict, path=None) -> int:
    cols = ["segment", "symbol", "side", "qty", "entry_date", "entry", "exit_date", "exit", "charges",
            "idea_id", "notes"]
    with _conn(path) as c:
        cur = c.execute(f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [t.get(k) for k in cols])
        return cur.lastrowid


def close_trade(trade_id: int, exit_date: str, exit_px: float, charges: float | None = None, path=None):
    with _conn(path) as c:
        if charges is None:
            c.execute("UPDATE trades SET exit_date=?, exit=? WHERE id=?", (exit_date, exit_px, trade_id))
        else:
            c.execute("UPDATE trades SET exit_date=?, exit=?, charges=? WHERE id=?",
                      (exit_date, exit_px, charges, trade_id))


def trades(prices: dict | None = None, path=None) -> pd.DataFrame:
    with _conn(path) as c:
        df = pd.read_sql("SELECT * FROM trades ORDER BY id", c)
    if df.empty:
        return df
    sign = np.where(df["side"] == "long", 1, -1)
    last = df["symbol"].map(lambda s: float(prices[s]["Close"].iloc[-1]) if prices and s in prices else np.nan)
    df["mark"] = np.where(df["exit"].notna(), df["exit"], last)
    df["status"] = np.where(df["exit"].notna(), "closed", "open")
    df["pnl"] = (df["mark"] - df["entry"]) * df["qty"] * sign - df["charges"].fillna(0)
    return df
