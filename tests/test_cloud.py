"""Supabase backend tested against an in-memory fake of its REST + Storage HTTP APIs.

The fake mimics what matters: JSON round-trips (100.0 comes back as 100), UNIQUE(prev_hash) -> 409,
the append-only trigger, PATCH filters, upsert on conflict, limit/offset paging, and Storage 400 for
missing objects. It exercises the real request-building code in core/cloud.py.
"""
import json
from urllib.parse import urlparse

import pandas as pd
import pytest

from core import cloud, config, data, ledger, market


class Resp:
    def __init__(self, status, body=None, content=b""):
        self.status_code, self._body = status, body
        self.content = content if body is None else json.dumps(body).encode()
        self.text = self.content.decode(errors="ignore")

    def json(self):
        return self._body


def pg(v):
    """PostgREST serialises 100.0 (double precision) as 100."""
    return int(v) if isinstance(v, float) and v.is_integer() else v


class FakeSupabase:
    def __init__(self):
        self.tables = {"ideas": [], "trades": [], "fii_dii": []}
        self.objects = {}
        self.calls = []

    def request(self, method, url, headers=None, params=None, json=None, data=None, timeout=None):
        assert headers.get("apikey") == "sb_secret_test"
        path = urlparse(url).path
        self.calls.append((method, path))
        params = dict(params or {})
        if path.startswith("/storage/v1/object/"):
            key = path[len("/storage/v1/object/"):]
            if method == "POST":
                self.objects[key] = data
                return Resp(200, {"Key": key})
            return Resp(200, content=self.objects[key]) if key in self.objects else Resp(400, {"error": "not_found"})
        table = path.rsplit("/", 1)[-1]
        rows = self.tables[table]
        if method == "GET":
            out = list(rows)
            order = params.get("order", "")
            if order:
                col, direction = order.split(".")
                out.sort(key=lambda r: r[col], reverse=direction == "desc")
            off, lim = int(params.get("offset", 0)), int(params.get("limit", 10 ** 9))
            out = out[off:off + lim]
            if params.get("select", "*") != "*":
                keep = params["select"].split(",")
                out = [{k: r[k] for k in keep} for r in out]
            return Resp(200, [{k: pg(v) for k, v in r.items()} for r in out])
        if method == "POST":
            created = []
            for r in json:
                if table == "ideas" and any(x["prev_hash"] == r["prev_hash"] for x in rows):
                    return Resp(409, {"code": "23505", "message": "duplicate key value violates unique constraint"})
                if params.get("on_conflict"):
                    keys = params["on_conflict"].split(",")
                    rows[:] = [x for x in rows if any(x[k] != r[k] for k in keys)]
                r = dict(r)
                if table != "fii_dii":
                    r["id"] = len(rows) + 1
                rows.append(r)
                created.append(r)
            return Resp(201, [{k: pg(v) for k, v in r.items()} for r in created])
        if method == "PATCH":
            if table == "ideas":
                return Resp(400, {"message": "idea book is append-only: UPDATE blocked"})
            match = {k: v.removeprefix("eq.") for k, v in params.items()}
            hit = [r for r in rows if all(str(r[k]) == v for k, v in match.items())]
            for r in hit:
                r.update(json)
            return Resp(200, hit)
        raise AssertionError(f"unexpected {method} {path}")


@pytest.fixture
def supa(monkeypatch, tmp_path):
    fake = FakeSupabase()
    cloud.set_client(cloud.Supa("https://x.supabase.co", "sb_secret_test", session=fake))
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    yield fake
    cloud.set_client(None)


def idea(i):
    return {"kind": "swing", "symbol": f"S{i}", "setup": "t", "side": "long", "entry": 100.0, "stop": 90.0,
            "target": 120.0, "horizon_days": 10, "instrument": "Equity", "lot_size": None, "rationale": "x"}


def test_publish_and_verify_through_supabase(supa):
    ids = [ledger.publish_idea(idea(i)) for i in range(3)]
    assert ids == [1, 2, 3]
    df = ledger.ideas()
    assert len(df) == 3 and df["prev_hash"].iloc[0] == ledger.GENESIS
    ok, msg = ledger.verify_chain()     # despite 100.0 coming back as 100
    assert ok, msg


