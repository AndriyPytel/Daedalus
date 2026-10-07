"""
The gateway persists users, Sessions, Runs and events in Postgres, and every row carries its owner.

The tests run against a real Postgres named by `DAEDALUS_TEST_DATABASE_URL` and skip, saying why, when it is unset or
unreachable. Each test gets a throwaway empty database so migrations are exercised from nothing.
"""
import os
import threading
import time
import uuid

import psycopg
import psycopg.conninfo
import psycopg.errors
import pytest
from fastapi.testclient import TestClient

from service import db, ids
from service.app import app
from service.repository import Repository

URL_VARIABLE = "DAEDALUS_TEST_DATABASE_URL"


def _admin_url():
    url = os.environ.get(URL_VARIABLE)
    if not url:
        pytest.skip("%s is not set; start a Postgres and export its URL" % URL_VARIABLE)
    try:
        psycopg.connect(url, connect_timeout=3).close()
    except psycopg.OperationalError as error:
        pytest.skip("%s is set but unreachable: %s" % (URL_VARIABLE, error))
    return url


@pytest.fixture
def database_url():
    admin = _admin_url()
    name = "dae_%s" % uuid.uuid4().hex
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute('CREATE DATABASE "%s"' % name)
    yield psycopg.conninfo.make_conninfo(admin, dbname=name)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute('DROP DATABASE "%s" WITH (FORCE)' % name)


@pytest.fixture
def conn(database_url):
    with db.connect(database_url) as connection:
        db.migrate(connection)
        yield connection


@pytest.fixture
def repo(conn):
    return Repository(conn)


def test_migrations_apply_on_an_empty_database_and_a_second_start_applies_none(database_url):
    with db.connect(database_url) as connection:
        first = db.migrate(connection)
        second = db.migrate(connection)
    assert first
    assert second == []
    assert first == sorted(first)


def test_starting_the_gateway_migrates_the_database_named_by_the_environment(database_url, monkeypatch):
    monkeypatch.setenv(db.URL_VARIABLE, database_url)
    with TestClient(app):
        pass
    with TestClient(app):
        pass
    with db.connect(database_url) as connection:
        applied = [row[0] for row in connection.execute("SELECT version FROM schema_migrations")]
    assert applied == sorted(set(applied)) and applied


def test_a_session_and_a_run_read_back_with_owner_state_and_cost(repo):
    owner = repo.create_user()
    session = repo.create_session(owner)
    run = repo.create_run(owner, session["id"])
    repo.update_run(owner, run["id"], state="succeeded", cost_usd="0.4200")
    assert repo.get_session(owner, session["id"])["owner_id"] == owner
    stored = repo.get_run(owner, run["id"])
    assert (stored["owner_id"], stored["session_id"], stored["state"]) == (owner, session["id"], "succeeded")
    assert str(stored["cost_usd"]) == "0.4200"
    assert repo.create_run(owner, session["id"])["state"] == "queued"


def test_preferences_are_stored_per_owner(repo):
    owner = repo.create_user()
    repo.set_preference(owner, "theme", {"dark": True})
    repo.set_preference(owner, "theme", {"dark": False})
    assert repo.list_preferences(owner) == {"theme": {"dark": False}}


def test_events_keep_insertion_order_and_the_payload_round_trips(repo):
    owner = repo.create_user()
    run = repo.create_run(owner, repo.create_session(owner)["id"])
    for index in range(25):
        repo.append_event(owner, run["id"], "tick", {"n": index})
    events = repo.list_events(owner, run["id"])
    assert [event["payload"]["n"] for event in events] == list(range(25))
    assert [event["seq"] for event in events] == sorted(event["seq"] for event in events)
    assert [event["seq"] for event in repo.list_events(owner, run["id"], after=events[9]["seq"])] == [e["seq"] for e in events[10:]]


def test_the_repository_offers_no_way_to_change_or_remove_an_event():
    public = [name for name in dir(Repository) if not name.startswith("_")]
    assert [name for name in public if "event" in name] == ["append_event", "list_events"]


def test_the_database_itself_refuses_to_update_or_delete_an_event(repo, conn):
    owner = repo.create_user()
    run = repo.create_run(owner, repo.create_session(owner)["id"])
    repo.append_event(owner, run["id"], "tick", {})
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE events SET type = 'x'")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM events")
    assert len(repo.list_events(owner, run["id"])) == 1


def test_listing_rows_of_another_owner_returns_nothing(repo):
    mine, theirs = repo.create_user(), repo.create_user()
    session = repo.create_session(mine)
    run = repo.create_run(mine, session["id"])
    repo.append_event(mine, run["id"], "tick", {})
    repo.set_preference(mine, "k", 1)
    assert repo.list_sessions(theirs) == []
    assert repo.list_runs(theirs, session["id"]) == []
    assert repo.list_events(theirs, run["id"]) == []
    assert repo.list_preferences(theirs) == {}
    assert repo.get_session(theirs, session["id"]) is None
    assert repo.get_run(theirs, run["id"]) is None


def test_another_owner_cannot_write_into_my_session_or_run(repo):
    mine, theirs = repo.create_user(), repo.create_user()
    session = repo.create_session(mine)
    run = repo.create_run(mine, session["id"])
    with pytest.raises(LookupError):
        repo.create_run(theirs, session["id"])
    with pytest.raises(LookupError):
        repo.append_event(theirs, run["id"], "tick", {})
    with pytest.raises(LookupError):
        repo.update_run(theirs, run["id"], state="failed")
    assert repo.get_run(mine, run["id"])["state"] == "queued"


def test_ids_carry_their_prefix_and_a_uuid_v7():
    for prefix in ("ses", "run", "blob"):
        value = ids.new_id(prefix)
        head, _, tail = value.partition("_")
        assert head == prefix
        assert uuid.UUID(tail).version == 7


def test_ids_made_later_sort_later():
    made = []
    for _ in range(5):
        made.append(ids.new_id("ses"))
        time.sleep(0.003)
    assert made == sorted(made)


def test_a_notify_carries_only_the_event_row_id_to_a_direct_connection(database_url, repo):
    owner = repo.create_user()
    run = repo.create_run(owner, repo.create_session(owner)["id"])
    received = []
    ready = threading.Event()

    def listen():
        with psycopg.connect(database_url, autocommit=True) as listener:
            listener.execute("LISTEN events")
            ready.set()
            for note in listener.notifies(timeout=5, stop_after=1):
                received.append(note.payload)

    thread = threading.Thread(target=listen)
    thread.start()
    assert ready.wait(5)
    seq = repo.append_event(owner, run["id"], "tick", {"secret": "x"})
    thread.join(10)
    assert received == [str(seq)]


def test_two_gateways_migrating_at_once_apply_each_file_once(database_url):
    results = []

    def start():
        with db.connect(database_url) as connection:
            results.append(db.migrate(connection))

    threads = [threading.Thread(target=start) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    applied = [version for result in results for version in result]
    assert len(results) == 4
    assert applied == sorted(set(applied)) and applied


def test_the_migration_lock_key_is_derived_from_its_name():
    assert db.LOCK == db.lock_key("daedalus.schema_migrations")
    assert db.lock_key("a") != db.lock_key("b")
    assert -(2 ** 63) <= db.LOCK < 2 ** 63
