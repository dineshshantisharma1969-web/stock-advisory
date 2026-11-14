import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from core import live

spec = importlib.util.spec_from_file_location("relay", Path(__file__).parent.parent / "relay" / "kotak_live_relay.py")
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


def test_to_row_parses_kotak_quote():
    q = {"exchange_token": "1333", "ltp": "801.6000", "per_change": "1.06", "last_volume": "30043533",
         "lstup_time": "1794630000", "ohlc": {"open": "798.5", "high": "804.45", "low": "796", "close": "793.2"}}
    r = relay.to_row("HDFCBANK", q)
    assert r["ltp"] == 801.6 and r["prev_close"] == 793.2 and r["change_pct"] == 1.06 and r["quote_time"].endswith("+05:30")
    assert relay.to_row("X", {"ltp": "0"}) is None and relay.to_row("X", {"ltp": "abc"}) is None


def test_secrets_are_masked_in_logs():
    relay.SECRETS[:] = ["abcd1234efgh5678ijkl"]
    assert "1234efgh" not in relay.mask("Kotak said: key abcd1234efgh5678ijkl is invalid")


class FakeKotak:
    def __init__(self):
        self.calls = []

    def quotes(self, pairs):
        self.calls.append(pairs)
        if pairs[0][1] == "Nifty 50":
            raise RuntimeError("not supported")
        return [{"exchange_token": t, "ltp": "100", "ohlc": {"close": "99"}} for _, t in pairs]


class FakeApp:
    def __init__(self):
        self.pushed = None

    def push(self, rows, msg):
        self.pushed = (rows, msg)
        return len(rows)


def test_one_round_batches_by_50_maps_symbols_and_drops_unsupported_indices():
    k, a = FakeKotak(), FakeApp()
    tok = {f"S{i}": str(1000 + i) for i in range(120)}
    idx = {"on": True}
    n, msg = relay.one_round(k, a, tok, list(tok) + ["NOTOKEN"], idx)
    assert n == 120 and [len(c) for c in k.calls[:3]] == [50, 50, 20]
    assert {r["symbol"] for r in a.pushed[0]} == set(tok) and "1 symbols without a Kotak token" in msg
    assert idx["on"] is False                       # index request failed once -> not asked again
    relay.one_round(k, a, tok, list(tok), idx)
    assert all(c[0][1] != "Nifty 50" for c in k.calls[4:])


def test_market_window():
    ist = relay.IST
    assert relay.in_window(datetime(2026, 11, 16, 9, 14, tzinfo=ist))
    assert not relay.in_window(datetime(2026, 11, 16, 15, 33, tzinfo=ist))
    assert not relay.in_window(datetime(2026, 11, 14, 11, 0, tzinfo=ist))       # Saturday


# ------------------------------------------------------------------ app side
class DB:
    def __init__(self, rows=None, status=None):
        self.rows, self.st, self.updates = rows or [], status or {}, []

    def select(self, table, params=None):
        return self.rows if table == "live_quotes" else [self.st]

    def update(self, table, match, values):
        self.updates.append((table, match, values))


def test_passphrase_is_stored_only_as_hash():
    db = DB()
    live.set_passphrase(db, "  my long relay phrase ")
    stored = db.updates[0][2]["token_hash"]
    assert len(stored) == 64 and "phrase" not in stored
    import hashlib
    assert stored == hashlib.sha256(b"my long relay phrase").hexdigest()
    with pytest.raises(ValueError):
        live.set_passphrase(db, "short")


NOW = datetime(2026, 11, 16, 5, 0, tzinfo=timezone.utc)      # 10:30 IST


def test_quotes_keep_only_fresh_rows_and_live_pnl():
    db = DB([{"symbol": "GLAND", "ltp": 3340.0, "updated_at": "2026-11-16T04:58:00+00:00"},
             {"symbol": "OLD", "ltp": 10.0, "updated_at": "2026-11-13T09:59:00+00:00"}])
    q = live.quotes(db, now=NOW)
    assert list(q.index) == ["GLAND"]
    trades = pd.DataFrame([{"symbol": "GLAND", "side": "long", "fill": 3268.5, "qty": 61, "status": "open"},
                           {"symbol": "GLAND", "side": "long", "fill": 3000.0, "qty": 10, "status": "stop"}])
    p = live.live_pnl(trades, q)
    assert len(p) == 1 and p["live_pnl"].iloc[0] == pytest.approx((3340 - 3268.5) * 61 - 0.001 * 3268.5 * 61)


def test_status_lines():
    assert live.describe_status({})[0] == "off"
    assert live.describe_status({"token_hash": "x"})[0] == "off"
    assert live.describe_status({"token_hash": "x", "last_push": "2026-11-16T04:58:00+00:00", "last_count": 498},
                                now=NOW) == ("ok", "Live: last update 16 Nov 10:28 (498 prices).")
    assert live.describe_status({"token_hash": "x", "last_push": "2026-11-13T09:59:00+00:00"}, now=NOW)[0] == "stale"


def test_settings_accept_percent_and_symbols(tmp_path, monkeypatch):
    ini = tmp_path / "relay_settings.ini"
    ini.write_text("[kotak]\nconsumer_key = abc123def456ghi\n[app]\npassphrase = My%$pass#word 100%!\n"
                   "supabase_url = https://x.supabase.co/\npublishable_key = sb_publishable_x\n", encoding="utf-8")
    monkeypatch.setattr(relay, "SETTINGS", ini)
    s = relay.settings()
    assert s["passphrase"] == "My%$pass#word 100%!" and s["url"] == "https://x.supabase.co"
