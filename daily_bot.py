"""Daily paper-trading bot: does everything "Refresh live data" + "Prepare data for Claude" did, by itself.

Run every weekday before the 9:15 open (GitHub Actions at about 8:15 am IST; or by hand on a PC):
  1. Prices: adds yesterday's official NSE bhavcopy to the 5-year history (Yahoo full refresh once a week).
  2. Snapshot tables + scoring of every paper trade (fills, stops, P&L) from real prices.
  3. The three rule-based paper books place today's trades (filled at today's 9:15 open):
       Momentum leaders · Pullbacks in F&O stocks · Nifty weekly condor (real NSE option prices).
     No new trades are placed if the prices are stale (refresh failed).
  4. A WhatsApp-ready P&L summary is saved in the app (Paper trading page -> "Bot summary").
  5. Market pulse: oil, FII/DII cash, FII futures & options positions, breadth - added to the summary and
     saved in the daily journal (desk_journal).
  6. Upkeep after the trades are in: fundamentals and setup grades once a week (slow).
Paper trading only: it never connects to Kotak or places a real order.

    python daily_bot.py            normal run
    python daily_bot.py --dry-run  everything except writing trades / tables (prints what it would do)

Settings: bot_settings.ini next to this file (copy bot_settings_TEMPLATE.ini). Keep it private.
"""
from __future__ import annotations

import argparse
import configparser
import logging
import os
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
IST = timezone(timedelta(hours=5, minutes=30))
log = logging.getLogger("bot")


def load_settings() -> None:
    """Supabase URL + secret key from bot_settings.ini (or the environment) -> environment for core.cloud."""
    if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"):
        return
    ini = HERE / "bot_settings.ini"
    if not ini.exists():
        sys.exit(f"Settings file not found: {ini}\nCopy bot_settings_TEMPLATE.ini to bot_settings.ini and fill it in.")
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(ini, encoding="utf-8")
    url = cp.get("supabase", "url", fallback="").strip().rstrip("/")
    key = cp.get("supabase", "secret_key", fallback="").strip().strip('"')
    if not url or not key or key.upper().startswith("PASTE"):
        sys.exit("Please fill in bot_settings.ini (the Supabase secret key).")
    os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"] = url, key


def last_session(now: datetime) -> date:
    """The latest session whose closing prices are published: today after 7 pm IST, else the previous weekday."""
    d = now.date() if now.hour >= 19 else now.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def already_done(client, now: datetime) -> bool:
    """True if a run earlier today (IST) finished without problems."""
    rows = client.select("bot_runs", {"order": "id.desc", "limit": "1"})
    if not rows or not rows[0].get("ok"):
        return False
    ran = datetime.fromisoformat(str(rows[0]["run_at"]).replace("Z", "+00:00"))
    return ran.astimezone(IST).date() == now.astimezone(IST).date()


def money(x: float) -> str:
    return f"{'+' if x >= 0 else '−'}₹{abs(x):,.0f}"


def book_lines(name: str, trades: list[dict], results: list[dict], placed: list[dict]) -> list[str]:
    res = {r["trade_id"]: r for r in results}
    mine = [(t, res.get(t["id"], {})) for t in trades]
    opn = [(t, r) for t, r in mine if r.get("status") == "open"]
    closed = [(t, r) for t, r in mine if r.get("status") in ("target", "stop", "trail stop", "expired", "exit signal")]
    realised = sum(r.get("net_pnl") or 0 for _, r in closed)
    unreal = sum(r.get("net_pnl") or 0 for _, r in opn)
    out = [f"*{name}*", f"Total P&L: *{money(realised + unreal)}* (open {money(unreal)}, booked {money(realised)})"]
    for t, r in opn:
        out.append(f"• {t['symbol']}: {money(r.get('net_pnl') or 0)}")
    if placed:
        out.append("New today: " + ", ".join(p["symbol"] for p in placed) + " (at 9:15 open)")
    if not opn and not placed:
        out.append("No open positions.")
    return out


def summary_text(client, prices: dict, placed: dict, condor: dict, session: date) -> str:
    from core import forward, income, snapshot
    trades = client.select("paper_trades", {"order": "id.asc"})
    results = snapshot.evaluate_paper(trades, prices)
    lines = [f"*Paper portfolio – prices of {session:%d %b %Y}*", ""]
    for book, name in ((forward.BOOK, "Momentum leaders"), (forward.DIP_BOOK, "Pullbacks (F&O stocks)")):
        lines += book_lines(name, [t for t in trades if t.get("book") == book], results, placed.get(book, [])) + [""]
    ct = client.select("condor_trades", {"order": "expiry.asc"})
    lines.append("*Nifty weekly condor*")
    done = [t for t in ct if t["status"] != "open"]
    live = [t for t in ct if t["status"] == "open"]
    booked = sum(float(t.get("pnl_rupees") or 0) for t in done)
    lines.append(f"Booked: *{money(booked)}* over {len(done)} week(s)")
    for t in live:
        pts = t.get("open_pnl_points")
        lines.append(f"• Open, expiry {t['expiry']}: {income.describe(t)}"
                     + (f" – now {money(float(pts) * int(t['lot_size']))}" if pts is not None else ""))
    for t in condor.get("opened", []):
        lines.append(f"New: {income.describe(t)} for {t['expiry']}, credit {float(t['credit']):.0f} pts")
    lines += ["", "_Paper trades on ₹20 lakh virtual capital per book – not a recommendation._"]
    return "\n".join(lines)


