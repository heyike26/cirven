"""Actual UI/API/SQLite/installed SDK; original external native JSON and loopback MCP only."""
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from claude_agent_sdk import ClaudeSDKClient
import httpx
from playwright.sync_api import expect, sync_playwright
import pytest
import uvicorn

from apps.api.config import Settings
from apps.api.main import create_app
from conftest import LiveServer
from test_desktop_design import project, proof, record_json, upload
from test_image_documents import original_image
from test_sdk_images import environment
from test_sdk_search_mcp import SearchWire, local_mcp, search_config
from test_web import launch_browser


@pytest.fixture
def actual_native_consent_server(tmp_path, monkeypatch, local_mcp):
    environment(monkeypatch, enabled=True)
    search_config(tmp_path, monkeypatch, local_mcp, limits={"max_calls": 2})
    wires = []
    def actual_client(options):
        wire = SearchWire(options)
        wires.append(wire)
        return ClaudeSDKClient(options=options, transport=wire)
    monkeypatch.setattr("packages.adapters.claude_sdk.ClaudeSDKClient", actual_client)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    settings = Settings(tmp_path, port=port, worker_enabled=True)
    app = create_app(settings)
    server = uvicorn.Server(uvicorn.Config(app, host=settings.host, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        with httpx.Client(trust_env=False) as client:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    if client.get(settings.origin + "/health", timeout=.2).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.02)
            else:
                pytest.fail("Actual native consent HTTP fixture did not start")
        yield SimpleNamespace(origin=settings.origin, data_dir=tmp_path, wires=wires, mcp=local_mcp)
    finally:
        server.should_exit = True
        thread.join(10)
        assert not thread.is_alive()


def image_task(page, server):
    page.goto(server.origin)
    pid = project(page, "原创图像审阅权限")
    raw = original_image("PNG")
    if os.environ.get("EEAS_EVIDENCE_DIR"):
        Path(os.environ["EEAS_EVIDENCE_DIR"], "original-native-consent.png").write_bytes(raw)
    upload(page, "原创图像权限.png", raw, "image/png")
    doc = page.request.get(f"{server.origin}/api/projects/{pid}/documents").json()["documents"][0]
    page.locator("#keyword").fill("原创图像权限")
    page.get_by_role("button", name="检索资料", exact=True).click()
    page.get_by_role("button", name="引用该来源", exact=True).click()
    page.locator("nav [data-view='requirements']").click()
    page.locator("#requirement-title").fill("原件输入而非图像理解资格")
    page.locator("#requirement-text").fill("人工核对原始 PNG，原生审阅只产生建议")
    page.locator("#acceptance-text").fill("冻结原图 SHA，未经独立验收的建议保持 NOT_RUN")
    page.locator("#confirmed").check()
    page.get_by_role("button", name="保存需求新修订", exact=True).click()
    expect(page.locator("#requirement-state")).to_contain_text("已保存 v1")
    page.locator("nav [data-view='tasks']").click()
    page.locator("#task-goal").fill("original-native-consent")
    page.get_by_role("button", name="创建执行任务", exact=True).click()
    page.locator("#execution-tasks").get_by_role("button", name="启动任务", exact=True).click()
    expect(page.locator("#run-status")).to_contain_text("候选已返回", timeout=15000)
    page.locator("#verify-run").click()
    expect(page.locator("#run-status")).to_contain_text("验收通过", timeout=15000)
    route = parse_qs(urlparse(page.url).query)
    run = page.request.get(f"{server.origin}/api/projects/{pid}/runs/{route['run'][0]}").json()
    page.locator("#native-review-panel summary").click()
    page.locator("#load-native-context").click()
    expect(page.locator("#native-context-panel")).to_be_visible()
    expect(page.locator("#native-image-list input")).to_have_count(1)
    asset = page.request.get(f"{server.origin}/api/projects/{pid}/documents/{doc['id']}/assets?revision=1").json()["assets"][0]
    return pid, doc, asset, raw, run


def test_actual_native_images_search_explicit_freeze_fork_and_window_restore(actual_native_consent_server):
    server = actual_native_consent_server
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        pid, doc, asset, raw, formal_before = image_task(page, server)
        expect(page.locator("#native-include-images")).not_to_be_checked()
        expect(page.locator("#native-search-enabled")).not_to_be_checked()
        expect(page.locator("#native-image-list input")).to_be_disabled()
        expect(page.locator("#native-input-consent-status")).to_contain_text("兼容性尚未验证")
        page.locator("#native-include-images").check()
        page.locator("#native-image-list input").check()
        page.locator("#native-search-enabled").check()
        page.wait_for_function("() => document.querySelector('#native-search-badge').getAnimations().some(animation => animation.playState === 'running' && animation.currentTime > 0)")
        expect(page.locator("#native-search-badge")).to_contain_text("已明确勾选")
        proof(page, "native-explicit-image-search-selection.png")
        with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path.endswith("/native-reviews")) as admitted:
            page.locator("#start-native-review").click()
        assert admitted.value.status == 202
        body = admitted.value.request.post_data_json
        assert body["include_images"] is True and body["search_enabled"] is True and body["needs_tools"] is False
        expected_ref = {"document_id": doc["id"], "revision": 1, "asset_id": asset["asset_id"], "sha256": asset["sha256"]}
        assert body["image_selections"] == [expected_ref]
        expect(page.locator("#native-review-status")).to_contain_text("RETURNED", timeout=20000)
        original = admitted.value.json()["admission_id"]
        original_row = page.request.get(f"{server.origin}/api/projects/{pid}/native-reviews/{original}").json()
        native_request = json.loads(original_row["native_execution"]["request"])
        assert native_request["image_refs"] == [expected_ref] and native_request["search_enabled"] is True
        assert original_row["verification_status"] == original_row["native_execution"]["verification_status"] == "NOT_RUN"
        assert len(server.wires) == len(server.mcp["calls"]) == 1
        users = [event for event in server.wires[0].writes if event["type"] == "user"]
        image = users[0]["message"]["content"][1]
        assert image["type"] == "image" and base64.b64decode(image["source"]["data"]) == raw
        expect(page.locator("#native-include-images")).to_be_disabled()
        expect(page.locator("#native-search-enabled")).to_be_disabled()
        page.locator("#native-followup-prompt").fill("Continue readonly with inherited exact original image and search consent")
        with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path.endswith("/fork")) as forked:
            page.locator("#fork-native-review").click()
        assert forked.value.status == 202
        child = forked.value.json()["admission_id"]
        expect(page.locator("#native-review-identity")).to_contain_text(child)
        expect(page.locator("#native-review-status")).to_contain_text("RETURNED", timeout=20000)
        child_row = page.request.get(f"{server.origin}/api/projects/{pid}/native-reviews/{child}").json()
        child_request = json.loads(child_row["native_execution"]["request"])
        assert child_request["image_refs"] == native_request["image_refs"] and child_request["search_enabled"] is True
        assert child_row["native_execution_id"] != original_row["native_execution_id"]
        assert len(server.wires) == len(server.mcp["calls"]) == 2
        assert all(call["tool"] == "original_approved_search" for call in server.mcp["calls"])
        with page.expect_popup() as opened:
            page.locator("#open-run-window").click()
        popup = opened.value
        popup.wait_for_load_state()
        popup.locator("#native-review-panel summary").click()
        expect(popup.locator("#native-review-identity")).to_contain_text(child)
        expect(popup.locator("#native-input-consent-status")).to_contain_text("已冻结：1 份原始图像")
        expect(popup.locator("#native-search-enabled")).to_be_checked()
        expect(popup.locator("#native-search-enabled")).to_be_disabled()
        popup.reload()
        popup.locator("#native-review-panel summary").click()
        expect(popup.locator("#native-input-consent-status")).to_contain_text("联网检索已明确允许")
        assert len(server.wires) == len(server.mcp["calls"]) == 2
        assert page.request.get(f"{server.origin}/api/projects/{pid}/runs/{formal_before['id']}").json() == formal_before
        with sqlite3.connect(server.data_dir / "eeas.sqlite3") as database:
            scopes = database.execute("SELECT root_budget_usd,prior_spent_usd,call_budget_usd FROM agent_scopes ORDER BY prior_spent_usd").fetchall()
        assert scopes == [("0.01", "0", "0.01"), ("0.01", "0.002", "0.008")]
        record_json("native-explicit-consent-backend.json", {"http_status": admitted.value.status, "posted_flags": body, "source_image_sha256": hashlib.sha256(raw).hexdigest(), "original_admission": original, "child_admission": child, "exact_image_ref": expected_ref, "native_verification": child_row["verification_status"], "actual_installed_sdk_client": True, "actual_native_wires": len(server.wires), "actual_local_mcp_calls": server.mcp["calls"], "shared_budget": scopes, "formal_run_unchanged": True, "popup_url": popup.url})
        proof(popup, "native-frozen-consent-restored-window.png")
        assert not errors
        browser.close()