def test_tampered_row_in_supabase_is_detected(supa):
    for i in range(3):
        ledger.publish_idea(idea(i))
    supa.tables["ideas"][1]["target"] = 150.0   # someone edits the database directly
    ok, msg = ledger.verify_chain()
    assert not ok and "#2" in msg


def test_publish_retries_when_another_publish_races(supa):
    ledger.publish_idea(idea(0))
    real = supa.request
    state = {"raced": False}

    def racing(method, url, **kw):
        # just before our insert, another session publishes on top of the same parent
        if method == "POST" and url.endswith("/ideas") and not state["raced"]:
            state["raced"] = True
            mine = kw["json"][0]
            other = {k: mine[k] for k in ledger.IDEA_FIELDS} | {"symbol": "OTHER"}
            other |= {"prev_hash": mine["prev_hash"], "hash": ledger._digest(other, mine["prev_hash"])}
            real("POST", url, **{**kw, "json": [other]})
        return real(method, url, **kw)

    supa.request = racing
    ledger.publish_idea(idea(1))
    df = ledger.ideas()
    assert list(df["symbol"]) == ["S0", "OTHER", "S1"]
    assert ledger.verify_chain()[0]           # chain stayed linear, no fork


def test_trades_round_trip(supa):
    tid = ledger.add_trade({"segment": "Futures", "symbol": "ABC", "side": "long", "qty": 500, "entry_date": "2026-11-01",
                            "entry": 200.0, "charges": 100.0})
    ledger.close_trade(tid, "2026-11-05", 210.0, 250.0)
    t = ledger.trades()
    assert t.iloc[0]["pnl"] == pytest.approx(10 * 500 - 250)


def test_fii_dii_upsert(supa):
    day = pd.DataFrame([{"date": pd.Timestamp("2026-11-06"), "category": "FII", "buy": 10.0, "sell": 12.0, "net": -2.0}])
    market.merge_fii_dii(day)
    market.merge_fii_dii(day.assign(net=-3.0))       # NSE revises the provisional figure
    h = market.load_fii_dii_history()
    assert len(h) == 1 and h.iloc[0]["net"] == -3


def test_cache_syncs_between_machines(supa, tmp_path, monkeypatch):
    data.save_cache("prices_live", {"ABC": 1})       # e.g. the scheduled refresh job
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "other_machine")  # e.g. Streamlit Cloud after restart
    obj, saved = data.load_cache("prices_live")
    assert obj == {"ABC": 1} and saved is not None
    assert data.cache_saved_at("prices_live") == saved


def test_missing_cache_is_none(supa):
    assert data.load_cache("nothing") == (None, None)


def test_paging_reads_more_than_one_page(supa, monkeypatch):
    monkeypatch.setattr(cloud, "_PAGE", 2)
    for i in range(5):
        ledger.publish_idea(idea(i))
    assert len(ledger.ideas()) == 5


def test_prepare_for_claude_writes_everything(supa, monkeypatch):
    """The one-button daily step: backtest + grades + snapshot tables, against the fake Supabase."""
    from core import data as d, nse, state
    for t in ("market_snapshot", "signals", "breadth_daily", "backtest_grades", "paper_trades", "paper_results", "fo_lots"):
        supa.tables[t] = []
    u = d.load_universe().head(25)
    px = d.DemoProvider(days=320).prices(list(u["symbol"]))
    fu = d.DemoProvider(days=320).all_fundamentals(list(u["symbol"]))
    monkeypatch.setattr(state, "load_prices", lambda demo: px)
    monkeypatch.setattr(state, "load_universe", lambda: u)
    monkeypatch.setattr(state, "load_screen", lambda demo: __import__("core.valuation", fromlist=["x"]).screen(fu, u))
    monkeypatch.setattr(nse, "fetch_lot_sizes", lambda s=None: {"ABC": 100})
    state.prepare_for_claude()
    assert len(supa.tables["market_snapshot"]) == 25
    assert len(supa.tables["backtest_grades"]) >= 2 and supa.tables["breadth_daily"]
    assert {r["setup"] for r in supa.tables["backtest_grades"]} >= {"Momentum leaders (RS top 10%, trailing stop)"}
    assert supa.tables["fo_lots"] == [{"symbol": "ABC", "lot_size": 100, "id": 1}]
