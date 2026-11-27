"""Original browser fixtures exercise real extension API/processes; SDK LIVE remains unqualified."""
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright

from test_extensions import archive
from test_web import launch_browser, open_project_form


def view(page, name):
    page.locator(f"nav [data-view='{name}']").click()


def create_project(page, name):
    view(page, "projects")
    open_project_form(page)
    page.locator("#name").fill(name)
    page.get_by_role("button", name="创建项目", exact=True).click()
    project = page.locator("#projects").get_by_role("button", name=name, exact=True)
    project.wait_for()
    pid = project.get_attribute("data-project-id")
    project.click()
    expect(page.locator("#project-context")).to_have_text(name)
    page.wait_for_url(lambda url: parse_qs(urlparse(url).query).get("project") == [pid])
    return pid


def import_archive(page, version, raw=None):
    page.locator("#extension-file").set_input_files({"name": f"original-v{version}.zip", "mimeType": "application/zip", "buffer": raw or archive(version)})
    page.get_by_role("button", name="导入候选快照", exact=True).click()
    title = f"eeas.fixture.formatter v{version}"
    row = page.locator("#extensions > li").filter(has=page.get_by_role("heading", name=title, exact=True))
    row.wait_for()
    return row, row.get_attribute("data-snapshot-id")


def approve_review(page, permissions):
    expect(page.locator("#approve-extension")).to_be_disabled()
    for permission in permissions:
        page.locator(f"#extension-review-permissions input[value='{permission}']").check()
    expect(page.locator("#approve-extension")).to_be_enabled()
    page.locator("#approve-extension").click()
    expect(page.locator("#extension-review")).to_be_hidden()


def authenticated_headers(page, origin):
    csrf = page.request.get(origin + "/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"}).json()["csrf_token"]
    return {"Origin": origin, "X-EEAS-CSRF": csrf}


def create_task(page, origin, pid, goal, headers):
    req = page.request.post(f"{origin}/api/projects/{pid}/requirements", headers=headers, data={
        "schema_version": "eeas.requirements.v1", "title": "原创扩展窗口需求",
        "requirements": [{"id": "R1", "text": "实际调用固定版本格式插件", "source_refs": []}],
        "acceptance": [{"id": "A1", "text": "核对版本、格式、快照与独立候选", "requirement_ids": ["R1"]}],
        "confirmed": True, "origin": "manual", "assumptions": [], "questions": []})
    assert req.ok, req.text()
    requirement = req.json()
    created = page.request.post(f"{origin}/api/projects/{pid}/tasks", headers=headers, data={
        "goal": goal, "requirement_refs": [{"requirement_id": requirement["id"], "revision": requirement["revision"], "sha256": requirement["sha256"]}],
        "allowed_write_paths": ["candidate.txt"], "dependencies": []})
    assert created.ok, created.text()
    return created.json()


def start_and_observe(context, page, origin, pid, task, headers):
    response = page.request.post(f"{origin}/api/projects/{pid}/tasks/{task['id']}/runs", headers=headers, data={
        "revision": task["revision"], "operation_id": f"original_browser_{task['id']}"})
    assert response.ok, response.text()
    run = response.json()
    window = context.new_page()
    window.goto(f"{origin}/?project={pid}&run={run['id']}&view=results&window=run")
    expect(window.locator("#run-status")).to_contain_text("候选已返回", timeout=20000)
    expect(window.locator("#run-events")).to_contain_text("原创格式调用：")
    events = page.request.get(f"{origin}/api/projects/{pid}/runs/{run['id']}/events").json()["events"]
    actual = next(item["payload"] for item in events if item["payload"]["type"] == "extension_result")
    return window, run, actual