def saved_screen(universe):
    """The fundamentals screen from the saved fundamentals (None if there are none yet)."""
    from core import data, valuation
    try:
        fu, _ = data.load_cache("fund_live")
        return valuation.screen(fu, universe) if fu else None
    except Exception as e:                        # never let a fundamentals problem stop the scoring
        log.warning("Fundamentals screen skipped: %s", e)
        return None


def book_totals(client, prices: dict) -> dict:
    """{book: {"open": rupees, "booked": rupees, "positions": n}} for the journal."""
    from core import snapshot
    trades = client.select("paper_trades", {"order": "id.asc"})
    res = {r["trade_id"]: r for r in snapshot.evaluate_paper(trades, prices)}
    out: dict = {}
    for t in trades:
        r, b = res.get(t["id"], {}), out.setdefault(t.get("book") or "claude", {"open": 0.0, "booked": 0.0, "positions": 0})
        if r.get("status") == "open":
            b["open"] += r.get("net_pnl") or 0
            b["positions"] += 1
        elif r.get("status") in ("target", "stop", "trail stop", "expired", "exit signal"):
            b["booked"] += r.get("net_pnl") or 0
    return {k: {kk: round(vv, 0) for kk, vv in v.items()} for k, v in out.items()}


def market_pulse(client, prices: dict, session: date, dry_run: bool = False) -> list[str]:
    """Oil, FII/DII cash, FII futures & options positions, breadth: update the tables, save the day in the
    journal (the desk's "experience") and return the WhatsApp lines. Every part is optional."""
    from core import market, pulse
    if not dry_run:
        try:
            market.merge_fii_dii(market.fetch_fii_dii_today())
        except Exception as e:                    # NSE often blocks scripts; the figures can be uploaded in the app
            log.warning("FII/DII cash figures not updated: %s", e)
        try:
            n = pulse.update_participant(client, session, log=lambda m: log.warning("  %s", m))
            log.info("FII positions (participant-wise OI): %d day(s) added", n)
        except Exception as e:
            log.warning("FII positions not updated: %s", e)
    macro = None
    try:
        macro = pulse.fetch_macro()
    except Exception as e:
        log.warning("Oil / Nifty / VIX not available: %s", e)
    p = pulse.load(client, macro)
    if not dry_run:
        pulse.save_journal(client, pulse.journal_row(p, session, macro, book_totals(client, prices)))
    return pulse.text(p)


