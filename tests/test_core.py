import concurrent.futures
import time
from datetime import datetime, timedelta
import pytest
from companion.models import Watchlist, queries, matches, today, in_quiet_hours, DHAKA
from companion.store import Store, initial_state
from companion.engine import claim, permitted, set_mode, Engine
from companion.railway import parse_results, RailwayError, departure_clock


def watch(**overrides):
    data = dict(name="Weekend", origins=["Dhaka", "Biman_Bandar"], destinations=["Sylhet"],
        dates=[today()], classes=["SNIGDHA", "S_CHAIR"], passengers=2, action="payment")
    data.update(overrides)
    return dict(Watchlist(**data).model_dump(), id="watch1", revision=1)


def result(**overrides):
    data = dict(id="result1", origin="Dhaka", destination="Sylhet", date=today(),
        train="PARABAT EXPRESS (709)", trip_id="1", **{"class": "SNIGDHA"}, seats=2,
        fare="99999", departure_time="22:00", checked=time.time(), url="https://eticket.railway.gov.bd/",
        query=["Dhaka", "Sylhet", today(), "SNIGDHA"])
    data.update(overrides)
    return data


def ready_state():
    s = initial_state()
    s.update(mode="at_pc", watches=[watch()], results=[result()], agent={"id":"desktop", "ready":True, "seen":time.time()})
    return s


def test_matching_ignores_price_and_requires_full_group():
    assert matches(watch(), result(fare="999999"))
    assert not matches(watch(), result(seats=1))
    assert not matches(watch(), result(destination="Chattogram"))
    assert not matches(watch(), result(**{"class":"AC_B"}))


def test_overlapping_watchlists_share_queries_and_same_station_is_excluded():
    w = watch(origins=["Dhaka", "Sylhet"], destinations=["Sylhet", "Dhaka"])
    q = queries([w, dict(w, id="watch2")])
    assert len(q) == 4
    assert all(len(subscribers) == 2 for subscribers in q.values())
    assert all(a != b for a, b, _, _ in q)


def test_old_dates_are_not_polled():
    old = (datetime.now(DHAKA).date() - timedelta(days=1)).isoformat()
    assert queries([watch(dates=[old])]) == {}


def test_overnight_departure_and_quiet_hours():
    w = watch(departure_after="21:00", departure_before="05:00")
    assert matches(w, result(departure_time="23:30"))
    assert matches(w, result(departure_time="03:00"))
    assert not matches(w, result(departure_time="12:00"))
    assert not matches(w, result(departure_time=""))
    cfg = {"quiet_start":"22:00", "quiet_end":"06:00"}
    assert in_quiet_hours(cfg, datetime(2026, 1, 1, 23, 0, tzinfo=DHAKA))
    assert not in_quiet_hours(cfg, datetime(2026, 1, 1, 12, 0, tzinfo=DHAKA))


@pytest.mark.parametrize("change", [lambda s:s.update(mode="away"), lambda s:s.update(paused=True),
    lambda s:s["agent"].update(ready=False), lambda s:s["agent"].update(seen=time.time()-30),
    lambda s:s["results"][0].update(checked=time.time()-61), lambda s:s["results"][0].update(error=True),
    lambda s:s["watches"][0].update(enabled=False), lambda s:s["watches"][0].update(action="watch"),
    lambda s:s["results"][0].update(seats=1)])
def test_claim_rejects_unusable_or_away_state(change):
    s = ready_state()
    change(s)
    assert claim(s, "desktop") is None


def test_claim_chooses_preferences_and_only_one_attempt():
    s = ready_state()
    s["results"].insert(0, result(id="other", **{"class":"S_CHAIR"}))
    job = claim(s, "desktop")
    assert job["result"]["class"] == "SNIGDHA"
    assert claim(s, "desktop") is None
    assert permitted(s, job["id"], "desktop")
    assert not permitted(s, job["id"], "other-desktop")


def test_mode_change_and_watch_edit_invalidate_inflight_permission():
    s = ready_state()
    job = claim(s, "desktop")
    s["watches"][0]["revision"] += 1
    assert not permitted(s, job["id"], "desktop")
    set_mode(s, "away")
    assert job["status"] == "reconcile"
    set_mode(s, "at_pc")
    assert not permitted(s, job["id"], "desktop")
    assert claim(s, "desktop") is None


def test_atomic_claims_across_concurrent_requests(tmp_path):
    store = Store(tmp_path)
    store.mutate(lambda s:s.update(ready_state()))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        answers = list(pool.map(lambda _:store.mutate(lambda s:claim(s, "desktop")), range(16)))
    assert sum(a is not None for a in answers) == 1
    store.close()


def test_secrets_encrypted_and_persisted(tmp_path):
    store = Store(tmp_path)
    store.set_secret("railway", {"token":"do-not-leak-this-token"})
    raw = store.db.execute("SELECT value FROM secrets WHERE name='railway'").fetchone()[0]
    assert "do-not-leak" not in raw
    store.close()
    store = Store(tmp_path)
    assert store.secret("railway")["token"] == "do-not-leak-this-token"
    store.close()


def test_alert_deduplication_and_whole_group_threshold(tmp_path):
    store = Store(tmp_path)
    store.mutate(lambda s:s.update(watches=[watch()]))
    engine = Engine(store)
    query = result()["query"]
    engine.accept(query, [result(seats=1)])
    assert not any(e["kind"] == "match" for e in store.read()["events"])
    engine.accept(query, [result(seats=2)])
    engine.accept(query, [result(seats=2)])
    assert sum(e["kind"] == "match" for e in store.read()["events"]) == 1
    assert store.read()["outbox"] == []  # globally disabled Telegram stays disabled
    store.close()


def test_response_shape_unknown_is_not_sold_out():
    q = ("Dhaka", "Sylhet", today(), "SNIGDHA")
    with pytest.raises(RailwayError):
        parse_results({"data":{"unexpected":[]}}, q)
    assert parse_results({"data":{"trains":[]}}, q) == []
    parsed = parse_results({"data":{"trains":[{"trip_number":"TEST (123)", "seat_types":[
        {"type":"SNIGDHA", "seat_counts":{"online":2,"offline":99},"fare":300}]}]}}, q)
    assert parsed[0]["seats"] == 2
    assert departure_clock("17 Oct, 12:30 am") == "00:30"
    assert departure_clock("17 Oct, 02:30 pm") == "14:30"
