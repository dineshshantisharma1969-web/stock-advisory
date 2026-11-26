"""End-of-day P&L and the calls carried into the next session.

One calculation for the Streamlit End of day page and for `daily_bot.py --eod`,
so the two cannot drift. It only reads the idea book, the forward-test book and
the manual journal. Nothing here writes a call or touches the hash chain.

Today's P&L is the realised result of calls closed on the session, plus the
session's price move on calls that stay open. The carried-forward MTM is the
open result measured from entry, which is a different number: it includes every
prior session.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pandas as pd

from . import ledger, snapshot

IST = timezone(timedelta(hours=5, minutes=30))
CASH_CLOSE = time(15, 30)
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

IDEA_CLOSED = {"target", "stop", "expired"}
IDEA_OPEN = {"open", "holding"}
PAPER_CLOSED = {"target", "stop", "trail stop", "expired", "exit signal"}
EXIT_REASON = {
    "stop": "stop",
    "target": "target",
    "expired": "time",
    "trail stop": "trail stop",
    "exit signal": "exit signal",
}

CLOSED_COLUMNS = ["symbol", "book", "strategy", "side", "entry_date", "entry_price",
                  "exit_price", "exit_reason", "pnl_inr", "pnl_pct"]
CARRIED_COLUMNS = ["symbol", "book", "strategy", "side", "entry_date", "entry_price",
                   "close", "pnl_inr", "pnl_pct", "stop", "target", "days_held"]


@dataclass
class Call:
    symbol: str
    book: str
    strategy: str
    side: str
    entry_date: str | None
    entry_price: float | None
    price: float | None
    exit_reason: str | None
    pnl_inr: float | None
    pnl_pct: float | None
    stop: float | None
    target: float | None
    days_held: int | None
    day_mtm_inr: float | None
    state: str
    exit_on: date | None = None
    qty: float | None = None
    charges: float = 0.0
    cost_rate: float = 0.0
    prev_close: float | None = None
    no_target: bool = False


@dataclass
class EodSummary:
    as_of: datetime
    generated_at: datetime
    session: date | None
    closed_today: list[Call]
    carried_forward: list[Call]
    realised_pnl: float
    unrealised_mtm: float
    today_unrealised_mtm: float
    total_day_pnl: float
    scope_note: str = ""
    prices_note: str = ""
    warning: str = ""
    calls: list[Call] = field(default_factory=list)


def stamp(when: datetime) -> str:
    """Clock time in IST with an English month, independent of the machine locale."""
    when = when.astimezone(IST)
    return f"{when.day} {MONTHS[when.month - 1]} {when.year}, {when:%H:%M} IST"


def end_of_day_summary(ideas=None, prices=None, paper_trades=None, journal=None, *,
                       now: datetime | None = None, session: date | None = None,
                       marks: dict | None = None) -> EodSummary:
    """Build the end-of-day summary from data the caller already loaded.

    `ideas` is a raw idea-book frame (as `ledger.ideas()` returns it).
    `paper_trades` is the paper-trade rows; only book `fwd-momentum` is included.
    `journal` is a marked journal frame (as `ledger.trades(prices)` returns it).
    `marks` is an optional {symbol: last price} for a session that is not in the
    daily bars yet (the office-PC relay at 15:30). A mark through the stop or the
    fixed target closes the call on that session. An official bar on the session
    date is kept in preference to a mark.
    """
    prices = prices or {}
    marks = {str(k): float(v) for k, v in (marks or {}).items() if _num(v) is not None}
    now_ist = _as_ist(now)
    session = session or _resolve_session(prices, now_ist, bool(marks))
    calls = (_from_ideas(ideas, prices, session)
             + _from_paper(paper_trades, prices, session)
             + _from_journal(journal, prices, session))
    if marks and session is not None and now_ist.date() == session:
        calls = [_apply_mark(c, marks, prices, session) for c in calls]
    closed, carried = _bucket(calls, session)
    key = lambda c: (c.symbol, c.book, c.entry_date or "")
    closed.sort(key=key)
    carried.sort(key=key)
    realised = _sum(c.pnl_inr for c in closed)
    unrealised = _sum(c.pnl_inr for c in carried)
    today_open = _sum(c.day_mtm_inr for c in carried)
    note = ""
    if (session is not None and now_ist.weekday() < 5 and now_ist.time() >= CASH_CLOSE
            and session < now_ist.date() and not marks):
        note = (f"Latest closing prices on file are {session.day} {MONTHS[session.month - 1]} {session.year}. "
                "Today's cash close is not in the file yet, so this is the previous session.")
    return EodSummary(
        as_of=_as_of(session, now_ist), generated_at=now_ist, session=session,
        closed_today=closed, carried_forward=carried, realised_pnl=realised, unrealised_mtm=unrealised,
        today_unrealised_mtm=today_open, total_day_pnl=realised + today_open, prices_note=note, calls=calls,
    )


def closed_frame(summary: EodSummary) -> pd.DataFrame:
    rows = [{"symbol": c.symbol, "book": c.book, "strategy": c.strategy, "side": c.side,
             "entry_date": c.entry_date, "entry_price": c.entry_price, "exit_price": c.price,
             "exit_reason": c.exit_reason, "pnl_inr": c.pnl_inr, "pnl_pct": c.pnl_pct}
            for c in summary.closed_today]
    return pd.DataFrame(rows, columns=CLOSED_COLUMNS)


def carried_frame(summary: EodSummary) -> pd.DataFrame:
    rows = [{"symbol": c.symbol, "book": c.book, "strategy": c.strategy, "side": c.side,
             "entry_date": c.entry_date, "entry_price": c.entry_price, "close": c.price,
             "pnl_inr": c.pnl_inr, "pnl_pct": c.pnl_pct, "stop": c.stop, "target": c.target,
             "days_held": c.days_held}
            for c in summary.carried_forward]
    return pd.DataFrame(rows, columns=CARRIED_COLUMNS)


def format_eod(summary: EodSummary) -> str:
    """WhatsApp-sized text for the daily bot. Same figures as the page."""
    closed = [_closed_line(c) for c in summary.closed_today] or ["No calls closed today."]
    carried = [_carried_line(c) for c in summary.carried_forward] or ["No open calls carried forward."]
    lines = [
        f"*End of day – {stamp(summary.as_of)}*",
        "",
        f"Today's P&L: *{_inr(summary.total_day_pnl)}* "
        f"(booked {_inr(summary.realised_pnl)}, open MTM today {_inr(summary.today_unrealised_mtm)})",
        f"Open book MTM from entry: {_inr(summary.unrealised_mtm)}",
    ]
    if summary.prices_note:
        lines.append(summary.prices_note)
    lines += ["", f"*Closed today ({len(summary.closed_today)})*", *closed,
              "", f"*Carried forward ({len(summary.carried_forward)})*", *carried]
    return "\n".join(lines)


def load_summary(prices, *, admin: bool, now: datetime | None = None, client=None,
                 demo: bool = False) -> EodSummary:
    """Load the books this viewer is allowed to see and summarise them.

    Clients get the published idea book only. The forward-test book and the
    manual journal stay on the research desk, matching the rest of the app.
    Demo prices are synthetic, so the forward-test book is left out of them.
    """
    ideas = ledger.ideas()
    paper, journal, marks = [], None, {}
    warning = ""
    if admin:
        journal = ledger.trades(prices)
        if demo:
            scope = ("Books in this view: published idea book and the manual journal. "
                     "Demo prices are on, so the forward-test book is left out.")
        else:
            from . import cloud
            db = client if client is not None else cloud.client()
            if db is None:
                warning = ("The forward-test book lives in Supabase, which is not connected. "
                           "Showing the idea book and the journal.")
                scope = "Books in this view: published idea book and the manual journal."
            else:
                try:
                    paper = db.select("paper_trades", {"order": "id.asc"})
                except Exception as e:
                    warning = f"Forward-test book could not be read ({type(e).__name__})."
                marks = _marks_from_client(db, now)
                scope = "Books in this view: published idea book, forward-test fwd-momentum, and the manual journal."
    else:
        scope = "Published idea book. The forward-test book and the trade journal stay on the research desk."
    summary = end_of_day_summary(ideas=ideas, prices=prices or {}, paper_trades=paper, journal=journal,
                                 now=now, marks=marks or None)
    summary.scope_note = scope
    summary.warning = warning
    return summary


def show(summary: EodSummary) -> None:
    """Draw the summary. Used by the End of day page and the Track record tab."""
    import streamlit as st

    st.caption(f"As of {stamp(summary.as_of)} · compiled {stamp(summary.generated_at)}")
    if summary.scope_note:
        st.caption(summary.scope_note)
    if summary.warning:
        st.warning(summary.warning)
    if summary.prices_note:
        st.info(summary.prices_note)
    k = st.columns(4)
    k[0].metric("Total day P&L", _rupee(summary.total_day_pnl),
                help="Realised P&L of calls closed on this session, plus today's price move on calls still open.")
    k[1].metric("Realised today", _rupee(summary.realised_pnl), f"{len(summary.closed_today)} closed")
    k[2].metric("Today's open MTM", _rupee(summary.today_unrealised_mtm),
                help="Move since the previous close for calls that stay open. A call opened today contributes its full open P&L.")
    k[3].metric("Carried-forward MTM", _rupee(summary.unrealised_mtm), f"{len(summary.carried_forward)} open",
                help="Open profit or loss from the entry price, marked to the latest price.")

    st.subheader("Today's P&L")
    st.caption("Calls closed on this session.")
    if not summary.closed_today:
        st.info("No calls closed today.")
    else:
        closed = closed_frame(summary)
        st.dataframe(closed, hide_index=True, width="stretch", column_config=_columns(st, closed))

    st.subheader("Carried-forward calls")
    st.caption("Open calls rolling into the next trading session.")
    if not summary.carried_forward:
        st.info("No open calls carried forward.")
    else:
        carried = carried_frame(summary)
        cfg = _columns(st, carried)
        cfg.update({"close": st.column_config.NumberColumn("Close", format="%.2f"),
                    "stop": st.column_config.NumberColumn("Stop", format="%.2f"),
                    "target": st.column_config.NumberColumn("Target", format="%.2f"),
                    "days_held": st.column_config.NumberColumn("Days held", format="%d")})
        st.dataframe(carried, hide_index=True, width="stretch", column_config=cfg)


# ---------------------------------------------------------------- internals
def _as_ist(now: datetime | None) -> datetime:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    return now.astimezone(IST)


def _as_of(session: date | None, now_ist: datetime) -> datetime:
    if session is None:
        return now_ist
    close = datetime.combine(session, CASH_CLOSE, tzinfo=IST)
    if now_ist.date() == session and now_ist.time() < CASH_CLOSE:
        return now_ist
    return close


def _latest_bar(prices: dict) -> date | None:
    found = []
    for df in prices.values():
        if df is not None and len(df):
            found.append(pd.Timestamp(df.index[-1]).date())
    return max(found) if found else None


def _resolve_session(prices: dict, now_ist: datetime, has_marks: bool) -> date | None:
    """Last daily bar, or today after the cash close when a live mark stands in for today's bar."""
    bar = _latest_bar(prices)
    if has_marks and now_ist.weekday() < 5 and now_ist.time() >= CASH_CLOSE and (bar is None or now_ist.date() >= bar):
        return now_ist.date()
    return bar