def housekeeping(client, prices: dict, universe, step, max_age_days: float = 7) -> list[str]:
    """What 'Refresh live data' used to do by hand, so the app's other pages do not go stale:
    fundamentals + setup grades once a week. Runs after the trades are placed.
    Returns the names of the parts that were refreshed."""
    from core import backtest, data, market, snapshot
    did = []

    def fundamentals():
        old, saved = data.load_cache("fund_live")
        if old and saved and datetime.now() - saved < timedelta(days=max_age_days):
            log.info("Fundamentals are fresh (%s); skipping", f"{saved:%d %b}")
            return
        syms = list(universe["symbol"])
        log.info("Refreshing fundamentals for %d companies (weekly, 10-20 minutes)...", len(syms))
        merged, n_ok = data.merge_refresh(old, data.YahooProvider().all_fundamentals(syms, prices=prices),
                                          data.usable_fundamentals)
        if n_ok:
            data.save_cache("fund_live", merged)
            did.append("fundamentals")
        log.info("Fundamentals: %d updated, %d kept from the previous run", n_ok, len(syms) - n_ok)
        if n_ok < 0.5 * len(syms):
            raise RuntimeError(f"only {n_ok} of {len(syms)} companies returned usable fundamentals")
    step("Fundamentals", fundamentals)

    def grades():
        bt, saved = data.load_cache("backtest_live")
        if bt is not None and saved and datetime.now() - saved < timedelta(days=max_age_days):
            log.info("Backtest grades are fresh (%s); skipping", f"{saved:%d %b}")
            return
        log.info("Re-running the backtest (weekly, a few minutes)...")
        tr = backtest.run(prices)
        data.save_cache("backtest_live", backtest.summary(tr) if len(tr) else tr)
        did.append("backtest grades")
    step("Backtest", grades)

    if {"fundamentals", "backtest grades"} & set(did):
        def republish():
            bt, _ = data.load_cache("backtest_live")
            snapshot.publish(client, prices, universe, saved_screen(universe), bt, log=lambda m: log.warning("  %s", m))
        step("Snapshot (after weekly refresh)", republish)
    log.info("Upkeep: %s", ", ".join(did) or "nothing due")
    return did


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true", help="do nothing if today's run already succeeded (for a back-up schedule)")
    a = ap.parse_args(argv)
    quiet = bool(os.environ.get("BOT_QUIET"))     # cloud runs: the run log is public, so keep trades and P&L out of it
    (HERE / "data").mkdir(exist_ok=True)
    out = logging.StreamHandler(sys.stdout)
    out.setLevel(logging.WARNING if quiet else logging.INFO)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%d-%b %H:%M:%S",
                        handlers=[out, logging.FileHandler(HERE / "data" / "bot_log.txt", encoding="utf-8")])
    load_settings()
    sys.path.insert(0, str(HERE))
    from core import backtest, cloud, data, forward, income, snapshot

    c = cloud.client()
    if c is None:
        log.error("Could not connect to the app database - check bot_settings.ini")
        return 1
    now = datetime.now(IST)
    session = last_session(now)
    problems, notes = [], []
    if a.once and already_done(c, now):
        print("Today's run already succeeded - nothing to do.")
        return 0

    def step(name, fn):
        try:
            return fn()
        except Exception as e:
            problems.append(f"{name}: {e}")
            log.error("%s failed: %s", name, type(e).__name__ if quiet else e)
            try:
                c.insert("app_errors", [{"step": f"Bot: {name}", "error": f"{type(e).__name__}: {e}"[:2000],
                                         "traceback": traceback.format_exc()[-6000:], "versions": "daily_bot"}])
            except Exception:
                pass
            return None

    log.info("Daily paper-trading bot - prices of %s", f"{session:%a %d %b %Y}")
    u = data.load_universe()

    # 1. prices
    def prices():
        px, msg = data.refresh_prices(list(u["symbol"]), log=lambda m: log.info("  %s", m), today=session)
        log.info("Prices: %s", msg)
        notes.append(msg)
        if px is None:
            raise RuntimeError(msg)
        if not a.dry_run:
            data.save_cache("prices_live", px)
        return px
    px = step("Prices", prices)
    if not px:
        px, _ = data.load_cache("prices_live")
        if not px:
            log.error("No prices at all - stopping.")
            return 1
        log.warning("Using the previously saved prices.")

    # 2. snapshot + scoring, with the grades and fundamentals already saved (the slow weekly jobs run in
    #    step 5, AFTER the trades are placed, so they can never push an order past the 9:15 open)
    summ, _ = data.load_cache("backtest_live")
    if not a.dry_run:
        def snap():
            done = snapshot.publish(c, px, u, saved_screen(u), summ, log=lambda m: log.warning("  %s", m))
            log.info("Snapshot: %s", ", ".join(f"{k} {v}" for k, v in done.items()))
        step("Snapshot", snap)

    # 3. the three books
    placed = {}
    if a.dry_run:
        existing = c.select("paper_trades", {"order": "id.asc"})
        placed[forward.BOOK] = forward.plan(px, existing)
        placed[forward.DIP_BOOK] = forward.dip_plan(px, existing, [r["symbol"] for r in c.select("fo_lots")])
        condor = {"opened": [], "closed": []}
        for b, rows in placed.items():
            log.info("[dry run] %s would place: %s", b, [r["symbol"] for r in rows] or "nothing")
    else:
        placed[forward.BOOK] = step("Momentum book", lambda: forward.run(c, px)) or []
        placed[forward.DIP_BOOK] = step("Pullback book", lambda: forward.run_dip(c, px)) or []
        condor = step("Condor book", lambda: income.step(c, log=lambda m: log.info("  %s", m))) or {"opened": [], "closed": []}
        for b, rows in placed.items():
            log.info("%s: %d new trade(s) %s", b, len(rows), [r["symbol"] for r in rows])
        log.info("Condor: opened %d, closed %d", len(condor["opened"]), len(condor["closed"]))

    # 4. summary
    text = step("Summary", lambda: summary_text(c, px, placed, condor, session)) or "Summary could not be built."
    lines = step("Market pulse", lambda: market_pulse(c, px, session, a.dry_run))
    if lines:
        head, _, foot = text.rpartition("\n\n")
        text = "\n\n".join([head, "\n".join(lines), foot]) if head else text + "\n\n" + "\n".join(lines)
    if not quiet:
        print("\n" + text + "\n")
    if not a.dry_run:
        step("Save summary", lambda: c.insert("bot_runs", [{
            "session": session.isoformat(), "ok": not problems, "summary": text,
            "log": "\n".join(notes + problems)[:4000]}]))

    # 5. weekly upkeep: FII/DII, fundamentals, backtest grades (slow; nothing here affects today's trades)
    if not a.dry_run:
        housekeeping(c, px, u, step)
    if problems:
        log.error("Finished with problems in: %s", ", ".join(p.split(":")[0] for p in problems) if quiet else "; ".join(problems))
        return 1
    log.info("All done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
