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


def live_pnl(trades: pd.DataFrame, q: pd.DataFrame, cost_per_side: float = 0.001, today=None) -> pd.DataFrame:
    """Paper positions marked to the live price. If the live price is through the stop (or the fixed target),
    the position is squared off at that level - the same rule the end-of-day scorer applies with the day's
    low/high, so the live view and the next morning's official result agree.
      * The stop is the one in force today: `stop_now` (the trailed stop from the scorer) when present.
      * A market order placed for today ("pending", entry at the open) is filled at today's opening price.
    trades needs symbol, side, fill, qty, status, stop, target (stop_now / trail_atr / exit_rule / entry_type /
    trade_date optional). Result adds live, live_fill, live_status, live_exit, live_pnl, live_time."""
    if trades.empty or q.empty:
        return pd.DataFrame()
    today = pd.Timestamp(today or datetime.now(IST).date())
    is_open = trades["status"] == "open"
    new = pd.Series(False, index=trades.index)
    if {"entry_type", "trade_date"} <= set(trades.columns) and "open" in q:
        day_open = pd.to_numeric(trades["symbol"].map(q["open"]), errors="coerce")
        new = ((trades["status"] == "pending") & (trades["entry_type"] == "open") & (day_open > 0)
               & (pd.to_datetime(trades["trade_date"], errors="coerce") <= today))
    o = trades[(is_open | new) & trades["symbol"].isin(q.index)].copy()
    if o.empty:
        return pd.DataFrame()
    new = new.reindex(o.index)
    o["live"] = o["symbol"].map(q["ltp"])
    for col in ("stop", "target", "fill"):   # a position without a level is simply never squared off on it
        if col not in o:
            o[col] = float("nan")
    o["live_fill"] = pd.to_numeric(o["fill"], errors="coerce")
    if new.any():
        o.loc[new, "live_fill"] = pd.to_numeric(o.loc[new, "symbol"].map(q["open"]), errors="coerce")
    stop = pd.to_numeric(o["stop"], errors="coerce")
    trailed = pd.Series(False, index=o.index)
    if "stop_now" in o:
        now_stop = pd.to_numeric(o["stop_now"], errors="coerce")
        trailed = now_stop.notna() & ~new & (now_stop != stop)
        stop = now_stop.where(now_stop.notna() & ~new, stop)
    side = o["side"].map({"long": 1, "short": -1}).fillna(1)
    trail = o["trail_atr"].notna() if "trail_atr" in o else pd.Series(False, index=o.index)
    rule = o["exit_rule"].notna() if "exit_rule" in o else pd.Series(False, index=o.index)
    stop_hit = ((side > 0) & (o["live"] <= stop)) | ((side < 0) & (o["live"] >= stop))
    tgt_hit = ~trail & ~rule & ~stop_hit & (((side > 0) & (o["live"] >= o["target"])) |
                                            ((side < 0) & (o["live"] <= o["target"])))
    o["live_status"] = "open"
    o.loc[stop_hit, "live_status"] = "stop (squared off)"
    o.loc[stop_hit & trailed, "live_status"] = "trail stop (squared off)"
    o.loc[tgt_hit, "live_status"] = "target (squared off)"
    exit_px = o["live"].where(~stop_hit, stop).where(~tgt_hit, o["target"])
    done = stop_hit | tgt_hit
    costs = cost_per_side * o["live_fill"] * o["qty"] + done * cost_per_side * exit_px * o["qty"]
    o["live_exit"] = exit_px
    o["live_pnl"] = (exit_px - o["live_fill"]) * o["qty"] * side - costs
    o["live_time"] = o["symbol"].map(q["updated_at"])
    return o


def latest(client) -> pd.DataFrame:
    """Every quote the relay has saved (any age), indexed by symbol, with updated_at as a timestamp."""
    df = pd.DataFrame(client.select("live_quotes"))
    if df.empty:
        return df
    df["updated_at"] = pd.to_datetime(df["updated_at"], utc=True, errors="coerce")
    return df.set_index("symbol").sort_index()


def ticker_symbols(client, extra: tuple = ("NIFTY 50", "NIFTY BANK")) -> list[str]:
    """Indices first, then stocks in the paper books (newest first, no duplicates)."""
    trades = client.select("paper_trades", {"order": "id.desc", "select": "symbol"})
    seen, out = set(), []
    for s in list(extra) + [t["symbol"] for t in trades]:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def health(client, now: datetime | None = None) -> list[tuple[str, str]]:
    """Is the office PC doing its job today? [(tone, plain line)] for the bot and the relay.
    tone: ok / wait / bad. Judged against the clock: 9:05 bot, 9:14-15:32 relay, weekdays only."""
    now = (now or datetime.now(timezone.utc)).astimezone(IST)
    hm, weekday, today = (now.hour, now.minute), now.weekday() < 5, now.date()
    out = []
    runs = client.select("bot_runs", {"order": "id.desc", "limit": "1"})
    ran = _ts(runs[0]["run_at"]).tz_convert(IST) if runs else None
    if ran is not None and ran.date() == today:
        ok = runs[0].get("ok")
        out.append(("ok" if ok else "bad", f"Paper-trading bot ran today at {ran:%H:%M}"
                    + ("." if ok else " but reported a problem - see the bot summary on the Paper trading page.")))
    elif not weekday or hm < (9, 5):
        out.append(("wait", "Paper-trading bot: next run at 9:05 am on the next weekday"
                    + (f" (last ran {ran:%a %d %b %H:%M})." if ran is not None else " (it has never run yet).")))
    elif hm < (9, 45):
        out.append(("wait", "Paper-trading bot: started at 9:05, no result yet (it can take up to 30 minutes)."))
    else:
        out.append(("bad", "Paper-trading bot has NOT run today. On the office PC, double-click run_daily_bot.bat in "
                           "C:\\nifty-desk (the PC must be on and logged in at 9:05)."))
    push = _ts(status(client).get("last_push"))
    push = push.tz_convert(IST) if push is not None else None
    age = (now - push).total_seconds() / 60 if push is not None else None
    market_open = weekday and (9, 16) <= hm <= (15, 30)
    if market_open and (age is None or age > 5):
        last = f"last price update {push:%a %d %b %H:%M}" if push is not None else "no update ever received"
        out.append(("bad", f"Kotak live prices are NOT coming in ({last}). On the office PC, double-click "
                           "start_relay.bat in C:\\kotak-relay. Until then P&L uses the last closing prices."))
    elif market_open:
        out.append(("ok", f"Kotak live prices are flowing (last update {push:%H:%M})."))
    else:
        out.append(("wait", "Market closed - live prices resume at 9:15 am on the next weekday"
                    + (f" (last update {push:%a %d %b %H:%M})." if push is not None else ".")))
    return out


def show_health(st, client) -> None:
    """Draw the status lines on a page (never raises)."""
    try:
        for tone, line in health(client):
            {"ok": st.success, "bad": st.error, "wait": st.info}[tone](
                {"ok": "🟢 ", "bad": "🔴 ", "wait": "⚪ "}[tone] + line)
    except Exception as e:
        st.caption(f"Office-PC status not available: {e}")
