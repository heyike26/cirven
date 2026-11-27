"""Real selected folders, bytes/CAS and local-material document pipeline."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
from threading import Barrier
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from apps.api.config import Settings
from apps.api.main import create_app
from apps.api.local_workspaces import register_local_workspace_routes
from apps.api.taskbooks import register_taskbook_routes
from packages.domain.local_workspaces import FileEditInput
from test_pptx_export import template


def sha(raw): return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def local(tmp_path):
    data = tmp_path / "controller-data"; actual = tmp_path / "真实工作区 空格"
    actual.mkdir(); (actual / "src").mkdir()
    (actual / "src/中文说明.md").write_bytes("  原件空白\r\nREQ: 精确文件。\r\nACCEPT: 保留验证。\r\n\r\n".encode())
    (actual / "README.txt").write_bytes(b"original source file\n")
    (actual / "binary.png").write_bytes(b"\x89PNG\x00original binary fixture")
    settings = Settings(data); app = create_app(settings)
    register_local_workspace_routes(app, app.state.store, data)
    register_taskbook_routes(app, app.state.store, "original-local-workspace-caller")
    with TestClient(app, base_url=settings.origin) as client:
        csrf = client.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"}).json()["csrf_token"]
        client.headers.update({"Origin": settings.origin, "X-EEAS-CSRF": csrf})
        pid = client.post("/api/projects", json={"name": "实际本地目录项目"}).json()["id"]
        value = client.post(f"/api/projects/{pid}/local-workspaces", json={"idempotency_key": "original_folder", "root": str(actual), "allowed_write_paths": ["*"]})
        assert value.status_code == 201, value.text
        binding = value.json(); base = f"/api/projects/{pid}/local-workspaces/{binding['id']}"
        yield client, actual, pid, binding, base, settings


@pytest.fixture(autouse=True)
def original_local_receipt(request, local):
    yield
    if not os.environ.get("EEAS_EVIDENCE_DIR"): return
    client, actual, _, _, _, settings = local
    out = Path(os.environ["EEAS_EVIDENCE_DIR"]) / "fixtures" / request.node.name.replace("/", "_").replace("\\", "_")
    out.mkdir(parents=True)
    def exclude(folder, names):
        return [name for name in names if (Path(folder) / name).is_symlink()]
    if actual.exists(): shutil.copytree(actual, out / "actual-selected-folder", ignore=exclude)
    if (settings.data_dir / "local-workspaces").exists():
        shutil.copytree(settings.data_dir / "local-workspaces", out / "actual-source-registry", ignore=exclude)
    with client.app.state.store.connect() as connection:
        values = [dict(row) for row in connection.execute("SELECT document_id,revision,sha256,source FROM document_versions")]
    (out / "actual-document-source-bindings.json").write_text(json.dumps(values, ensure_ascii=False, indent=2))


def read(client, base, path="src/中文说明.md", revision=None):
    params = {"path": path}
    if revision: params["revision"] = revision
    response = client.get(base + "/file", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def edit_body(value, text="用户纠正后的真实内容\r\n", key="edit_1"):
    ref = value["source_ref"]
    return {"operation_id": key, "path": ref["path"], "expected_revision": ref["revision"],
            "expected_sha256": ref["sha256"], "content": text}


def materials(client, base, paths, key="material_1", expected=None):
    body = {"operation_id": key, "paths": paths}
    if expected is not None: body["expected_sources"] = expected
    return client.post(base + "/materials", json=body)


def test_real_unicode_folder_tree_exact_crlf_binary_and_no_whole_mirror(local):
    client, actual, pid, binding, base, settings = local
    assert binding["root"] == str(actual) and binding["native_location"]["directory"] == str(actual)
    assert client.get(f"/api/projects/{pid}/local-workspaces").json()["workspaces"] == [binding]
    tree = client.get(base + "/tree", params={"limit": 1}).json()
    assert tree["entries"][0]["type"] == "directory" and tree["next_offset"] == 1
    assert not (settings.data_dir / "local-workspaces" / pid / binding["id"] / "files").exists()
    value = read(client, base); raw = (actual / "src/中文说明.md").read_bytes()
    assert value["content"].encode() == raw and value["source_ref"]["sha256"] == sha(raw)
    assert value["current"] and value["encoding"] == "utf-8"
    binary = read(client, base, "binary.png")
    assert binary["encoding"] == "base64" and binary["source_ref"]["sha256"] == sha((actual / "binary.png").read_bytes())
    assert len(list((settings.data_dir / "local-workspaces" / pid / binding["id"] / "files").iterdir())) == 2


def test_scoped_cas_changes_actual_file_and_keeps_history_diff_permissions_and_retry(local):
    client, actual, _, _, base, _ = local
    path = actual / "src/中文说明.md"; original = path.read_bytes(); path.chmod(0o640)
    source = read(client, base); body = edit_body(source)
    saved = client.post(base + "/edits", json=body)
    assert saved.status_code == 200, saved.text
    value = saved.json(); assert value["outcome"] == "APPLIED" and value["source_ref"]["revision"] == 2
    assert "用户纠正" in value["diff"] and path.read_bytes() == body["content"].encode()
    assert path.stat().st_mode & 0o777 == 0o640
    assert read(client, base, revision=1)["content"].encode() == original
    retry = client.post(base + "/edits", json=body)
    assert retry.json()["replayed"] and len(client.get(base + "/history", params={"path": body["path"]}).json()["versions"]) == 2
    changed = {**body, "content": "different content"}
    assert client.post(base + "/edits", json=changed).status_code == 409


def test_external_source_change_marks_old_refs_stale_and_rejects_old_cas(local):
    client, actual, _, _, base, _ = local
    old = read(client, base); (actual / "src/中文说明.md").write_bytes("外部真实修改\n".encode())
    checked = client.post(base + "/source-check", json={"sources": [old["source_ref"]]}).json()
    assert checked["sources"][0]["status"] == "STALE"
    response = client.post(base + "/edits", json=edit_body(old))
    assert response.status_code == 409 and "SOURCE_DRIFT" in response.text
    assert (actual / "src/中文说明.md").read_bytes() == "外部真实修改\n".encode()
    current = read(client, base); assert current["source_ref"]["revision"] == 2
    assert read(client, base, revision=1)["current"] is False


def test_api_concurrency_accepts_one_cas_and_rejects_the_stale_other(local):
    client, actual, _, _, base, _ = local; source = read(client, base); barrier = Barrier(2)
    def submit(number):
        barrier.wait(timeout=5)
        return client.post(base + "/edits", json=edit_body(source, f"actual writer {number}\n", f"writer_{number}"))
    with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(submit, [1, 2]))
    assert sorted(value.status_code for value in results) == [200, 409], [value.text for value in results]
    assert (actual / "src/中文说明.md").read_bytes() in {b"actual writer 1\n", b"actual writer 2\n"}
    assert len(client.get(base + "/history", params={"path": "src/中文说明.md"}).json()["versions"]) == 2


def test_new_file_creation_is_real_revision_one_and_obeys_same_grant(local):
    client, actual, _, _, base, _ = local
    body = {"operation_id": "create_original", "path": "src/新文件.txt", "expected_revision": 0,
            "expected_sha256": None, "content": "真正新建的原件\n"}
    response = client.post(base + "/edits", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["source_ref"]["revision"] == 1 and (actual / body["path"]).read_text() == body["content"]


def test_partial_write_outcome_recovers_actual_saved_bytes_without_second_replace(local):
    client, actual, pid, binding, base, _ = local; source = read(client, base); body = edit_body(source)
    service = client.app.state.local_workspaces; real = service._replace
    def replace_then_lose_response(*args):
        real(*args)
        raise OSError("original response lost after actual replacement")
    with patch.object(service, "_replace", side_effect=replace_then_lose_response):
        with pytest.raises(OSError): service.edit(pid, binding["id"], FileEditInput.model_validate(body))
    assert (actual / body["path"]).read_bytes() == body["content"].encode()
    with patch.object(service, "_replace", side_effect=AssertionError("must not replace registered outcome again")):
        response = client.post(base + "/edits", json=body)
    assert response.status_code == 200 and response.json()["recovered"] and response.json()["replayed"], response.text
    state = client.get(base + "/operations/" + body["operation_id"]).json()
    assert state["status"] == "APPLIED" and len(state["failures"]) == 1


def test_interrupted_unknown_outcome_is_not_automatically_reapplied(local):
    client, actual, pid, binding, base, _ = local; body = edit_body(read(client, base))
    service = client.app.state.local_workspaces
    with patch.object(service, "_replace", side_effect=OSError("original write stopped before effect")):
        with pytest.raises(OSError): service.edit(pid, binding["id"], FileEditInput.model_validate(body))
    with patch.object(service, "_replace", side_effect=AssertionError("unknown outcome must not retry write")):
        response = client.post(base + "/edits", json=body)
    assert response.status_code == 409 and "UNKNOWN_OUTCOME" in response.text
    assert client.get(base + "/operations/" + body["operation_id"]).json()["status"] == "UNKNOWN_OUTCOME"
    assert (actual / body["path"]).read_bytes() != body["content"].encode()


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "src/../../x", "C:\\outside.txt", ".eeas-sdk-state/auth.json", ".git/config"])
def test_escape_and_controller_owned_paths_are_rejected(local, path):
    client, _, _, _, base, _ = local
    assert client.get(base + "/file", params={"path": path}).status_code == 422


def test_symlink_read_edit_tree_search_never_follows_external_file(local, tmp_path):
    client, actual, _, _, base, _ = local
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "private.txt").write_text("outside original must not leak")
    (actual / "alias").symlink_to(outside, target_is_directory=True)
    (actual / "alias-file.txt").symlink_to(outside / "private.txt")
    assert client.get(base + "/file", params={"path": "alias-file.txt"}).status_code == 422
    assert client.get(base + "/tree", params={"path": "alias"}).status_code == 422
    assert client.get(base + "/search", params={"path": "alias", "q": "outside"}).status_code == 422
    search = client.get(base + "/search", params={"q": "outside"})
    assert search.status_code == 200 and search.json()["hits"] == [] and "outside original must not leak" not in search.text


def test_read_only_and_directory_prefix_grants_are_enforced_without_extra_prompt(local):
    client, actual, pid, _, _, _ = local
    for key, scopes, allowed in [("readonly", [], False), ("src_only", ["src/"], True)]:
        binding = client.post(f"/api/projects/{pid}/local-workspaces", json={"idempotency_key": key, "root": str(actual), "allowed_write_paths": scopes}).json()
        base = f"/api/projects/{pid}/local-workspaces/{binding['id']}"
        result = client.post(base + "/edits", json=edit_body(read(client, base), key=key))
        assert result.status_code == (200 if allowed else 422)
        readme = read(client, base, "README.txt")
        assert client.post(base + "/edits", json=edit_body(readme, key=key + "_outside")).status_code == 422


def test_folder_replacement_and_cross_project_binding_are_rejected(local, tmp_path):
    client, actual, _, binding, base, _ = local
    other = client.post("/api/projects", json={"name": "other"}).json()["id"]
    assert client.get(f"/api/projects/{other}/local-workspaces/{binding['id']}").status_code == 404
    actual.rename(tmp_path / "original-moved-folder"); actual.mkdir()
    response = client.get(base)
    assert response.status_code == 409 and "ROOT_DRIFT" in response.text


def test_real_literal_search_returns_exact_source_refs_without_trim_or_fake_completion(local):
    client, actual, _, _, base, _ = local
    response = client.get(base + "/search", params={"q": "REQ:"})
    assert response.status_code == 200, response.text
    value = response.json(); assert not value["truncated"] and len(value["hits"]) == 1
    assert value["hits"][0]["source_ref"]["sha256"] == sha((actual / "src/中文说明.md").read_bytes())
    assert value["hits"][0]["line_number"] == 2


def test_selected_local_files_bridge_into_actual_intake_taskbook_and_report_without_upload(local):
    client, actual, pid, _, base, _ = local
    (actual / "用户任务书模板.md").write_text("# 用户实际模板\r\n{{taskbook:first}}\r\n{{taskbook:rest}}\r\n原模板末尾。", newline="")
    response = materials(client, base, ["src/中文说明.md", "用户任务书模板.md"])
    assert response.status_code == 201, response.text
    selected = response.json(); assert selected["status"] == "SAVED" and all(row["status"] == "SAVED" for row in selected["materials"])
    source, user_template = [row["document_ref"] for row in selected["materials"]]
    intake = client.post(f"/api/projects/{pid}/intakes", json={"idempotency_key": "actual_local_intake", "materials": [source]} )
    assert intake.status_code == 201, intake.text
    value = intake.json(); kinds = ["GOAL", "SCOPE", "DELIVERABLES", "ENVIRONMENT", "INTERFACES", "DATA", "DEPENDENCIES", "CONSTRAINTS", "ACCEPTANCE", "NOTES", "RISKS", "OPEN_QUESTIONS", "ASSUMPTIONS"]
    sections = [{"section_id": kind.lower(), "kind": kind, "heading": kind, "entries": []} for kind in kinds]
    sections[0]["entries"] = [{"entry_id": "actual_user_goal", "origin": "USER_STATEMENT", "text": "直接本地资料，共同澄清且保留来源。", "source_anchors": []}]
    book = client.post(f"/api/projects/{pid}/taskbooks", json={"idempotency_key": "actual_local_book", "intake_ref": {"intake_id": value["id"], "revision": value["revision"], "sha256": value["sha256"]}, "title": "真实本地任务书", "sections": sections, "template_ref": user_template,
        "template_mapping": [{"slot_id": "first", "section_ids": ["goal"]}, {"slot_id": "rest", "section_ids": [kind.lower() for kind in kinds[1:]]}]})
    assert book.status_code == 201, book.text
    assert "原模板末尾。" in book.json()["payload"]["markdown"] and book.json()["payload"]["model_calls"] == 0
    report_ref = {"kind": "document", "project_id": pid, **source}
    report = client.post(f"/api/projects/{pid}/report-artifacts", json={"idempotency_key": "actual_local_report", "report": {"title": "本地来源结果", "source_refs": [report_ref], "sections": [{"title": "边界", "paragraphs": ["实际字节及未运行事项。"]}]}})
    assert report.status_code == 201, report.text
    manifest = report.json()["artifact_manifest"]
    assert sha(client.get(manifest["download_url"]).content) == manifest["sha256"]
    assert report_ref["sha256"] == sha((actual / "src/中文说明.md").read_bytes())


def test_bridge_actual_pptx_original_unsupported_format_partial_and_safe_retry(local):
    client, actual, pid, _, base, _ = local
    (actual / "原用户母版.pptx").write_bytes(template())
    (actual / "不支持.csv").write_text("original,csv,not-invented-parser\n")
    response = materials(client, base, ["原用户母版.pptx", "不支持.csv"])
    assert response.status_code == 207, response.text
    rows = response.json()["materials"]
    assert rows[0]["status"] == "SAVED" and rows[1]["status"] == "FAILED"
    assert rows[0]["document_ref"]["sha256"] == sha((actual / "原用户母版.pptx").read_bytes())
    with client.app.state.store.connect() as connection: before = connection.execute("SELECT count(*) FROM documents").fetchone()[0]
    retry = materials(client, base, ["原用户母版.pptx", "不支持.csv"])
    assert retry.status_code == 207 and retry.json()["materials"][0] == rows[0]
    with client.app.state.store.connect() as connection: assert connection.execute("SELECT count(*) FROM documents").fetchone()[0] == before == 1


def test_changed_local_material_creates_document_revision_and_invalidates_old_taskbook(local):
    client, actual, pid, _, base, _ = local
    first = materials(client, base, ["README.txt"]).json()["materials"][0]
    intake = client.post(f"/api/projects/{pid}/intakes", json={"idempotency_key": "source_change_intake",
        "materials": [first["document_ref"]]}).json()
    kinds = ["GOAL", "SCOPE", "DELIVERABLES", "ENVIRONMENT", "INTERFACES", "DATA", "DEPENDENCIES", "CONSTRAINTS", "ACCEPTANCE", "NOTES", "RISKS", "OPEN_QUESTIONS", "ASSUMPTIONS"]
    book = client.post(f"/api/projects/{pid}/taskbooks", json={"idempotency_key": "source_change_book",
        "intake_ref": {"intake_id": intake["id"], "revision": intake["revision"], "sha256": intake["sha256"]},
        "title": "实际来源复核任务书", "sections": [{"section_id": kind.lower(), "kind": kind,
        "heading": kind, "entries": []} for kind in kinds]})
    assert book.status_code == 201 and book.json()["review_status"] == "CURRENT", book.text
    (actual / "README.txt").write_bytes(b"actual changed local source\n")
    old = materials(client, base, ["README.txt"])
    assert old.status_code == 200 and old.json()["source_checks"][0]["status"] == "STALE"
    second = materials(client, base, ["README.txt"], "new_actual_source").json()["materials"][0]
    assert first["document_ref"]["document_id"] == second["document_ref"]["document_id"]
    assert first["document_ref"]["revision"] == 1 and second["document_ref"]["revision"] == 2
    assert first["file_source_ref"]["revision"] == 1 and second["file_source_ref"]["revision"] == 2
    assert client.get(f"/api/projects/{pid}/taskbooks/{book.json()['id']}").json()["review_status"] == "NEEDS_REVIEW"


def test_material_expected_refs_reject_drift_and_concurrent_keys_deduplicate_document(local):
    client, actual, _, _, base, _ = local
    ref = read(client, base, "README.txt")["source_ref"]; (actual / "README.txt").write_bytes(b"source changed before material selection\n")
    response = materials(client, base, ["README.txt"], "stale_selection", [ref])
    assert response.status_code == 409 and "SOURCE_DRIFT" in response.text
    barrier = Barrier(2)
    def submit(number):
        barrier.wait(timeout=5)
        return materials(client, base, ["README.txt"], f"concurrent_material_{number}")
    with ThreadPoolExecutor(max_workers=2) as pool: responses = list(pool.map(submit, [1, 2]))
    assert all(response.status_code == 201 for response in responses), [response.text for response in responses]
    refs = [response.json()["materials"][0]["document_ref"] for response in responses]
    assert refs[0] == refs[1]
    with client.app.state.store.connect() as connection: assert connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 1


def test_restart_replays_material_and_edit_history_without_repeating_side_effect(local):
    client, actual, pid, binding, base, settings = local
    source = read(client, base, "README.txt"); body = edit_body(source)
    assert client.post(base + "/edits", json=body).status_code == 200
    material = materials(client, base, ["README.txt"]).json()
    app = create_app(settings); register_local_workspace_routes(app, app.state.store, settings.data_dir)
    with TestClient(app, base_url=settings.origin) as restarted:
        csrf = restarted.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"}).json()["csrf_token"]
        restarted.headers.update({"Origin": settings.origin, "X-EEAS-CSRF": csrf})
        with patch.object(app.state.local_workspaces, "_replace", side_effect=AssertionError("must not write again")):
            assert restarted.post(base + "/edits", json=body).json()["replayed"]
        replay = materials(restarted, base, ["README.txt"])
        assert replay.status_code == 200 and replay.json()["replayed"]
        assert replay.json()["materials"] == material["materials"]
        assert restarted.get(base).json()["root"] == str(actual)


def test_completed_material_replay_never_fakes_success_for_deleted_document(local):
    client, _, pid, _, base, _ = local
    source = materials(client, base, ["README.txt"]).json()["materials"][0]["document_ref"]
    assert client.delete(f"/api/projects/{pid}/documents/{source['document_id']}").status_code == 200
    replay = materials(client, base, ["README.txt"])
    assert replay.status_code == 409 and "no longer registered" in replay.text