def _num(v):
    try:
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return None
        x = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


def _sum(values) -> float:
    total = 0.0
    for v in values:
        n = _num(v)
        if n is not None:
            total += n
    return total


def _date_of(v) -> date | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    try:
        ts = pd.Timestamp(v)
    except (TypeError, ValueError):
        return None
    if pd.isna(ts):
        return None
    return ts.date()


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _days(entry: date | None, end: date | None) -> int | None:
    if entry is None or end is None or end < entry:
        return None
    return int(np.busday_count(np.datetime64(entry), np.datetime64(end))) + 1


def _text(v, fallback: str) -> str:
    if v is None:
        return fallback
    try:
        if pd.isna(v):
            return fallback
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return s or fallback


def _published_day(created) -> pd.Timestamp:
    """Same clock-stripping the idea scorer uses, so the fill bar matches."""
    t = pd.Timestamp(created)
    if t.tzinfo is not None:
        t = t.tz_localize(None)
    return t.normalize()


def _prev_close(px: pd.DataFrame | None, session: date | None) -> float | None:
    if px is None or session is None or len(px) == 0:
        return None
    before = [float(px["Close"].iloc[i]) for i, ts in enumerate(px.index) if pd.Timestamp(ts).date() < session]
    return before[-1] if before else None


def _has_bar(px: pd.DataFrame | None, session: date | None) -> bool:
    if px is None or session is None or len(px) == 0:
        return False
    return any(pd.Timestamp(ts).date() == session for ts in px.index)


