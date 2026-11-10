import hashlib
import json
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from core import cloud, config, data, kite


class R:
    def __init__(self, status, body=None, text=None):
        self.status_code = status
        self._b = body
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        return self._b


INSTR_NSE = ("instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,"
             "instrument_type,segment,exchange\n"
             "738561,2885,RELIANCE,RELIANCE INDUSTRIES,0,,0,0.05,1,EQ,NSE,NSE\n"
             "408065,1594,INFY,INFOSYS,0,,0,0.05,1,EQ,NSE,NSE\n"
             "256265,1001,NIFTY 50,NIFTY 50,0,,0,0,0,EQ,INDICES,NSE\n")
INSTR_NFO = ("instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,"
             "instrument_type,segment,exchange\n"
             "1,1,RELIANCE26DECFUT,RELIANCE,0,2026-12-31,0,0.1,500,FUT,NFO-FUT,NFO\n"
             "2,2,RELIANCE26NOVFUT,RELIANCE,0,2026-11-26,0,0.1,250,FUT,NFO-FUT,NFO\n"
             "3,3,INFY26NOV1500CE,INFY,0,2026-11-26,1500,0.05,400,CE,NFO-OPT,NFO\n"
             "4,4,INFY26NOVFUT,INFY,0,2026-11-26,0,0.1,400,FUT,NFO-FUT,NFO\n")


class FakeKite:
    def __init__(self):
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(url)
        assert headers["Authorization"] == "token key1:tok1"
        if url.endswith("/instruments/NSE"):
            return R(200, text=INSTR_NSE)
        if url.endswith("/instruments/NFO"):
            return R(200, text=INSTR_NFO)
        if "/historical/738561/" in url:
            return R(200, {"status": "success", "data": {"candles": [
                ["2026-11-06T00:00:00+0530", 1500, 1520, 1490, 1510, 100000],
                ["2026-11-09T00:00:00+0530", 1510, 1530, 1505, 1525, 120000]]}})
        if "/historical/" in url:
            return R(200, {"status": "success", "data": {"candles": []}})
        return R(404, {})


@pytest.fixture
def keys(monkeypatch, tmp_path):
    monkeypatch.setenv("KITE_API_KEY", "key1")
    monkeypatch.setenv("KITE_API_SECRET", "sec1")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    cloud.set_client(None)
    yield


def test_checksum_matches_kite_spec():
    assert kite.checksum("a", "b", "c") == hashlib.sha256(b"abc").hexdigest()


def test_token_expires_at_six_am_ist():
    ist = kite.IST
    assert kite.valid_until(datetime(2026, 11, 10, 8, 0, tzinfo=ist)) == datetime(2026, 11, 11, 6, 0, tzinfo=ist)
    assert kite.valid_until(datetime(2026, 11, 10, 5, 0, tzinfo=ist)) == datetime(2026, 11, 10, 6, 0, tzinfo=ist)


def test_exchange_stores_session_and_expires(keys):
    class S:
        def post(self, url, headers=None, data=None, timeout=None):
            assert data["checksum"] == kite.checksum("key1", "rt1", "sec1")
            return R(200, {"status": "success", "data": {"access_token": "tok1", "user_id": "AB1234"}})
    blob = kite.exchange_token("rt1", session=S())
    assert blob["access_token"] == "tok1" and kite.session_info()["user_id"] == "AB1234"
    later = datetime.fromisoformat(blob["valid_until"]).astimezone(timezone.utc).replace(microsecond=1)
    assert kite.session_info(now=later) is None                      # expired after 6 am


def test_failed_login_raises(keys):
    class S:
        def post(self, *a, **k):
            return R(403, {"status": "error", "message": "Token is invalid or has expired."})
    with pytest.raises(RuntimeError, match="invalid"):
        kite.exchange_token("bad", session=S())


def test_prices_and_lot_sizes():
    kc = kite.KiteClient("key1", "tok1", session=FakeKite())
    px = kite.prices(["RELIANCE", "INFY", "NOTLISTED"], kc=kc, today=date(2026, 11, 10), pause=0)
    assert list(px) == ["RELIANCE"]                                   # INFY had no candles, NOTLISTED unknown
    df = px["RELIANCE"]
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"] and df["Close"].iloc[-1] == 1525
    assert df.index[-1] == pd.Timestamp("2026-11-09")
    assert kite.lot_sizes(kc) == {"RELIANCE": 250, "INFY": 400}      # nearest-expiry futures


def test_expired_session_message():
    class S:
        def get(self, *a, **k):
            return R(403, {"status": "error"})
    with pytest.raises(PermissionError, match="Connect Zerodha"):
        kite.KiteClient("k", "t", session=S()).instruments()


def test_refresh_prefers_kite_then_falls_back(keys, monkeypatch):
    syms = ["A", "B"]
    frame = pd.DataFrame({"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]},
                         index=[pd.Timestamp("2026-11-09")])
    monkeypatch.setattr(kite, "session_info", lambda now=None: {"access_token": "t", "valid_until": "2099-01-01T00:00:00+00:00"})
    monkeypatch.setattr(kite, "prices", lambda s, progress=None: {x: frame for x in s})
    px, msg = data.refresh_prices(syms, log=lambda m: None)
    assert "Zerodha" in msg and set(px) == set(syms)
    monkeypatch.setattr(kite, "prices", lambda s, progress=None: (_ for _ in ()).throw(PermissionError("expired")))
    monkeypatch.setattr(data.YahooProvider, "prices", lambda self, s, progress=None: {x: frame for x in s})
    px, msg = data.refresh_prices(syms, log=lambda m: None)
    assert "Yahoo" in msg


def test_pasted_keys_are_cleaned(monkeypatch):
    monkeypatch.setenv("KITE_API_KEY", '  "abcd1234efgh5678"\n')
    monkeypatch.setenv("KITE_API_SECRET", " sec ")
    assert kite.settings() == ("abcd1234efgh5678", "sec")
    assert kite.login_url().endswith("api_key=abcd1234efgh5678")
    assert kite.masked_key() == "abcd…5678 (16 chars)"
