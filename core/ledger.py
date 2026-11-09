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

from . import cloud, config

GENESIS = "0" * 64
IDEA_FIELDS = ["created_utc", "kind", "symbol", "setup", "side", "entry", "stop", "target",
               "horizon_days", "instrument", "lot_size", "rationale", "tracks"]
# Client tracks a call can be published to
TRACKS = {"longterm": "🛡️ Long-term wealth", "swing": "⚖️ Swing trading", "fno": "⚡ F&O"}
DEFAULT_TRACK = {"fundamental": "longterm", "swing": "swing", "fno": "fno"}


def _conn(path=None):
    c = sqlite3.connect(path or config.JOURNAL_DB)
    c.execute("""CREATE TABLE IF NOT EXISTS ideas (id INTEGER PRIMARY KEY, created_utc TEXT, kind TEXT,
        symbol TEXT, setup TEXT, side TEXT, entry REAL, stop REAL, target REAL, horizon_days INTEGER,
        instrument TEXT, lot_size INTEGER, rationale TEXT, prev_hash TEXT, hash TEXT, tracks TEXT)""")
    cols = [r[1] for r in c.execute("PRAGMA table_info(ideas)")]
    if "tracks" not in cols:  # databases created before client tracks existed
        c.execute("ALTER TABLE ideas ADD COLUMN tracks TEXT")
    c.execute("""CREATE TABLE IF NOT EXISTS risk_profiles (id INTEGER PRIMARY KEY, created_utc TEXT, client_name TEXT,
        contact TEXT, answers TEXT, score INTEGER, tracks TEXT, consent INTEGER)""")
    c.execute("""CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, segment TEXT, symbol TEXT,
        side TEXT, qty REAL, entry_date TEXT, entry REAL, exit_date TEXT, exit REAL, charges REAL,
        idea_id INTEGER, notes TEXT)""")
    c.execute("""CREATE TRIGGER IF NOT EXISTS ideas_no_update BEFORE UPDATE ON ideas
        BEGIN SELECT RAISE(ABORT, 'idea book is append-only'); END""")
    c.execute("""CREATE TRIGGER IF NOT EXISTS ideas_no_delete BEFORE DELETE ON ideas
        BEGIN SELECT RAISE(ABORT, 'idea book is append-only'); END""")
    return c


def _digest(rec: dict, prev: str) -> str:
    fields = {k: rec.get(k) for k in IDEA_FIELDS}
    if fields.get("tracks") is None:  # ideas published before tracks existed hash exactly as they did then
        fields.pop("tracks")
    payload = json.dumps(fields, sort_keys=True, default=str)
    return hashlib.sha256((prev + payload).encode()).hexdigest()


def _cloud(path):
    """Use Supabase unless a local path is given explicitly (tests) or Supabase is not configured."""
    return None if path is not None else cloud.client()


def _normalise(rec: dict) -> dict:
    rec = {k: rec.get(k) for k in IDEA_FIELDS}
    rec["created_utc"] = rec["created_utc"] or datetime.now(timezone.utc).isoformat(timespec="seconds")
    for k in ("entry", "stop", "target"):
        rec[k] = round(float(rec[k]), 2) if rec[k] is not None else None
    for k in ("lot_size", "horizon_days"):
        rec[k] = int(rec[k]) if rec[k] is not None and not pd.isna(rec[k]) else None
    t = rec.get("tracks")
    if isinstance(t, (list, tuple, set)):
        t = ",".join(sorted(x for x in t if x in TRACKS)) or None
    rec["tracks"] = t or None
    return rec


def publish_idea(rec: dict, path=None) -> int:
    rec = _normalise(rec)
    c = _cloud(path)
    if c:
        for attempt in range(3):  # prev_hash is UNIQUE in the database, so a race fails instead of forking
            last = c.select("ideas", {"select": "hash", "order": "id.desc", "limit": 1})
            prev = last[0]["hash"] if last else GENESIS
            try:
                row = c.insert("ideas", [{**rec, "prev_hash": prev, "hash": _digest(rec, prev)}])
                return int(row[0]["id"])
            except cloud.SupabaseError as e:
                if e.status != 409 or attempt == 2:
                    raise
    with _conn(path) as c:
        row = c.execute("SELECT hash FROM ideas ORDER BY id DESC LIMIT 1").fetchone()
        prev = row[0] if row else GENESIS
        h = _digest(rec, prev)
        cur = c.execute(f"INSERT INTO ideas ({','.join(IDEA_FIELDS)},prev_hash,hash) VALUES "
                        f"({','.join('?' * len(IDEA_FIELDS))},?,?)", [rec[k] for k in IDEA_FIELDS] + [prev, h])
        return cur.lastrowid


