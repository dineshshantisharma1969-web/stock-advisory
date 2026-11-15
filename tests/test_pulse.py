from datetime import date

import pandas as pd
import pytest

from core import pulse

HEAD = ('"Participant wise Open Interest (no. of contracts) in Equity Derivatives as on Nov 13, 2026",,,,,,,,,,,,,,\n'
        "Client Type,Future Index Long,Future Index Short,Future Stock Long,Future Stock Short       ,"
        "Option Index Call Long,Option Index Put Long,Option Index Call Short,Option Index Put Short,"
        "Option Stock Call Long,Option Stock Put Long,Option Stock Call Short,Option Stock Put Short,"
        "Total Long Contracts      ,Total Short Contracts\n")


def csv(fii_long, fii_short, call_long=700, put_long=1000):
    body = [f"Client,170,110,3,3,3,3,3,3,2,1,1,1,14,10", f"DII,54,28,3,4,2,1,1,1,2,4,4,3,5,4",
            f"FII,{fii_long},{fii_short},35,25,{call_long},{put_long},900,700,3,4,4,3,6,5",
            f"Pro,25,58,7,5,1,1,1,1,1,1,1,1,5,6", "TOTAL,1,1,1,1,1,1,1,1,1,1,1,1,1,1"]
    return (HEAD + "\n".join(body)).encode()


def rows():
    return (pulse.parse_participant(csv(60000, 110000), date(2026, 11, 12))
            + pulse.parse_participant(csv(62000, 118000, call_long=900), date(2026, 11, 13)))


def test_parse_keeps_the_four_participants_and_strips_padded_headers():
    r = pulse.parse_participant(csv(62546, 118466), date(2026, 11, 13))
    assert [x["participant"] for x in r] == ["Client", "DII", "FII", "Pro"]
    fii = r[2]
    assert fii["fut_idx_long"] == 62546 and fii["fut_stk_short"] == 25 and fii["date"] == "2026-11-13"
    with pytest.raises(ValueError):
        pulse.parse_participant((HEAD + "Client,1,1,1,1,1,1,1,1,1,1,1,1,1,1").encode(), date(2026, 11, 13))


def test_positions_net_change_and_build_up_label():
    f = pulse.positions(rows(), "FII").iloc[-1]
    assert f["fut_net"] == -56000 and f["fut_net_chg"] == -6000 and f["fut_action"] == "Short build-up"
    assert f["fut_long_pct"] == pytest.approx(62000 / 180000 * 100)
    assert f["opt_net"] == (900 - 900) - (1000 - 700) and f["opt_net_chg"] == 200      # 200 more calls bought
    assert pulse.fut_action(500, 100) == "Long build-up" and pulse.fut_action(-500, 100) == "Long unwinding"
    assert pulse.fut_action(10, -300) == "Short covering" and pulse.fut_action(0, 0) == "-"


class DB:
    def __init__(self, have=None):
        self.t = {"participant_oi": list(have or [])}

    def select(self, table, params=None):
        return [r for r in self.t.get(table, []) if r["participant"] == "FII"]

    def insert(self, table, rows, upsert_on=None):
        self.t.setdefault(table, []).extend(rows)


def test_update_fetches_only_missing_weekdays_and_survives_errors(monkeypatch):
    asked = []

    def fetch(d):
        asked.append(d)
        if d == date(2026, 11, 12):
            raise RuntimeError("timeout")
        return None if d == date(2026, 11, 11) else pulse.parse_participant(csv(1, 2), d)

    db = DB(pulse.parse_participant(csv(1, 2), date(2026, 11, 9)))
    monkeypatch.setattr(pulse, "MIN_HISTORY", 1)
    n = pulse.update_participant(db, date(2026, 11, 16), fetch=fetch)
    assert asked == [date(2026, 11, d) for d in (10, 11, 12, 13, 16)]            # after the 9th, no weekend
    assert n == 3                                                                # holiday and error skipped


def test_pulse_text_and_journal_only_store_values_of_that_session():
    idx = pd.bdate_range(end="2026-11-13", periods=10)
    macro = pd.DataFrame({"nifty": range(25000, 25010), "vix": 15.0, "brent": [90.0] * 9 + [99.0], "wti": 80.0}, index=idx)
    p = pulse.build(rows(), [{"date": "2026-11-12", "category": "FII", "net": -1200.0},
                             {"date": "2026-11-12", "category": "DII", "net": 900.0}],
                    [{"date": "2026-11-13", "advances": 158, "declines": 336, "pct_above_50dma": 56.8,
                      "pct_above_200dma": 46.7, "new_52w_highs": 22, "new_52w_lows": 1, "mcclellan": 7, "regime": "Mixed"}],
                    macro)
    t = "\n".join(pulse.text(p))
    assert "Brent $99.0 (+10.0% day" in t and "FII −₹1,200 cr" in t and "Short build-up" in t and "158 up / 336 down" in t
    j = pulse.journal_row(p, date(2026, 11, 13), macro, {"fwd-dip": {"open": 1.0}})
    assert j["fii_fut_net_chg"] == -6000 and j["brent_chg_pct"] == 10.0 and j["advances"] == 158
    assert "fii_cash_net" not in j                       # the cash figure is the 12th's: not stored under the 13th
    assert pulse.text({}) == []


def test_first_runs_back_fill_the_window_but_skip_days_already_saved():
    asked = []
    db = DB(pulse.parse_participant(csv(1, 2), date(2026, 11, 13)))          # only a small seed is saved
    pulse.update_participant(db, date(2026, 11, 16), backfill_days=6, fetch=lambda d: asked.append(d))
    assert asked == [date(2026, 11, d) for d in (10, 11, 12, 16)]