def test_actual_extension_ui_permissions_fixed_versions_and_rollback(live_server):
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(live_server.origin)
        old_project = create_project(page, "原创固定旧组合")
        view(page, "capabilities")
        first, v1 = import_archive(page, 1)
        expect(first.locator(".extension-facts")).to_contain_text("支持：是 · 已加载：否 · 资格通过：否")
        assert page.request.get(live_server.origin + "/api/extensions").json()["extensions"][0]["channel"] == "CANDIDATE"
        first.get_by_role("button", name="执行原创资格检查", exact=True).click()
        expect(first.locator(".extension-facts")).to_contain_text("已加载：是 · 资格通过：是", timeout=15000)
        expect(first.locator(".extension-facts")).to_contain_text("候选")
        first.get_by_role("button", name="审阅切换或回滚", exact=True).click()
        expect(page.locator("#extension-review-action")).to_contain_text(v1)
        approve_review(page, ["text.format"])
        expect(first.locator(".extension-facts")).to_contain_text("稳定")
        first.get_by_role("button", name="为当前项目固定此组合", exact=True).click()
        expect(page.locator("#extension-binding")).to_contain_text("v1")
        original_binding = page.request.get(f"{live_server.origin}/api/projects/{old_project}/extension-binding").json()["binding"]
        assert original_binding["lock"]["snapshot_id"] == v1
        headers = authenticated_headers(page, live_server.origin)
        old_task = create_task(page, live_server.origin, old_project, "original-v1-ui", headers)
        assert old_task["spec"]["extension_lock"]["snapshot_id"] == v1
        second, v2 = import_archive(page, 2)
        expect(second.locator(".extension-facts")).to_contain_text("候选")
        second.get_by_role("button", name="执行原创资格检查", exact=True).click()
        expect(second.locator(".extension-facts")).to_contain_text("资格通过：是", timeout=15000)
        second.get_by_role("button", name="审阅切换或回滚", exact=True).click()
        expect(page.locator("#extension-review-diff")).to_contain_text("新增权限：text.length")
        expect(page.locator("#extension-review-action")).to_contain_text(v1)
        expect(page.locator("#extension-review-action")).to_contain_text(v2)
        page.locator("#extension-review-permissions input[value='text.format']").check()
        expect(page.locator("#approve-extension")).to_be_disabled()
        with sqlite3.connect(live_server.data_dir / "eeas.sqlite3") as connection:
            row = connection.execute("SELECT id,action_hash FROM extension_reviews WHERE snapshot_id=? AND status='PENDING'", (v2,)).fetchone()
        incomplete = page.request.post(f"{live_server.origin}/api/extension-reviews/{row[0]}/approve", headers=headers, data={"action_hash": row[1], "permissions": ["text.format"]})
        assert incomplete.status == 409
        actual = page.request.get(live_server.origin + "/api/extensions").json()["extensions"]
        assert next(item for item in actual if item["id"] == v1)["channel"] == "STABLE"
        if os.environ.get("EEAS_EVIDENCE_DIR"):
            page.screenshot(path=str(Path(os.environ["EEAS_EVIDENCE_DIR"]) / "new-permission-review.png"), full_page=True)
        page.locator("#extension-review-permissions input[value='text.length']").check()
        page.locator("#approve-extension").click()
        expect(second.locator(".extension-facts")).to_contain_text("稳定")
        expect(page.locator("#extension-binding")).to_contain_text("v1")
        assert page.request.get(f"{live_server.origin}/api/projects/{old_project}/extension-binding").json()["binding"] == original_binding
        new_project = create_project(page, "原创固定新组合")
        view(page, "capabilities")
        second.get_by_role("button", name="为当前项目固定此组合", exact=True).click()
        expect(page.locator("#extension-binding")).to_contain_text("v2")
        new_binding = page.request.get(f"{live_server.origin}/api/projects/{new_project}/extension-binding").json()["binding"]
        assert new_binding["lock"]["snapshot_id"] == v2
        new_task = create_task(page, live_server.origin, new_project, "original-v2-ui", headers)
        assert new_task["spec"]["extension_lock"]["snapshot_id"] == v2
        old_window, old_run, old_call = start_and_observe(context, page, live_server.origin, old_project, old_task, headers)
        new_window, new_run, new_call = start_and_observe(context, page, live_server.origin, new_project, new_task, headers)
        assert old_call["snapshot_id"] == v1 and old_call["result"] == "v1|original-v1-ui"
        assert new_call["snapshot_id"] == v2 and new_call["result"] == {"format": "v2", "text": "original-v2-ui", "length": len("original-v2-ui")}
        first.get_by_role("button", name="审阅切换或回滚", exact=True).click()
        expect(page.locator("#extension-review-diff")).to_contain_text("移除权限：text.length")
        approve_review(page, ["text.format"])
        expect(first.locator(".extension-facts")).to_contain_text("稳定")
        expect(second.locator(".extension-facts")).to_contain_text("候选")
        expect(page.locator("#extension-binding")).to_contain_text("v2")
        assert page.request.get(f"{live_server.origin}/api/projects/{old_project}/extension-binding").json()["binding"] == original_binding
        assert page.request.get(f"{live_server.origin}/api/projects/{new_project}/extension-binding").json()["binding"] == new_binding
        with sqlite3.connect(live_server.data_dir / "eeas.sqlite3") as connection:
            proof = json.loads(connection.execute("SELECT payload FROM extension_evidence WHERE snapshot_id=?", (v2,)).fetchone()[0])
            assert proof["actual_call"]["result"]["format"] == "v2"
            assert proof["sdk_plugin_live"] == "NOT_VALIDATED"
            assert connection.execute("SELECT snapshot_id FROM extension_stable").fetchone()[0] == v1
            assert connection.execute("SELECT COUNT(*) FROM extension_bindings").fetchone()[0] == 2
        if os.environ.get("EEAS_EVIDENCE_DIR"):
            directory = Path(os.environ["EEAS_EVIDENCE_DIR"])
            page.screenshot(path=str(directory / "rollback-keeps-project-v2.png"), full_page=True)
            old_window.screenshot(path=str(directory / "actual-v1-call.png"), full_page=True)
            new_window.screenshot(path=str(directory / "actual-v2-call.png"), full_page=True)
            (directory / "extension-backend.json").write_text(json.dumps({"old_binding": original_binding, "new_binding": new_binding,
                "old_run": old_run, "new_run": new_run, "old_actual_call": old_call, "new_actual_call": new_call,
                "v2_qualification": proof, "rolled_back_stable": v1, "environment": "WSL Chromium; SDK LIVE plugin NOT_VALIDATED"}, ensure_ascii=False, indent=2), encoding="utf-8")
        assert not errors
        browser.close()


