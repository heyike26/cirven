import hashlib
import sqlite3

import pytest
from packages.domain.store import Store


def project(client, name="资料夹具"):
    return client.post("/api/projects", json={"name": name}).json()["id"]


def upload(client, pid, content=b"REQ: keep original files\nACCEPT: hashes match\n", name="original.md"):
    return client.post(f"/api/projects/{pid}/documents", files={"file": (name, content)},
                       data={"source": "original:synthetic-fixture"})


def test_raw_hash_source_search_and_version_history(client, tmp_path):
    pid = project(client)
    original = b"# Original\r\nREQ: hashes match\r\n"
    first = upload(client, pid, original).json()
    assert first["sha256"] == hashlib.sha256(original).hexdigest()
    store = Store(tmp_path / "eeas.sqlite3")
    assert store.document(pid, first["id"], 1)["raw_bytes"] == original
    assert client.get(f"/api/projects/{pid}/search", params={"keyword": "HASHES"}).json()["hits"][0]["line_start"] == 2
    second = client.put(f"/api/projects/{pid}/documents/{first['id']}",
                        files={"file": ("original.md", b"new version\n")},
                        data={"expected_revision": 1, "source": "original:edited"})
    assert second.json()["revision"] == 2
    assert client.get(f"/api/projects/{pid}/search", params={"keyword": "hashes"}).json()["hits"] == []
    assert len(client.get(f"/api/projects/{pid}/search", params={"keyword": "hashes", "revision": 1}).json()["hits"]) == 1
    assert store.document(pid, first["id"], 1)["raw_bytes"] == original
    conflict = client.put(f"/api/projects/{pid}/documents/{first['id']}",
                          files={"file": ("original.md", b"stale edit")}, data={"expected_revision": 1})
    assert conflict.status_code == 409
    with store.connect() as connection, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("UPDATE document_versions SET content='tampered'")


@pytest.mark.parametrize("name,data", [("../evil.md", b"x"), ("C:\\evil.txt", b"x"),
    ("original.pdf", b"x"), ("original.txt", b"\xff"), ("original.md", b"a\x00b"),
    ("huge.txt", b"x" * (1024 * 1024 + 1))])
def test_invalid_documents_are_rejected_without_records(client, name, data):
    pid = project(client)
    assert upload(client, pid, data, name).status_code == 422
    assert client.get(f"/api/projects/{pid}/documents").json()["documents"] == []


def test_project_filter_and_logical_delete_revoke_lookup(client, tmp_path):
    first, second = project(client, "A"), project(client, "B")
    doc = upload(client, first).json()
    assert client.get(f"/api/projects/{second}/documents/{doc['id']}").status_code == 404
    assert client.get(f"/api/projects/{second}/search", params={"keyword": "original"}).json()["hits"] == []
    assert client.delete(f"/api/projects/{second}/documents/{doc['id']}").status_code == 404
    assert client.delete(f"/api/projects/{first}/documents/{doc['id']}").status_code == 200
    assert client.get(f"/api/projects/{first}/documents/{doc['id']}").status_code == 404
    assert client.get(f"/api/projects/{first}/search", params={"keyword": "original"}).json()["hits"] == []
    with Store(tmp_path / "eeas.sqlite3").connect() as connection:
        assert connection.execute("SELECT count(*) FROM document_versions").fetchone()[0] == 1


def test_streamed_body_limit_without_content_length(client):
    pid = project(client)
    response = client.post(f"/api/projects/{pid}/documents",
                           content=iter([b"x" * 1024 * 1024] * 18), headers={"Content-Type": "multipart/form-data"})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "BODY_TOO_LARGE"
