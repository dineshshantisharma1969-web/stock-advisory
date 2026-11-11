import io
import zipfile
from datetime import date

import pandas as pd
import pytest

from core import config, data, nse

COLS = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,"
        "OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,"
        "ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4")


def bhav(d: str, rows) -> bytes:
    lines = [COLS]
    for sym, series, o, h, l, c, v in rows:
        lines.append(f"{d},{d},CM,NSE,STK,1,INE000000000,{sym},{series},,,,,{sym} LTD,{o},{h},{l},{c},{c},{o},,,,,"
                     f"{v},1,1,F1,1,,,,,")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("BhavCopy.csv", "\n".join(lines))
    return buf.getvalue()


def test_parse_keeps_equity_series_only():
    df = nse.parse_bhavcopy(bhav("2026-11-06", [("ABC", "EQ", 10, 12, 9, 11, 1000), ("ABC", "N1", 1, 1, 1, 1, 1),
                                                 ("XYZ", "BE", 5, 6, 4, 5.5, 300)]))
    assert list(df.index) == ["ABC", "XYZ"]
    assert df.loc["ABC", "Close"] == 11 and df.loc["XYZ", "Volume"] == 300
    assert df.loc["ABC", "date"] == pd.Timestamp("2026-11-06")


def fake_fetch(days: dict):
    def f(d):
        raw = days.get(d)
        return None if raw is None else nse.parse_bhavcopy(raw)
    return f


def test_extend_appends_missing_days_and_skips_holidays():
    idx = pd.bdate_range("2026-10-26", "2026-11-04")
    old = {"ABC": pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1}, index=idx)}
    days = {date(2026, 11, 6): bhav("2026-11-06", [("ABC", "EQ", 10, 12, 9, 11, 1000)])}  # 5 Nov = holiday
    out, added = nse.extend_prices(old, ["ABC"], fetch=fake_fetch(days), today=date(2026, 11, 7))
    assert added == [date(2026, 11, 6)]
    assert out["ABC"].index[-1] == pd.Timestamp("2026-11-06") and out["ABC"]["Close"].iloc[-1] == 11
    assert len(out["ABC"]) == len(idx) + 1


def test_build_history_from_scratch():
    days = {date(2026, 11, 5): bhav("2026-11-05", [("ABC", "EQ", 10, 11, 9, 10, 5)]),
            date(2026, 11, 6): bhav("2026-11-06", [("ABC", "EQ", 10, 12, 9, 11, 7), ("OTHER", "EQ", 1, 1, 1, 1, 1)])}
    h = nse.build_history(["ABC"], days=5, fetch=fake_fetch(days), today=date(2026, 11, 6))
    assert list(h) == ["ABC"] and list(h["ABC"]["Close"]) == [10, 11]


def test_refresh_prices_falls_back_to_nse(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    syms = ["ABC", "XYZ"]
    idx = pd.bdate_range("2026-10-01", "2026-11-04")
    frame = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1}, index=idx)
    data.save_cache("prices_live", {"ABC": frame, "XYZ": frame})
    monkeypatch.setattr(data.YahooProvider, "prices", lambda self, s, progress=None: {})   # Yahoo blocked
    monkeypatch.setattr(nse, "extend_prices", lambda old, s, **kw: ({**old}, [date(2026, 11, 6)]))
    px, msg = data.refresh_prices(syms, log=lambda m: None)
    assert px is not None and "NSE" in msg


def test_refresh_prices_keeps_old_data_when_everything_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(data.YahooProvider, "prices", lambda self, s, progress=None: {})
    monkeypatch.setattr(nse, "build_history", lambda s, progress=None: {})
    px, msg = data.refresh_prices(["ABC"], log=lambda m: None)
    assert px is None and "kept" in msg


def _long(end="2026-11-10"):
    idx = pd.bdate_range(end=end, periods=1250)
    return pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1}, index=idx)


def test_daily_refresh_uses_nse_when_weekly_refresh_is_recent(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    data.save_cache("prices_live", {"A": _long(), "B": _long()})
    data.save_cache("prices_full_at", True)                       # Yahoo full refresh just done
    called = {}
    monkeypatch.setattr(data.YahooProvider, "prices", lambda self, s, progress=None: called.setdefault("yahoo", {}))
    monkeypatch.setattr(nse, "extend_prices", lambda old, s, **kw: (old, [date(2026, 11, 11)]))
    px, msg = data.refresh_prices(["A", "B"], log=lambda m: None, today=date(2026, 11, 11))
    assert "NSE bhavcopy" in msg and "yahoo" not in called


def test_weekly_full_refresh_goes_to_yahoo(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    data.save_cache("prices_live", {"A": _long(), "B": _long()})   # no prices_full_at -> weekly refresh due
    monkeypatch.setattr(data.YahooProvider, "prices", lambda self, s, progress=None: {x: _long() for x in s})
    px, msg = data.refresh_prices(["A", "B"], log=lambda m: None, today=date(2026, 11, 11))
    assert "Yahoo" in msg and "weekly" in msg
    assert data.cache_saved_at("prices_full_at") is not None


def test_nse_not_published_yet_falls_back_to_yahoo(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    data.save_cache("prices_live", {"A": _long(), "B": _long()})
    data.save_cache("prices_full_at", True)
    monkeypatch.setattr(nse, "extend_prices", lambda old, s, **kw: (old, []))   # nothing new yet
    monkeypatch.setattr(data.YahooProvider, "prices", lambda self, s, progress=None: {x: _long("2026-11-11") for x in s})
    px, msg = data.refresh_prices(["A", "B"], log=lambda m: None, today=date(2026, 11, 11))
    assert "Yahoo" in msg


def test_up_to_date_on_weekend(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    data.save_cache("prices_live", {"A": _long("2026-11-13"), "B": _long("2026-11-13")})
    data.save_cache("prices_full_at", True)
    monkeypatch.setattr(nse, "extend_prices", lambda old, s, **kw: (old, []))
    px, msg = data.refresh_prices(["A", "B"], log=lambda m: None, today=date(2026, 11, 14))   # Saturday
    assert "up to date" in msg and px is not None
