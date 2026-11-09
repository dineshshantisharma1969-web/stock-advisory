# Nifty 500 Research Desk (work in progress)

Streamlit app for fundamental and technical research on the Nifty 500.

**Run:** `pip install -r requirements.txt` then `streamlit run app.py`. Click **Refresh live data** in the
sidebar (Yahoo Finance, needs internet) or switch on **Demo data** to explore offline. Demo data is synthetic.

| Page | What it does |
|---|---|
| Home | Market regime, breadth, top ideas, today's signals, track-record snapshot |
| Fundamental Ideas | Nifty 500 scored on DCF, reverse DCF, PE vs industry, PEG, EV/EBITDA, profit growth, ROCE/ROE, margins, leverage; quality gate |
| Stock Analysis | Candles + DMA/Bollinger/MACD/RSI; interactive DCF, reverse DCF, sensitivity grid; financials |
| Trading Ideas | MACD, DMA cross, Bollinger squeeze, RSI pullback, 52w breakout, Supertrend setups with ATR stop/target; position sizing; futures and option-spread structuring |
| Backtest | No look-ahead, costs, out-of-sample split, A/B/C grading of each setup |
| Market Breadth | A/D, % above 50/200 DMA, 52w highs/lows, McClellan, sector rotation |
| FII / DII | NSE daily provisional cash flows, stored to build history |
| Macro | VIX, USD/INR, crude, gold, US yields, global indices; World Bank India data |
| Track Record | Append-only, hash-chained idea book auto-tracked to P&L; manual F&O/swing journal |

Universe: `data/nifty500.csv` (unofficial mirror of NSE's list, dated 2026-05-15 - replace with the
current file from niftyindices.com). F&O lot sizes: save NSE's list as `data/fo_lots.csv` (symbol,lot_size).

Not investment advice. Publishing stock recommendations to the public in India requires SEBI Research
Analyst registration.

Still to do: unit tests, scheduled `refresh.py`, client-facing delivery layer.
