from concurrent.futures import ThreadPoolExecutor
import sqlite3

from apps.api.config import Settings
from packages.domain.store import Store
import pytest


def test_create_list_details_and_database_reopen(client, tmp_path):
    body = {"name": "原创项目", "description": "本地夹具，无企业资料"}
    response = client.post("/api/projects", json=body)
    assert response.status_code == 201
    project = response.json()
    assert client.get("/api/projects").json()["projects"] == [project]
    assert client.get(f"/api/projects/{project['id']}").json() == project
    assert Store(tmp_path / "eeas.sqlite3").project(project["id"]) == project
    assert client.get("/health").json()["storage"] == "sqlite"


@pytest.mark.parametrize("name", ["", "  ", "a" * 201])
def test_invalid_input_is_explained_without_inserting(client, name):
    response = client.post("/api/projects", json={"name": name})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"
    assert client.get("/api/projects").json()["projects"] == []


def test_not_found_and_no_raw_database_errors(client):
    assert client.get("/api/projects/missing").json()["error"]["code"] == "NOT_FOUND"


def test_local_auth_host_origin_and_csrf(client):
    assert client.get("/api/projects", headers={"Host": "evil.example"}).status_code == 403
    assert client.post("/api/projects", json={"name": "x"}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/projects", json={"name": "x"}, headers={"X-EEAS-CSRF": "wrong"}).status_code == 403
    assert client.get("/api/bootstrap").status_code == 403
    client.cookies.clear()
    assert client.get("/api/projects").status_code == 401
    assert client.get("/health").status_code == 200


def test_migrations_are_idempotent_and_project_writes_serialized(tmp_path):
    database = tmp_path / "fixture.sqlite3"
    store = Store(database)
    with store.connect() as connection:
        before_versions = [r[0] for r in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
    Store(database)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: store.create_project(str(i), "fixture"), range(16)))
    assert len(store.list_projects()) == 16
    with store.connect() as connection:
        assert [r[0] for r in connection.execute("SELECT version FROM schema_migrations ORDER BY version")] == before_versions


def test_remote_bind_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="localhost"):
        Settings(tmp_path, host="0.0.0.0")
