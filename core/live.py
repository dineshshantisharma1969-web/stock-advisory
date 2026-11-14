"""Live prices pushed by the Kotak relay on the office PC (see relay/README.md).

The relay writes live_quotes through a passphrase-protected database function. The app only reads them,
and sets the passphrase (stored as a SHA-256 hash, never as text)."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))
MIN_PASSPHRASE = 12


def set_passphrase(client, phrase: str) -> None:
    phrase = (phrase or "").strip()
    if len(phrase) < MIN_PASSPHRASE:
        raise ValueError(f"Use at least {MIN_PASSPHRASE} characters.")
    client.update("relay_status", {"id": 1}, {"token_hash": hashlib.sha256(phrase.encode()).hexdigest()})


def status(client) -> dict:
    rows = client.select("relay_status", {"id": "eq.1"})
    return rows[0] if rows else {}


def _ts(x):
    t = pd.to_datetime(x, utc=True, errors="coerce")
    return None if pd.isna(t) else t


def quotes(client, max_age_min: float = 10, now: datetime | None = None) -> pd.DataFrame:
    """Live quotes updated in the last `max_age_min` minutes, indexed by symbol (empty if the relay is off)."""
    df = pd.DataFrame(client.select("live_quotes"))
    if df.empty:
        return df
    df["updated_at"] = pd.to_datetime(df["updated_at"], utc=True, errors="coerce")
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    return df[df["updated_at"] >= now - pd.Timedelta(minutes=max_age_min)].set_index("symbol")


def describe_status(st: dict, now: datetime | None = None) -> tuple[str, str]:
    """(tone, plain-English line) for the desk."""
    if not st.get("token_hash"):
        return "off", "Not set up yet: set a relay passphrase below, then follow relay/README.md on the office PC."
    t = _ts(st.get("last_push"))
    if t is None:
        return "off", "Passphrase set. Waiting for the first update from the office PC."
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    age = (now - t).total_seconds() / 60
    when = t.tz_convert(IST).strftime("%d %b %H:%M")
    if age <= 5:
        return "ok", f"Live: last update {when} ({st.get('last_count') or 0} prices)."
    return "stale", f"Last update {when} ({age:.0f} min ago). {st.get('last_message') or ''}".strip()


def live_pnl(trades: pd.DataFrame, q: pd.DataFrame, cost_per_side: float = 0.001) -> pd.DataFrame:
    """Open paper positions marked to the live price. trades needs symbol, side, fill, qty, status."""
    if trades.empty or q.empty:
        return pd.DataFrame()
    o = trades[trades["status"] == "open"].copy()
    o = o[o["symbol"].isin(q.index)]
    if o.empty:
        return pd.DataFrame()
    o["live"] = o["symbol"].map(q["ltp"])
    side = o["side"].map({"long": 1, "short": -1}).fillna(1)
    o["live_pnl"] = (o["live"] - o["fill"]) * o["qty"] * side - cost_per_side * o["fill"] * o["qty"]
    o["live_time"] = o["symbol"].map(q["updated_at"])
    return o
