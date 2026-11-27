import base64
import hashlib
import json

import pytest

from test_diagram_import import archive, visio_parts, xmind_json, original_png
from test_documents import project, upload


def manual_requirement(doc, line=1):
    return {"schema_version": "eeas.requirements.v1", "title": "原创图示审阅",
            "requirements": [{"id": "R1", "text": "核对原图节点和连线", "source_refs": [
                {"document_id": doc["id"], "revision": 1, "sha256": doc["sha256"], "line_start": line, "line_end": line}]}],
            "acceptance": [{"id": "A1", "text": "原图哈希、节点与关系可追溯", "requirement_ids": ["R1"]}],
            "confirmed": True, "origin": "manual"}


@pytest.mark.parametrize("name,raw,format", [
    ("架构.vsdx", archive(visio_parts()), "visio-vsdx"),
    ("任务.xmind", archive([("content.json", json.dumps(xmind_json(), ensure_ascii=False)), ("resources/原创.png", original_png())]), "xmind-json")])
def test_real_diagram_upload_graph_source_assets_and_frozen_native_text(client, name, raw, format):
    pid = project(client)
    response = upload(client, pid, raw, name)
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc["format"] == format and doc["sha256"] == hashlib.sha256(raw).hexdigest()
    path = f"/api/projects/{pid}/documents/{doc['id']}"
    row = client.get(path).json()
    structure = client.get(path + "/structure", params={"revision": 1}).json()
    assert structure["sha256"] == doc["sha256"] and structure["graph"] == row["graph"]
    assert "content" not in structure and "raw_bytes" not in structure
    assert row["graph"]["nodes"] and row["warnings"]
    assert all(node["source"]["part"] for node in row["graph"]["nodes"])
    assets = client.get(path + "/assets").json()["assets"]
    assert assets and all(asset["source_anchors"] for asset in assets)
    for asset in assets:
        preview = client.get(path + f"/assets/{asset['asset_id']}", params={"revision": 1})
        assert hashlib.sha256(preview.content).hexdigest() == asset["sha256"]
    req = client.post(f"/api/projects/{pid}/requirements", json=manual_requirement(doc)).json()
    task = client.post(f"/api/projects/{pid}/tasks", json={"goal": "审阅真实解析内容", "requirement_refs": [
        {"requirement_id": req["id"], "revision": 1, "sha256": req["sha256"]}]}).json()
    route = f"/api/projects/{pid}/tasks/{task['id']}/runs"
    run = client.post(route, json={"revision": 1, "operation_id": "diagram-run"}).json()
    captured = json.loads(run["frozen_inputs"])["documents"][0]
    assert base64.b64decode(captured["raw_base64"]) == raw
    assert captured["parse_receipt"]["content_sha256"] == hashlib.sha256(row["content"].encode()).hexdigest()
    scope = client.get(route + f"/{run['id']}/native-review-scope")
    assert scope.status_code == 200, scope.text
    native = client.app.state.native_reviews.frozen_scope(pid, task["id"], run["id"])
    assert list(native["source_files"].values()) == [row["content"]]
    assert client.get(path + "/download", params={"revision": 1}).content == raw


def test_image_native_scope_uses_frozen_metadata_without_fake_ocr(client):
    pid = project(client)
    doc = upload(client, pid, original_png(), "frame.png").json()
    req = client.post(f"/api/projects/{pid}/requirements", json=manual_requirement(doc)).json()
    task = client.post(f"/api/projects/{pid}/tasks", json={"goal": "人工核对框图", "requirement_refs": [
        {"requirement_id": req["id"], "revision": 1, "sha256": req["sha256"]}]}).json()
    route = f"/api/projects/{pid}/tasks/{task['id']}/runs"
    run = client.post(route, json={"revision": 1, "operation_id": "image-run"}).json()
    scope = client.get(route + f"/{run['id']}/native-review-scope")
    assert scope.status_code == 200
    native = client.app.state.native_reviews.frozen_scope(pid, task["id"], run["id"])
    assert "尚未提取" in next(iter(native["source_files"].values()))


def test_unsafe_diagrams_are_rejected_before_any_database_write(client):
    pid = project(client)
    for name, raw in (("legacy.vsd", b"old binary"), ("bad.vsdx", b"broken zip"),
                      ("unsafe.xmind", archive([("../content.json", "[]")])),
                      ("entity.xmind", archive([("content.xml", '<!DOCTYPE x [<!ENTITY y "unsafe">]><x>&y;</x>')]))):
        assert upload(client, pid, raw, name).status_code == 422
        assert client.get(f"/api/projects/{pid}/documents").json()["documents"] == []
