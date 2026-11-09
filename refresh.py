"""Daily data refresh - run after market close (e.g. 7:30 pm IST, after NSE posts FII/DII figures).

    python refresh.py                 # prices + FII/DII every day; fundamentals if older than 7 days
    python refresh.py --fundamentals  # force fundamentals + quarterly results (slow: ~500 tickers)
    python refresh.py --prices-only

Schedule it:
  Linux/Mac cron :  30 19 * * 1-5  cd /path/to/nifty500-app && python refresh.py >> data/refresh.log 2>&1
  Windows        :  Task Scheduler -> Daily 19:30 -> Program: python  Arguments: refresh.py  Start in: this folder
Exit code is non-zero if a step failed, so a scheduler can alert you.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta

from core import backtest, data, market

log = logging.getLogger("refresh")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fundamentals", action="store_true", help="force fundamentals refresh")
    ap.add_argument("--prices-only", action="store_true")
    ap.add_argument("--no-backtest", action="store_true", help="skip re-grading the setups")
    ap.add_argument("--max-fund-age-days", type=float, default=7)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    u = data.load_universe()
    syms = list(u["symbol"])
    yp = data.YahooProvider()
    failed = []

    log.info("Prices for %d symbols...", len(syms))
    try:
        px = yp.prices(syms)
        if len(px) < 0.8 * len(syms):
            raise RuntimeError(f"only {len(px)} of {len(syms)} symbols returned data")
        data.save_cache("prices_live", px)
        missing = sorted(set(syms) - set(px))
        log.info("Prices saved: %d symbols (%d missing%s)", len(px), len(missing),
                 f": {', '.join(missing[:10])}..." if missing else "")
    except Exception as e:
        log.error("Price refresh failed: %s", e)
        failed.append("prices")
        px = None

    if a.prices_only:
        return 1 if failed else 0

    try:
        today = market.fetch_fii_dii_today()
        hist = market.merge_fii_dii(today)
        log.info("FII/DII saved for %s (history %d days)", today["date"].max().date(), hist["date"].nunique())
    except Exception as e:
        log.error("FII/DII fetch failed: %s", e)
        failed.append("fii_dii")

    _, saved = data.load_cache("fund_live")
    stale = saved is None or datetime.now() - saved > timedelta(days=a.max_fund_age_days)
    if a.fundamentals or stale:
        log.info("Fundamentals + quarterly results for %d symbols (takes a while)...", len(syms))
        try:
            fu = yp.all_fundamentals(syms)
            bad = [s for s, f in fu.items() if str(f.get("source", "")).startswith("error")]
            data.save_cache("fund_live", fu)
            log.info("Fundamentals saved: %d ok, %d errors", len(fu) - len(bad), len(bad))
        except Exception as e:
            log.error("Fundamentals failed: %s", e)
            failed.append("fundamentals")
    else:
        log.info("Fundamentals are fresh (saved %s); skipping", f"{saved:%d %b %H:%M}")

    if px and not a.no_backtest:
        log.info("Re-grading setups with the backtester...")
        tr = backtest.run(px)
        data.save_cache("backtest_live", backtest.summary(tr) if len(tr) else tr)
        log.info("Backtest saved (%d trades)", len(tr))

    if failed:
        log.error("Finished with failures: %s", ", ".join(failed))
        return 1
    log.info("All done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