def _side(name) -> tuple[str, int]:
    label = "short" if str(name).lower() == "short" else "long"
    return label, (-1 if label == "short" else 1)


def _pct(pnl, entry, qty):
    if pnl is None or not entry or not qty:
        return None
    return pnl / (entry * qty)


def _day_move(entry_on, session, entry, price, qty, sign, pnl, prev) -> float | None:
    """Session move. A position opened today contributes its whole open P&L; an older one, the price change."""
    if session is None or price is None or qty is None:
        return None
    if entry_on == session:
        return pnl
    base = prev if prev is not None else entry
    if base is None:
        return None
    return (price - base) * qty * sign


def _levels_for_idea(row, fill) -> tuple[float | None, float | None]:
    """Stop and target actually in force. Swing and F&O are shifted onto the fill, as the scorer does."""
    published_stop, published_target = _num(row.get("stop")), _num(row.get("target"))
    if row.get("kind") == "fundamental" or fill is None or _num(row.get("entry")) is None:
        return published_stop, published_target
    if published_stop is None or published_target is None:
        return published_stop, published_target
    sign = -1 if str(row.get("side")).lower() == "short" else 1
    entry = float(row["entry"])
    risk = abs(entry - published_stop)
    return fill - sign * risk, fill + sign * abs(published_target - entry)


