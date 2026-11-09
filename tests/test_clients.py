import pandas as pd
import pytest

from core import ledger, profile


def answers(**kw):
    base = {"age": "30-45", "horizon": "More than 5 years", "income": "Stable salary or business",
            "emergency": "Yes", "experience": "Futures & options", "reaction": "Buy more",
            "max_loss": "30%", "share": "Less than 10%"}
    base.update(kw)
    return base


def test_aggressive_experienced_client_gets_all_tracks():
    r = profile.assess(answers())
    assert r["tracks"] == ["longterm", "swing", "fno"] and r["profile"] == "Aggressive"


def test_no_emergency_fund_blocks_fno_even_with_high_score():
    r = profile.assess(answers(emergency="No"))
    assert "fno" not in r["tracks"] and any("emergency fund" in x for x in r["reasons"])


def test_beginner_blocked_from_fno():
    r = profile.assess(answers(experience="Mutual funds / FDs only"))
    assert "fno" not in r["tracks"] and "swing" in r["tracks"]


def test_conservative_client_long_term_only():
    r = profile.assess(answers(age="Above 60", horizon="Within 1 year", emergency="No", experience="None",
                               reaction="Sell everything", max_loss="5%", share="More than 50%",
                               income="Unstable / irregular"))
    assert r["tracks"] == ["longterm"] and r["profile"] == "Conservative"


def test_missing_answer_rejected():
    a = answers()
    del a["age"]
    with pytest.raises(ValueError):
        profile.assess(a)


def idea(**kw):
    d = {"created_utc": "2026-11-05T10:00:00+00:00", "kind": "swing", "symbol": "ABC", "setup": "t", "side": "long",
         "entry": 100, "stop": 90, "target": 120, "horizon_days": 10, "instrument": "Equity", "lot_size": None,
         "rationale": "x"}
    d.update(kw)
    return d


def test_tracks_publish_filter_and_chain(tmp_path):
    db = tmp_path / "j.db"
    ledger.publish_idea(idea(), db)                                         # no tracks -> default by kind
    ledger.publish_idea(idea(kind="fundamental", tracks=["longterm", "swing"]), db)
    ledger.publish_idea(idea(kind="fno", tracks=["fno"]), db)
    df = ledger.ideas(db)
    assert ledger.verify_chain(db)[0]
    assert list(ledger.ideas_for_track(df, "swing")["id"]) == [1, 2]
    assert list(ledger.ideas_for_track(df, "longterm")["id"]) == [2]
    assert list(ledger.ideas_for_track(df, "fno")["id"]) == [3]


def test_ideas_published_before_tracks_still_verify(tmp_path):
    """An idea hashed by the old code (no 'tracks' field) must still verify with the new code."""
    import hashlib, json, sqlite3
    db = tmp_path / "old.db"
    old_fields = [f for f in ledger.IDEA_FIELDS if f != "tracks"]
    rec = ledger._normalise(idea())
    payload = json.dumps({k: rec[k] for k in old_fields}, sort_keys=True, default=str)
    h = hashlib.sha256((ledger.GENESIS + payload).encode()).hexdigest()
    with sqlite3.connect(db) as c:   # database created by the old version: no tracks column
        c.execute("""CREATE TABLE ideas (id INTEGER PRIMARY KEY, created_utc TEXT, kind TEXT, symbol TEXT, setup TEXT,
            side TEXT, entry REAL, stop REAL, target REAL, horizon_days INTEGER, instrument TEXT, lot_size INTEGER,
            rationale TEXT, prev_hash TEXT, hash TEXT)""")
        c.execute(f"INSERT INTO ideas ({','.join(old_fields)},prev_hash,hash) VALUES ({','.join('?' * len(old_fields))},?,?)",
                  [rec[k] for k in old_fields] + [ledger.GENESIS, h])
    assert ledger.verify_chain(db)[0]
    ledger.publish_idea(idea(symbol="NEW", tracks=["swing"]), db)          # new idea chains onto the old one
    assert ledger.verify_chain(db)[0]


def test_profiles_saved(tmp_path):
    db = tmp_path / "j.db"
    r = profile.assess(answers())
    ledger.save_profile({"client_name": "Test", "answers": answers(), "score": r["score"], "tracks": r["tracks"],
                         "consent": True}, db)
    p = ledger.profiles(db)
    assert len(p) == 1 and p.iloc[0]["tracks"] == "longterm,swing,fno" and p.iloc[0]["answers"]["age"] == "30-45"


def test_only_grade_a_and_strong_ideas_publishable():
    from core import state
    assert state.publish_block_reason("swing", "A - publishable") is None
    assert state.publish_block_reason("fno", "A - publishable") is None
    assert "grade" in state.publish_block_reason("swing", "B - monitor")
    assert "not tested" in state.publish_block_reason("fno", None)
    assert state.publish_block_reason("fundamental", verdict="Strong idea", gate="pass") is None
    assert state.publish_block_reason("fundamental", verdict="Watch", gate="pass")
    assert state.publish_block_reason("fundamental", verdict="Strong idea", gate="debt/equity > 2")
    assert "demo" in state.publish_block_reason("swing", "A - publishable", demo=True)