IDEA_COLS = ["id"] + IDEA_FIELDS + ["prev_hash", "hash"]


def ideas(path=None) -> pd.DataFrame:
    c = _cloud(path)
    if c:
        return pd.DataFrame(c.select("ideas", {"order": "id.asc"}), columns=IDEA_COLS)
    with _conn(path) as c:
        return pd.read_sql("SELECT * FROM ideas ORDER BY id", c)


def verify_chain(path=None) -> tuple[bool, str]:
    df = ideas(path)
    prev = GENESIS
    for _, r in df.iterrows():
        # normalise exactly as at publish time (databases may return 100.0 as 100, ints as floats, NaN for NULL)
        rec = {k: (None if not isinstance(v, str) and pd.isna(v) else v) for k, v in r.to_dict().items()}
        if r["prev_hash"] != prev or _digest(_normalise(rec), prev) != r["hash"]:
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
TRADE_COLS = ["segment", "symbol", "side", "qty", "entry_date", "entry", "exit_date", "exit", "charges",
              "idea_id", "notes"]


def add_trade(t: dict, path=None) -> int:
    cols = TRADE_COLS
    c = _cloud(path)
    if c:
        return int(c.insert("trades", [{k: t.get(k) for k in cols}])[0]["id"])
    with _conn(path) as c:
        cur = c.execute(f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [t.get(k) for k in cols])
        return cur.lastrowid


def close_trade(trade_id: int, exit_date: str, exit_px: float, charges: float | None = None, path=None):
    c = _cloud(path)
    if c:
        vals = {"exit_date": exit_date, "exit": exit_px}
        if charges is not None:
            vals["charges"] = charges
        c.update("trades", {"id": int(trade_id)}, vals)
        return
    with _conn(path) as c:
        if charges is None:
            c.execute("UPDATE trades SET exit_date=?, exit=? WHERE id=?", (exit_date, exit_px, trade_id))
        else:
            c.execute("UPDATE trades SET exit_date=?, exit=?, charges=? WHERE id=?",
                      (exit_date, exit_px, charges, trade_id))


def trades(prices: dict | None = None, path=None) -> pd.DataFrame:
    c = _cloud(path)
    if c:
        df = pd.DataFrame(c.select("trades", {"order": "id.asc"}), columns=["id"] + TRADE_COLS)
        df = df.astype({k: float for k in ("qty", "entry", "exit", "charges")})
    else:
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


def ideas_for_track(df: pd.DataFrame, track: str) -> pd.DataFrame:
    """Ideas published to a client track. Ideas without tags fall back to their kind's default track."""
    if df.empty:
        return df
    tr = df["tracks"].where(df["tracks"].notna() & (df["tracks"] != ""), df["kind"].map(DEFAULT_TRACK))
    return df[tr.fillna("").str.split(",").apply(lambda xs: track in xs)]


# ------------------------------------------------------------------ client risk profiles
def save_profile(p: dict, path=None) -> int:
    rec = {"created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "client_name": p.get("client_name"), "contact": p.get("contact"), "answers": p["answers"],
           "score": int(p["score"]), "tracks": ",".join(p["tracks"]), "consent": bool(p.get("consent"))}
    c = _cloud(path)
    if c:
        return int(c.insert("risk_profiles", [rec])[0]["id"])
    with _conn(path) as c:
        cur = c.execute("INSERT INTO risk_profiles (created_utc,client_name,contact,answers,score,tracks,consent) "
                        "VALUES (?,?,?,?,?,?,?)", [rec["created_utc"], rec["client_name"], rec["contact"],
                                                   json.dumps(rec["answers"]), rec["score"], rec["tracks"],
                                                   int(rec["consent"])])
        return cur.lastrowid


def profiles(path=None) -> pd.DataFrame:
    c = _cloud(path)
    if c:
        return pd.DataFrame(c.select("risk_profiles", {"order": "id.desc"}))
    with _conn(path) as c:
        df = pd.read_sql("SELECT * FROM risk_profiles ORDER BY id DESC", c)
    if len(df):
        df["answers"] = df["answers"].map(json.loads)
    return df