def _make(symbol, book, strategy, side, entry_on, entry, price, reason, pnl, stop, target,
          session, state, qty, prev, charges=0.0, cost_rate=0.0, no_target=False, exit_on=None) -> Call:
    end = exit_on if state == "closed" else session
    day = None
    if state == "open":
        day = _day_move(entry_on, session, entry, price, qty, -1 if side == "short" else 1, pnl, prev)
    return Call(
        symbol=str(symbol), book=book, strategy=strategy, side=side, entry_date=_iso(entry_on),
        entry_price=entry, price=price, exit_reason=reason, pnl_inr=pnl, pnl_pct=_pct(pnl, entry, qty),
        stop=stop, target=target, days_held=_days(entry_on, end), day_mtm_inr=day, state=state,
        exit_on=exit_on, qty=qty, charges=charges or 0.0, cost_rate=cost_rate, prev_close=prev,
        no_target=no_target,
    )


def _from_ideas(ideas, prices, session) -> list[Call]:
    if ideas is None or len(ideas) == 0:
        return []
    ev = ledger.evaluate_ideas(ideas, prices)
    out = []
    for _, r in ev.iterrows():
        status = r.get("status")
        if status not in IDEA_CLOSED and status not in IDEA_OPEN:
            continue
        px = prices.get(r["symbol"])
        side, sign = _side(r.get("side"))
        lot = r.get("lot_size")
        qty = float(lot) if _num(lot) else 1.0
        fill, price = _num(r.get("fill")), _num(r.get("exit"))
        entry_on = None
        if px is not None and len(px):
            day = _published_day(r["created_utc"])
            after = px[px.index >= day] if r.get("kind") == "fundamental" else px[px.index > day]
            if len(after):
                entry_on = pd.Timestamp(after.index[0]).date()
        closed = status in IDEA_CLOSED
        exit_on = _date_of(r.get("exit_date")) if closed else None
        pnl = _num(r.get("pnl_per_lot"))
        if pnl is None and fill is not None and price is not None:
            pnl = (price - fill) * sign * qty
        stop, target = _levels_for_idea(r, fill)
        out.append(_make(
            r["symbol"], "Idea book", _text(r.get("setup"), _text(r.get("kind"), "Idea")), side, entry_on, fill,
            price, EXIT_REASON.get(status) if closed else None, pnl, stop, target, session,
            "closed" if closed else "open", qty, _prev_close(px, session), exit_on=exit_on,
        ))
    return out


