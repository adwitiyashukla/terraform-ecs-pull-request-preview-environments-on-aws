import os

import pytest
from fastapi.testclient import TestClient

import main

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+pysqlite:///:memory:")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PREVIEW_NAME", "pr-7")
    monkeypatch.setenv("IMAGE_TAG", "pr-7-abc1234")
    monkeypatch.setenv("EXPIRES_AT", "2026-10-01T00:00:00Z")
    app = main.create_app(DATABASE_URL)
    with TestClient(app) as client:
        with app.state.engine.begin() as conn:
            conn.execute(main.notes.delete())
        yield client


def test_healthz_reports_ok(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_info_reflects_environment(client):
    body = client.get("/api/info").json()
    assert body["preview"] == "pr-7"
    assert body["image_tag"] == "pr-7-abc1234"
    assert body["expires_at"] == "2026-10-01T00:00:00Z"
    assert body["notes"] == 0
    assert body["uptime_seconds"] >= 0


def test_notes_round_trip_through_json_api(client):
    created = client.post("/api/notes", json={"body": "hello reviewer"})
    assert created.status_code == 201
    note = created.json()
    assert note["body"] == "hello reviewer"
    assert note["id"] >= 1
    listed = client.get("/api/notes").json()
    assert [n["body"] for n in listed] == ["hello reviewer"]
    assert client.get("/api/info").json()["notes"] == 1


def test_newest_note_comes_first(client):
    client.post("/api/notes", json={"body": "first"})
    client.post("/api/notes", json={"body": "second"})
    listed = client.get("/api/notes").json()
    assert [n["body"] for n in listed] == ["second", "first"]


def test_rejects_empty_and_oversized_notes(client):
    assert client.post("/api/notes", json={"body": ""}).status_code == 422
    assert client.post("/api/notes", json={"body": "x" * 281}).status_code == 422
    assert client.get("/api/notes").json() == []


def test_form_post_redirects_home_and_escapes_html(client):
    response = client.post("/notes", data={"body": "<b>bold</b>"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    page = client.get("/").text
    assert "&lt;b&gt;bold&lt;/b&gt;" in page
    assert "<b>bold</b>" not in page


def test_index_shows_preview_details(client):
    page = client.get("/").text
    assert "<h1>pr-7</h1>" in page
    assert "image pr-7-abc1234" in page
    assert "expires 2026-10-01T00:00:00Z" in page
    assert "no notes yet" in page


def test_database_url_prefers_explicit_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///x.db")
    assert main.database_url() == "sqlite+pysqlite:///x.db"


def test_database_url_builds_postgres_url_from_parts(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_USER", "app")
    monkeypatch.setenv("DB_PASSWORD", "p@ss/word")
    monkeypatch.setenv("DB_NAME", "app")
    url = main.database_url()
    assert url.drivername == "postgresql+psycopg"
    assert url.host == "127.0.0.1"
    assert url.password == "p@ss/word"
    assert url.database == "app"


def test_database_url_falls_back_to_sqlite(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_HOST", raising=False)
    assert main.database_url() == "sqlite+pysqlite:///:memory:"


def test_wait_for_database_gives_up_after_attempts():
    engine = main.make_engine("postgresql+psycopg://app:app@127.0.0.1:1/app")
    with pytest.raises(main.OperationalError):
        main.wait_for_database(engine, attempts=2, delay=0)


def test_as_utc_converts_aware_and_keeps_naive():
    from datetime import UTC, datetime, timedelta, timezone

    aware = datetime(2026, 9, 21, 18, 0, tzinfo=timezone(timedelta(hours=-4)))
    assert main.as_utc(aware) == datetime(2026, 9, 21, 22, 0, tzinfo=UTC)
    naive = datetime(2026, 9, 21, 18, 0)
    assert main.as_utc(naive) is naive


@pytest.fixture
def prefixed(monkeypatch):
    monkeypatch.setenv("PREVIEW_NAME", "pr-7")
    app = main.create_app(DATABASE_URL, prefix="/pr-7")
    with TestClient(app) as client:
        with app.state.engine.begin() as conn:
            conn.execute(main.notes.delete())
        yield client


def test_prefix_serves_every_route_under_the_path(prefixed):
    assert prefixed.get("/pr-7/healthz").json() == {"status": "ok"}
    assert prefixed.get("/pr-7/api/info").json()["preview"] == "pr-7"
    assert prefixed.post("/pr-7/api/notes", json={"body": "under prefix"}).status_code == 201
    assert [n["body"] for n in prefixed.get("/pr-7/api/notes").json()] == ["under prefix"]
    assert prefixed.get("/healthz").status_code == 404


def test_prefix_redirects_bare_path_and_form_posts_back_under_it(prefixed):
    bare = prefixed.get("/pr-7", follow_redirects=False)
    assert bare.status_code == 307
    assert bare.headers["location"].endswith("/pr-7/")
    page = prefixed.get("/pr-7/").text
    assert 'action="/pr-7/notes"' in page
    posted = prefixed.post("/pr-7/notes", data={"body": "form"}, follow_redirects=False)
    assert posted.status_code == 303
    assert posted.headers["location"] == "/pr-7/"


def test_prefix_comes_from_environment_by_default(monkeypatch):
    monkeypatch.setenv("PATH_PREFIX", "/pr-9")
    app = main.create_app(DATABASE_URL)
    with TestClient(app) as client:
        assert client.get("/pr-9/healthz").status_code == 200
