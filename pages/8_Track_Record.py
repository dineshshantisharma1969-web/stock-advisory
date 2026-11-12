from datetime import date

import pandas as pd
import plotly.express as px_
import streamlit as st

from core import ledger, state

st.set_page_config(page_title="Track Record & P&L", page_icon="📒", layout="wide")
demo = state.sidebar()
state.hero("Track record &amp; P&amp;L", "Every published call, tracked automatically and tamper-evident.")

px = state.load_prices(demo)
tab_i, tab_j = st.tabs(["Published ideas (auto-tracked)", "My trades journal (F&O / swing)"])

with tab_i:
    ok, msg = ledger.verify_chain()
    _note = (f"Integrity: {msg}. Ideas are append-only and hash-chained; "
             "past calls cannot be edited or deleted.")
    if ok:
        st.success(_note)
    else:
        st.error(_note)
    ib = ledger.ideas()
    if ib.empty:
        st.info("No ideas published yet.")
    else:
            ev = ledger.evaluate_ideas(ib, px)
            tr = st.radio("Track", ["all"] + list(ledger.TRACKS), horizontal=True,
                          format_func=lambda t: "All tracks" if t == "all" else ledger.TRACKS[t])
            if tr != "all":
                ev = ledger.ideas_for_track(ev, tr)
            closed = ev[ev["status"].isin(["target", "stop", "expired"])]
            c = st.columns(6)
            c[0].metric("Ideas", len(ev))
            c[1].metric("Open", int((ev["status"].isin(["open", "holding"])).sum()))
            c[2].metric("Hit rate (closed)", state.pct(closed["ret"].gt(0).mean()) if len(closed) else "-")
            c[3].metric("Avg return", state.pct(closed["ret"].mean()) if len(closed) else "-")
            c[4].metric("Avg R", state.num(closed["r_multiple"].mean(), 2) if len(closed) else "-")
            c[5].metric("F&O P&L per lot (closed)",
                        f"₹{closed.loc[closed['kind'] == 'fno', 'pnl_per_lot'].sum():,.0f}" if len(closed) else "-")
            if len(closed):
                cc = closed.sort_values("exit_date").assign(cum=lambda d: (1 + d["ret"]).cumprod() - 1)
                st.plotly_chart(px_.line(cc, x="exit_date", y="cum", color="kind", title="Cumulative return of closed ideas"),
                                width="stretch")
                st.dataframe(closed.groupby(["kind", "setup"]).agg(ideas=("id", "count"), hit_rate=("ret", lambda s: (s > 0).mean()),
                                                                   avg_ret=("ret", "mean"), avg_r=("r_multiple", "mean")),
                             width="stretch")
            N = st.column_config.NumberColumn
            st.dataframe(ev[["id", "created_utc", "kind", "symbol", "setup", "side", "instrument", "entry", "stop", "target",
                             "status", "fill", "exit", "exit_date", "ret", "r_multiple", "pnl_per_lot", "rationale", "hash"]],
                         hide_index=True, width="stretch",
                         column_config={"ret": N("Return", format="percent"), "r_multiple": N("R", format="%.2f"),
                                        "pnl_per_lot": N("P&L / lot ₹", format="%.0f"),
                                        "fill": N(format="%.1f"), "exit": N(format="%.1f")})
            st.download_button("Export track record (CSV)", ev.to_csv(index=False), "track_record.csv")
            st.caption("Ideas are filled at the next session's open after publication, the same rule as the backtest.")

    with tab_j:
      if not state.is_admin():
        st.info("The trade journal is private to the admin.")
      else:
        with st.expander("Add a trade", expanded=False):
            with st.form("add_trade"):
                a, b, c_, d = st.columns(4)
                seg = a.selectbox("Segment", ["Swing (cash)", "Futures", "Options", "Intraday"])
                sym = b.text_input("Symbol / contract (e.g. RELIANCE or NIFTY 25NOV 24500 CE)").upper()
                side = c_.selectbox("Side", ["long", "short"])
                qty = d.number_input("Quantity (shares, or lots x lot size)", 1.0, 1e7, 1.0)
                e1, e2, e3, e4 = st.columns(4)
                ed = e1.date_input("Entry date", date.today())
                ep = e2.number_input("Entry price", 0.0, 1e7, 0.0)
                ch = e3.number_input("Charges ₹ (brokerage, STT, GST, stamp)", 0.0, 1e7, 0.0)
                idea = e4.number_input("Linked idea # (optional)", 0, 1_000_000, 0)
                note = st.text_input("Notes")
                if st.form_submit_button("Save trade"):
                    ledger.add_trade({"segment": seg, "symbol": sym, "side": side, "qty": qty, "entry_date": str(ed),
                                      "entry": ep, "charges": ch, "idea_id": idea or None, "notes": note})
                    st.success("Saved.")
        t = ledger.trades(px)
        if t.empty:
            st.info("No trades in the journal yet.")
        else:
            opn = t[t["status"] == "open"]
            if len(opn):
                with st.expander("Close a trade"):
                    with st.form("close"):
                        tid = st.selectbox("Trade", opn["id"], format_func=lambda i: f"#{i} {opn.set_index('id').loc[i, 'symbol']}")
                        xd, xp, xc = st.columns(3)
                        d_ = xd.date_input("Exit date", date.today())
                        p_ = xp.number_input("Exit price", 0.0, 1e7, 0.0)
                        c2 = xc.number_input("Total charges ₹", 0.0, 1e7, float(opn.set_index("id").loc[tid, "charges"] or 0))
                        if st.form_submit_button("Close"):
                            ledger.close_trade(int(tid), str(d_), p_, c2)
                            st.rerun()
            k = st.columns(5)
            cl = t[t["status"] == "closed"]
            k[0].metric("Realised P&L", f"₹{cl['pnl'].sum():,.0f}")
            k[1].metric("Unrealised P&L", f"₹{opn['pnl'].sum():,.0f}" if len(opn) else "₹0",
                        help="Cash symbols marked to latest close; F&O contracts need a manual exit price")
            k[2].metric("Win rate", state.pct((cl["pnl"] > 0).mean()) if len(cl) else "-")
            k[3].metric("Charges paid", f"₹{t['charges'].fillna(0).sum():,.0f}")
            k[4].metric("Trades", len(t))
            st.dataframe(t.groupby(["segment", "status"])["pnl"].agg(["count", "sum"]).rename(columns={"sum": "P&L ₹"}),
                         width="stretch")
            if len(cl):
                eq = cl.sort_values("exit_date").assign(cum=lambda d: d["pnl"].cumsum())
                st.plotly_chart(px_.line(eq, x="exit_date", y="cum", title="Realised equity curve (₹)"), width="stretch")
            st.dataframe(t, hide_index=True, width="stretch")
