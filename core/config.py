"""Central settings. Edit these numbers to match your own assumptions."""
from pathlib import Path

APP_VERSION = "2026-11-12 v18"  # bump on each release; shown in the sidebar
HISTORY_YEARS = 5   # price history used for indicators and backtests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
UNIVERSE_CSV = DATA_DIR / "nifty500.csv"
JOURNAL_DB = DATA_DIR / "journal.db"
FII_DII_CSV = DATA_DIR / "fii_dii_history.csv"

# --- Valuation defaults (India) ---
RISK_FREE = 0.068        # ~10y G-Sec yield; update to current
EQUITY_RISK_PREMIUM = 0.07
TERMINAL_GROWTH = 0.05   # long-run nominal growth
DCF_YEARS = 10           # explicit forecast horizon (2-stage: high growth then fade)
DEFAULT_TAX = 0.25

# Sectors where FCFF-based DCF / EV metrics do not apply (balance-sheet businesses)
FINANCIAL_SECTORS = {"Financial Services"}

# --- Technicals ---
ATR_STOP_MULT = 2.0      # stop = entry - 2 x ATR(14)
REWARD_RISK = 2.0        # target = entry + 2R

# --- Macro tickers (Yahoo Finance) ---
MACRO_TICKERS = {
    "Nifty 50": "^NSEI",
    "Nifty Bank": "^NSEBANK",
    "India VIX": "^INDIAVIX",
    "USD/INR": "INR=X",
    "Brent Crude": "BZ=F",
    "Gold": "GC=F",
    "US 10Y Yield": "^TNX",
    "Dollar Index": "DX-Y.NYB",
    "S&P 500": "^GSPC",
}
# World Bank indicators for India (annual)
WB_INDICATORS = {
    "GDP growth (%)": "NY.GDP.MKTP.KD.ZG",
    "CPI inflation (%)": "FP.CPI.TOTL.ZG",
    "Current account (% GDP)": "BN.CAB.XOKA.GD.ZS",
}

DISCLAIMER = (
    "For research and education only. Screens and ideas are generated mechanically from "
    "public data and are not investment advice. Distributing stock recommendations to the "
    "public in India requires SEBI Research Analyst registration."
)
