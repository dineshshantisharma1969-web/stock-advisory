import json
from datetime import date

import pandas as pd
import pytest

from core import config, data, kotak

PATHS = {"data": {"baseFolder": "x", "filesPaths": [
    "https://lapi.kotaksecurities.com/wso2-scripmaster/v1/prod/2026-11-10/transformed/nse_com.csv",
    "https://lapi.kotaksecurities.com/wso2-scripmaster/v1/prod/2026-11-10/transformed/nse_fo.csv",
    "https://lapi.kotaksecurities.com/wso2-scripmaster/v1/prod/2026-11-10/transformed-v1/nse_cm-v1.csv"]}}
NSE_CM = ("pSymbol,pTrdSymbol,pExchSeg,pSymbolName,pGroup\n"
          "2885,RELIANCE-EQ,nse_cm,RELIANCE,EQ\n"
          "1594,INFY-EQ,nse_cm,INFY,EQ\n"
          "9999,RELINFRA-BE,nse_cm,RELINFRA,BE\n"
          "7777,RELIANCE-BL,nse_cm,RELIANCE,BL\n")
NSE_FO = ("pSymbol,pTrdSymbol,pExchSeg,pSymbolName,pInstType,lLotSize,pExpiryDate,dStrikePrice;\n"
          "1,RELIANCE26NOVFUT,nse_fo,RELIANCE,FUTSTK,500,1,0\n"
          "2,RELIANCE26DECFUT,nse_fo,RELIANCE,FUTSTK,500,2,0\n"
          "3,INFY26NOV1500CE,nse_fo,INFY,OPTSTK,400,1,150000\n"
          "4,NIFTY26NOVFUT,nse_fo,NIFTY,FUTIDX,75,1,0\n")


class R:
    def __init__(self, status, body=None, text=None):
        self.status_code, self._b = status, body
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        return self._b


class FakeKotak:
    def __init__(self):
        self.hist_calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        if url.endswith(".csv"):        # scrip files sit on a public file store: the key must NOT be sent there
            assert "Authorization" not in headers
        else:
            assert headers["Authorization"] == "ck1"
        if url.endswith("/masterscrip/file-paths"):
            return R(200, PATHS)
        if url.endswith("nse_cm-v1.csv"):
            return R(200, text=NSE_CM)
        if url.endswith("nse_fo.csv"):
            return R(200, text=NSE_FO)
        if url.endswith("/historical/details"):
            self.hist_calls.append(params)
            assert params["interval"] == "D"
            if params["neosymbol"] == "nse_cm|2885":
                return R(200, {"status": "success", "data": {"candles": [
                    ["2026-11-09T00:00:00+0530", 1500, 1520, 1490, 1510, 100000, 0],
                    ["2026-11-10T00:00:00+0530", 1510, 1530, 1505, 1525, 120000, 0]]}})
            return R(200, {"status": "success", "data": {"candles": []}})
        return R(404, {})


def frame(dates, close=1.0):
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1.0},
                        index=pd.to_datetime(dates))


def test_tokens_prefer_eq_and_ignore_other_series():
    kc = kotak.KotakClient("ck1", session=FakeKotak())
    assert kotak.equity_tokens(kc) == {"RELIANCE": 2885, "INFY": 1594, "RELINFRA": 9999}


def test_top_up_appends_only_missing_days():
    fk = FakeKotak()
    kc = kotak.KotakClient("ck1", session=fk)
    old = {"RELIANCE": frame(["2026-11-05", "2026-11-06"]), "INFY": frame(["2026-11-06"])}
    out, n = kotak.top_up(old, ["RELIANCE", "INFY", "UNKNOWN"], kc=kc, today=date(2026, 11, 10), pause=0)
    assert n == 1 and list(out["RELIANCE"].index.strftime("%d")) == ["05", "06", "09", "10"]
    assert out["RELIANCE"]["Close"].iloc[-1] == 1525 and len(out["INFY"]) == 1      # INFY unchanged
    assert fk.hist_calls[0]["fromdate"] == "2026-11-07" and fk.hist_calls[0]["todate"] == "2026-11-10"


def test_top_up_never_asks_beyond_180_days():
    fk = FakeKotak()
    kc = kotak.KotakClient("ck1", session=fk)
    kotak.top_up({"RELIANCE": frame(["2025-01-01"])}, ["RELIANCE"], kc=kc, today=date(2026, 11, 10), pause=0)
    span = (pd.Timestamp(fk.hist_calls[0]["todate"]) - pd.Timestamp(fk.hist_calls[0]["fromdate"])).days + 1
    assert span <= kotak.MAX_DAILY_SPAN


def test_lot_sizes_from_futures_only():
    kc = kotak.KotakClient("ck1", session=FakeKotak())
    assert kotak.lot_sizes(kc) == {"RELIANCE": 500, "NIFTY": 75}


def test_bad_key_message():
    class S:
        def get(self, *a, **k):
            return R(403, {"message": "invalid"})
    with pytest.raises(PermissionError, match="consumer_key"):
        kotak.KotakClient("bad", session=S()).scrip_file()


def test_clean_key(monkeypatch):
    monkeypatch.setenv("KOTAK_CONSUMER_KEY", '  "abcd1234wxyz9876"\n')
    assert kotak.consumer_key() == "abcd1234wxyz9876" and kotak.masked_key() == "abcd…9876 (16 chars)"


def test_rejected_key_is_masked_in_errors():
    fake = "abcd1234-0000-4000-8000-00000000wxyz".replace("wxyz", "9f9f")

    class S:
        def get(self, *a, **k):
            return R(424, {"error": True, "message": f"Consumer key '{fake}' is invalid. "})
    with pytest.raises(PermissionError) as e:
        kotak.KotakClient(fake, session=S()).scrip_file()
    assert fake not in str(e.value) and "abcd...9f9f" in str(e.value)
