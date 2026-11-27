import base64
import json

import pytest

from test_documents import project, upload
from test_image_documents import original_image
from test_diagram_documents import manual_requirement
from test_task_sdk_api import configured, until_review
from test_claude_sessions import NativeFactory


def formal_image(client):
    pid = project(client)
    raw = original_image()
    doc = upload(client, pid, raw, "原始框图.png").json()
    req = client.post(f"/api/projects/{pid}/requirements", json=manual_requirement(doc)).json()
    task = client.post(f"/api/projects/{pid}/tasks", json={"goal": "只读分析被选框图", "requirement_refs": [
        {"requirement_id": req["id"], "revision": 1, "sha256": req["sha256"]}]}).json()
    route = f"/api/projects/{pid}/tasks/{task['id']}/runs"
    run = client.post(route, json={"revision": 1, "operation_id": "formal-image"}).json()
    scope = client.get(route + f"/{run['id']}/native-review-scope").json()
    body = {"operation_id": "selected-image", "task_binding": scope["task_binding"], "input_hash": scope["input_hash"],
            "source_hash": scope["source_hash"], "needs_tools": False, "native": {}, "include_images": True}
    asset = scope["document_contexts"][0]["assets"][0]
    ref = {"document_id": doc["id"], "revision": 1, "asset_id": asset["asset_id"], "sha256": asset["sha256"]}
    return pid, route + f"/{run['id']}/native-reviews", run, body, ref, raw


def test_formal_task_selected_image_reaches_installed_sdk_and_keeps_run_unverified(claude_review_client, monkeypatch):
    client = claude_review_client
    pid, route, run, body, ref, raw = formal_image(client)
    factory = NativeFactory([0.002])
    configured(monkeypatch, factory)
    monkeypatch.setenv("EEAS_MODEL_VISION_ENABLED", "1")
    submitted = client.post(route, json={**body, "image_selections": [ref]})
    assert submitted.status_code == 202, submitted.text
    final = until_review(client, pid, submitted.json()["admission_id"], {"RETURNED"})
    wire = [row for row in factory.transports[0].writes if row["type"] == "user"][0]
    assert wire["message"]["content"][0]["type"] == "text"
    image = wire["message"]["content"][1]
    assert image["type"] == "image" and base64.b64decode(image["source"]["data"]) == raw
    request = json.loads(final["native_execution"]["request"])
    assert request["image_refs"] == [ref]
    assert final["verification_status"] == "NOT_RUN"
    assert client.get(f"/api/projects/{pid}/runs/{run['id']}").json() == run


@pytest.mark.parametrize("reason", ["default_profile", "wrong_hash", "another_document", "duplicate", "not_enabled", "arbitrary_url"])
def test_image_preflight_and_exact_frozen_selection_have_no_native_side_effects(claude_review_client, monkeypatch, reason):
    client = claude_review_client
    pid, route, _, body, ref, _ = formal_image(client)
    factory = NativeFactory([0.002])
    configured(monkeypatch, factory)
    monkeypatch.setenv("EEAS_MODEL_VISION_ENABLED", "0" if reason == "default_profile" else "1")
    selected = [ref]
    if reason == "wrong_hash": selected = [{**ref, "sha256": "0" * 64}]
    if reason == "another_document": selected = [{**ref, "document_id": "not-the-frozen-source"}]
    if reason == "duplicate": selected = [ref, ref]
    if reason == "not_enabled": body["include_images"] = False
    if reason == "arbitrary_url": selected = [{**ref, "url": "https://example.invalid/never-fetch"}]
    response = client.post(route, json={**body, "image_selections": selected})
    assert response.status_code == (503 if reason == "default_profile" else 422), response.text
    if reason == "default_profile": assert "MODEL_VISION_NOT_ENABLED" in response.json()["error"]["reasons"]
    assert not factory.transports
    with client.app.state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM task_native_reviews").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 0