def _from_paper(trades, prices, session) -> list[Call]:
    from . import forward
    mine = [t for t in (trades or []) if t.get("book") == forward.BOOK]
    if not mine:
        return []
    scored = {int(r["trade_id"]): r for r in snapshot.evaluate_paper(mine, prices)}
    out = []
    for t in mine:
        r = scored.get(int(t["id"]))
        if not r:
            continue
        status = r.get("status")
        if status not in PAPER_CLOSED and status != "open":
            continue
        px = prices.get(t["symbol"])
        side, _ = _side(t.get("side"))
        qty = _num(t.get("qty")) or 0.0
        fill, price = _num(r.get("fill")), _num(r.get("exit"))
        pnl = _num(r.get("net_pnl"))
        closed = status in PAPER_CLOSED
        stop = _num(r.get("stop_now")) if status == "open" and _num(r.get("stop_now")) is not None else _num(t.get("stop"))
        trail = _num(t.get("trail_atr")) is not None
        rule = isinstance(t.get("exit_rule"), str) and bool(t.get("exit_rule"))
        out.append(_make(
            t["symbol"], forward.BOOK, _text(t.get("setup"), forward.BOOK), side, _date_of(r.get("fill_date")),
            fill, price, EXIT_REASON.get(status) if closed else None, pnl, stop, _num(t.get("target")), session,
            "closed" if closed else "open", qty, _prev_close(px, session), cost_rate=snapshot.COST_PER_SIDE,
            no_target=trail or rule, exit_on=_date_of(r.get("exit_date")) if closed else None,
        ))
    return out


def _from_journal(journal, prices, session) -> list[Call]:
    if journal is None or len(journal) == 0 or "status" not in getattr(journal, "columns", []):
        return []
    out = []
    for _, r in journal.iterrows():
        status = r.get("status")
        if status not in ("open", "closed"):
            continue
        px = prices.get(r["symbol"]) if prices else None
        side, _ = _side(r.get("side"))
        qty = _num(r.get("qty")) or 0.0
        entry = _num(r.get("entry"))
        price = _num(r.get("mark"))
        if price is None:
            price = _num(r.get("exit"))
        charges = _num(r.get("charges")) or 0.0
        closed = status == "closed"
        out.append(_make(
            r["symbol"], "Journal", _text(r.get("segment"), "Journal"), side, _date_of(r.get("entry_date")),
            entry, price, "manual" if closed else None, _num(r.get("pnl")), None, None, session,
            "closed" if closed else "open", qty, _prev_close(px, session), charges=charges,
            exit_on=_date_of(r.get("exit_date")) if closed else None,
        ))
    return out