def test_unconfigured_consent_is_visible_disabled_and_no_fake_admission(tmp_path, monkeypatch):
    for key in ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "EEAS_MODEL_ID", "EEAS_LIVE_MAX_COST_USD", "EEAS_MODEL_VISION_ENABLED"]:
        monkeypatch.setenv(key, "")
    monkeypatch.delenv("EEAS_SEARCH_MCP_CONFIG_PATH", raising=False)
    monkeypatch.delenv("EEAS_CLAUDE_SETTINGS_PATH", raising=False)
    server = LiveServer(tmp_path).start()
    try:
        with sync_playwright() as playwright:
            browser = launch_browser(playwright)
            page = browser.new_page(viewport={"width": 1440, "height": 1050})
            pid, doc, asset, raw, formal_before = image_task(page, server)
            expect(page.locator("#native-include-images")).to_be_disabled()
            expect(page.locator("#native-search-enabled")).to_be_disabled()
            expect(page.locator("#native-include-images")).not_to_be_checked()
            expect(page.locator("#native-search-enabled")).not_to_be_checked()
            expect(page.locator("#native-input-consent-status")).to_contain_text("MODEL_VISION_NOT_ENABLED")
            expect(page.locator("#native-input-consent-status")).to_contain_text("NOT_CONFIGURED")
            with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path.endswith("/native-reviews")) as blocked:
                page.locator("#start-native-review").click()
            assert blocked.value.status == 503
            body = blocked.value.request.post_data_json
            assert body["include_images"] is False and body["search_enabled"] is False and "image_selections" not in body
            expect(page.locator("#native-review-status")).to_contain_text("NO_CREDENTIALS")
            expect(page.locator("#native-review-identity")).to_have_text("")
            assert page.request.get(f"{server.origin}/api/projects/{pid}/runs/{formal_before['id']}").json() == formal_before
            with sqlite3.connect(tmp_path / "eeas.sqlite3") as database:
                assert database.execute("SELECT COUNT(*) FROM agent_executions").fetchone()[0] == 0
                assert database.execute("SELECT COUNT(*) FROM task_native_reviews").fetchone()[0] == 0
            proof(page, "native-unconfigured-image-search-gates.png")
            record_json("native-unconfigured-consent.json", {"posted_flags": body, "status": blocked.value.status, "agent_executions": 0, "admissions": 0, "formal_run_unchanged": True})
            browser.close()
    finally:
        server.stop()