def test_unknown_native_and_tampered_extension_stay_unavailable(live_server):
    def native(files):
        manifest = json.loads(files["manifest.json"])
        manifest.update(provider="other.sdk.plugin", adapter="sdk-native", native_parameters={"future_feature": {"option": 123}})
        files["manifest.json"] = json.dumps(manifest).encode()
        files["plugin.py"] = b"from pathlib import Path; Path('BAD').write_text('must never execute')"
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.goto(live_server.origin + "/?view=capabilities")
        page.locator("#extension-file").set_input_files({"name": "original-native.zip", "mimeType": "application/zip", "buffer": archive(2, native)})
        page.get_by_role("button", name="导入候选快照", exact=True).click()
        unknown = page.locator("#extensions > li").filter(has=page.get_by_role("heading", name="other.sdk.plugin v2", exact=True))
        expect(unknown.locator(".extension-facts")).to_contain_text("支持：否 · 已加载：否 · 资格通过：否")
        expect(unknown.get_by_role("button", name="执行原创资格检查", exact=True)).to_be_disabled()
        expect(unknown.get_by_role("button", name="审阅切换或回滚", exact=True)).to_be_disabled()
        unknown.locator("summary").click()
        expect(unknown.locator("pre")).to_contain_text('"option": 123')
        expect(unknown.locator(".extension-limit")).to_contain_text("原生参数已保留")
        assert not (live_server.data_dir / "extension-journal").exists()
        first, v1 = import_archive(page, 1)
        first.get_by_role("button", name="执行原创资格检查", exact=True).click()
        expect(first.locator(".extension-facts")).to_contain_text("资格通过：是", timeout=15000)
        (live_server.data_dir / "extensions" / v1 / "plugin.py").unlink()
        page.locator("#refresh-capabilities").click()
        expect(first.locator(".extension-facts")).to_contain_text("支持：是 · 已加载：否 · 资格通过：否")
        expect(first.locator(".extension-facts")).to_contain_text("INTEGRITY_ERROR")
        expect(first.get_by_role("button", name="执行原创资格检查", exact=True)).to_be_disabled()
        expect(first.get_by_role("button", name="审阅切换或回滚", exact=True)).to_be_disabled()
        actual = page.request.get(live_server.origin + "/api/extensions").json()
        assert actual["sdk_plugin_live"] == "NOT_VALIDATED"
        assert not any(item["qualified"] or item["loaded"] for item in actual["extensions"])
        assert not list(live_server.data_dir.rglob("BAD"))
        if os.environ.get("EEAS_EVIDENCE_DIR"):
            page.screenshot(path=str(Path(os.environ["EEAS_EVIDENCE_DIR"]) / "unavailable-extensions.png"), full_page=True)
        browser.close()