def _apply_mark(call: Call, marks: dict, prices: dict, session: date) -> Call:
    """Reprice an open call from a live mark when this session has no official bar yet."""
    if call.state != "open" or call.symbol not in marks or _has_bar(prices.get(call.symbol), session):
        return call
    mark = marks[call.symbol]
    sign = -1 if call.side == "short" else 1
    reason, price = None, mark
    if call.stop is not None and ((sign > 0 and mark <= call.stop) or (sign < 0 and mark >= call.stop)):
        reason, price = "stop", call.stop
    elif (not call.no_target) and call.target is not None and (
            (sign > 0 and mark >= call.target) or (sign < 0 and mark <= call.target)):
        reason, price = "target", call.target
    closed = reason is not None
    pnl = None
    if call.entry_price is not None and call.qty:
        gross = (price - call.entry_price) * call.qty * sign
        costs = call.cost_rate * (call.entry_price + (price if closed else 0.0)) * call.qty
        pnl = gross - costs - (call.charges or 0.0)
    entry_on = _date_of(call.entry_date)
    exit_on = session if closed else None
    end = exit_on if closed else session
    day = None if closed else _day_move(entry_on, session, call.entry_price, price, call.qty, sign, pnl, call.prev_close)
    return replace(
        call, price=price, exit_reason=reason, pnl_inr=pnl, pnl_pct=_pct(pnl, call.entry_price, call.qty),
        state="closed" if closed else "open", exit_on=exit_on, days_held=_days(entry_on, end), day_mtm_inr=day,
    )


def _bucket(calls: list[Call], session: date | None) -> tuple[list[Call], list[Call]]:
    closed, carried = [], []
    for c in calls:
        if c.state == "open":
            carried.append(c)
        elif c.state == "closed" and session is not None and c.exit_on == session:
            closed.append(c)
    return closed, carried


def _inr(x: float) -> str:
    n = _num(x)
    if n is None:
        return "—"
    sign = "+" if n >= 0 else "−"
    return f"{sign}₹{abs(n):,.0f}"


def _rupee(x: float) -> str:
    n = _num(x) or 0.0
    return f"₹{n:,.0f}"


def _pct_text(x) -> str:
    n = _num(x)
    return "—" if n is None else f"{n:+.1%}"


def _closed_line(c: Call) -> str:
    return f"• {c.symbol} · {c.book} · {c.exit_reason} · {_inr(c.pnl_inr)} ({_pct_text(c.pnl_pct)})"


def _carried_line(c: Call) -> str:
    def level(v):
        return f"{v:.2f}" if v is not None else "—"

    entry = level(c.entry_price)
    close = level(c.price)
    held = f"{c.days_held}d" if c.days_held is not None else "—"
    return (f"• {c.symbol} · {c.book} · entry {c.entry_date} @ {entry} · close {close} · "
            f"{_inr(c.pnl_inr)} ({_pct_text(c.pnl_pct)}) · stop {level(c.stop)} · target {level(c.target)} · {held}")


def _money_config(st):
    n = st.column_config.NumberColumn
    return {"entry_price": n("Entry", format="%.2f"), "exit_price": n("Exit", format="%.2f"),
            "pnl_inr": n("P&L ₹", format="%.0f"), "pnl_pct": n("P&L %", format="percent")}


def _columns(st, frame):
    return {k: v for k, v in _money_config(st).items() if k in frame.columns}


def _marks_from_client(client, now: datetime | None) -> dict:
    """Live prices pushed today by the office PC. Older quotes are ignored."""
    try:
        from . import live
        quotes = live.latest(client)
    except Exception:
        return {}
    if quotes is None or len(quotes) == 0 or "ltp" not in quotes.columns:
        return {}
    today = _as_ist(now).date()
    out = {}
    for sym, row in quotes.iterrows():
        ltp = _num(row.get("ltp"))
        if ltp is None:
            continue
        ts = row.get("updated_at")
        if ts is None or pd.isna(ts):
            continue
        stamp_ = pd.Timestamp(ts)
        if stamp_.tzinfo is None:
            stamp_ = stamp_.tz_localize("UTC")
        if stamp_.tz_convert(IST).date() != today:
            continue
        out[str(sym)] = ltp
    return out
