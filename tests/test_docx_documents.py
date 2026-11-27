import base64
import hashlib
import json

from test_documents import project, upload
from test_docx_import import word_parts
from test_diagram_import import archive
from test_diagram_documents import manual_requirement


def test_actual_long_docx_upload_source_parts_images_task_scope_revision_and_restart(client, tmp_path):
    pid = project(client)
    content = "长任务书" * 18001 + "最终中文尾部条件"
    raw = archive(word_parts(content))
    response = upload(client, pid, raw, "复杂 长任务书.docx")
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc["format"] == "docx" and doc["character_length"] > 70000
    assert doc["sha256"] == hashlib.sha256(raw).hexdigest() and doc["asset_count"] == 2
    path = f"/api/projects/{pid}/documents/{doc['id']}"
    row = client.get(path).json()
    for phrase in (content, "表格精确尾端", "原创页眉末端", "脚注完整末端", "批注完整末端"):
        assert phrase in row["content"]
    structure = client.get(path + "/structure", params={"revision": 1}).json()
    assert structure["format"] == "docx" and "content" not in structure
    assert structure["outline"]["parts"] and structure["graph"]["nodes"]
    assets = client.get(path + "/assets").json()["assets"]
    assert assets and all(asset["source_anchors"] for asset in assets)
    for asset in assets:
        preview = client.get(path + f"/assets/{asset['asset_id']}", params={"revision": 1})
        assert hashlib.sha256(preview.content).hexdigest() == asset["sha256"]
    hit = client.get(f"/api/projects/{pid}/search", params={"keyword": "最终中文尾部条件"}).json()["hits"][0]
    assert "最终中文尾部条件" in hit["excerpt"]
    req = client.post(f"/api/projects/{pid}/requirements", json=manual_requirement(doc, hit["line_start"])).json()
    task = client.post(f"/api/projects/{pid}/tasks", json={"goal": "核验Word来源", "requirement_refs": [
        {"requirement_id": req["id"], "revision": 1, "sha256": req["sha256"]}]}).json()
    route = f"/api/projects/{pid}/tasks/{task['id']}/runs"
    run = client.post(route, json={"revision": 1, "operation_id": "original-word-run"}).json()
    captured = json.loads(run["frozen_inputs"])["documents"][0]
    assert base64.b64decode(captured["raw_base64"]) == raw and captured["parse_receipt"]
    assert client.get(route + f"/{run['id']}/native-review-scope").status_code == 200
    native = client.app.state.native_reviews.frozen_scope(pid, task["id"], run["id"])
    assert content in next(iter(native["source_files"].values()))
    from packages.domain.store import Store
    reopened = Store(tmp_path / "eeas.sqlite3")
    assert reopened.document(pid, doc["id"], 1)["raw_bytes"] == raw
    assert reopened.document_assets(pid, doc["id"], 1) == assets
    changed = upload(client, project(client, "other"), raw, "隔离.docx").json()
    assert client.get(path + f"/assets/{assets[0]['asset_id']}", params={"revision": 2}).status_code == 404
    assert changed["project_id"] != pid
    assert client.put(path, files={"file": ("new.txt", b"new text")}, data={"expected_revision": 1}).status_code == 200
    assert client.get(path + "/download", params={"revision": 1}).content == raw
    assert client.get(f"/api/projects/{pid}/requirements/{req['id']}").json()["source_status"] == "STALE"


def test_docx_invalid_legacy_macros_and_unsafe_packages_reject_before_records(client):
    pid = project(client)
    for name, raw in (("legacy.doc", b"old binary"), ("macro.docm", archive(word_parts())),
                      ("broken.docx", b"not OOXML"), ("unsafe.docx", archive([("../word/document.xml", "x")]))):
        assert upload(client, pid, raw, name).status_code == 422
        assert client.get(f"/api/projects/{pid}/documents").json()["documents"] == []
